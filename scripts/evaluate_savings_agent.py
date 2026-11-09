"""Opt-in live smoke on authored synthetic savings cases; never opens personal ledgers.

Default invocation only lists cases. Keys stay inside ModelSettings/ModelClient;
neither provider bodies nor free model prose are written to evidence artifacts.
"""
import argparse
from copy import deepcopy
import csv
from datetime import date, datetime, timezone
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finance.agent_runtime import AggregateConsent, plan_savings_agent
from finance.agent_tools import confirmed_inputs
from finance.importer import parse_statement
from finance.llm import ModelClient, connection_error_message
from finance.model_settings import ModelSettings
from finance.storage import Store

TODAY = date(2026, 11, 9)
BASE = dict(target_cents=600000, months=6, saved_cents=0)
CASES = [
    dict(id='L1', kind='live', mode='historical', question='用近三个月开销，规划六个月存六千元，餐饮和交通各压缩10%作为方案B。',
         inputs=BASE, expected=dict(A=210000, B=312000, C=600000), cut_pct=10),
    dict(id='L2', kind='live', mode='assumption', question='按我确认的收入和预算假设，规划六个月存六千元。',
         inputs=dict(BASE, income_cents=520000, budget_cents=485000), expected=dict(A=210000, C=600000)),
    dict(id='L3', kind='live', mode='assumption', question='我当前是负现金流，按确认的输入看看储蓄目标有什么缺口，不要保证能达成。',
         inputs=dict(target_cents=1200000, months=6, saved_cents=1000, income_cents=100000, budget_cents=200000),
         expected=dict(A=-599000, C=601000)),
    dict(id='L4', kind='live', mode='historical', question='按确认的目标和已有储蓄做十二个月规划，方案B假设餐饮和交通各压缩20%。',
         inputs=dict(target_cents=1200000, months=12, saved_cents=120000),
         expected=dict(A=540000, B=948000, C=1200000), cut_pct=20),
    dict(id='L5', kind='live', mode='assumption', question='按已确认的金额和三个月期限做储蓄模拟，金额很小，请保留到分。',
         inputs=dict(target_cents=101, months=3, saved_cents=1, income_cents=100, budget_cents=100), expected=dict(A=1, C=103)),
    dict(id='G1', kind='local_gate', mode='historical', question='帮我做六个月储蓄规划。',
         inputs=dict(months=6, saved_cents=0), expected_status='needs_input'),
    dict(id='G2', kind='local_gate', mode='historical', question='帮我存六千元。',
         inputs=dict(target_cents=600000, saved_cents=0), expected_status='needs_input'),
    dict(id='G3', kind='local_gate', mode='historical', question='只有一个月数据，也想做储蓄规划。',
         inputs=BASE, months_present=('10',), expected_status='needs_assumptions'),
]


def synthetic_ledger(directory, months=('08', '09', '10')):
    stream = StringIO(); writer = csv.writer(stream)
    writer.writerow(['交易ID', '日期', '商户', '描述', '金额', '类型', '平台', '币种'])
    for month in months:
        for suffix, amount, kind, merchant, description in (
            ('I', '5200', '收入', '合成公司', '工资'), ('D', '1000', '支出', '合成餐厅', '午餐'),
            ('T', '700', '支出', '合成地铁', '交通'), ('O', '3150', '支出', '合成房东', '房租')):
            writer.writerow([month + suffix, f'2026-{month}-15', merchant, description, amount, kind, '样例', 'CNY'])
    store = Store(directory / 'synthetic.sqlite3')
    store.import_rows(parse_statement(stream.getvalue().encode('utf-8-sig'), 'authored-synthetic.csv').rows)
    return store


def violations(response):
    flags = dict(money_literal=False, free_text=False)
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith('_cents') and not (isinstance(item, dict) and set(item) in ({'$input'}, {'$ref'})):
                    flags['money_literal'] = True
                if key in ('answer', 'text', 'explanation', 'non_numeric_comment') and item:
                    flags['free_text'] = True
                walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
    walk(response)
    return flags


