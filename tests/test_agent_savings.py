"""Business checks for the isolated, read-only savings MVP, without an API key."""
from datetime import date
from pathlib import Path
import subprocess
import sys

import pytest

from finance.agent_tools import AgentTools, InputFact, ToolValidationError, confirmed_inputs
from finance.agent_planner import plan_savings_rules, render_rules_plan
from finance.storage import Store
from finance.importer import parse_statement
from test_core import csv_bytes
from test_m2_1 import ledger

TODAY = date(2026, 11, 9)


def inputs(**changes):
    values = dict(target_cents=600000, months=6, saved_cents=0)
    values.update(changes)
    return confirmed_inputs(**values)


def history(tmp_path):
    store = Store(tmp_path / 'savings.sqlite3')
    rows = []
    for month in ('08', '09', '10'):
        for suffix, amount, kind, merchant, description in (
            ('I', '5200', '收入', '公司', '工资'),
            ('D', '1000', '支出', '餐厅', '午餐'),
            ('T', '700', '支出', '地铁', '交通'),
            ('O', '3150', '支出', '房东', '房租')):
            rows.append([month + suffix, f'2026-{month}-15 12:00:00', merchant, description,
                         amount, kind, '样例', 'CNY'])
    store.import_rows(parse_statement(csv_bytes(rows), 'synthetic.csv').rows)
    return store


def scenarios(plan):
    return {row['label']: row for row in plan['simulation']['facts']['scenarios']}


def test_history_original_example_real_import_and_no_writes(tmp_path):
    store = history(tmp_path)
    before = Path(store.path).read_bytes()
    plan = plan_savings_rules(store, inputs=inputs(), today=TODAY)
    assert plan['status'] == 'ok' and plan['planner'] == 'local_rules'
    assert plan['as_of'] == TODAY.isoformat()
    assert {item['name']: item['value'] for item in plan['inputs']} == dict(
        target_cents=600000, months=6, saved_cents=0)
    rows = scenarios(plan)
    assert [(rows[k]['projected_cents'], rows[k]['gap_cents']) for k in 'ABC'] == [
        (210000, 390000), (312000, 288000), (600000, 0)]
    assert rows['B']['spending_cut_cents'] == 17000
    assert rows['C']['spending_cut_cents'] == 65000 and rows['C']['conditional']
    assert len(plan['trace']) == 3 and len(plan['simulation']['evidence_ids']) == 12
    assert plan['facts']['S1']['facts']['completeness'] == 'unknown'
    assert Path(store.path).read_bytes() == before
    assert '¥3,120.00' in render_rules_plan(plan)


def test_assumptions_never_read_store_and_do_not_invent_category_spending():
    class ForbiddenStore:
        def spending_view(self):
            pytest.fail('assumption mode read a personal ledger')
    plan = plan_savings_rules(ForbiddenStore(), mode='assumption', today=TODAY,
                             inputs=inputs(income_cents=520000, budget_cents=485000))
    rows = scenarios(plan)
    assert plan['basis'] == 'input_assumption' and len(plan['trace']) == 1
    assert rows['A']['projected_cents'] == 210000 and rows['C']['gap_cents'] == 0
    assert rows['B']['status'] == 'insufficient_data'
    assert plan['simulation']['evidence_ids'] == []
    assert 'IN:income_cents' in plan['simulation']['input_fact_ids']


def test_no_history_does_not_become_zero_or_a_fake_plan(tmp_path):
    store = Store(tmp_path / 'empty.sqlite3')
    plan = plan_savings_rules(store, inputs=inputs(), today=TODAY)
    assert plan['status'] == 'needs_assumptions' and plan['simulation'] is None
    assert set(q['input'] for q in plan['questions']) == {'income_cents', 'budget_cents'}
    assert plan['facts']['S1']['facts']['missing_months'] == ['2026-08', '2026-09', '2026-10']


def test_month_with_records_is_not_a_claim_of_complete_import(tmp_path):
    store = history(tmp_path)
    with store.connect() as db:
        db.execute("DELETE FROM transactions WHERE date LIKE '2026-09%'")
    plan = plan_savings_rules(store, inputs=inputs(), today=TODAY)
    assert plan['status'] == 'needs_assumptions'
    assert plan['facts']['S1']['facts']['missing_months'] == ['2026-09']


@pytest.mark.parametrize('name,value', [('target_cents', 0), ('months', 0), ('months', 121),
    ('saved_cents', -1), ('income_cents', True), ('budget_cents', 2.5), ('unknown', 10)])
def test_inputs_reject_untrusted_types_and_ranges(name, value):
    with pytest.raises(ToolValidationError):
        InputFact(name, value, True)


def test_inputs_require_confirmation_and_no_duplicates():
    with pytest.raises(ToolValidationError):
        InputFact('target_cents', 600000, False)
    with pytest.raises(ToolValidationError):
        AgentTools(mode='assumption', inputs=inputs() + inputs())


