import json
from datetime import date
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from finance.storage import Store
from qt_finance.chat_router import interpret_turn, resolve_turn
from qt_finance.expense_draft import expense_draft, revise_expense
from qt_finance.finance_dialogue import analyze_finances, depends_on_analysis_context, rank_categories
from qt_finance.services import (
    parse_ledger_turn,
    present_ledger_turn,
    query_ledger,
    run_ledger_turn,
    validate_ledger_turn,
)


CASES = json.loads(Path(__file__).with_name('qt_spoken_cases.json').read_text(encoding='utf-8-sig'))
QUERY = '2026-10 餐饮净支出多少？'
GENERATION = 3
ANALYSIS = {'E04', 'F02', 'F03', 'F04', 'F05', 'A38', 'A39', 'A40', 'A41',
            'A42', 'B46', 'B47', 'F69', 'F70', 'F72'}
NO_EXACT_AMOUNT = {'C01', 'C02', 'C05', 'B43', 'B44', 'C49', 'C50', 'C51', 'C53', 'C54'}
RECENT_MONTHS = ['2026-08', '2026-09', '2026-10']

EXPECTED = {
    'A01': dict(route='analysis', periods=['2026-11'], contains=('没有已导入记录',)),
    'A02': dict(route='query', status='ok', scope='2026-10', contains=('255.00', '预算')),
    'A03': dict(route='interpret', status='unsupported', contains=('明确',)),
    'A04': dict(route='interpret', status='unsupported', contains=('不会',)),
    'A05': dict(route='query', status='unsupported', contains=('时间', '不会')),
    'A06': dict(route='interpret', status='unsupported', contains=('多个年份',)),
    'B01': dict(route='query', status='unsupported', contains=('哪家商户',)),
    'B02': dict(route='query', status='unsupported', contains=('无法可靠理解',)),
    'B03': dict(route='query', status='unsupported', contains=('排除', '月份')),
    'B04': dict(route='analysis', periods=RECENT_MONTHS, not_contains=('查账',)),
    'B05': dict(route='interpret', status='unsupported', contains=('分类', '月份')),
    'B06': dict(route='interpret', status='unsupported', contains=('月份', '分类')),
    'C01': dict(route='expense', amount_cents='', category='餐饮', date='2026-11-18'),
    'C02': dict(route='expense', amount_cents='', category='交通'),
    'C03': dict(route='interpret', status='unsupported', contains=('明确',)),
    'C04': dict(route='interpret', status='unsupported', contains=('明确',)),
    'C05': dict(route='expense', amount_cents='', category='医疗', date='2026-11-18'),
    'C06': dict(route='interpret', status='unsupported', contains=('明确',)),
    'D01': dict(route='query', status='unsupported', contains=('明确',)),
    'D02': dict(route='query', status='unsupported', contains=('起止日期',)),
    'D03': dict(route='interpret', status='unsupported', contains=('月份',)),
    'D04': dict(route='analysis', contains=('时间范围',)),
    'D05': dict(route='query', status='unsupported', contains=('明确',)),
    'D06': dict(route='analysis', contains=('开启',)),
    'E01': dict(route='query', status='ok', scope='2026-10', category='交通', contains=('25.00',)),
    'E02': dict(route='query', status='unsupported', contains=('分类或商户',)),
    'E03': dict(route='query', status='unsupported', contains=('哪个月',)),
    'E04': dict(route='analysis', periods=['2026-10'], category='餐饮'),
    'E05': dict(route='query', status='unsupported', contains=('分类或商户',)),
    'E06': dict(route='query', status='unsupported', contains=('哪家商户',),),
    'F01': dict(route='analysis', periods=RECENT_MONTHS),
    'F02': dict(route='analysis', periods=RECENT_MONTHS),
    'F03': dict(route='analysis', periods=RECENT_MONTHS),
    'F04': dict(route='analysis', periods=RECENT_MONTHS),
    'F05': dict(route='analysis', periods=RECENT_MONTHS),
    'F06': dict(route='savings', target_cents='', months='', protected=('居住',)),
    'A37': dict(route='analysis', periods=['2026-11'], contains=('没有已导入记录',)),
    'A38': dict(route='analysis', periods=RECENT_MONTHS),
    'A39': dict(route='analysis', periods=RECENT_MONTHS),
    'A40': dict(route='analysis', periods=RECENT_MONTHS),
    'A41': dict(route='analysis', periods=RECENT_MONTHS),
    'A42': dict(route='analysis', periods=RECENT_MONTHS),
    'B43': dict(route='expense', amount_cents='', category='餐饮', date='2026-11-18'),
    'B44': dict(route='expense', amount_cents='', category='购物'),
    'B45': dict(route='query', status='unsupported', contains=('哪个月', '交通'),
                error_code='missing_period'),
    'B46': dict(route='analysis', periods=RECENT_MONTHS, category='餐饮'),
    'B47': dict(route='analysis', periods=['2026-11'], category='购物'),
    'B48': dict(route='navigation', contains=('其他支出', '人情')),
    'C49': dict(route='expense', amount_cents=''),
    'C50': dict(route='expense', amount_cents='', category='餐饮', channel='微信'),
    'C51': dict(route='expense', amount_cents='', category='交通', channel='支付宝'),
    'C52': dict(route='expense', amount_cents='100.00'),
    'C53': dict(route='expense', amount_cents=''),
    'C54': dict(route='expense', amount_cents='', multiple=True),
    'D55': dict(route='query', status='missing', scope='2026-11',
                contains=('没有', '没有记录')),
    'D56': dict(route='query', status='unsupported', contains=('起止日期',)),
    'D57': dict(route='query', status='unsupported', contains=('多个年份',)),
    'D58': dict(route='query', status='unsupported', contains=('多个年份',)),
    'D59': dict(route='query', status='unsupported', contains=('排除', '月份')),
    'D60': dict(route='query', status='unsupported', contains=('哪个月',),
                error_code='missing_period'),
    'E61': dict(route='query', status='unsupported', contains=('外卖', '餐饮')),
    'E62': dict(route='query', status='ok', scope='2026-10', category='餐饮',
                contains=('110.00',)),
    'E63': dict(route='query', status='ok', scope='2026-10', category='交通',
                contains=('25.00',)),
    'E64': dict(route='interpret', status='unsupported', contains=('明确',)),
    'E65': dict(route='revise_expense', channel='支付宝'),
    'E66': dict(route='cancel_expense'),
    'F67': dict(route='savings', target_cents='', months=''),
    'F68': dict(route='savings', target_cents='', months='', budget_cents=''),
    'F69': dict(route='analysis', periods=RECENT_MONTHS),
    'F70': dict(route='analysis', periods=RECENT_MONTHS),
    'F71': dict(route='savings', target_cents='', months='',
                protected=('居住', '餐饮')),
    'F72': dict(route='analysis', periods=RECENT_MONTHS),
}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import qt_finance.finance_dialogue as analysis
    import qt_finance.services as services

    clock = SimpleNamespace(today=lambda: date(2026, 11, 19))
    monkeypatch.setattr(services, 'date', clock)
    monkeypatch.setattr(analysis, 'date', clock)
    monkeypatch.setattr('qt_finance.expense_draft.date', type('TestDate', (date,), {
        'today': classmethod(lambda cls: clock.today()),
    }))
    store = Store(tmp_path / 'spoken.sqlite3')
    for day, merchant, cents, kind, category in (
        ('2025-10-05', '旧餐馆', 5000, '支出', '餐饮'),
        ('2026-08-01', '合成餐馆', 6800, '支出', '餐饮'),
        ('2026-08-02', '地铁', 2500, '支出', '交通'),
        ('2026-08-03', '超市', 5000, '支出', '购物'),
        ('2026-09-01', '合成餐馆', 9500, '支出', '餐饮'),
        ('2026-09-02', '地铁', 2400, '支出', '交通'),
        ('2026-09-03', '书店', 8000, '支出', '教育'),
        ('2026-10-01', '合成餐馆', 7800, '支出', '餐饮'),
        ('2026-10-02', '星巴克', 3200, '支出', '餐饮'),
        ('2026-10-03', '地铁', 2500, '支出', '交通'),
        ('2026-10-04', '医院', 12000, '支出', '医疗'),
        ('2026-10-05', '工资', 520000, '收入', '工资'),
    ):
        store.add_manual_transaction(day, merchant, cents, kind, category)
    return store


