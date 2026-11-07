from pathlib import Path
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import json
import pytest
from finance.storage import Store
from finance.importer import parse_statement
from scripts.m2_ui_fixture import seed_ui_refunds


def test_account_edit_audit_and_type_is_not_editable(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');identifier=store.add_account('银行','debit','我的卡','1234')
    assert store.update_account(identifier,label=' 新名称 ',channel=' 新平台 ',last4=' 4321 ',confirmed=True)
    row=store.accounts()[0]
    assert (row['label'],row['channel'],row['last4'],row['type'])==('新名称','新平台','4321','debit') and row['updated_at']
    with store.connect(read_only=True) as db:
        info=json.loads(db.execute("SELECT detail FROM audit_events WHERE event='account_updated'").fetchone()[0])
        assert info['old']['last4']=='1234' and info['new']['last4']=='4321'
    with pytest.raises(TypeError):store.update_account(identifier,type='wallet',confirmed=True)
    before=Path(store.path).read_bytes()
    assert not store.update_account(identifier,label='新名称',confirmed=True)
    assert Path(store.path).read_bytes()==before


def test_archive_restore_duplicate_and_history_are_retained(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');identifier=store.add_account('Bank','debit',' Card ','1234')
    with pytest.raises(ValueError,match='相同'):store.add_account(' bank ','debit','card',' 1234 ')
    assert store.archive_account(identifier,confirmed=True)
    assert store.accounts()[0]['archived_at'] and not store.accounts(include_archived=False)
    replacement=store.add_account('bank','debit','card','1234')
    with pytest.raises(ValueError,match='相同'):store.restore_account(identifier,confirmed=True)
    store.update_account(identifier,label='旧卡',confirmed=True)
    assert store.restore_account(identifier,confirmed=True)
    before=Path(store.path).read_bytes();assert not store.restore_account(identifier,confirmed=True)
    assert Path(store.path).read_bytes()==before and len(store.accounts(include_archived=False))==2
    with store.connect(read_only=True) as db:assert db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]==2


def test_archive_requires_ended_binding_and_blocks_all_new_uses(tmp_path):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3');wallet=next(a for a in store.accounts() if a['type']=='wallet');bank=next(a for a in store.accounts() if a['type']=='debit')
    with pytest.raises(ValueError,match='绑定'):store.archive_account(wallet['id'],confirmed=True)
    link=store.funding_links()[0];store.end_funding_link(link['id'],(date.today()-timedelta(days=1)).isoformat())
    before=store.spending_view().amount_cents.tolist()
    store.archive_account(wallet['id'],confirmed=True)
    assert store.spending_view().amount_cents.tolist()==before
    assert store.transaction_source(ids['P-W'])['account_id']==wallet['id']
    with pytest.raises(ValueError,match='归档'):store.add_funding_link(wallet['id'],bank['id'],date.today().isoformat())
    with pytest.raises(ValueError,match='归档'):store.end_funding_link(link['id'],date.today().isoformat())
    content='交易时间,交易对方,商品,收/支,金额(元),当前状态,交易单号\n2026-11-01 12:00:00,合成餐厅,午餐,支出,10.00,支付成功,NEW\n'
    parsed=parse_statement(content.encode(),'synthetic.csv',adapter='wechat_csv',account_id=wallet['id'])
    snapshot=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='归档'):store.import_statement(parsed)
    assert Path(store.path).read_bytes()==snapshot
    assert len(store.source_transactions())==4


@pytest.mark.parametrize('route',['update','archive','restore'])
@pytest.mark.parametrize('consent',[False,None,1,'yes'])
def test_account_mutations_require_exact_confirmation(tmp_path,route,consent):
    store=Store(tmp_path/'ledger.sqlite3');identifier=store.add_account('银行','debit','卡')
    before=Path(store.path).read_bytes()
    method={'update':store.update_account,'archive':store.archive_account,'restore':store.restore_account}[route]
    with pytest.raises(ValueError):method(identifier,confirmed=consent)
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('route',['add','update','archive','restore'])
def test_account_audit_failure_rolls_back(tmp_path,route):
    store=Store(tmp_path/'ledger.sqlite3');identifier=store.add_account('银行','debit','卡')
    if route=='restore':store.archive_account(identifier,confirmed=True)
    before=Path(store.path).read_bytes()
    with patch('finance.reconciliation.audit',side_effect=RuntimeError('synthetic audit failure')):
        with pytest.raises(RuntimeError):
            if route=='add':store.add_account('银行','credit','另一张卡')
            elif route=='update':store.update_account(identifier,label='新卡名',confirmed=True)
            elif route=='archive':store.archive_account(identifier,confirmed=True)
            else:store.restore_account(identifier,confirmed=True)
    assert Path(store.path).read_bytes()==before


def test_duplicate_update_and_readonly_refusals(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');one=store.add_account('银行','debit','卡A');two=store.add_account('银行','debit','卡B')
    with pytest.raises(ValueError,match='相同'):store.update_account(two,label='卡A',confirmed=True)
    readonly=Store(store.path,read_only=True);before=Path(store.path).read_bytes()
    for method in (readonly.update_account,readonly.archive_account,readonly.restore_account):
        with pytest.raises(ValueError,match='只读'):method(one,confirmed=True)
    with pytest.raises(ValueError):readonly.add_account('银行','credit','新卡')
    assert Path(store.path).read_bytes()==before


def test_simultaneous_duplicate_account_creation_has_one_winner(tmp_path):
    path=tmp_path/'ledger.sqlite3';Store(path)
    def create(_):
        try:return Store(path).add_account(' Bank ','debit',' Card ','1234')
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create,range(2)))
    assert sum(r is not None for r in results)==1 and len(Store(path).accounts())==1


def test_platform_edit_invalidates_evidence_but_name_does_not(tmp_path):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3');store.confirm_refund_link(ids['R-W'],ids['P-W'])
    wallet=next(a for a in store.accounts() if a['type']=='wallet')
    store.update_account(wallet['id'],label='新名称',confirmed=True)
    assert store.refund_links()[0]['status']=='confirmed'
    store.update_account(wallet['id'],channel='其他平台',confirmed=True)
    assert store.refund_links()[0]['status']=='needs_review'


@pytest.mark.parametrize('changed',[{'channel':'其他银行'},{'account_type':'credit'},{'last4':'5678'},{'label':'另一张卡'}])
def test_similar_but_distinct_accounts_are_allowed(tmp_path,changed):
    store=Store(tmp_path/'ledger.sqlite3');base=dict(channel='银行',account_type='debit',label='我的卡',last4='1234')
    store.add_account(**base);store.add_account(**dict(base,**changed))
    assert len(store.accounts(include_archived=False))==2


@pytest.mark.parametrize('changed',[{'label':''},{'label':'x'*61},{'channel':' '},{'channel':'x'*31},{'last4':'123'},{'last4':'12345'},{'last4':'abcd'},{'last4':None}])
def test_invalid_creation_rolls_back_without_audit(tmp_path,changed):
    store=Store(tmp_path/'ledger.sqlite3');before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.add_account(**dict(dict(channel='银行',account_type='debit',label='卡',last4='1234'),**changed))
    assert Path(store.path).read_bytes()==before
