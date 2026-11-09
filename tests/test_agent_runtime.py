"""Savings agent protocol, privacy boundary, local verification and safe fallback."""
from copy import deepcopy
import json
from pathlib import Path
import threading

import pytest

from finance.agent_tools import AgentTools, ToolValidationError
from finance.agent_planner import plan_savings_rules, render_rules_plan
from finance.agent_runtime import AggregateConsent, plan_savings_agent, aggregate_payload
from finance.agent_verifier import default_claims, verify_claims, render_claims, ClaimValidationError
from finance.llm import ModelClient
from finance.storage import Store
from test_agent_savings import history, inputs, TODAY, simulation_args


def action(tool, **args):
    return dict(action='call_tool', tool=tool, args=args)


def batch(historical=True, cut_pct=10):
    args = simulation_args()
    if historical:
        args.update(base_income_cents={'$ref': 'S2.average_income_cents'},
                    base_spending_cents={'$ref': 'S2.average_expense_cents'},
                    category_spending_cents={'$ref': 'S2.category_spending_cents'})
    args['scenarios'] = [dict(label='A', strategy='current'),
                         dict(label='B', strategy='cut_categories', categories=['餐饮', '交通'], cut_pct=cut_pct),
                         dict(label='C', strategy='required_cut')]
    return action('scenario_simulate_batch', **args)


class ScriptedModel:
    """A deterministic model substitute, NOT evidence of real-model task quality."""
    def __init__(self, actions=None, explanation=None, on_call=None):
        self.actions = actions or [action('data_coverage', months=3),
                                   action('spending_overview', period='last_3_months'),
                                   batch(), dict(action='finish')]
        self.explanation = explanation
        self.on_call = on_call
        self.payloads = []
        self.timeouts = []
        self.index = 0

    def plan_savings_action(self, payload, *, timeout):
        self.payloads.append(deepcopy(payload)); self.timeouts.append(timeout)
        if self.on_call: self.on_call(self, payload)
        item = self.actions[min(self.index, len(self.actions) - 1)]
        self.index += 1
        if isinstance(item, BaseException): raise item
        if payload['first_turn']:
            return dict(preview_steps=[], first_action=deepcopy(item))
        return deepcopy(item)

    def explain_savings_claims(self, payload, *, timeout):
        self.payloads.append(deepcopy(payload)); self.timeouts.append(timeout)
        if self.on_call: self.on_call(self, payload)
        if callable(self.explanation): return self.explanation(payload)
        return deepcopy(self.explanation) if self.explanation is not None else dict(
            claims=payload['allowed_claims'], suggestion='adjust_assumptions')


def run(store=None, **kwargs):
    defaults = dict(question='规划六个月存六千元', inputs=inputs(), today=TODAY,
                    client=ScriptedModel(), consent=AggregateConsent(True))
    defaults.update(kwargs)
    return plan_savings_agent(store, **defaults)


def test_explicit_assumption_switch_returns_action_even_when_inputs_already_ready(tmp_path):
    store = Store(tmp_path / 'empty.sqlite3')
    confirmed = inputs(income_cents=520000, budget_cents=485000)
    pending = plan_savings_rules(store, inputs=confirmed, today=TODAY)
    assert pending['status'] == 'needs_assumptions'
    assert pending['can_use_assumptions'] and pending['assumption_inputs_ready']
    assert pending['questions'] == []
    assert pending['available_actions'] == [dict(action='use_assumptions', mode='assumption', label='改用输入假设继续')]
    assert '改用输入假设继续' in render_rules_plan(pending)
    continued = plan_savings_rules(mode=pending['available_actions'][0]['mode'], inputs=confirmed, today=TODAY)
    assert continued['status'] == 'ok' and continued['basis'] == 'input_assumption'
    assert '未使用历史账单' in continued['assumptions'][0]