@pytest.mark.parametrize('case', CASES, ids=[item['id'] for item in CASES])
def test_report_cases_read_only_and_priority_contracts(ledger, case):
    identifier = case['id']
    history, draft = [], None
    if identifier.startswith('E') and identifier not in ('E65', 'E66'):
        first = query_ledger(ledger.path, QUERY, None, Event())
        assert first['status'] == 'ok'
        first['ledger_generation'] = GENERATION
        history.append(('assistant', first))
    if identifier in ('E65', 'E66'):
        draft = dict(date='2026-11-18', category='餐饮', channel='微信', amount_cents=3800, multiple=False)
    before = Path(ledger.path).read_bytes()
    decision = resolve_turn(case['text'], history, GENERATION, expense=draft)
    route = decision.route
    result = {}
    if route.kind in ('query', 'interpret'):
        result = query_ledger(ledger.path, decision.text, None, Event(), previous=decision.query)
    elif route.kind == 'analysis':
        prior = decision.analysis if depends_on_analysis_context(decision.text, decision.analysis) else None
        result = analyze_finances(ledger.path, decision.text, None, Event(), prior)
    elif route.kind == 'rank':
        result = rank_categories(ledger.path, decision.text, Event(), decision.analysis)
    assert Path(ledger.path).read_bytes() == before
    expected = EXPECTED[identifier]
    assert route.kind == expected['route']
    if route.kind in ('query', 'interpret'):
        assert isinstance(result, dict) and result
    if route.kind == 'analysis':
        assert isinstance(result, dict) and result
    if 'status' in expected:
        assert result.get('status') == expected['status']
    if 'error_code' in expected:
        assert result.get('error', {}).get('code') == expected['error_code']
    if 'scope' in expected and expected['scope']:
        assert result.get('scope') == expected['scope']
    if 'category' in expected and expected['category']:
        if route.kind == 'expense':
            assert route.payload['category'] == expected['category']
        elif route.kind == 'analysis':
            assert result['analysis_context']['category'] == expected['category']
        else:
            assert result['query_state']['category'] == expected['category']
    if 'periods' in expected:
        assert result['analysis_context']['periods'] == expected['periods']
    if route.kind == 'expense':
        assert route.payload['amount_cents'] == expected.get('amount_cents', route.payload['amount_cents'])
        if 'date' in expected:
            assert route.payload['date'] == expected['date']
        if 'channel' in expected:
            assert route.payload['channel'] == expected['channel']
        if 'multiple' in expected:
            assert route.payload['multiple'] is expected['multiple']
    if route.kind == 'savings':
        assert route.payload['needs_confirmation'] is True
        for key in ('target_cents', 'months', 'budget_cents'):
            if key in expected:
                assert route.payload[key] == expected[key]
        if 'protected' in expected:
            assert tuple(route.payload['protected_categories']) == expected['protected']
    if route.kind == 'revise_expense':
        assert route.payload['channel'] == expected['channel']
    answer = ''
    if route.kind == 'navigation' and isinstance(route.payload, tuple):
        answer = route.payload[1]
    elif isinstance(result, dict):
        answer += result.get('answer', '')
        answer += '\n' + result.get('analysis_facts', '')
    elif isinstance(route.payload, dict):
        answer = route.payload.get('answer', '')
    if 'contains' in expected:
        assert all(text in answer for text in expected['contains']), (identifier, answer)
    if 'not_contains' in expected:
        assert all(text not in answer for text in expected['not_contains']), (identifier, answer)

    if identifier in ANALYSIS:
        assert route.kind == 'analysis'
    if identifier == 'E04':
        assert decision.analysis['periods'] == ['2026-10']
        assert decision.analysis['category'] == '餐饮'
        assert result['analysis_context']['category'] == '餐饮'
    if identifier in ('B01', 'E06'):
        assert result['status'] == 'unsupported'
        assert '哪家商户' in result['answer']
        assert not result['evidence']
    if identifier == 'A02':
        assert result['status'] == 'ok' and result['scope'] == '2026-10'
        assert '没有对应的预算' in result['answer']
    if identifier in NO_EXACT_AMOUNT and route.kind == 'expense':
        assert not route.payload['amount_cents']
    if identifier in ('C02', 'C53', 'C54'):
        assert route.kind == 'expense'
    if identifier == 'C54':
        assert route.payload['multiple']
        assert revise_expense(route.payload, '45元') is None
    if identifier == 'E65':
        assert route.kind == 'revise_expense'
        assert route.payload == dict(draft, channel='支付宝')
    if identifier == 'E66':
        assert route.kind == 'cancel_expense'
    if identifier in ('E01', 'E62', 'E63'):
        assert result['status'] == 'ok' and result['scope'] == '2026-10'
        assert result['query_state']['category'] == ('餐饮' if identifier == 'E62' else '交通')
    if identifier == 'E61':
        assert '2026-10' in result['answer'] and '外卖' in result['answer']
    if identifier == 'D57':
        assert all('2026-08到2025-10' not in value for value in result['choices'].values())
    if identifier == 'D60':
        assert result['error']['code'] == 'missing_period'


