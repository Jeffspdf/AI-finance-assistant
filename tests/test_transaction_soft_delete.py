from pathlib import Path
from unittest.mock import patch
import json
import pytest
from finance.storage import Store
from finance.analytics import budget_report
from finance.qa import answer_question
from scripts.m2_ui_fixture import seed_ui_refunds


def test_import_delete_preserves_sources_and_invalidates_relationships(tmp_path):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    store.confirm_refund_link(ids['R-W'],ids['P-W'])
    original=store.transaction_source(ids['P-W'])
    assert store.soft_delete_transaction(ids['P-W'],confirmed=True)
    assert ids['P-W'] not in set(store.source_transactions().id)
    assert ids['P-W'] not in set(store.transactions().id)
    assert ids['P-W'] not in set(store.spending_view().id)
    assert store.transaction_source(ids['P-W'])['raw_json']==original['raw_json']
    assert store.refund_links()[0]['status']=='needs_review'
    assert any(g['stored_status']=='needs_review' for g in store.reconciliation_groups())
    assert Store(store.path).transaction_source(ids['P-W'])['status']=='deleted'
    with store.connect(read_only=True) as db:
        detail=json.loads(db.execute("SELECT detail FROM audit_events WHERE event='transaction_soft_deleted'").fetchone()[0])
        assert detail['amount_cents']==10000 and detail['raw_source_retained']


def test_manual_delete_every_projection_and_idempotence(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3')
    txid=store.add_manual_transaction('2026-11-01','合成商户',10000,'支出','餐饮','合成')
    store.save_budget('2026-11','餐饮',20000)
    assert store.soft_delete_transaction(txid,confirmed=True)
    assert store.spending_view().empty
    assert budget_report(store.spending_view(),'2026-11',store.budgets('2026-11')).iloc[0]['已支出']==0
    answer=answer_question(store.spending_view(),'2026-11餐饮花了多少？')
    assert not answer['evidence']
    before=Path(store.path).read_bytes()
    assert store.soft_delete_transaction(txid,confirmed=True) is False
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('consent',[False,None,1,'yes'])
def test_delete_requires_exact_confirmation(tmp_path,consent):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3');before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.soft_delete_transaction(ids['P-W'],confirmed=consent)
    assert Path(store.path).read_bytes()==before


def test_readonly_missing_and_audit_failure(tmp_path):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3');before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):Store(store.path,read_only=True).soft_delete_transaction(ids['P-W'],confirmed=True)
    with pytest.raises(ValueError):store.soft_delete_transaction('missing',confirmed=True)
    with patch('finance.storage.audit',side_effect=RuntimeError('synthetic audit failure')):
        with pytest.raises(RuntimeError):store.soft_delete_transaction(ids['P-W'],confirmed=True)
    assert Path(store.path).read_bytes()==before
