"""Verified seventh-review gaps, explicit per-group maintenance and safe snapshots."""
from pathlib import Path
import sqlite3
import pytest
from finance.storage import Store
from finance.categories import category_options
from finance.m1a_demo import seed_six


@pytest.mark.parametrize('kind,category',[('收入','餐饮'),('支出','工资')])
@pytest.mark.parametrize('route',['manual','insert','single','batch','edit','model','undo'])
def test_income_expense_category_conflicts_atomic(tmp_path,kind,category,route):
    store=Store(tmp_path/'ledger.sqlite3')
    valid='工资' if kind=='收入' else '餐饮'
    identifier=store.add_manual_transaction('2026-09-01','合成商户',1234,kind,valid)
    row=store.source_transactions().iloc[0].to_dict()
    source='待确认' if route=='model' else '用户修正'
    with store.connect() as db:db.execute('UPDATE transactions SET category_source=? WHERE id=?',(source,identifier))
    before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='类型冲突'):
        if route=='manual':store.add_manual_transaction('2026-09-01','合成商户',1234,kind,category)
        elif route=='insert':store.import_rows([dict(row,id='bad',signature='bad',category=category)])
        elif route=='single':store.correct_categories([(identifier,category)],learn=True)
        elif route=='batch':store.batch_category([identifier],category)
        elif route=='edit':store.edit_transaction(identifier,dict(row,category=category))
        elif route=='model':store.apply_model_category(identifier,category)
        else:store.undo_category_batch([dict(id=identifier,category=category,source='规则',new=valid)])
    assert Path(store.path).read_bytes()==before


def test_category_sets_and_legacy_income_expense_are_not_rewritten(tmp_path):
    assert category_options('收入')==['工资','生活费','其他收入']
    assert not set(category_options('收入'))&set(category_options('支出'))
    assert category_options('转账')==['转账']
    store=Store(tmp_path/'ledger.sqlite3')
    identifier=store.add_manual_transaction('2026-09-01','合成店',100,'收入','工资')
    with store.connect() as db:
        db.execute("UPDATE transactions SET category='餐饮' WHERE id=?",(identifier,))
        db.execute("INSERT INTO merchant_rules(merchant,kind,category) VALUES ('合成店','收入','餐饮')")
    before=Path(store.path).read_bytes();reopened=Store(store.path)
    assert reopened.semantic_backup is None and Path(store.path).read_bytes()==before
    assert reopened.transactions().iloc[0].category=='餐饮'
    from finance.importer import parse_statement
    rows=parse_statement('交易ID,日期,商户,描述,金额,类型,平台\nNEW,2026-09-02,合成店,收款,2,收入,银行\n'.encode(),'income.csv').rows
    reopened.import_rows(rows)
    assert reopened.source_transactions().set_index('id').loc[rows[0]['id'],'category']=='其他收入'
    old=reopened.source_transactions().set_index('id').loc[identifier].to_dict()
    with pytest.raises(ValueError):reopened.edit_transaction(identifier,dict(old,description='只改描述'))
    reopened.edit_transaction(identifier,dict(old,category='其他收入'))
    assert reopened.transactions().set_index('id').loc[identifier,'amount_cents']==100


def invalid_groups(store):
    seed_six(store);store.generate_candidates()
    groups=store.reconciliation_groups()
    for group in groups:store.decide_group(group['id'],'confirmed')
    with store.connect() as db:
        for group in store.reconciliation_groups():
            member=next(r for r in group['members'] if r['role']=='funding')
            db.execute("UPDATE transactions SET channel='支付宝' WHERE id=?",(member['id'],))
    return groups


def test_targeted_repair_persists_only_selected_and_is_idempotent(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');groups=invalid_groups(store)
    before=Path(store.path).read_bytes()
    assert all(g['repair_pending'] for g in store.reconciliation_groups())
    assert Path(store.path).read_bytes()==before
    gid=groups[0]['id'];assert store.repair_invalid_groups(identifier=gid,source='user_selected')==1
    with store.connect(read_only=True) as db:
        assert db.execute('SELECT status FROM link_groups WHERE id=?',(gid,)).fetchone()[0]=='needs_review'
        assert db.execute('SELECT status FROM link_groups WHERE id=?',(groups[1]['id'],)).fetchone()[0]=='confirmed'
        assert db.execute('SELECT COUNT(*) FROM transactions WHERE link_group_id=?',(gid,)).fetchone()[0]==0
    after=Path(store.path).read_bytes();assert store.repair_invalid_groups(identifier=gid)==0
    assert Path(store.path).read_bytes()==after
    with pytest.raises(ValueError,match='当前来源'):store.reopen_group(gid)
    assert next(g for g in store.reconciliation_groups() if g['id']==gid)['stored_status']=='needs_review'
    with pytest.raises(ValueError,match='不存在'):store.repair_invalid_groups(identifier='missing')
    readonly=Store(store.path,read_only=True)
    with pytest.raises(ValueError,match='只读'):readonly.repair_invalid_groups(identifier=groups[1]['id'])


def repaired_ledger(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');identifier=store.add_manual_transaction('2026-09-01','合成店',100,'支出','餐饮')
    with store.connect() as db:db.execute("UPDATE transactions SET category='转账' WHERE id=?",(identifier,))
    return Store(store.path)


def test_snapshot_inventory_history_preview_restart_and_explicit_delete(tmp_path):
    store=repaired_ledger(tmp_path);snapshot=store.semantic_backup;manual=store.backup()
    other=Store(tmp_path/'personal.sqlite3');other_backup=other.backup()
    reopened=Store(store.path);before=Path(store.path).read_bytes()
    rows=reopened.managed_backups();assert {Path(r['path']) for r in rows}=={snapshot,manual}
    assert next(r for r in rows if Path(r['path'])==snapshot)['purpose']=='语义修复前'
    assert next(r for r in rows if Path(r['path'])==manual)['purpose']=='旧版/通用快照（用途未记录）'
    assert reopened.preview_managed_backup(snapshot)['counts']['transactions']==1
    assert reopened.semantic_repair_history()[0]['detail']['old_category']=='转账'
    assert Path(store.path).read_bytes()==before
    for consent in [False,None,'true',1]:
        with pytest.raises(ValueError,match='确认'):reopened.delete_managed_backup(snapshot,confirmed=consent)
    readonly=Store(store.path,read_only=True)
    with pytest.raises(ValueError,match='只读'):readonly.delete_managed_backup(snapshot,confirmed=True)
    assert snapshot.is_file()
    reopened.delete_managed_backup(snapshot,confirmed=True)
    assert not snapshot.exists() and manual.is_file() and other_backup.is_file()
    assert Path(store.path).read_bytes()==before
    assert reopened.semantic_repair_history()[0]['detail']['old_category']=='转账'
    for forbidden in [store.path,other_backup,tmp_path/'outside.sqlite3']:
        with pytest.raises(ValueError,match='当前空间'):reopened.delete_managed_backup(forbidden,confirmed=True)


def test_snapshot_preview_invalid_file_fails_without_mutation(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');backup=store.backup();backup.write_bytes(b'not a sqlite database')
    before=backup.read_bytes()
    with pytest.raises(ValueError,match='SQLite'):store.preview_managed_backup(backup)
    assert backup.read_bytes()==before
