"""Isolated executable checks for category domains, targeted repair and snapshots."""
from pathlib import Path
import json,uuid
from .storage import Store
from .m1a_demo import seed_six


def verify_round7(directory):
    folder=Path(directory)/('round7-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    store=Store(folder/'ledger.sqlite3');seed_six(store);store.generate_candidates()
    for kind,category in [('收入','餐饮'),('支出','工资')]:
        before=Path(store.path).read_bytes()
        try:store.add_manual_transaction('2026-09-01','合成店',100,kind,category)
        except ValueError:pass
        else:raise AssertionError('Income/expense category conflict accepted')
        assert Path(store.path).read_bytes()==before
    groups=store.reconciliation_groups()
    for group in groups:store.decide_group(group['id'],'confirmed')
    with store.connect() as db:
        for group in store.reconciliation_groups():
            member=next(r for r in group['members'] if r['role']=='funding')
            db.execute("UPDATE transactions SET channel='支付宝' WHERE id=?",(member['id'],))
    before=Path(store.path).read_bytes();assert all(g['repair_pending'] for g in store.reconciliation_groups())
    assert Path(store.path).read_bytes()==before
    assert store.repair_invalid_groups(identifier=groups[0]['id'],source='round7')==1
    assert sum(g['repair_pending'] for g in store.reconciliation_groups())==1
    assert store.repair_invalid_groups(identifier=groups[0]['id'])==0
    identifier=store.add_manual_transaction('2026-09-08','合成旧分类',100,'收入','工资')
    with store.connect() as db:db.execute("UPDATE transactions SET category='餐饮' WHERE id=?",(identifier,))
    legacy=Store(store.path);assert legacy.transactions().set_index('id').loc[identifier,'category']=='餐饮'
    with store.connect() as db:db.execute("UPDATE transactions SET category='转账' WHERE id=?",(identifier,))
    repaired=Store(store.path);snapshot=repaired.semantic_backup;assert snapshot and snapshot.is_file()
    reopened=Store(store.path);before=Path(store.path).read_bytes()
    assert reopened.preview_managed_backup(snapshot)['counts']['transactions']==7
    assert reopened.semantic_repair_history()[0]['detail']['old_category']=='转账'
    readonly=Store(store.path,read_only=True)
    try:readonly.delete_managed_backup(snapshot,confirmed=True)
    except ValueError:pass
    else:raise AssertionError('Read-only ledger deleted snapshot')
    reopened.delete_managed_backup(snapshot,confirmed=True)
    assert not snapshot.exists() and Path(store.path).read_bytes()==before
    report=dict(data='synthetic only',income_expense_categories_guarded=True,targeted_repair_only_selected=True,
                query_bytes_unchanged=True,legacy_income_expense_preserved=True,backup_discoverable_after_restart=True,
                backup_preview_and_explicit_delete=True,read_only_delete_rejected=True)
    (Path(directory)/'round7-self-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
