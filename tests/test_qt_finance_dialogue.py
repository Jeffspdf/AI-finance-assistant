from datetime import date
from threading import Event
from types import SimpleNamespace

import pytest

from finance.qa import month_shift
from finance.storage import Store
from qt_finance.chat_router import classify_chat_route
from qt_finance.finance_dialogue import (
    analyze_finances,
    depends_on_analysis_context,
    finance_summary,
    is_analysis_reply,
)


def test_finance_opinion_has_separate_read_only_route(tmp_path):
    question = '你感觉我在餐饮上的开销会不会有点太大了'
    assert classify_chat_route(question).kind == 'analysis'
    assert classify_chat_route('上个月餐饮花了多少？').kind == 'query'
    assert classify_chat_route('帮我记一笔100元餐饮支出').kind == 'expense'
    assert classify_chat_route('我想半年存6000，房租不能动').kind == 'savings'
    trial = '我想在11月比10月多存1000元，收入不变的情况下，怎么省？'
    assert classify_chat_route(trial).kind == 'query'

    path = tmp_path / 'ledger.sqlite3'
    store = Store(path)
    store.add_manual_transaction('2025-10-09', '仅本机商户', 10000, '支出', '餐饮', '仅本机备注')
    store.add_manual_transaction('2025-10-10', '仅本机票据', 30000, '支出', '购物')
    before = path.read_bytes()
    captured = []

    def model(payload):
        captured.append(payload)
        return {'analysis': '餐饮占有一定比重。可以先检查是否容易减少外卖，再定适合自己的预算。'}

    result = analyze_finances(path, '你觉得我在2025-10的餐饮开销是否偏高？',
                              SimpleNamespace(analyze_finances=model), Event())
    assert '适合自己的预算' in result['answer'] and '25.0%' in result['analysis_facts']
    assert len(captured) == 1 and captured[0]['ledger_summary']['periods'][0]['budget_status'] == '未设置'
    assert '仅本机' not in repr(captured) and '10000' not in repr(captured)
    assert path.read_bytes() == before


def test_explicit_period_and_unavailable_cover_are_retained(tmp_path):
    store = Store(tmp_path / 'empty.sqlite3')
    result = finance_summary(store, '我上个月支出太高吗？', date(2025, 11, 18))
    assert result['months'] == [dict(month='2025-10', available=False, in_progress=False)]
    assert finance_summary(store, '我去年支出太高吗？', date(2025, 11, 18)) is None


def test_contextual_advice_followups_use_previous_analysis():
    context = dict(category='餐饮', periods=['2026-08', '2026-09', '2026-10'],
                   question='我的餐饮开销是不是太大了', ledger_generation=3)
    for question in ('那这算不算超支？', '这个是不是合理？', '那我该设多少合适？',
                     '那我是不是应该减少外卖？', '为什么会这样？'):
        assert classify_chat_route(question, None, context).kind == 'analysis', question


@pytest.mark.parametrize(('question', 'expected'), (
    ('那十月呢？', 'query'), ('只看星巴克', 'query'), ('十月的餐饮明细有哪些？', 'query'),
    ('这笔记录是不是餐饮？', 'query'), ('今天花了100元喝了杯星巴克', 'expense'),
))
def test_contextual_followups_do_not_capture_queries_or_expenses(question, expected):
    context = dict(category='餐饮', periods=['2026-08', '2026-09', '2026-10'],
                   question='我的餐饮开销是不是太大了', ledger_generation=3)
    route = classify_chat_route(question, None, context)
    assert route.kind == expected


def test_context_dependency_requires_an_implicit_follow_up():
    context = dict(category='餐饮', periods=['2026-08', '2026-09', '2026-10'])
    assert depends_on_analysis_context('那这算不算超支？', context)
    assert not depends_on_analysis_context('那十月餐饮开销是不是太大了？', context)


def test_short_answers_to_model_followup_stay_in_analysis():
    context = dict(
        category='交通',
        periods=['2026-08', '2026-09', '2026-10'],
        question='你感觉我在交通上的开销会不会有点太大了',
        follow_up_question='你平时通勤主要靠公共交通还是打车或自驾？',
        ledger_generation=3,
    )
    for answer in ('主要靠公共交通', '平时主要坐地铁和公交', '打车比较多',
                   '要靠公共交通还是打车或自驾'):
        assert is_analysis_reply(answer, context)
        assert classify_chat_route(answer, None, context).kind == 'analysis'
    for query in ('上个月交通花了多少？', '看交通明细', '只看十月交通', '那十月呢？', '给我讲个笑话', '谢谢'):
        assert classify_chat_route(query, None, context).kind == 'query'
        assert not depends_on_analysis_context(query, context)
    assert classify_chat_route('哪些交通开支可以减少？', None, context).kind == 'analysis'
    assert not is_analysis_reply('取消', context)
    assert not depends_on_analysis_context('那查一下上月的支出', context)
    assert depends_on_analysis_context('主要靠公共交通', context)
    assert classify_chat_route('把交通预算设为300', None, context).kind == 'navigation'