def test_switch_with_partial_input_only_asks_for_missing_values(tmp_path):
    plan = plan_savings_rules(Store(tmp_path / 'empty.sqlite3'), inputs=inputs(income_cents=520000), today=TODAY)
    assert plan['can_use_assumptions'] and not plan['assumption_inputs_ready']
    assert [q['input'] for q in plan['questions']] == ['budget_cents']


def test_model_loop_runs_actual_tools_and_verified_whole_sentence_claims(tmp_path):
    store = history(tmp_path); before = Path(store.path).read_bytes()
    client = ScriptedModel()
    plan = run(store, client=client)
    assert plan['planner'] == 'model_tools' and plan['model_status'] == 'verified'
    assert plan['model_calls'] == 5 and len(plan['trace']) == 3
    assert plan['total_tool_computations'] == 3
    assert [f['tool'] for f in plan['model_trace']] == ['data_coverage', 'spending_overview', 'scenario_simulate_batch']
    assert '¥2,100.00' in plan['answer'] and '¥3,120.00' in plan['answer'] and '¥650.00' in plan['answer']
    assert '模型理解 + 代码计算' in plan['answer']
    assert all(0 < t <= 15 for t in client.timeouts)
    assert Path(store.path).read_bytes() == before


def test_assumption_model_path_has_no_ledger_access():
    class ForbiddenStore:
        def spending_view(self): pytest.fail('must not read historical data')
    client = ScriptedModel([batch(False), dict(action='finish')])
    plan = run(ForbiddenStore(), mode='assumption', client=client,
               inputs=inputs(income_cents=520000, budget_cents=485000))
    assert plan['model_status'] == 'verified' and plan['model_calls'] == 3
    assert '未使用历史账单' in plan['answer'] and '缺少分类预算依据' in plan['answer']
    assert len(plan['facts']) == 1


@pytest.mark.parametrize('consent', [None, AggregateConsent(), AggregateConsent('true')])
def test_no_model_request_without_explicit_aggregate_consent(tmp_path, consent):
    client = ScriptedModel()
    plan = run(history(tmp_path), client=client, consent=consent)
    assert plan['planner'] == 'local_rules' and plan['model_status'] == 'consent_required'
    assert not client.payloads and plan['model_calls'] == 0


def test_not_configured_and_missing_inputs_are_local_results(tmp_path):
    store = history(tmp_path)
    assert run(store, client=None)['model_status'] == 'not_configured'
    client = ScriptedModel()
    plan = run(store, client=client, inputs=[])
    assert plan['status'] == 'needs_input' and plan['model_status'] == 'inputs_required'
    assert not client.payloads


def test_insufficient_history_does_not_send_aggregates_or_silently_switch(tmp_path):
    client = ScriptedModel()
    plan = run(Store(tmp_path / 'empty.sqlite3'), client=client,
               inputs=inputs(income_cents=520000, budget_cents=485000))
    assert plan['status'] == 'needs_assumptions' and plan['mode'] == 'historical'
    assert plan['can_use_assumptions'] and plan['assumption_inputs_ready']
    assert not client.payloads


