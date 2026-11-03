"""Packaged regression for read-only discovery and confirmation-time snapshot identity."""
from pathlib import Path
import json,uuid
from .storage import Store


def verify_round8(directory):
    folder=Path(directory)/('round8-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    store=Store(folder/'ledger.sqlite3')
    income=store.add_manual_transaction('2025-01-01','合成旧收入',100,'收入','工资')
    expense=store.add_manual_transaction('2026-09-01','合成旧支出',200,'支出','餐饮')
    with store.connect() as db:
        db.execute("UPDATE transactions SET category='餐饮' WHERE id=?",(income,))
        db.execute("UPDATE transactions SET category='工资' WHERE id=?",(expense,))
    before=Path(store.path).read_bytes()
    reopened=Store(store.path);readonly=Store(store.path,read_only=True)
    assert set(reopened.category_conflicts().id)==set(readonly.category_conflicts().id)=={income,expense}
    assert Path(store.path).read_bytes()==before
    reopened.correct_categories([(income,'其他收入')])
    assert reopened.category_conflicts().id.tolist()==[expense]
    row=reopened.source_transactions().set_index('id').loc[income]
    assert row.kind=='收入' and row.amount_cents==100
    snapshot=reopened.backup();selected=reopened.managed_backups()[0]
    before=Path(store.path).read_bytes();snapshot.write_bytes(b'synthetic changed snapshot')
    try:reopened.delete_managed_backup(snapshot,confirmed=True,expected_identity=selected['identity'])
    except ValueError:pass
    else:raise AssertionError('Changed snapshot was deleted after stale confirmation')
    assert snapshot.read_bytes()==b'synthetic changed snapshot'
    refreshed=reopened.managed_backups()[0]
    reopened.delete_managed_backup(snapshot,confirmed=True,expected_identity=refreshed['identity'])
    assert not snapshot.exists() and Path(store.path).read_bytes()==before
    report=dict(data='synthetic only',legacy_conflicts_discovered_readonly=True,no_startup_income_expense_rewrite=True,
                explicit_correction_preserves_kind_amount=True,stale_snapshot_identity_rejected=True,
                fresh_confirmation_deletes_only_selected=True,live_database_unchanged_by_cleanup=True)
    (Path(directory)/'round8-self-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
