from threading import Event
from pathlib import Path
import pytest
from finance.agent_runtime import plan_savings_agent
from finance.agent_tools import AgentTools, ToolValidationError
from test_agent_savings import history, inputs, TODAY
from qt_finance.planning_dialogue import draft_request, understand_request
from qt_finance.services import savings_follow_up, savings_inputs


def test_compound_goal_and_relative_revision_keep_protections():
    draft = draft_request('我想半年存6000，房租不能动，周末聚餐尽量保留。帮我看看怎么安排。')
    assert draft['months'] == '6' and draft['target_cents'] == '6000'
    assert draft['protected_categories'] == ['居住', '餐饮']
    assert draft['needs_confirmation'] and len(draft['constraint_notes']) == 2
    revised = draft_request('那晚两个月，聚餐还是保留', draft)
    assert revised['months'] == '8' and revised['protected_categories'] == draft['protected_categories']
    revised = savings_follow_up('改成 9 个月', revised)
    assert revised['months'] == '9' and revised['protected_categories'] == draft['protected_categories']
    assert len(savings_inputs(dict(revised, saved_cents='1000'))) == 3


def test_constraints_enforced_in_fallback_and_snapshot(tmp_path):
    store = history(tmp_path)
    before = Path(store.path).read_bytes()
    result = plan_savings_agent(store, question='保护居住餐饮', inputs=inputs(), today=TODAY,
                                protected_categories=('居住', '餐饮'))
    rows = {r['label']: r for r in result['simulation']['facts']['scenarios']}
    assert rows['A']['projected_cents'] == 210000
    assert rows['B']['spending_cut_cents'] == 7000
    assert rows['B']['projected_cents'] == 252000
    assert rows['C']['adjustable_cents'] == 70000
    assert Path(store.path).read_bytes() == before
    large = plan_savings_agent(store, question='保护居住餐饮', inputs=inputs(target_cents=1200000), today=TODAY,
                               protected_categories=('居住', '餐饮'))
    c = large['simulation']['facts']['scenarios'][2]
    assert c['spending_cut_cents'] == 70000 and c['gap_cents'] > 0 and not c['feasible']


def test_model_cannot_cut_protected_category():
    tools = AgentTools(inputs=inputs(), mode='assumption', protected_categories=('餐饮',))
    assert tools.fresh_session().protected_categories == ('餐饮',)
    with pytest.raises(ToolValidationError, match='保护'):
        tools._scenario_simulate_batch(600000, 6, 0, 520000, 485000,
            [dict(label='B', strategy='cut_categories', categories=['餐饮'], cut_pct=10)], {'餐饮': 100000})
    facts = tools._scenario_simulate_batch(600000, 6, 0, 520000, 485000,
            [dict(label='C', strategy='required_cut')])[0]
    assert facts['scenarios'][0]['spending_cut_cents'] == 0


@pytest.mark.parametrize('response', [dict(months='八'), dict(protected_categories=['虚构分类']),
                                      dict(target_cents=6000), dict(sql='DELETE'), dict(months='0')])
def test_invalid_model_draft_keeps_local_state(response):
    class Model:
        def understand_savings(self, payload):
            return response
    draft = draft_request('我想半年存6000，房租不能动')
    result = understand_request('问题', None, draft, Model(), Event())
    assert result['months'] == '6' and '居住' in result['protected_categories']
    assert '未完成' in result['interpretation']


def test_model_draft_is_unconfirmed_and_no_ledger_payload():
    class Model:
        def understand_savings(self, payload):
            assert set(payload) == {'question', 'previous'}
            assert 'evidence' not in payload['previous']
            return dict(months='8', protected_categories=[])
    previous = dict(draft_request('我想半年存6000，房租不能动'), evidence='private')
    result = understand_request('延后两个月', previous, previous, Model(), Event())
    assert result['months'] == '8' and result['needs_confirmation']
    assert '居住' in result['protected_categories']
    event = Event(); event.set()
    assert understand_request('问题', previous, previous, None, event) is None


def test_all_categories_protected_and_assumptions(tmp_path):
    from finance.categories import category_options
    protected = category_options('支出')
    result = plan_savings_agent(history(tmp_path), question='都保留', inputs=inputs(), today=TODAY,
                                protected_categories=protected)
    assert all(r['spending_cut_cents'] == 0 for r in result['simulation']['facts']['scenarios'])
    result = plan_savings_agent(mode='assumption', question='保留餐饮', today=TODAY,
        inputs=inputs(income_cents=520000, budget_cents=485000), protected_categories=('餐饮',))
    assert result['status'] == 'ok'
    assert result['simulation']['facts']['scenarios'][2]['spending_cut_cents'] == 0


def test_partial_invalid_model_response_is_atomic():
    class Model:
        def understand_savings(self, payload):
            return dict(months='8', protected_categories=['不存在'])
    draft = draft_request('我想半年存6000')
    assert understand_request('问题', None, draft, Model(), Event())['months'] == '6'


def test_ordinary_queries_do_not_become_planning_drafts():
    assert draft_request('上个月存了多少？') is None
    assert draft_request('我想11月比10月多存1000') is None


def test_confirmed_relative_revision_does_not_ask_again():
    previous = dict(draft_request('我想半年存6000，周末聚餐保留'), needs_confirmation=False)
    assert not draft_request('那晚两个月，聚餐还是保留', previous)['needs_confirmation']
    no_protection = dict(previous, protected_categories=[])
    assert draft_request('那晚两个月，聚餐还是保留', no_protection)['needs_confirmation']