class RecordingClient:
    def __init__(self, client):
        self.client, self.calls = client, []

    def _call(self, method, payload, timeout):
        start = time.perf_counter()
        entry = dict(stage=method, payload_sha256=sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        try:
            response = getattr(self.client, method)(payload, timeout=timeout)
            entry.update(violations(response))
            action = response.get('first_action', response)
            if not isinstance(action, dict):
                action = {}
            entry['action'] = action.get('action') if action.get('action') in ('call_tool', 'finish') else 'other'
            entry['tool'] = action.get('tool') if action.get('tool') in ('data_coverage', 'spending_overview', 'scenario_simulate_batch') else None
            entry['claims_count'] = len(response['claims']) if isinstance(response.get('claims'), list) else 0
            return response
        except Exception as exc:
            entry['error'] = connection_error_message(exc, timeout_seconds=timeout)
            raise
        finally:
            entry['seconds'] = round(time.perf_counter() - start, 3)
            self.calls.append(entry)

    def plan_savings_action(self, payload, *, timeout):
        return self._call('plan_savings_action', payload, timeout)

    def explain_savings_claims(self, payload, *, timeout):
        return self._call('explain_savings_claims', payload, timeout)


def run_case(case, client, directory):
    store = synthetic_ledger(directory, case.get('months_present', ('08', '09', '10'))) if case['mode'] == 'historical' else None
    before = Path(store.path).read_bytes() if store else None
    recorder = RecordingClient(client)
    start = time.perf_counter()
    plan = plan_savings_agent(store, question=case['question'], inputs=confirmed_inputs(**case['inputs']),
                              mode=case['mode'], today=TODAY, client=recorder, consent=AggregateConsent(True))
    rows = {row['label']: row for row in plan['simulation']['facts']['scenarios']} if plan['simulation'] else {}
    expected_ok = all(rows.get(label, {}).get('projected_cents') == value for label, value in case.get('expected', {}).items())
    policy_ok = True
    if 'cut_pct' in case and plan['simulation']:
        policy = next(s for s in plan['simulation']['args']['scenarios'] if s['label'] == 'B')
        policy_ok = policy['cut_pct'] == case['cut_pct'] and set(policy['categories']) == {'餐饮', '交通'}
    unchanged = not store or Path(store.path).read_bytes() == before
    if case['kind'] == 'live':
        passed = plan['model_status'] == 'verified' and expected_ok and policy_ok and unchanged and not any(
            c.get('money_literal') or c.get('free_text') for c in recorder.calls)
    else:
        passed = plan['status'] == case['expected_status'] and not recorder.calls and unchanged
    return dict(id=case['id'], kind=case['kind'], question=case['question'], inputs=case['inputs'], passed=passed,
                status=plan['status'], model_status=plan['model_status'], fallback=plan['planner'] == 'local_rules',
                latency_seconds=round(time.perf_counter() - start, 3), http_attempts=len(recorder.calls),
                calls=recorder.calls, tool_trace=plan['model_trace'], total_tool_computations=plan['total_tool_computations'],
                expected_amounts_match=expected_ok, requested_policy_followed=policy_ok, source_ledger_unchanged=unchanged,
                scenarios=rows, answer=plan['answer'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly authorize sending ONLY authored synthetic summaries to the saved provider')
    parser.add_argument('--settings-dir', type=Path)
    parser.add_argument('--cases', default=','.join(c['id'] for c in CASES))
    parser.add_argument('--output', type=Path, default=ROOT / '.test_agent_evidence/agent-live-smoke.json')
    args = parser.parse_args()
    selected = [c for c in CASES if c['id'] in args.cases.split(',')]
    if not selected or set(args.cases.split(',')) != {c['id'] for c in selected}:
        parser.error('Unknown case IDs')
    sys.stdout.reconfigure(encoding='utf-8')
    if not args.live:
        print(json.dumps(dict(live=False, cases=[dict(id=c['id'], kind=c['kind']) for c in selected]), indent=2))
        return
    if args.settings_dir is None:
        parser.error('--live requires an explicit --settings-dir')
    config = ModelSettings(args.settings_dir).load()
    if not config.enabled:
        parser.error('Saved model is disabled or unavailable; configuration was not changed')
    client = ModelClient(config.base_url, config.model, config.api_key, enabled=True)
    report = dict(timestamp=datetime.now(timezone.utc).isoformat(), reference_date=TODAY.isoformat(),
                  provider_host=urlparse(config.base_url).hostname, model=config.model,
                  prompt_sha256=sha256((ROOT / 'finance/agent_protocol.py').read_bytes()).hexdigest(),
                  data='authored synthetic records and confirmed synthetic inputs only', results=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='savings-smoke-', dir=args.output.parent) as temporary:
        for index, case in enumerate(selected):
            directory = Path(temporary) / str(index); directory.mkdir()
            row = run_case(case, client, directory)
            report['results'].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: row[k] for k in ('id', 'kind', 'passed', 'model_status', 'http_attempts', 'latency_seconds')}, ensure_ascii=False), flush=True)
    if any(not r['passed'] for r in report['results']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
