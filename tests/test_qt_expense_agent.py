from datetime import date
from types import SimpleNamespace

import pytest

from qt_finance.chat_router import classify_chat_route
from qt_finance.expense_draft import (
    expense_draft,
    full_amount_candidates,
    is_expense_candidate,
    revise_expense,
    understand_expense,
)


@pytest.mark.parametrize('text', ['找一下昨天花了100元的记录', '看看今天花了100元的那笔'])
def test_lookup_stays_read_only(text):
    assert classify_chat_route(text).kind == 'query'


def test_multiturn_and_literal_amount():
    result = dict(intent='expense', amount_text='', merchant='星巴克', multiple=False)
    client = SimpleNamespace(understand_expense=lambda text: result)
    day = date(2026, 11, 18)
    question = '昨晚去星巴克，用微信结的'
    draft = understand_expense(client, question, today=day)
    assert (draft['date'], draft['merchant'], draft['channel'], draft['amount_cents']) == (
        '2026-11-17', '星巴克', '微信', '')
    draft = revise_expense(revise_expense(draft, '三十八'), '改成支付宝')
    assert (draft['amount_cents'], draft['channel']) == ('38.00', '支付宝')
    result['amount_text'] = '三十八'
    assert understand_expense(client, question, today=day) is None
    assert understand_expense(client, question + '，我那杯三十八', today=day)['amount_cents'] == '38.00'


def test_multiple_and_negated_channel():
    draft = expense_draft('刚才打车，微信付了32块，另加5小费')
    assert draft['multiple'] and not draft['amount_cents']
    assert revise_expense(draft, '合计37元')['amount_cents'] == '37.00'
    draft = expense_draft('帮我记一笔昨天不用微信，用支付宝花了38元，可以吗？')
    assert draft['channel'] == '支付宝'

@pytest.mark.parametrize('question', [
    '今天花了100元喝了杯星巴克', '今天花了100元喝了一杯星巴克',
    '今天花了100元喝了2杯星巴克', '今天在星巴克花了100元',
])
def test_quantity_is_not_part_of_merchant(question):
    assert expense_draft(question)['merchant'] == '星巴克'


def test_model_query_is_never_coerced_to_expense():
    text = '今天花了100元喝了杯星巴克'
    result = dict(intent='query', amount_text='100元', merchant='星巴克', multiple=False)
    assert understand_expense(SimpleNamespace(understand_expense=lambda _: result), text) is None


@pytest.mark.parametrize('question', ['今天花了100元那笔', '昨天花了100元吗？'])
def test_history_request_is_not_an_expense_candidate(question):
    assert not is_expense_candidate(question)



def test_model_can_omit_a_missing_amount_using_null():
    text = '昨晚我在星巴克，用微信结的，帮我记一笔'
    result = dict(type='expense', amount_text=None, merchant='星巴克', multiple=False)
    assert understand_expense(SimpleNamespace(understand_expense=lambda _: result), text)['amount_cents'] == ''
    result['intent'] = 'query'
    assert understand_expense(SimpleNamespace(understand_expense=lambda _: result), text) is None


def test_model_amount_must_be_a_complete_literal_phrase():
    text = '昨晚去星巴克，用微信结的，我那杯三十八'
    result = dict(intent='expense', amount_text='八', merchant='星巴克', multiple=False)
    client = SimpleNamespace(understand_expense=lambda _: result)
    draft = understand_expense(client, text, today=date(2026, 11, 18))
    assert full_amount_candidates(text) == ['三十八']
    assert draft['amount_cents'] == ''

    result['amount_text'] = '三十八'
    assert understand_expense(client, text, today=date(2026, 11, 18))['amount_cents'] == '38.00'
