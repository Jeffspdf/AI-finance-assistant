"""Deletion stays a user decision until an explicit, scoped and audited restore."""
from pathlib import Path
from unittest.mock import patch
import json
import pytest

from finance.storage import Store
from finance.importer import parse_statement
from test_core import csv_bytes
from scripts.m2_ui_fixture import seed_ui_refunds


def statement(extra=False, amount='10', account=None):
    rows=[['ONE','2026-09-01 12:00:00','合成餐厅','午餐',amount,'支出','样例','CNY'],
          ['TWO','2026-09-02 12:00:00','地铁','交通','20','支出','样例','CNY']]
    if extra:rows.append(['THREE','2026-09-03 12:00:00','商店','购物','30','支出','样例','CNY'])
    return parse_statement(csv_bytes(rows),'synthetic.csv',retain_source=True,account_id=account)


def deleted(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');parsed=statement();store.import_statement(parsed)
    identifier=parsed.rows[0]['id'];store.soft_delete_transaction(identifier,confirmed=True)
    return store,parsed,identifier


def test_preview_is_readonly_and_explains_single_deletion(tmp_path):
    store,parsed,identifier=deleted(tmp_path);before=Path(store.path).read_bytes()
    preview=store.preview_import(parsed)
    assert preview['deleted_count']==1
    assert preview['deleted_rows'][0]['id']==identifier
    assert preview['deleted_rows'][0]['reason']=='单笔删除'
    assert preview['deleted_rows'][0]['delete_event']>0
    assert Path(store.path).read_bytes()==before


def test_default_import_keeps_deletion_but_adds_missing_records(tmp_path):
    store,_,identifier=deleted(tmp_path)
    result=store.import_statement(statement(extra=True))
    assert result==(1,1,[]) and result.deleted_skipped==1 and result.restored==0
    assert store.transaction_source(identifier)['status']=='deleted'
    assert set(store.spending_view().external_id)=={'TWO','THREE'}
    assert int(store.spending_view().amount_cents.sum())==5000
    repeated=store.import_statement(statement(extra=True))
    assert repeated==(0,2,[]) and repeated.deleted_skipped==1


def test_explicit_restore_keeps_id_writes_audit_and_reports_separate_count(tmp_path):
    store,parsed,identifier=deleted(tmp_path)
    preview=store.preview_import(parsed)
    result=store.import_statement(parsed,restore_deleted=True,restore_ids=[r['id'] for r in preview['deleted_rows']])
    assert result==(1,1,[]) and result.restored==1 and result.deleted_skipped==0
    assert store.transaction_source(identifier)['status']=='normal'
    assert set(store.spending_view().id)=={r['id'] for r in parsed.rows}
    with store.connect(read_only=True) as db:
        events=[dict(r) for r in db.execute("SELECT * FROM audit_events WHERE event='transaction_restored'")]
        detail=json.loads(events[0]['detail'])
        assert len(events)==1 and events[0]['entity_id']==identifier and detail['explicit_restore'] is True
        assert detail['old_status']=='deleted' and detail['new_status']=='normal'
        assert detail['amount_cents']==1000
        batch=json.loads(db.execute("SELECT detail FROM audit_events WHERE event='batch_imported' ORDER BY id DESC LIMIT 1").fetchone()[0])
        assert batch['restored']==1 and batch['deleted_skipped']==0
    again=store.import_statement(parsed,restore_deleted=True)
    assert again==(0,2,[]) and again.restored==0


def test_rows_deleted_after_preview_are_not_in_checkbox_authorization(tmp_path):
    store,parsed,identifier=deleted(tmp_path);preview=store.preview_import(parsed)
    other=parsed.rows[1]['id'];store.soft_delete_transaction(other,confirmed=True)
    result=store.import_statement(parsed,restore_deleted=True,restore_ids=[r['id'] for r in preview['deleted_rows']])
    assert result.restored==1 and result.deleted_skipped==1
    assert store.transaction_source(identifier)['status']=='normal'
    assert store.transaction_source(other)['status']=='deleted'


def test_batch_delete_also_requires_explicit_restore_and_is_labeled(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');parsed=statement();store.import_statement(parsed)
    store.delete_import_batch(store.import_batches()[0]['id'])
    preview=store.preview_import(parsed)
    assert preview['deleted_count']==2 and {r['reason'] for r in preview['deleted_rows']}=={'整批删除'}
    default=store.import_statement(parsed)
    assert default.deleted_skipped==2 and store.spending_view().empty
    restored=store.import_statement(parsed,restore_deleted=True)
    assert restored.restored==2 and len(store.spending_view())==2


def test_source_parser_account_change_cannot_bypass_tombstone(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3')
    first=store.add_account('银行','debit','合成一');second=store.add_account('银行','debit','合成二')
    original=statement(account=first);store.import_statement(original)
    store.delete_import_batch(store.import_batches()[0]['id'])
    changed=statement(account=second)
    preview=store.preview_import(changed)
    assert {r['id'] for r in preview['deleted_rows']}=={r['id'] for r in original.rows}
    result=store.import_statement(changed)
    assert result.deleted_skipped==2 and store.spending_view().empty
    result=store.import_statement(changed,restore_deleted=True)
    assert result.restored==2
    assert set(store.spending_view().id)=={r['id'] for r in original.rows}
    assert set(store.spending_view().account_id)=={second}


def test_legacy_unscoped_deletion_is_not_bypassed_by_retained_import(tmp_path):
    raw=statement();rows=parse_statement(csv_bytes([
        ['ONE','2026-09-01 12:00:00','合成餐厅','午餐','10','支出','样例','CNY'],
        ['TWO','2026-09-02 12:00:00','地铁','交通','20','支出','样例','CNY']]),'legacy.csv').rows
    store=Store(tmp_path/'ledger.sqlite3');store.import_rows(rows)
    old_id=rows[0]['id'];store.soft_delete_transaction(old_id,confirmed=True)
    assert store.preview_import(raw)['deleted_count']==1
    result=store.import_statement(raw)
    assert result.deleted_skipped==1 and result[0]==0
    result=store.import_statement(raw,restore_deleted=True)
    assert result.restored==1 and store.transaction_source(old_id)['status']=='normal'
    assert store.import_rows([rows[0]])==(0,1,[])


def test_conflicting_amount_does_not_restore_or_overwrite_deleted_record(tmp_path):
    store,_,identifier=deleted(tmp_path)
    result=store.import_statement(statement(amount='99'),restore_deleted=True)
    assert result[0]==0 and len(result[2])==1 and result.restored==0
    source=store.transaction_source(identifier)
    assert source['status']=='deleted' and source['amount_cents']==1000


def test_restore_audit_failure_rolls_back_entire_import(tmp_path):
    store,parsed,_=deleted(tmp_path);before=Path(store.path).read_bytes()
    with patch('finance.storage.audit',side_effect=RuntimeError('synthetic audit failure')):
        with pytest.raises(RuntimeError):store.import_statement(parsed,restore_deleted=True)
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('value',[None,1,'yes'])
def test_restore_flag_requires_exact_boolean(tmp_path,value):
    store,parsed,_=deleted(tmp_path);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.import_statement(parsed,restore_deleted=value)
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('endpoint',['P-W','R-W'])
def test_explicit_restore_does_not_reconfirm_old_refund_or_duplicate_group(tmp_path,endpoint):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    store.confirm_refund_link(ids['R-W'],ids['P-W'])
    with store.connect(read_only=True) as db: original=dict(db.execute('SELECT * FROM transactions WHERE id=?',(ids[endpoint],)).fetchone())
    store.soft_delete_transaction(ids[endpoint],confirmed=True)
    assert store.import_rows([original]).deleted_skipped==1
    result=store.import_rows([original],restore_deleted=True)
    assert result.restored==1 and store.refund_links()[0]['stored_status']=='needs_review'
    assert store.transaction_source(ids[endpoint])['id']==original['id']
    assert all(g['stored_status']!='confirmed' for g in store.reconciliation_groups() if original['id'] in [r['id'] for r in g['members']])