def test_missing_goal_values_are_questions_not_defaults(tmp_path):
    plan = plan_savings_rules(history(tmp_path), inputs=[], today=TODAY)
    assert plan['status'] == 'needs_input'
    assert {q['input'] for q in plan['questions']} == {'target_cents', 'months', 'saved_cents'}
    assert [r['tool'] for r in plan['trace']] == ['data_coverage', 'spending_overview']


def test_edit_inputs_creates_new_snapshot_and_recalculates(tmp_path):
    store = history(tmp_path)
    old = plan_savings_rules(store, inputs=inputs(), today=TODAY)
    new = plan_savings_rules(store, inputs=inputs(months=12), today=TODAY)
    assert scenarios(old)['A']['projected_cents'] == 210000
    assert scenarios(new)['A']['projected_cents'] == 420000
    assert scenarios(new)['C']['spending_cut_cents'] == 15000


def test_one_snapshot_does_not_mix_edited_ledger_or_basis(tmp_path):
    store = history(tmp_path)
    tools = AgentTools(store, inputs=inputs(), today=TODAY)
    with store.connect() as db:
        db.execute("UPDATE transactions SET amount_cents=1 WHERE kind='支出'")
    store.set_spending_basis('original_purchase_month', confirmed=True)
    overview = tools.call('spending_overview', dict(period='last_3_months'))
    assert overview['basis'] == 'cash'
    assert overview['facts']['average_expense_cents'] == 485000
    fresh = plan_savings_rules(store, inputs=inputs(), today=TODAY)
    assert fresh['basis'] == 'original_purchase_month'
    assert fresh['facts']['S2']['facts']['average_expense_cents'] == 3


@pytest.mark.parametrize('ref', ['S9.average_income_cents', 'S2.__class__', 'S1.missing',
                              'S1.facts.average_income_cents', '../ledger.sqlite3'])
def test_bad_or_future_references_fail_closed(ref):
    tools = AgentTools(mode='assumption', inputs=inputs())
    with pytest.raises(ToolValidationError):
        tools._resolve({'$ref': ref})


def simulation_args():
    return dict(target_cents={'$input': 'target_cents'}, months={'$input': 'months'},
                saved_cents={'$input': 'saved_cents'}, base_income_cents={'$input': 'income_cents'},
                base_spending_cents={'$input': 'budget_cents'},
                scenarios=[dict(label='A', strategy='current')])


@pytest.mark.parametrize('field', ['target_cents', 'months', 'saved_cents', 'base_income_cents', 'base_spending_cents'])
def test_raw_monetary_parameters_cannot_bypass_provenance(field):
    tools = AgentTools(mode='assumption', inputs=inputs(income_cents=10000, budget_cents=1000))
    args = simulation_args(); args[field] = 100
    with pytest.raises(ToolValidationError):
        tools.call('scenario_simulate_batch', args)
    assert tools.facts == {}


def test_invented_scenario_amounts_and_unknown_arguments_rejected():
    tools = AgentTools(mode='assumption', inputs=inputs(income_cents=10000, budget_cents=1000))
    args = simulation_args(); args['scenarios'][0]['spending_cut_cents'] = 100
    with pytest.raises(ToolValidationError):
        tools.call('scenario_simulate_batch', args)
    args = simulation_args(); args['extra'] = 'not allowed'
    with pytest.raises(ToolValidationError):
        tools.call('scenario_simulate_batch', args)


def test_cache_is_canonical_and_returns_defensive_copies(tmp_path):
    tools = AgentTools(history(tmp_path), inputs=inputs(), today=TODAY)
    first = tools.call('data_coverage', dict(months=3))
    first['facts']['months'].clear()
    again = tools.call('data_coverage', dict(months=3))
    assert len(again['facts']['months']) == 3 and len(tools.facts) == 1
    assert tools.trace[-1]['cached']
    saved = tools.facts; saved.clear()
    assert len(tools.facts) == 1


def test_unique_tool_call_limit(tmp_path):
    tools = AgentTools(history(tmp_path), today=TODAY)
    for months in range(1, 7):
        tools.call('data_coverage', dict(months=months))
    assert tools.call('data_coverage', dict(months=1))['step_id'] == 'S1'
    with pytest.raises(ToolValidationError, match='六次'):
        tools.call('data_coverage', dict(months=7))


def test_negative_cashflow_is_not_clamped_and_required_cut_cannot_exceed_budget():
    plan = plan_savings_rules(mode='assumption', today=TODAY,
                             inputs=inputs(income_cents=10000, budget_cents=20000, saved_cents=1000))
    rows = scenarios(plan)
    assert rows['A']['monthly_available_cents'] == -10000
    assert rows['A']['projected_cents'] == -59000 and rows['A']['negative_cashflow']
    assert not rows['A']['feasible']
    assert rows['C']['requested_cut_cents'] > rows['C']['spending_cut_cents'] == 20000
    assert not rows['C']['feasible'] and rows['C']['gap_cents'] > 0


def test_ceiling_and_existing_savings_use_integer_cents():
    plan = plan_savings_rules(mode='assumption', today=TODAY,
        inputs=inputs(target_cents=101, months=3, saved_cents=1, income_cents=100, budget_cents=100))
    assert plan['simulation']['facts']['required_monthly_cents'] == 34
    assert scenarios(plan)['C']['projected_cents'] == 103


