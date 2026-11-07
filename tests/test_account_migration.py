from pathlib import Path
import sqlite3
from contextlib import closing
from hashlib import sha256
from unittest.mock import patch
import pytest
from finance.storage import Store
import finance.migrations as migration
from m3_v5_fixture import create_v5
from test_m2_1 import snapshot


def test_frozen_v5_adds_only_two_columns_and_preserves_every_row(tmp_path):
    path=create_v5(tmp_path/'v5.sqlite3');old=snapshot(path)
    backup=migration.ensure_schema(path);new=snapshot(path)
    assert snapshot(backup)==old and new['version']==6
    assert set(old['rows'])==set(new['rows'])
    for name,rows in old['rows'].items():
        if name=='accounts':assert new['rows'][name]==[tuple(r)+(None,None) for r in rows]
        elif name=='schema_migrations':assert new['rows'][name][:len(rows)]==rows
        else:assert new['rows'][name]==rows
    for name,value in old['objects'].items():
        if name!='accounts':assert new['objects'][name]==value
    with closing(sqlite3.connect(path)) as db:
        assert [r[1] for r in db.execute('PRAGMA table_info(accounts)')][-2:]==['archived_at','updated_at']
        assert db.execute('SELECT checksum FROM schema_migrations WHERE version=6').fetchone()[0]==sha256('\n'.join(migration.M3).encode()).hexdigest()
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('fault',[0,1,2,'audit'])
def test_v5_migration_fault_restores_bytes_schema_and_history(tmp_path,monkeypatch,fault):
    path=create_v5(tmp_path/'v5.sqlite3');before=path.read_bytes();old=snapshot(path)
    if fault=='audit':
        with closing(sqlite3.connect(path)) as db:db.execute("CREATE TRIGGER reject_v6 BEFORE INSERT ON schema_migrations WHEN NEW.version=6 BEGIN SELECT RAISE(ABORT,'synthetic migration history fault'); END");db.commit()
        before=path.read_bytes();old=snapshot(path)
    else:
        monkeypatch.setattr(migration,'M3',migration.M3[:fault]+['SELECT synthetic_ddl_fault()']+migration.M3[fault:])
    with pytest.raises(sqlite3.DatabaseError):migration.ensure_schema(path)
    assert path.read_bytes()==before and snapshot(path)==old
    assert snapshot(next((tmp_path/'backups').glob('*.sqlite3')))==old


def test_readonly_v5_future_v7_backup_failure_and_idempotence(tmp_path):
    path=create_v5(tmp_path/'v5.sqlite3');before=path.read_bytes()
    with pytest.raises(ValueError):Store(path,read_only=True)
    assert path.read_bytes()==before
    with patch.object(migration,'backup_database',side_effect=OSError('synthetic backup failure')):
        with pytest.raises(OSError):migration.ensure_schema(path)
    assert path.read_bytes()==before
    migration.ensure_schema(path);before=path.read_bytes()
    assert migration.ensure_schema(path) is None
    Store(path,read_only=True)
    assert path.read_bytes()==before
    with closing(sqlite3.connect(path)) as db:db.execute('PRAGMA user_version=7');db.commit()
    before=path.read_bytes()
    with pytest.raises(ValueError):Store(path)
    assert path.read_bytes()==before