def test_model_followup_question_and_answer_are_passed_back(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    Store(path).add_manual_transaction('2026-10-09', '合成交通', 10000, '支出', '交通')
    previous = dict(
        category='交通',
        periods=['2026-08', '2026-09', '2026-10'],
        question='你感觉我在交通上的开销会不会有点太大了',
        follow_up_question='你平时通勤主要靠公共交通还是打车或自驾？',
        ledger_generation=3,
    )
    captured = []

    def model(payload):
        captured.append(payload)
        return {
            'analysis': '结合通勤方式看，交通占比仍需结合预算判断。',
            'follow_up_question': '你希望先设置一个交通预算吗？',
        }

    result = analyze_finances(path, '主要靠公共交通', SimpleNamespace(analyze_finances=model), Event(), previous)
    assert captured[0]['previous_follow_up_question'] == previous['follow_up_question']
    assert captured[0]['follow_up_answer'] == '主要靠公共交通'
    assert '模型上一条追问' in result['privacy_receipt']
    assert result['analysis_context']['follow_up_question'] == '你希望先设置一个交通预算吗？'


def test_keeps_multiple_user_constraints_across_turns(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    store = Store(path)
    store.add_manual_transaction('2026-10-01', '仅本机商户', 12000, '支出', '交通')
    before = path.read_bytes()
    captured = []

    def model(payload):
        captured.append(payload)
        return dict(analysis='可以先区分固定通勤和临时出行，再设置预算。', follow_up_question='')

    provider = SimpleNamespace(analyze_finances=model)
    context = dict(category='交通', periods=['2026-10'], question='我的交通开支太高了吗？',
                   follow_up_question='平时坐公交还是打车？')
    for question in ('主要坐公交', '周末偶尔打车', '那预算该设多少合适？'):
        assert classify_chat_route(question, None, context).kind == 'analysis'
        context = analyze_finances(path, question, provider, Event(), context)['analysis_context']
    assert [turn['question'] for turn in captured[-1]['analysis_history']][-2:] == ['主要坐公交', '周末偶尔打车']
    assert context['category'] == '交通' and context['periods'] == ['2026-10']
    assert '仅本机商户' not in repr(captured) and path.read_bytes() == before

def test_model_and_local_ranking_share_same_read_only_summary(tmp_path):
    from qt_finance.finance_dialogue import analysis_signals, rank_categories, rank_question

    path = tmp_path / 'rank.sqlite3'
    store = Store(path)
    for month, category, cents in (
        ('2026-10', '餐饮', 6000), ('2026-10', '交通', 3000), ('2026-10', '购物', 1000),
        ('2026-09', '交通', 4000), ('2026-09', '餐饮', 4000),
    ):
        store.add_manual_transaction(month + '-01', '合成店名', cents, '支出', category)
    before = path.read_bytes()
    signals = analysis_signals(finance_summary(store, '我的2026-10开销如何优化？', date(2026, 11, 19)))
    assert signals['periods'][0]['highest_expense_categories'] == ['餐饮']
    assert '6000' not in repr(signals) and '合成店名' not in repr(signals)
    question = '你不能帮我查看占比最大的类别吗'
    assert rank_question(question) == (True, False)
    assert rank_question('2026-09 哪个类别占比最高？') == (True, True)
    assert rank_question('除了餐饮哪个类别占比最高？')[0] is False
    result = rank_categories(path, question, Event(), dict(category='交通', periods=['2026-10']))
    assert '餐饮，占 60.0%' in result['answer'] and result['trace']
    tie = rank_categories(path, '2026-09 哪个类别占比最高？', Event())
    assert '交通、餐饮，占 50.0%' in tie['answer']
    assert path.read_bytes() == before


@pytest.mark.parametrize(('question', 'has_month'), (
    ('哪一类占比最大？', False),
    ('哪个类别花的钱占比最高？', False),
    ('哪项花销占当月净支出比例最高？', False),
    ('九月哪项花销占当月净支出比例最高？', True),
))
def test_rank_question_paraphrases(question, has_month):
    from qt_finance.finance_dialogue import rank_question

    assert rank_question(question) == (True, has_month)


def test_ranking_preserves_the_active_analysis_task(tmp_path):
    from qt_finance.finance_dialogue import rank_categories

    path = tmp_path / 'rank-context.sqlite3'
    store = Store(path)
    store.add_manual_transaction('2026-10-01', '合成公交', 3000, '支出', '交通')
    store.add_manual_transaction('2026-10-02', '合成餐厅', 7000, '支出', '餐饮')
    previous = dict(
        category='交通',
        periods=['2026-10'],
        question='我的交通开支太高了吗？',
        follow_up_question='平时坐公交还是打车？',
        turns=[dict(question='主要坐公交'), dict(question='周末偶尔打车')],
    )
    result = rank_categories(path, '2026-10 哪个类别占比最高？', Event(), previous)
    context = result['analysis_context']
    assert context['category'] == '交通'
    assert context['periods'] == ['2026-10']
    assert context['question'] == previous['question']
    assert context['follow_up_question'] == previous['follow_up_question']
    assert context['turns'] == previous['turns']
    assert classify_chat_route('那预算设多少？', None, context).kind == 'analysis'

def test_screenshot_spoken_rank_questions(tmp_path):
    from qt_finance.finance_dialogue import rank_categories, rank_question

    path = tmp_path / 'spoken-rank.sqlite3'
    store = Store(path)
    past = month_shift(date.today(), -1)
    earlier = month_shift(date.today(), -2)
    store.add_manual_transaction(past + '-09', '合成餐厅', 3200, '支出', '餐饮')
    store.add_manual_transaction(past + '-10', '合成交通', 7800, '支出', '交通')
    store.add_manual_transaction(earlier + '-09', '合成餐厅', 9900, '支出', '餐饮')
    query = '这几个月我开销最大的是哪一类型的支出'
    assert rank_question(query) == (True, True)
    merged = rank_categories(path, query, Event())
    assert '合计 占比最高：餐饮' in merged['answer']
    query = f'{past}我开销最大的是哪几个类型的支出'
    assert rank_question(query) == (True, True)
    single = rank_categories(path, query, Event())
    assert '交通，占 70.9%' in single['answer'] and '餐饮' in single['analysis_facts']