@pytest.mark.parametrize('basis,expected', [('cash', 7000), ('original_purchase_month', 7000)])
def test_refund_net_and_duplicate_consumption_not_gross(tmp_path, basis, expected):
    store = ledger(tmp_path)
    store.confirm_refund_link('R-W', 'P-B')
    store.set_spending_basis(basis, confirmed=True)
    # The fixture's one purchase event has two funding legs, only counted once.
    tools = AgentTools(store, today=TODAY)
    frame = tools._frame
    assert int(frame.loc[frame.kind == '支出', 'amount_cents'].sum()) == expected
    evidence = tools.call('data_coverage', dict(months=3))['evidence_ids']
    assert 'P-W' in evidence and 'R-W' in evidence
    assert tools.basis == basis


def test_negative_history_requires_nonnegative_future_assumptions(tmp_path):
    store = history(tmp_path)
    # Synthetic projection of a refund-only historical period.
    frame = store.spending_view()
    frame.loc[frame.kind == '支出', 'amount_cents'] *= -1
    class Snapshot:
        def spending_view(self): return frame
    plan = plan_savings_rules(Snapshot(), inputs=inputs(), today=TODAY)
    assert plan['status'] == 'needs_assumptions' and plan['simulation'] is None


def test_category_amount_literal_cannot_bypass_ledger_snapshot(tmp_path):
    tools = AgentTools(history(tmp_path), inputs=inputs(), today=TODAY)
    overview = tools.call('spending_overview', dict(period='last_3_months'))
    step = overview['step_id']
    args = simulation_args()
    args.update(base_income_cents={'$ref': step + '.average_income_cents'},
                base_spending_cents={'$ref': step + '.average_expense_cents'},
                category_spending_cents={'餐饮': 100000})
    with pytest.raises(ToolValidationError, match='分类金额'):
        tools.call('scenario_simulate_batch', args)


@pytest.mark.parametrize('categories,pct', [(['餐饮', '餐饮'], 10), ([], 10), (['餐饮'], 101), (['餐饮'], True)])
def test_invalid_cut_policy_is_rejected(tmp_path, categories, pct):
    with pytest.raises(ToolValidationError):
        plan_savings_rules(history(tmp_path), inputs=inputs(), today=TODAY,
                           categories=categories, cut_pct=pct)


def test_already_saved_target_does_not_require_extra_cut():
    plan = plan_savings_rules(mode='assumption', inputs=inputs(saved_cents=600000,
                             income_cents=520000, budget_cents=485000), today=TODAY)
    assert plan['simulation']['facts']['required_monthly_cents'] == 0
    assert scenarios(plan)['C']['spending_cut_cents'] == 0


def test_cross_year_calendar_window_excludes_current_month(tmp_path):
    tools = AgentTools(history(tmp_path), today=date(2027, 1, 1))
    coverage = tools.call('data_coverage', dict(months=3))
    assert coverage['facts']['months'] == ['2026-10', '2026-11', '2026-12']
    assert coverage['facts']['available_months'] == ['2026-10']


@pytest.mark.parametrize('basis,expected', [('cash', 3333), ('original_purchase_month', 2333)])
def test_cross_month_refund_changes_planning_baseline_by_selected_basis(tmp_path, basis, expected):
    store = ledger(tmp_path)
    store.confirm_refund_link('R-W', 'P-B')
    store.set_spending_basis(basis, confirmed=True)
    rows = [[f'INC-{m}', f'2026-{m}-15', '公司', '工资', '5200', '收入', '样例', 'CNY']
            for m in ('07', '08', '09')]
    store.import_rows(parse_statement(csv_bytes(rows), 'synthetic-income.csv').rows)
    before = Path(store.path).read_bytes()
    plan = plan_savings_rules(store, inputs=inputs(), today=date(2026, 10, 9), categories=('餐饮',))
    assert plan['facts']['S2']['facts']['average_expense_cents'] == expected
    assert plan['simulation']['basis'] == basis
    assert scenarios(plan)['A']['monthly_available_cents'] == 520000 - expected
    assert Path(store.path).read_bytes() == before


@pytest.mark.parametrize('json_output', [True, False])
def test_local_cli_preview_without_api_or_personal_data(json_output):
    import os
    command = [sys.executable, '-m', 'finance.agent_planner', '--target-cents', '600000',
        '--months', '6', '--saved-cents', '0', '--income-cents', '520000', '--budget-cents', '485000',
        '--confirm-inputs']
    if json_output:
        command.append('--json')
    proc = subprocess.run(command, env=dict(os.environ, PYTHONIOENCODING='gbk'),
                          capture_output=True, check=True)
    text = proc.stdout.decode('utf-8')
    if json_output:
        import json
        result = json.loads(text)
        assert result['status'] == 'ok' and result['planner'] == 'local_rules'
    else:
        assert '本地规则规划' in text and '¥2,100.00' in text