def test_expected_covers_all_spoken_cases():
    assert set(EXPECTED) == {case['id'] for case in CASES}
    assert len(EXPECTED) == 72


def test_parse_ledger_turn_is_pure_and_normalizes_one_month():
    plan = parse_ledger_turn(
        '2026年10月 餐饮净支出多少？', None, {'2026-10'}, date(2026, 11, 19),
    )
    assert plan['normalized'] == '2026-10 餐饮净支出多少？'
    assert plan['temporal_error'] is None
    assert plan['explicit_months'] == {'2026-10'}


def test_validate_ledger_turn_keeps_write_requests_out_of_the_ledger(ledger):
    frame = Store(ledger.path, read_only=True).spending_view()
    months = set(frame.attrs['source_months'])
    plan = parse_ledger_turn('帮我记一笔 2026-10 餐饮 20 元', None, months, date(2026, 11, 19))
    result = validate_ledger_turn(plan, frame, {}, months)
    assert result['response']['status'] == 'unsupported'
    assert '只查询账单' in result['response']['answer']


def test_run_ledger_turn_executes_only_from_a_validated_plan(ledger):
    frame = Store(ledger.path, read_only=True).spending_view()
    months = set(frame.attrs['source_months'])
    plan = validate_ledger_turn(
        parse_ledger_turn('2026-10 餐饮净支出多少？', None, months, date(2026, 11, 19)),
        frame, {}, months,
    )
    execution = run_ledger_turn(frame, plan, {}, None, Event())
    assert execution['final'] is False
    assert execution['result']['status'] == 'ok'
    assert execution['parsed']['category'] == '餐饮'