def test_aggregate_payload_does_not_disclose_sources_ids_merchants_or_descriptions(tmp_path):
    store = history(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE transactions SET merchant='PRIVATE_MERCHANT',description='PRIVATE_DESCRIPTION',source_file='C:/PRIVATE_FILE.csv'")
    client = ScriptedModel()
    plan = run(store, client=client)
    assert plan['model_status'] == 'verified'
    outgoing = json.dumps(client.payloads, ensure_ascii=False)
    for forbidden in ('PRIVATE_', 'evidence_ids', 'source_file', 'refund_txn_id', 'source_txn_id', 'Authorization', 'api_key'):
        assert forbidden not in outgoing
    for fact in plan['facts'].values():
        for identifier in fact['evidence_ids']:
            assert identifier not in outgoing
    assert 'evidence_ids' in json.dumps(plan)


@pytest.mark.parametrize('stage', ['planning', 'explanation'])
def test_consent_can_be_revoked_mid_request_and_response_is_discarded(tmp_path, stage):
    consent = AggregateConsent(True)
    def revoke(client, payload):
        if (stage == 'planning' and client.index == 0) or (stage == 'explanation' and 'allowed_claims' in payload):
            consent.revoke()
    client = ScriptedModel(on_call=revoke)
    plan = run(history(tmp_path), client=client, consent=consent)
    assert plan['model_status'] == 'consent_revoked' and plan['planner'] == 'local_rules'
    assert not plan['aggregate_authorized']
    assert plan['model_calls'] == (1 if stage == 'planning' else 5)


def test_cancellation_stops_subsequent_calls(tmp_path):
    event = threading.Event()
    client = ScriptedModel(on_call=lambda *_: event.set())
    plan = run(history(tmp_path), client=client, cancel_event=event)
    assert plan['model_status'] == 'cancelled' and len(client.payloads) == 1


def test_deadline_discards_late_model_response(tmp_path):
    timer = [0.0]
    client = ScriptedModel(on_call=lambda *_: timer.__setitem__(0, 100.0))
    plan = run(history(tmp_path), client=client, clock=lambda: timer[0], time_budget=1)
    assert plan['model_status'] == 'timed_out' and plan['model_calls'] == 1
    assert client.timeouts == [1]


def test_repeated_cached_calls_still_hit_total_round_limit(tmp_path):
    client = ScriptedModel([action('data_coverage', months=3)])
    plan = run(history(tmp_path), client=client, max_rounds=5)
    assert plan['model_status'] == 'round_limit' and plan['model_calls'] == 4
    assert all(f['cached'] for f in plan['model_trace'])
    assert plan['total_tool_computations'] == 3  # Shared preflight, no hidden recomputation.


@pytest.mark.parametrize('bad', [dict(action='finish'), action('transactions'),
    action('delete_transaction', id='private'), action('spending_overview', period='last_3_months'),
    dict(action='ask', question='请重新输入目标金额'), dict(action='call_tool', tool=[], args={}),
    dict(action='finish', answer='你肯定能达标')])
def test_invalid_actions_fail_closed_without_executing_writes(tmp_path, bad):
    store = history(tmp_path); before = Path(store.path).read_bytes()
    plan = run(store, client=ScriptedModel([bad]))
    assert plan['planner'] == 'local_rules' and plan['model_status'] == 'invalid_action'
    assert Path(store.path).read_bytes() == before


def test_preview_is_not_executed(tmp_path):
    class PreviewOnly(ScriptedModel):
        def plan_savings_action(self, payload, *, timeout):
            result = super().plan_savings_action(payload, timeout=timeout)
            if payload['first_turn']:
                result['preview_steps'] = [dict(tool='scenario_simulate_batch', args={})]
            return result
    plan = run(history(tmp_path), client=PreviewOnly())
    assert plan['model_status'] == 'verified' and len(plan['model_trace']) == 3


def test_model_amount_cannot_replace_input_reference(tmp_path):
    fake_batch = batch(); fake_batch['args']['target_cents'] = 600000
    plan = run(history(tmp_path), client=ScriptedModel([
        action('data_coverage', months=3), action('spending_overview', period='last_3_months'), fake_batch]))
    assert plan['model_status'] == 'invalid_action'


def test_model_can_choose_different_cut_assumption_but_cannot_compute_money(tmp_path):
    client = ScriptedModel([action('data_coverage', months=3),
        action('spending_overview', period='last_3_months'), batch(cut_pct=20), dict(action='finish')])
    plan = run(history(tmp_path), client=client)
    assert plan['model_status'] == 'verified'
    row = next(r for r in plan['simulation']['facts']['scenarios'] if r['label'] == 'B')
    assert row['spending_cut_cents'] == 34000 and row['projected_cents'] == 414000
    assert plan['total_tool_computations'] == 4
    assert '假设餐饮、交通各压缩 20%' in plan['answer']
    final_payload = client.payloads[-2]
    policy = final_payload['facts'][-1]['facts']['scenario_policy']
    assert policy[1] == dict(label='B', strategy='cut_categories', categories=['餐饮', '交通'], cut_pct=20)
    assert final_payload['last_tool_call']['tool'] == 'scenario_simulate_batch'
    assert final_payload['remaining_rounds'] < 10


def test_fallback_reuses_same_snapshot_when_ledger_changes_during_model_request(tmp_path):
    store = history(tmp_path)
    def change(*_):
        with store.connect() as db:
            db.execute("UPDATE transactions SET amount_cents=1 WHERE kind='支出'")
    client = ScriptedModel([TimeoutError('SECRET_KEY=private')], on_call=change)
    plan = run(store, client=client)
    assert plan['model_status'] == 'model_failure'
    assert plan['facts']['S2']['facts']['average_expense_cents'] == 485000
    assert 'SECRET_KEY' not in json.dumps(plan)


@pytest.mark.parametrize('mutation', ['wrong_period', 'wrong_basis', 'wrong_fact', 'wrong_statement',
                                    'extra_amount', 'free_comment', 'missing_scenario', 'duplicate_claim', 'bad_suggestion'])
def test_verifier_rejects_wrong_meaning_not_only_wrong_amounts(tmp_path, mutation):
    plan = plan_savings_rules(history(tmp_path), inputs=inputs(), today=TODAY)
    payload = dict(claims=default_claims(plan), suggestion='none')
    if mutation == 'wrong_period': payload['claims'][0]['scope']['period'] = '2020-01'
    elif mutation == 'wrong_basis': payload['claims'][0]['scope']['basis'] = 'original_purchase_month'
    elif mutation == 'wrong_fact': payload['claims'][0]['fact_id'] = 'F1'
    elif mutation == 'wrong_statement': payload['claims'][0]['statement'] = 'category_total'
    elif mutation == 'extra_amount': payload['claims'][0]['amount_cents'] = 1
    elif mutation == 'free_comment': payload['non_numeric_comment'] = '其他分类都没有问题'
    elif mutation == 'missing_scenario': payload['claims'].pop()
    elif mutation == 'duplicate_claim': payload['claims'][-1] = deepcopy(payload['claims'][-2])
    elif mutation == 'bad_suggestion': payload['suggestion'] = '肯定能达标'
    with pytest.raises(ClaimValidationError): verify_claims(plan, payload)


def test_invalid_claims_fall_back_to_full_local_answer(tmp_path):
    client = ScriptedModel(explanation=dict(claims=[], non_numeric_comment='没有其他问题'))
    plan = run(history(tmp_path), client=client)
    assert plan['model_status'] == 'invalid_claims' and plan['planner'] == 'local_rules'
    assert '¥3,120.00' in plan['answer'] and '没有其他问题' not in plan['answer']


def test_valid_claims_can_be_reordered_without_changing_facts(tmp_path):
    plan = plan_savings_rules(history(tmp_path), inputs=inputs(), today=TODAY)
    claims = list(reversed(default_claims(plan)))
    answer = render_claims(plan, dict(claims=claims, suggestion='none'))
    assert '¥3,120.00' in answer and '口径：cash' in answer


def test_model_adapter_uses_existing_transport_and_request_timeout(monkeypatch):
    client = ModelClient('https://example.invalid/v1', 'test-model', 'PRIVATE_API_KEY', enabled=True)
    calls = []
    def fake(instruction, data, *, timeout):
        calls.append((instruction, data, timeout))
        return dict(action='finish')
    monkeypatch.setattr(client, '_request_json', fake)
    client.plan_savings_action(dict(test='planning'), timeout=4)
    client.explain_savings_claims(dict(test='claims'), timeout=2)
    assert [c[2] for c in calls] == [4, 2] and client.connection_verified
    assert 'PRIVATE_API_KEY' not in json.dumps(calls)


@pytest.mark.parametrize('kwargs', [dict(max_rounds=0), dict(max_rounds=True),
                                  dict(time_budget=float('nan')), dict(time_budget=200)])
def test_runtime_limits_are_validated(kwargs):
    with pytest.raises(ToolValidationError):
        run(mode='assumption', inputs=inputs(income_cents=1, budget_cents=0), **kwargs)


def test_existing_http_json_adapter_can_execute_the_whole_protocol(monkeypatch, tmp_path):
    """Wire-format integration with a synthetic provider; no external HTTP call."""
    from io import BytesIO
    import finance.llm as llm
    outgoing = []
    responses = [dict(preview_steps=[], first_action=action('data_coverage', months=3)),
                 action('spending_overview', period='last_3_months'), batch(), dict(action='finish')]
    def request(req, *, timeout):
        payload = json.loads(req.data)
        data = json.loads(payload['messages'][1]['content'])
        outgoing.append(data)
        result = responses.pop(0) if responses else dict(claims=data['allowed_claims'], suggestion='none')
        assert timeout <= 15 and req.get_header('Authorization') == 'Bearer PRIVATE_API_KEY'
        return BytesIO(json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(
            content=json.dumps(result)))] )).encode())
    monkeypatch.setattr(llm, 'urlopen', request)
    client = ModelClient('https://example.invalid/v1', 'synthetic', 'PRIVATE_API_KEY', enabled=True)
    plan = run(history(tmp_path), client=client)
    assert plan['model_status'] == 'verified' and client.connection_verified
    assert len(outgoing) == 5 and 'PRIVATE_API_KEY' not in json.dumps(outgoing)


