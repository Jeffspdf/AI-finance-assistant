"""Legacy category discovery and snapshot deletion at confirmation boundaries."""
from pathlib import Path
from types import SimpleNamespace
import os,stat
import pytest
from finance.storage import Store
from finance.m1a_demo import seed_six


def test_conflict_discovery_includes_excluded_sources_without_writing(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');seed_six(store);store.generate_candidates()
    for group in store.reconciliation_groups():store.decide_group(group['id'],'confirmed')
    funding=next(r for g in store.reconciliation_groups() for r in g['members'] if r['role']=='funding')
    income=store.add_manual_transaction('2026-09-08','合成收入',100,'收入','工资')
    deleted=store.add_manual_transaction('2026-09-09','已删除样例',100,'收入','工资')
    with store.connect() as db:
        db.execute("UPDATE transactions SET category='工资' WHERE id=?",(funding['id'],))
        db.execute("UPDATE transactions SET category='餐饮' WHERE id=?",(income,))
        db.execute("UPDATE transactions SET category='餐饮',status='deleted' WHERE id=?",(deleted,))
    before=Path(store.path).read_bytes()
    conflicts=store.category_conflicts()
    assert set(conflicts.id)=={funding['id'],income}
    assert not bool(conflicts.set_index('id').loc[funding['id'],'included_in_stats'])
    readonly=Store(store.path,read_only=True)
    assert set(readonly.category_conflicts().id)==set(conflicts.id)
    assert Path(store.path).read_bytes()==before
    store.correct_categories([(income,'其他收入')])
    assert set(store.category_conflicts().id)=={funding['id']}
    corrected=store.source_transactions().set_index('id').loc[income]
    assert corrected.kind=='收入' and corrected.amount_cents==100


def test_conflict_discovery_is_readonly_on_empty_and_transfer_legacy(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');assert store.category_conflicts().empty
    identifier=store.add_manual_transaction('2026-09-01','合成转账',100,'转账','转账')
    with store.connect() as db:db.execute("UPDATE transactions SET category='餐饮' WHERE id=?",(identifier,))
    before=Path(store.path).read_bytes()
    assert Store(store.path,read_only=True).category_conflicts().id.tolist()==[identifier]
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('change',['edit','replace'])
def test_selected_backup_changed_during_confirmation_is_not_deleted(tmp_path,change):
    store=Store(tmp_path/'ledger.sqlite3');snapshot=store.backup();row=store.managed_backups()[0]
    original=Path(store.path).read_bytes()
    if change=='edit':snapshot.write_bytes(b'synthetic replacement content')
    else:
        replacement=tmp_path/'new.sqlite3';replacement.write_bytes(b'synthetic replacement file');replacement.replace(snapshot)
    expected=snapshot.read_bytes()
    with pytest.raises(ValueError,match='确认期间已变化'):
        store.delete_managed_backup(snapshot,confirmed=True,expected_identity=row['identity'])
    assert snapshot.read_bytes()==expected and Path(store.path).read_bytes()==original


@pytest.mark.parametrize('change',['file','directory'])
def test_delete_revalidates_after_inventory_before_unlink(tmp_path,monkeypatch,change):
    store=Store(tmp_path/'ledger.sqlite3');snapshot=store.backup();row=store.managed_backups()[0]
    def replaced(_):
        snapshot.unlink()
        if change=='directory':snapshot.mkdir()
        else:snapshot.write_bytes(b'synthetic later file')
        return row
    monkeypatch.setattr(store,'_managed_backup',replaced)
    with pytest.raises(ValueError,match='变化'):store.delete_managed_backup(snapshot,confirmed=True)
    assert snapshot.exists()
    if change=='file':assert snapshot.read_bytes()==b'synthetic later file'
    else:assert snapshot.is_dir()


@pytest.mark.parametrize('mode,attributes',[(stat.S_IFLNK,0),(stat.S_IFREG,0x400)])
def test_final_delete_rejects_symlink_or_windows_reparse_attribute(tmp_path,monkeypatch,mode,attributes):
    store=Store(tmp_path/'ledger.sqlite3');snapshot=store.backup();row=store.managed_backups()[0]
    monkeypatch.setattr(store,'_managed_backup',lambda _:row)
    original=Path.lstat
    def lstat(path,*args,**kwargs):
        if path==snapshot:return SimpleNamespace(st_mode=mode,st_file_attributes=attributes)
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'lstat',lstat)
        with pytest.raises(ValueError,match='路径已变化'):store.delete_managed_backup(snapshot,confirmed=True)
    assert snapshot.is_file()


def test_deleting_snapshot_hardlink_preserves_other_link_and_live_db(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');snapshot=store.backup();alias=tmp_path/'retained-copy.sqlite3'
    os.link(snapshot,alias);expected=alias.read_bytes();live=Path(store.path).read_bytes()
    row=store.managed_backups()[0]
    store.delete_managed_backup(snapshot,confirmed=True,expected_identity=row['identity'])
    assert not snapshot.exists() and alias.read_bytes()==expected and Path(store.path).read_bytes()==live