def test_present_ledger_turn_attaches_scope_and_query_state(ledger):
    frame = Store(ledger.path, read_only=True).spending_view()
    months = set(frame.attrs['source_months'])
    plan = parse_ledger_turn('2026-10 餐饮净支出多少？', None, months, date(2026, 11, 19))
    execution = dict(
        final=False,
        parsed=dict(metric='expense', period='2026-10', category='餐饮'),
        follow_up=None,
        model_follow_up=False,
        combined=None,
        overrun=None,
        result=dict(
            status='ok', evidence=[],
            trace={'inputs': dict(metric='expense', period='2026-10', category='餐饮')},
            answer='测试回答',
        ),
    )
    result = present_ledger_turn(frame, execution, plan, {})
    assert result['scope'] == '2026-10'
    assert result['query_state']['category'] == '餐饮'
    assert result['query_text'] == '2026-10 餐饮净支出多少？'


def test_d58_year_choice_then_requires_category_instead_of_merging(ledger):
    first = query_ledger(ledger.path, '十月份吃喝玩乐大概多少', None, Event())
    assert first['error']['code'] == 'ambiguous_period'
    assert set(first['choices']) == {'2025-10', '2026-10'}
    selected = query_ledger(ledger.path, first['choices']['2026-10'], None, Event(), previous=first)
    assert selected['status'] == 'unsupported'
    assert '指定要统计的分类' in selected['answer']
    assert '餐饮' in selected['answer'] and '娱乐' in selected['answer']


def test_empty_analysis_does_not_claim_spending(monkeypatch, tmp_path):
    import qt_finance.finance_dialogue as analysis

    clock = SimpleNamespace(today=lambda: date(2026, 11, 19))
    monkeypatch.setattr(analysis, 'date', clock)
    store = Store(tmp_path / 'empty.sqlite3')
    result = analyze_finances(store.path, '我平时是不是在乱七八糟的地方花太多？', None, Event())
    assert result['analysis_context']['periods'] == ['2026-11']
    assert '没有已导入记录' in result['answer']
    assert '¥' not in result['answer']


