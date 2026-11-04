"""Explicit release-only exercises on caller-prepared synthetic schema-4 data."""
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from shutil import copyfile
import json
import sqlite3
from .storage import Store
from .migrations import ensure_schema,inspect_database
from .spending_presenter import spending_totals


def verify_m2_migration(folder):
    folder=Path(folder);path=folder/'personal.sqlite3'
    def snapshot(target):
        with closing(sqlite3.connect(Path(target).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return dict(version=db.execute('PRAGMA user_version').fetchone()[0],
                        rows={name:db.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall() for name in tables},
                        objects=db.execute('SELECT name,type,sql FROM sqlite_master ORDER BY name').fetchall())
    before=snapshot(path)
    assert before['version']==4,'Requires frozen synthetic schema-4 fixture'
    store=Store(path);assert inspect_database(path)['version']==5
    assert snapshot(store.migration_backup)==before
    upgraded=snapshot(path)
    for name,rows in before['rows'].items():
        if name=='schema_migrations':assert upgraded['rows'][name][0]==rows[0]
        else:assert upgraded['rows'][name]==rows
    assert all(obj in upgraded['objects'] for obj in before['objects'])
    result=dict(data='synthetic only',migration='schema 4 -> 5',pre_migration_backup_matches=True,
                original_tables_rows_ids_audit_checksum_preserved=True)
    link=store.confirm_refund_link('R-W','P-W')
    assert spending_totals(store.spending_view())['net_cents']==7000
    valid_backup=store.backup();store.revoke_refund_link(link['id'])
    assert spending_totals(store.spending_view())['net_cents']==10000
    store.restore(valid_backup,confirmed=True)
    assert spending_totals(store.spending_view())['net_cents']==7000
    store.restore(store.migration_backup,confirmed=True)
    assert spending_totals(store.spending_view())['net_cents']==10000 and not store.refund_links()
    store.restore(valid_backup,confirmed=True)
    result['v5_backup_restore_and_v4_restore_upgrade']=True
    expected=snapshot(path)
    with patch.object(Store,'maintain_links',side_effect=RuntimeError('synthetic restore maintenance failure')):
        try:store.restore(valid_backup,confirmed=True)
        except RuntimeError:pass
        else:raise AssertionError('Restore failure was not exercised')
    assert snapshot(path)==expected
    result['failed_restore_business_and_audit_rollback']=True
    failure=folder/'migration-failure.sqlite3';copyfile(store.migration_backup,failure)
    def fail(db):raise RuntimeError('synthetic DDL failure')
    try:ensure_schema(failure,hook=fail)
    except RuntimeError:pass
    else:raise AssertionError('DDL failure was not exercised')
    assert snapshot(failure)==before
    result['failed_ddl_schema_rows_audit_rollback']=True
    future=folder/'future.sqlite3';copyfile(path,future)
    with closing(sqlite3.connect(future)) as db:db.execute('PRAGMA user_version=6');db.commit()
    future_bytes=future.read_bytes()
    try:Store(future)
    except ValueError:pass
    else:raise AssertionError('Future version was accepted')
    assert future.read_bytes()==future_bytes
    result['future_schema_rejected_without_write']=True
    initial=path.read_bytes();readonly=Store(path,read_only=True)
    assert spending_totals(readonly.spending_view())['net_cents']==7000
    readonly.refund_sources();readonly.refund_links();readonly.refund_work_items()
    assert path.read_bytes()==initial
    result['read_only_bytes_unchanged']=True
    with store.connect(read_only=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    result['integrity_and_foreign_keys_ok']=True
    (folder/'m2-migration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result
