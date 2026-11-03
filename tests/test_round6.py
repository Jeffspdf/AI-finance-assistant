"""Semantic write guards, side-effect-free readers and explicit reconciliation choices."""
import json,sqlite3
from pathlib import Path
import pytest
from finance.storage import Store
from finance.importer import parse_statement
from finance.m1a_demo import seed_six


def expense(store):
    frame=store.transactions();return int(frame.loc[frame.kind=='支出','amount_cents'].sum())


def confirmed_six(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');seed_six(store);store.generate_candidates()
    groups=store.reconciliation_groups()
    for group in groups:store.decide_group(group['id'],'confirmed')
    group=next(g for g in groups if g['members'][0]['amount_cents']==2000)
    row=next(r for r in group['members'] if r['role']=='purchase')
    return store,group,row


@pytest.mark.parametrize('route',['single','batch','edit','model','manual','insert','undo'])
def test_expense_transfer_category_rejected_without_invalidating_or_writing(tmp_path,route):
    store,group,row=confirmed_six(tmp_path);before=Path(store.path).read_bytes()
    if route=='model':
        with store.connect() as db:db.execute("UPDATE transactions SET category_source='待确认' WHERE id=?",(row['id'],))
        before=Path(store.path).read_bytes()
    elif route=='undo':
        with store.connect() as db:db.execute("UPDATE transactions SET category_source='用户修正' WHERE id=?",(row['id'],))
        before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='转账'):
        if route=='single':store.correct_categories([(row['id'],'转账')],learn=True)
        elif route=='batch':store.batch_category([row['id']],'转账')
        elif route=='edit':store.edit_transaction(row['id'],dict(row,category='转账'))
        elif route=='model':store.apply_model_category(row['id'],'转账')
        elif route=='manual':store.add_manual_transaction('2026-09-01','店',2000,'支出','转账')
        elif route=='insert':store.import_rows([dict(row,id='bad',signature='bad',category='转账')])
        else:store.undo_category_batch([dict(id=row['id'],category='转账',source='规则',new=row['category'])])
    assert expense(store)==6000 and Path(store.path).read_bytes()==before
    assert all(g['status']=='confirmed' for g in store.reconciliation_groups())


@pytest.mark.parametrize('kind,category',[('收入','转账'),('转账','餐饮'),('转账','其他收入')])
def test_transfer_equivalence_guard_in_both_directions(tmp_path,kind,category):
    store=Store(tmp_path/'db.sqlite3')
    with pytest.raises(ValueError,match='转账'):store.add_manual_transaction('2026-09-01','店',100,kind,category)
    assert store.transactions().empty


def test_changing_kind_and_category_together_is_allowed(tmp_path):
    store,group,row=confirmed_six(tmp_path)
    store.edit_transaction(row['id'],dict(row,kind='转账',category='转账'))
    updated=store.source_transactions().set_index('id').loc[row['id']]
    assert updated.kind=='转账' and updated.category=='转账' and updated.link_group_id is None
    assert next(g for g in store.reconciliation_groups() if g['id']==group['id'])['status']=='needs_review'
    assert expense(store)==6000  # bank20 is independent; wallet20 is now an explicit transfer