def test_agreed_amount_is_literal_but_ambiguous_amount_is_not_guessed():
    assert expense_draft('买菜加水果凑了个整，算一百吧，记一笔。')['amount_cents'] == '100.00'
    ambiguous = expense_draft('刚买了个东西，一百二。')
    assert ambiguous is None or ambiguous['amount_cents'] == ''


def test_model_intent_uses_minimal_context_and_cannot_request_writes():
    seen = []

    def model(payload):
        seen.append(payload)
        return dict(action='analysis', reference='current', missing='none')

    client = SimpleNamespace(interpret_finance_turn=model)
    assert interpret_turn(client, '还能省在哪', has_context=True, pending_question=False)['action'] == 'analysis'
    assert seen == [dict(text='还能省在哪', has_context=True, pending_question=False)]
    assert interpret_turn(client, '还能省在哪', has_context=False, pending_question=False) is None
    client.interpret_finance_turn = lambda _: dict(action='write', reference='none', missing='none')
    assert interpret_turn(client, '记一下', has_context=False, pending_question=False) is None


def test_budget_overrun_uses_the_category_budget_and_local_spending(ledger):
    ledger.save_budget('2026-10', '餐饮', 9000)
    before = Path(ledger.path).read_bytes()
    result = query_ledger(ledger.path, '上个月餐饮花超了多少？', None, Event())
    assert result['query_state']['category'] == '餐饮'
    assert '超出 ¥20.00' in result['answer']
    assert Path(ledger.path).read_bytes() == before


@pytest.mark.parametrize('text, amount, multiple', [
    ('昨天喝咖啡花了一百，不对，是八十。', '80.00', False),
    ('今天星巴克花了38元，算一百吧，记一笔，其实别按一百记', '', False),
    ('昨天停车5元，今天洗车40元，别算一百吧，记一笔', '', True),
])
def test_negated_and_revised_expense_amounts(text, amount, multiple):
    from qt_finance.expense_draft import understand_expense
    draft = expense_draft(text)
    assert (draft['amount_cents'], draft['multiple']) == (amount, multiple)
    client = SimpleNamespace(understand_expense=lambda _: dict(
        intent='expense', amount_text='一百', merchant='', multiple=False,
    ))
    model_draft = understand_expense(client, text)
    assert (model_draft['amount_cents'], model_draft['multiple']) == (amount, multiple)


def test_knowledge_only_passes_question_to_model(ledger):
    calls = []
    client = SimpleNamespace(analyze_finances=lambda payload: (
        calls.append(payload) or dict(analysis='应急金用于处理突发开销。', follow_up_question='')
    ))
    prior = dict(periods=['2026-10'], turns=[dict(question='我在星巴克的开销')])
    question = '我想知道应急金是什么？'
    assert resolve_turn(question, [], GENERATION).route.kind == 'analysis'
    analyze_finances(ledger.path, question, client, Event(), prior)
    assert calls[0] == dict(question=question, ledger_summary=None)
    analyze_finances(ledger.path, '按我最近的开销，应急金该留多少？', client, Event())
    assert calls[1]['ledger_summary']['periods']


def test_resume_reduction_preserves_only_applicable_fields():
    first = resolve_turn('每月少花300元，房租不能动，先按半年。', [], GENERATION)
    state = first.route.payload
    assert (state['monthly_reduction'], state['months'], state['protected_categories']) == (
        '300.00', '6', ['居住'],
    )
    history = [('assistant', dict(savings_state=state, ledger_generation=GENERATION)),
               ('assistant', dict(answer='交通开销'))]
    revised = resolve_turn('回到计划，改成八个月。', history, GENERATION).route.payload
    assert (revised['monthly_reduction'], revised['months'], revised['protected_categories']) == (
        '300.00', '8', ['居住'],
    )
    assert resolve_turn('回到计划，改成八个月。', history, GENERATION+1).route.kind == 'clarify'


