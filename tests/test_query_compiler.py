from datetime import date
from threading import Event
from pathlib import Path

import pytest
from finance.storage import Store
from qt_finance.services import query_ledger


@pytest.fixture
def ledger(tmp_path):
    store = Store(tmp_path / 'demo.sqlite3')
    for month, cents in [('08',100), ('09',300), ('10',500)]:
        for merchant, kind, category, amount in [('星巴克','支出','餐饮',cents), ('京东商场','支出','购物',200), ('房东','支出','居住',900), ('公司','收入','工资',8000)]:
            store.add_manual_transaction('2026-'+month+'-08',merchant,amount,kind,category)
    return Path(store.path)


@pytest.mark.parametrize('text,field,expected', [
    ('那9月份收入是多少','metric','income'), ('上上个月餐饮花了多少','period','2026-09'),
    ('2026-09房租花了多少','category','居住'), ('2026-08到2026-09餐饮净支出多少','date_to','2026-09-30'),
    ('近三个月月均支出是多少','metric','average_expense'), ('近三个月餐饮花了多少','category','餐饮'),
    ('我九月份在京东商场花了多少钱','merchant','京东商场')])
def test_local_compilation(ledger, monkeypatch, text, field, expected):
    class Clock(date):
        @classmethod
        def today(cls): return cls(2026,11,18)
    monkeypatch.setattr('qt_finance.services.date',Clock)
    before = ledger.read_bytes()
    result = query_ledger(ledger,text,None,Event())
    assert result.get('query_state',{}).get(field) == expected, result
    assert ledger.read_bytes() == before


def test_direct_compare_and_merchant_clarification(ledger):
    compare = query_ledger(ledger,'八月和九月餐饮差多少',None,Event())
    assert compare['amounts'][-1][1] == 200
    prompt = query_ledger(ledger,'我在星巴克上一共花了多少钱',None,Event())
    selected = query_ledger(ledger,prompt['choices']['2026-09'],None,Event())
    assert selected['query_state']['merchant'] == '星巴克'
    assert selected['scope'] == '2026-09'
    broad = query_ledger(ledger,'九月花了多少',None,Event())
    assert not any(s.startswith('只看') for s in broad['suggestions'])

@pytest.mark.parametrize('patch', [
    {'sql':'select 1'}, {'set':{'amount_cents':300}}, {'action':'delete_transaction'},
    {'action':'patch','set':{'period':'all'},'clear':['period'],'clarification':None},
    {'action':'patch','set':{'merchant':'未提及商户'},'clear':[],'clarification':None},
    {'action':'patch','set':{'category':'购物'},'clear':[],'clarification':None},
    {'action':'patch','set':{'answer':'300'},'clear':[],'clarification':None},
])
def test_invalid_model_patch(patch):
    from finance.llm import ModelClient
    from unittest.mock import Mock
    client = Mock()
    client.parse_ledger_patch.return_value = patch
    assert ModelClient.plan_ledger_follow_up(client,{'question':'换成九月看看'}) is None