def test_legacy_category_repair_snapshots_and_preserves_kind_amount(tmp_path):
    store,group,row=confirmed_six(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE transactions SET category='转账' WHERE id=?",(row['id'],))
        db.execute("INSERT INTO merchant_rules(merchant,kind,category) VALUES ('旧规则','支出','转账')")
    reopened=Store(store.path)
    assert reopened.semantic_backup and reopened.semantic_backup.is_file()
    with sqlite3.connect(reopened.semantic_backup) as backup:
        assert backup.execute('SELECT category FROM transactions WHERE id=?',(row['id'],)).fetchone()[0]=='转账'
    fixed=reopened.source_transactions().set_index('id').loc[row['id']]
    assert fixed.kind=='支出' and fixed.amount_cents==2000 and fixed.category=='其他支出' and fixed.category_source=='待确认'
    assert expense(reopened)==8000
    with reopened.connect(read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='category_semantics_repaired'").fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM merchant_rules').fetchone()[0]==0
        old=json.loads(db.execute('SELECT old_values FROM transaction_edits WHERE transaction_id=?',(row['id'],)).fetchone()[0])
        assert old['category']=='转账' and old['amount_cents']==2000
    again=Store(store.path);assert again.semantic_backup is None


def tamper_confirmed(store,group):
    member=next(r for r in group['members'] if r['role']=='funding')
    with store.connect() as db:db.execute("UPDATE transactions SET channel='支付宝' WHERE id=?",(member['id'],))


def test_readers_do_not_repair_write_audit_or_touch_database_bytes(tmp_path):
    store,group,row=confirmed_six(tmp_path);tamper_confirmed(store,group)
    before=Path(store.path).read_bytes()
    for _ in range(2):
        assert expense(store)==8000
        frame=store.source_transactions();assert frame.attrs['invalid_groups']
        result=next(g for g in store.reconciliation_groups() if g['id']==group['id'])
        assert result['status']=='needs_review' and result['stored_status']=='confirmed' and result['repair_pending']
        store.pending_refunds();store.accounts();store.funding_links();store.candidate_summary();store.group_diagnostics(group['id'])
        store.budgets('2026-09');store.goals();store.import_batches();store.transaction_source(row['id'])
    assert Path(store.path).read_bytes()==before
    with store.connect(read_only=True) as db:
        assert db.execute('SELECT status FROM link_groups WHERE id=?',(group['id'],)).fetchone()[0]=='confirmed'
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='link_invalidated'").fetchone()[0]==0
    assert store.repair_invalid_groups(source='test_explicit')==1
    assert store.repair_invalid_groups(source='test_explicit_again')==0
    with store.connect(read_only=True) as db:
        detail=json.loads(db.execute("SELECT detail FROM audit_events WHERE event='link_invalidated'").fetchone()[0])
        assert detail['trigger']=='test_explicit'
        assert db.execute('SELECT link_group_id FROM transactions WHERE id=?',(row['id'],)).fetchone()[0] is None


def test_read_only_store_can_inspect_invalid_group_and_refuses_mutation(tmp_path):
    store,group,row=confirmed_six(tmp_path);tamper_confirmed(store,group)
    before=Path(store.path).read_bytes();readonly=Store(store.path,read_only=True)
    assert expense(readonly)==8000 and readonly.reconciliation_groups()[0]
    with readonly.connect() as db:
        assert db.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):db.execute('UPDATE transactions SET amount_cents=1')
    with pytest.raises(ValueError,match='只读'):readonly.repair_invalid_groups()
    with pytest.raises(ValueError,match='只读'):readonly.restore(tmp_path/'unused.sqlite3',confirmed=True)
    assert Path(store.path).read_bytes()==before
    reopened=Store(store.path)
    assert next(g for g in reopened.reconciliation_groups() if g['id']==group['id'])['stored_status']=='needs_review'


def bank_pair(store):
    a=store.add_account('银行','debit','合成银行A');b=store.add_account('银行','debit','合成银行B')
    content='交易ID,日期,商户,描述,金额,类型,平台\nA,2026-09-01 12:00:00,同店,同笔消费,20,支出,银行\nB,2026-09-01 12:00:30,同店,同笔消费,20,支出,银行\n'.encode()
    rows=parse_statement(content,'bank.csv').rows;rows[0]['account_id']=a;rows[1]['account_id']=b
    store.import_rows(rows);store.generate_candidates();return store.reconciliation_groups()[0]


@pytest.mark.parametrize('acknowledgement',[False,None,'false',1])
def test_non_wallet_group_requires_explicit_boolean_confirmation(tmp_path,acknowledgement):
    store=Store(tmp_path/'db.sqlite3');group=bank_pair(store)
    assert group['confidence']=='B' and store.group_diagnostics(group['id'])['wallet_count']==0
    with pytest.raises(ValueError,match='没有钱包'):store.decide_group(group['id'],'confirmed',acknowledge_no_wallet=acknowledgement)
    assert expense(store)==4000
    store.decide_group(group['id'],'confirmed',acknowledge_no_wallet=True);assert expense(store)==2000
    with store.connect(read_only=True) as db:
        detail=json.loads(db.execute("SELECT detail FROM audit_events WHERE event='link_confirmed'").fetchone()[0])
        assert detail['non_wallet_acknowledged'] is True


def test_C_diagnostics_identifies_binding_time_channel_and_account_gaps(tmp_path):
    store=Store(tmp_path/'db.sqlite3');seed_six(store)
    with store.connect() as db:db.execute('DELETE FROM funding_links')
    store.generate_candidates();group=next(g for g in store.reconciliation_groups() if g['members'][0]['amount_cents']==2000)
    before=Path(store.path).read_bytes();report=store.group_diagnostics(group['id'])
    assert 'binding' in {g['code'] for g in report['gaps']} and Path(store.path).read_bytes()==before
    member=next(r for r in group['members'] if r['role']=='funding')
    with store.connect() as db:
        db.execute("UPDATE transactions SET account_id=NULL,date='2026-09-01 12:30:00',description='未知渠道',merchant='普通银行' WHERE id=?",(member['id'],))
    report=store.group_diagnostics(group['id']);codes={g['code'] for g in report['gaps']}
    assert {'account','time','channel'}<=codes and report['requires_reimport']