def test_analysis_year_clarification_reuses_the_selected_month(ledger):
    text = '十月份那堆买吃的能不能少一点？'
    clarification = analyze_finances(ledger.path, text, None, Event())
    clarification['ledger_generation'] = GENERATION
    assert set(clarification['choices']) == {'2025-10', '2026-10'}
    history = [('assistant', clarification)]
    decision = resolve_turn('2026-10', history, GENERATION)
    assert decision.route.kind == 'analysis'
    result = analyze_finances(ledger.path, decision.text, None, Event())
    assert result['analysis_context']['periods'] == ['2026-10']


def test_preview36_period_clarification_and_latest_reaction(ledger):
    question = '交通这一块呢'
    first = resolve_turn(question, [], GENERATION)
    assert first.route.kind == 'clarify' and '交通' in first.route.payload['answer']
    pending = first.route.payload
    decision = resolve_turn('2026-10', [('assistant', pending)], GENERATION)
    traffic = query_ledger(ledger.path, decision.text, None, Event(), previous=decision.query)
    assert decision.route.kind == 'query'
    assert traffic['scope'] == '2026-10' and traffic['query_state']['category'] == '交通'
    assert '25.00' in traffic['answer']
    traffic['ledger_generation'] = GENERATION
    prior = dict(analysis_context=dict(category='餐饮', periods=['2026-09'],
                 ledger_generation=GENERATION, turns=[dict(question='九月餐饮')]))
    reaction = resolve_turn('这个算高吗', [('assistant', prior), ('assistant', traffic)], GENERATION)
    result = analyze_finances(ledger.path, reaction.text, None, Event(), reaction.analysis)
    assert reaction.route.kind == 'analysis'
    assert '2026-10' in result['analysis_facts'] and '交通净支出 ¥25.00' in result['analysis_facts']


def test_preview36_general_budget_after_personal_analysis(ledger):
    calls = []
    client = SimpleNamespace(analyze_finances=lambda payload: (
        calls.append(payload) or dict(analysis='先列必要支出。')))
    prior = dict(category='餐饮', periods=['2026-10'], turns=[dict(question='个人开销')])
    question = '一般来说，家庭预算应该怎么做？'
    assert resolve_turn(question, [], GENERATION).route.kind == 'analysis'
    analyze_finances(ledger.path, question, client, Event(), prior)
    assert calls[-1] == dict(question=question, ledger_summary=None)
    analyze_finances(ledger.path, '按我最近的开销，预算怎么做？', client, Event(), prior)
    assert calls[-1]['ledger_summary']['periods']


@pytest.mark.parametrize('question, expected', [
    ('每月少花1,500元，先按半年', '9,000.00 元'),
    ('每月少花300到500元，先按半年', '确切金额'),
    ('每月少花300元，先按121个月', '1–120'),
    ('每月能省下300元，别动房租，先按半年', '居住'),
    ('每月少花300元，先按0个月', '1–120'),
    ('每月少花三百五十元，先按半年', '2,100.00 元'),
])
def test_preview36_reduction_reply(question, expected):
    from qt_finance.chat import ChatPage
    route = resolve_turn(question, [], GENERATION).route
    replies = []
    assert route.kind == 'savings'
    ChatPage.show_reduction_estimate(SimpleNamespace(
        reply=lambda payload, _: replies.append(payload['answer'])), route.payload)
    assert expected in replies[0]


def test_preview36_keeps_recording_and_lookup_distinct():
    lookup = '帮我查昨天买药花了80元的支出'
    assert resolve_turn(lookup, [], GENERATION).route.kind == 'query'
    draft = expense_draft('昨天喝咖啡花了100元')
    fixed = resolve_turn('别按一百，实际八十，支付宝', [], GENERATION, expense=draft).route
    assert fixed.kind == 'revise_expense'
    assert (fixed.payload['amount_cents'], fixed.payload['channel']) == ('80.00', '支付宝')
    assert resolve_turn(lookup + '，不对，是90元', [], GENERATION, expense=draft).route.kind == 'query'
    mixed = expense_draft('昨天打车花了20元，买药花了100元，不对，是80元')
    assert mixed['multiple'] and not mixed['amount_cents']
    assert resolve_turn('改成八十', [], GENERATION, expense=mixed).route.kind == 'clarify'
    assert expense_draft('午餐花了12块5')['amount_cents'] == '12.50'
    assert expense_draft('去年10月1日买书花了80元', date(2026, 11, 20))['date'] == '2025-10-01'
