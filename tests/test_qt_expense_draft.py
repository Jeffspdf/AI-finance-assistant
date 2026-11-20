from datetime import date

import pytest

from qt_finance.expense_draft import expense_draft


def test_single_expense():
    draft = expense_draft('今天我花了100元喝星巴克', date(2026, 11, 18))
    assert (draft['date'], draft['amount_cents'], draft['merchant'], draft['category'],
            draft['channel']) == ('2026-11-18', '100.00', '星巴克', '餐饮', '')
    draft = expense_draft('昨天在星巴克用微信付了32.50元', date(2026, 1, 1))
    assert (draft['date'], draft['channel'], draft['merchant']) == ('2025-12-31', '微信', '星巴克')


@pytest.mark.parametrize('question', [
    '今天在星巴克花了多少钱？', '查一下昨天花了100元的交易', '我打算花100元买书',
    '昨天花了100元吗', '我每月支出100元', '我不想花100元', '查询支出明细',
    '如果我少花100元呢', '我花了100美元买书',
])
def test_other_intents(question):
    assert expense_draft(question) is None


def test_needs_confirmation():
    assert expense_draft('昨天花了10元和20元')['multiple']
    assert expense_draft('上周花了10元买书')['date'] == ''
    assert expense_draft('2026-02-30花了10元买书')['date'] == ''
    assert expense_draft('记一笔午饭')['amount_cents'] == ''
    assert expense_draft('支出1.234元')['amount_cents'] == ''
