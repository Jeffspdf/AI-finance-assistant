from datetime import date
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from finance.storage import Store
from qt_finance.query_context import (
    analysis_context_from_query,
    change_contributions,
    last_query,
    QueryModelReceipt,
)
from qt_finance.services import query_ledger


@pytest.fixture
def ledger(tmp_path):
    store = Store(tmp_path / 'query.sqlite3')
    for month, cents in (('08', 182060), ('09', 205261), ('10', 97299)):
        store.add_manual_transaction(f'2026-{month}-01', '星巴克', cents, '支出', '餐饮')
        store.add_manual_transaction(f'2026-{month}-02', '地铁', 2500, '支出', '交通')
    return store


@pytest.mark.parametrize('question', ['十月份的餐饮花了多少钱？', '10月餐饮支出多少', '二〇二六年十月餐饮花了多少钱'])
def test_bare_month_never_expands_to_all(ledger, question):
    before = Path(ledger.path).read_bytes()
    result = query_ledger(ledger.path, question, None, Event())
    assert result['status'] == 'ok' and result['scope'] == '2026-10'
    assert sum(row['amount_cents'] for row in result['evidence']) == 97299
    assert result['query_state']['period'] == '2026-10'
    assert result['suggestions'][0] == '那2026-09呢？'
    assert Path(ledger.path).read_bytes() == before


def test_ambiguous_month_and_missing_month(ledger):
    ledger.add_manual_transaction('2025-10-01', '旧记录', 999, '支出', '餐饮')
    result = query_ledger(ledger.path, '十月份的餐饮花了多少钱', None, Event())
    assert result['status'] == 'unsupported' and '多个年份' in result['answer']
    first = query_ledger(ledger.path, '2026-09 餐饮支出多少', None, Event())
    assert query_ledger(ledger.path, '那十月呢', None, Event(), previous=first)['status'] == 'unsupported'
    assert query_ledger(ledger.path, '那11月呢', None, Event(), previous=first)['status'] == 'missing'


def test_current_month_and_recovery_after_failed_turn(ledger):
    first = query_ledger(ledger.path, '十月份的餐饮花了多少钱', None, Event())
    first['ledger_generation'] = 3
    history = [('assistant', first), ('user', '那这个月呢'),
               ('assistant', {'status': 'missing', 'answer': '没有记录'})]
    assert last_query(history, 3) is first
    assert last_query(history, 4) is None
    assert last_query([], 3) is None
    with patch('qt_finance.services.date') as clock:
        clock.today.return_value = date(2026, 11, 17)
        missing = query_ledger(ledger.path, '那这个月呢？', None, Event(), previous=first)
    assert missing['status'] == 'missing' and missing['scope'] == '2026-11'
    second = query_ledger(ledger.path, '那九月呢', None, Event(), previous=last_query(history, 3))
    assert second['scope'] == '2026-09'
    assert sum(row['amount_cents'] for row in second['evidence']) == 205261
    only = query_ledger(ledger.path, '只看星巴克', None, Event(), previous=second)
    assert only['query_state']['merchant'] == '星巴克' and only['query_state']['category'] == '餐饮'


def test_contributions_partition_and_signed_refunds():
    first = dict(evidence=[dict(category='餐饮', merchant='甲', amount_cents=10000)])
    second = dict(evidence=[dict(category='餐饮', merchant='甲', amount_cents=3000),
                            dict(category='餐饮', merchant='甲', amount_cents=-4000),
                            dict(category='交通', merchant='乙', amount_cents=2500)])
    rows = change_contributions([first, second])
    assert sum(row['delta'] for row in rows) == -8500
    assert rows[0]['delta'] == -11000


def test_compare_uses_same_filters_and_exact_decomposition(ledger):
    first = query_ledger(ledger.path, '十月份的餐饮花了多少钱', None, Event())
    second = query_ledger(ledger.path, '那九月呢', None, Event(), previous=first)
    compared = query_ledger(ledger.path, '这两个月差多少', None, Event(), previous=second)
    assert sum(row['delta'] for row in compared['contributions']) == 205261 - 97299
    assert all(row['category'] == '餐饮' for row in compared['contributions'])
    assert compared['amounts'] == [('2026-10', 97299), ('2026-09', 205261), ('变化（后者 − 前者）', 107962)]
    assert compared['scope_display'] == '2026-10 与 2026-09'


def test_privacy_receipt_counts_failed_requests_without_secrets():
    class Model:
        endpoint = 'https://example.com/v1/chat/completions?key=secret'
        def parse_question(self, question):
            raise ValueError('secret')
    receipt = QueryModelReceipt(Model())
    assert '未调用' in receipt.summary()
    with pytest.raises(ValueError):
        receipt.parse_question('问题含商户')
    assert 'example.com' in receipt.summary() and '1 次' in receipt.summary()
    assert 'secret' not in receipt.summary() and '问题含商户' not in receipt.summary()


def test_model_cannot_widen_explicit_month(ledger):
    broad = dict(status='ok', parser='模型理解 + 代码计算', evidence=[],
                 trace={'inputs': {'period': 'all', 'metric': 'expense'}})
    with patch('qt_finance.services.answer_question', return_value=broad):
        result = query_ledger(ledger.path, '十月份的餐饮花了多少钱', None, Event())
    assert result['status'] == 'unsupported' and not result['evidence']


def test_analysis_context_bridges_previous_query(ledger):
    query = query_ledger(ledger.path, '2026-10 餐饮净支出多少？', None, Event())
    query['ledger_generation'] = 7
    context = analysis_context_from_query(query, 7)
    assert context['category'] == '餐饮'
    assert context['periods'] == ['2026-10']
    assert context['question'] == '2026-10 餐饮净支出多少？'
    assert context['turns'] == []
    assert analysis_context_from_query(query, 8) is None

def test_month_only_followup_from_all_history_preserves_merchant_and_metric(tmp_path):
    from threading import Event
    from finance.storage import Store
    from qt_finance.services import query_ledger

    path = tmp_path / 'carry.sqlite3'
    store = Store(path)
    for month, merchant, category, amount in (
        ('2026-10', '星巴克', '餐饮', 3200), ('2026-10', '奶茶店', '餐饮', 4200),
        ('2026-09', '星巴克', '餐饮', 5500), ('2026-10', '交通', '交通', 2200),
    ):
        store.add_manual_transaction(month + '-08', merchant, amount, '支出', category)
    all_meals = query_ledger(path, '餐饮花了多少', None, Event())
    assert all_meals['query_state']['category'] == '餐饮'
    october = query_ledger(path, '单单10月份呢', None, Event(), previous=all_meals)
    assert october['query_state']['category'] == '餐饮'
    assert october['scope'] == '2026-10' and sum(row['amount_cents'] for row in october['evidence']) == 7400
    filtered = query_ledger(path, '只看星巴克', None, Event(), previous=october)
    september = query_ledger(path, '单看九月呢', None, Event(), previous=filtered)
    assert september['query_state']['merchant'] == '星巴克' and september['query_state']['metric'] == 'expense'
    assert sum(row['amount_cents'] for row in september['evidence']) == 5500