def test_cli_explicit_switch_does_not_open_the_supplied_ledger(tmp_path):
    import subprocess
    import sys
    nonexistent = tmp_path / 'must-not-be-opened.sqlite3'
    command = [sys.executable, '-m', 'finance.agent_planner', '--ledger', str(nonexistent),
        '--use-assumptions', '--target-cents', '600000', '--months', '6', '--saved-cents', '0',
        '--income-cents', '520000', '--budget-cents', '485000', '--confirm-inputs', '--json']
    output = subprocess.run(command, capture_output=True, check=True)
    plan = json.loads(output.stdout)
    assert plan['mode'] == 'assumption' and plan['status'] == 'ok' and not nonexistent.exists()


def test_preflight_and_model_share_six_actual_computations_not_two_separate_budgets(tmp_path):
    actions = [action('data_coverage', months=3), action('spending_overview', period='last_3_months')]
    actions += [batch(cut_pct=pct) for pct in (20, 30, 40, 50)]
    plan = run(history(tmp_path), client=ScriptedModel(actions), max_rounds=12)
    assert plan['model_status'] == 'invalid_action'
    assert plan['total_tool_computations'] == 6
    assert plan['planner'] == 'local_rules' and '¥3,120.00' in plan['answer']


def test_preflight_cache_does_not_make_future_references_visible(tmp_path):
    tools = AgentTools(history(tmp_path), inputs=inputs(), today=TODAY)
    from finance.agent_planner import plan_with_tools
    plan_with_tools(tools.fresh_session(share_cache=True))
    assert tools.computation_count == 3 and tools.facts == {}
    with pytest.raises(ToolValidationError): tools._resolve({'$ref': 'S2.average_income_cents'})
    tools.call('data_coverage', dict(months=3))
    with pytest.raises(ToolValidationError): tools._resolve({'$ref': 'S2.average_income_cents'})


def test_agent_timeout_status_uses_actual_request_timeout_without_echoing_error(monkeypatch):
    client = ModelClient('https://example.invalid/v1', 'synthetic', 'PRIVATE_API_KEY', enabled=True)
    def timeout(*args, **kwargs): raise TimeoutError('PRIVATE_API_KEY')
    monkeypatch.setattr(client, '_request_json', timeout)
    with pytest.raises(TimeoutError): client.plan_savings_action({}, timeout=3)
    assert '3 秒' in client.last_error and '60 秒' not in client.last_error
    assert 'PRIVATE_API_KEY' not in client.last_error
