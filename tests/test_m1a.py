"""Synthetic independent invariants for migration, provenance and reconciliation."""
from datetime import date
from io import BytesIO
from pathlib import Path
import json
import sqlite3
from unittest.mock import patch
import pandas as pd
import pytest
from finance.storage import Store
from finance.migrations import ensure_schema,inspect_database,SCHEMA_VERSION
from finance.importer import parse_statement
from finance.analytics import overview,budget_report,estimate_from_records
from finance.qa import answer_question
from finance.insights import make_insights

SAMPLES=Path(__file__).resolve().parents[1]/'samples/acceptance'


def bank_bytes(frame=None):
    if frame is None:frame=pd.read_csv(SAMPLES/'m1a_bank_template_v1.csv',dtype=str,keep_default_na=False)
    stream=BytesIO();frame.to_excel(stream,index=False,engine='openpyxl');return stream.getvalue()


def six(store):
    wallet=store.add_account('微信','wallet','合成微信')
    bank=store.add_account('银行','debit','合成卡','1234')
    store.add_funding_link(wallet,bank,'2026-01-01')
    wx=parse_statement((SAMPLES/'m1a_wechat.csv').read_bytes(),'wechat.csv',adapter='wechat_csv',account_id=wallet)
    bk=parse_statement(bank_bytes(),'bank.xlsx',adapter='bank_template_v1',account_id=bank)
    assert not wx.issues and not bk.issues
    assert store.import_statement(wx)==(4,0,[])
    assert store.import_statement(bk)==(2,0,[])
    return wallet,bank,wx,bk


def expense(store):
    f=store.transactions();return int(f.loc[f.kind=='支出','amount_cents'].sum())


def test_six_rows_confirm_split_and_all_entry_points(tmp_path):
    store=Store(tmp_path/'six.sqlite3');six(store)
    assert expense(store)==12000
    assert len(store.transactions())==5 and len(store.source_transactions())==6
    result=store.generate_candidates();assert result['automatic'] is False
    groups=[g for g in store.reconciliation_groups() if g['confidence']=='A']
    assert len(groups)==2 and expense(store)==12000
    for group in groups:store.decide_group(group['id'],'confirmed')
    assert expense(store)==6000
    raw=store.source_transactions();projected=store.transactions()
    assert (raw.counting_note=='资金来源腿 · 不重复计数').sum()==2
    assert all(projected.loc[projected.kind=='支出','channel']=='微信')
    assert store.pending_refunds()==dict(count=1,amount_cents=1000)
    for f in (raw,projected):
        trend,cats=overview(f)
        assert trend.iloc[0]['支出']==60 and cats['支出'].sum()==60
        assert budget_report(f,'2026-09',{'餐饮':1000,'教育':1000})['已支出'].sum()==60
        answer=answer_question(f,'2026-09 支出多少？')
        assert '60.00' in answer['answer'] and len(answer['evidence'])==2
        assert len([e for e in answer['trace']['evidence'] if e['type']=='LinkDecision'])==2
        estimate=estimate_from_records(f,date(2026,10,1));assert estimate['average_expense_cents']==6000
    assert '未扣退款' in answer_question(projected,'2026-09 支出多少？')['answer']
    for group in groups:store.decide_group(group['id'],'split')
    assert expense(Store(store.path))==12000
    store.generate_candidates()
    assert all(g['status']=='split' for g in store.reconciliation_groups() if g['id'] in {r['id'] for r in groups})
    assert expense(store)==12000
    store.reopen_group(groups[0]['id'])
    store.decide_group(groups[0]['id'],'confirmed')
    assert expense(store)==12000-groups[0]['members'][0]['amount_cents']


def test_document_hash_reuse_batches_raw_privacy_and_delete(tmp_path):
    store=Store(tmp_path/'db.sqlite3');_,_,wx,_=six(store)
    assert store.import_statement(wx)==(0,4,[])
    batches=store.import_batches();assert len(batches)==3
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_documents').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM raw_rows').fetchone()[0]==6
        raw=''.join(r[0] for r in db.execute('SELECT raw_json FROM raw_rows'))
        assert '支付方式' not in raw and '测试卡1234' not in raw
    original=next(b for b in batches if b['added']==4)
    store.generate_candidates()
    for g in store.reconciliation_groups():store.decide_group(g['id'],'confirmed')
    assert store.delete_import_batch(original['id'])==4
    assert expense(store)==6000 and len(store.source_transactions())==2
    assert all(g['status']=='needs_review' for g in store.reconciliation_groups())
    assert store.import_statement(wx)==(4,0,[])
    assert expense(store)==12000  # no stale decision silently reapplied
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='batch_deleted'").fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM raw_rows').fetchone()[0]==6


def test_invalid_rows_and_failed_status_retained(tmp_path):
    content='交易ID,日期,商户,金额,类型,状态,完整卡号\n1,bad,店,1,支出,,123456789\n2,2026-09-01,店,3,支出,失败,123456789\n3,2026-09-01,店,3,支出,已退款,123456789\n'
    result=parse_statement(content.encode(),'bad.csv',retain_source=True)
    assert len(result.issues)==1 and result.skipped==1 and result.rows[0]['status']=='review'
    store=Store(tmp_path/'db.sqlite3');store.import_statement(result)
    assert store.transactions().empty and len(store.source_transactions())==1
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM raw_rows').fetchone()[0]==3
        assert '123456789' not in ''.join(r[0] for r in db.execute('SELECT raw_json FROM raw_rows'))


def test_all_invalid_batch_and_import_atomicity(tmp_path):
    store=Store(tmp_path/'db.sqlite3')
    result=parse_statement(b'date,amount,merchant\nbad,abc,shop\n','bad.csv',retain_source=True)
    store.import_statement(result);assert len(store.import_batches())==1
    result=parse_statement(b'date,amount,merchant\n2026-09-01,-10,shop\n','good.csv',retain_source=True)
    with patch.object(store,'_insert_rows',side_effect=RuntimeError('injected write failure')):
        with pytest.raises(RuntimeError):store.import_statement(result)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_documents').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM import_batches').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]==0


def test_no_link_no_automatic_and_rejected_persists(tmp_path):
    store=Store(tmp_path/'db.sqlite3');six(store)
    with store.connect() as db:db.execute('DELETE FROM funding_links')
    result=store.generate_candidates();assert not result['automatic']
    assert not any(g['confidence']=='A' for g in store.reconciliation_groups())
    assert expense(store)==12000
    group=store.reconciliation_groups()[0];store.decide_group(group['id'],'rejected');store.generate_candidates()
    assert next(g for g in store.reconciliation_groups() if g['id']==group['id'])['status']=='rejected'


def test_edit_invalidates_group_and_mismatch_refused(tmp_path):
    store=Store(tmp_path/'db.sqlite3');six(store);store.generate_candidates()
    group=store.reconciliation_groups()[0];store.decide_group(group['id'],'confirmed')
    record=group['members'][0];record['amount_cents']+=1
    store.edit_transaction(record['id'],record)
    assert expense(store)==12001
    assert next(g for g in store.reconciliation_groups() if g['id']==group['id'])['status']=='needs_review'
    with store.connect() as db:db.execute("UPDATE link_groups SET status='candidate' WHERE id=?",(group['id'],))
    with pytest.raises(ValueError,match='同额'):store.decide_group(group['id'],'confirmed')


def test_membership_is_fact_cache_cannot_change_projection(tmp_path):
    store=Store(tmp_path/'db.sqlite3');six(store);store.generate_candidates()
    group=store.reconciliation_groups()[0];store.decide_group(group['id'],'confirmed');before=expense(store)
    with store.connect() as db:db.execute('UPDATE transactions SET link_group_id=NULL')
    assert expense(store)==before
    member=group['members'][0]['id']
    with store.connect() as db:
        db.execute("INSERT INTO link_groups(id,kind,status,reason,confidence,algorithm_version) VALUES ('other','purchase_duplicate','candidate','test','C','test')")
        db.execute("INSERT INTO link_members VALUES ('other',?,'purchase')",(member,))
    with store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE link_groups SET status='confirmed' WHERE id='other'")
    assert expense(store)==before


def test_dense_candidates_bounded_and_ambiguous_not_A(tmp_path):
    store=Store(tmp_path/'db.sqlite3');wallet,bank,wx,bk=six(store)
    rows=[]
    for i in range(100):
        row=dict(wx.rows[0],id=f'dense-{i}',signature=f'dense-{i}',external_id=f'dense-{i}')
        rows.append(row)
    store.import_rows(rows)
    result=store.generate_candidates()
    assert result['overflow']>0 and result['candidates']<=len(store.transactions())*20
    assert not any(g['confidence']=='A' and g['members'][0]['amount_cents']==2000 for g in store.reconciliation_groups())
    assert expense(store)==212000


def test_insight_includes_historical_denominator_and_goal_inputs():
    frame=pd.DataFrame([dict(id='old',date='2026-08-01',kind='支出',category='餐饮',merchant='店',amount_cents=100),
                        dict(id='new',date='2026-09-01',kind='支出',category='餐饮',merchant='店',amount_cents=300)])
    cards=make_insights(frame,'2026-09',today=date(2026,10,1))
    unusual=next(c for c in cards if c['kind']=='unusual')
    assert set(unusual['ids'])=={'old','new'}
    assert all(c['trace']['formula_id'] and c['trace']['evidence'] and c['trace']['computed_at'] for c in cards)


def legacy(path):
    from finance.migrations import BASE
    with sqlite3.connect(path) as db:
        for statement in BASE:db.execute(statement)
        db.execute("INSERT INTO transactions VALUES ('old-id','original','2026-09-01','原商户','原描述',1234,'支出','教育','用户修正','旧平台','旧文件.csv',9,'old-signature')")
        db.execute("INSERT INTO budgets VALUES ('2026-09','教育',20000)")
        db.execute("INSERT INTO goals VALUES (1,'旧目标',10000,100,'2026-01-01','2027-01-01',30000,20000)")
        db.execute("INSERT INTO goal_contributions(goal_id,date,amount_cents) VALUES (1,'2026-02-01',500)")
        db.execute('PRAGMA user_version=3')


def test_legacy_migration_backup_and_restore(tmp_path):
    path=tmp_path/'legacy.sqlite3';legacy(path)
    store=Store(path);assert store.migration_backup
    assert inspect_database(store.migration_backup)['version']==3
    row=store.transactions().iloc[0]
    assert row.id=='old-id' and row.category=='教育' and row.amount_cents==1234 and row.account_id is None
    assert row.raw_row_id is None and row.source_document_id is None and row.status=='legacy'
    assert store.goals().iloc[0].saved_cents==100 and store.contributions(1).amount_cents.sum()==500
    assert store.budgets('2026-09')=={'教育':20000}
    before=store.backup();store.add_manual_transaction('2026-09-02','店',100,'支出','餐饮')
    with pytest.raises(ValueError,match='确认'):store.restore(before)
    recovery=store.restore(before,confirmed=True)
    assert len(store.transactions())==1 and inspect_database(recovery)['counts']['transactions']==2
    recovery2=store.restore(store.migration_backup,confirmed=True)
    assert row.id==store.transactions().iloc[0].id and recovery2.exists()
    with store.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert db.execute('SELECT checksum FROM schema_migrations').fetchone()[0]


def test_migration_failure_leaves_original_and_valid_backup(tmp_path):
    path=tmp_path/'fail.sqlite3';legacy(path)
    def fail(db):
        db.execute("UPDATE transactions SET amount_cents=999")
        raise RuntimeError('injected migration failure')
    with pytest.raises(RuntimeError):ensure_schema(path,hook=fail)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==3
        assert db.execute('SELECT amount_cents FROM transactions').fetchone()[0]==1234
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='accounts'").fetchone()
    assert inspect_database(next((tmp_path/'backups').glob('*.sqlite3')))['version']==3


def test_future_database_refused_before_any_write(tmp_path):
    path=tmp_path/'future.sqlite3';legacy(path)
    with sqlite3.connect(path) as db:db.execute('PRAGMA user_version=99')
    original=path.read_bytes()
    with pytest.raises(ValueError,match='升级'):Store(path)
    assert path.read_bytes()==original and not (tmp_path/'backups').exists()
    path2=tmp_path/'normal.sqlite3';store=Store(path2)
    with sqlite3.connect(path2) as db:db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError):store.save_budget('2026-09','教育',1)


def test_legacy_reimport_does_not_double_count(tmp_path):
    content=b'date,amount,merchant,id,channel\n2026-09-01,-20,shop,legacy,bank\n'
    store=Store(tmp_path/'db.sqlite3');store.import_rows(parse_statement(content,'old.csv').rows)
    result=parse_statement(content,'old.csv',retain_source=True)
    assert store.import_statement(result)==(0,1,[])
    assert expense(store)==2000
    account=store.add_account('bank','debit','old bank')
    result=parse_statement(content,'old.csv',retain_source=True,account_id=account)
    assert store.import_statement(result)[0]==0 and expense(store)==2000


def test_bank_template_rejects_both_sides_and_unknown_format():
    frame=pd.read_csv(SAMPLES/'m1a_bank_template_v1.csv',dtype=str,keep_default_na=False)
    frame.loc[0,'贷方发生额']='5'
    result=parse_statement(bank_bytes(frame),'bank.xlsx',adapter='bank_template_v1')
    assert len(result.issues)==1 and len(result.rows)==1 and len(result.raw_rows)==2
    with pytest.raises(ValueError):parse_statement((SAMPLES/'m1a_wechat.csv').read_bytes(),'wechat.csv',adapter='bank_template_v1')


@pytest.mark.parametrize('field,value',[('currency','USD'),('kind','转账'),('status','refund_pending')])
def test_incompatible_members_never_excluded(tmp_path,field,value):
    store=Store(tmp_path/'db.sqlite3');six(store);store.generate_candidates()
    group=store.reconciliation_groups()[0];store.decide_group(group['id'],'confirmed')
    member=group['members'][0]['id']
    # Simulate corruption/old external tooling: projection still fails closed.
    with store.connect() as db:db.execute(f'UPDATE transactions SET {field}=? WHERE id=?',(value,member))
    raw=store.source_transactions()
    other=raw[raw.id==group['members'][1]['id']].iloc[0]
    assert other.included_in_stats and '关联失效' in other.counting_note


def test_three_members_require_explicit_group_no_transitive_union(tmp_path):
    store=Store(tmp_path/'db.sqlite3');wallet,bank,wx,bk=six(store)
    other_bank=store.add_account('银行','debit','第二张卡','1234')
    assert other_bank!=bank
    row=dict(bk.rows[0],id='third',signature='third',account_id=other_bank,external_id='third')
    store.import_rows([row]);store.generate_candidates()
    group=next(g for g in store.reconciliation_groups() if {r['id'] for r in g['members']}=={wx.rows[0]['id'],bk.rows[0]['id']})
    store.decide_group(group['id'],'confirmed')
    overlapping=next(g for g in store.reconciliation_groups() if 'third' in {r['id'] for r in g['members']} and wx.rows[0]['id'] in {r['id'] for r in g['members']})
    with pytest.raises(ValueError,match='传递'):store.decide_group(overlapping['id'],'confirmed')
    assert expense(store)==12000  # 140 - one confirmed 20; third source is not silently added


def test_funding_interval_and_conflict_are_conservative(tmp_path):
    store=Store(tmp_path/'db.sqlite3');wallet,bank,wx,bk=six(store)
    with pytest.raises(ValueError,match='已有绑定'):store.add_funding_link(wallet,bank,'2026-08-01')
    link=store.funding_links()[0]
    store.end_funding_link(link['id'],'2026-08-31');store.generate_candidates()
    assert not any(g['confidence']=='A' for g in store.reconciliation_groups())
    assert expense(store)==12000


def test_compare_without_evidence_refuses_amount():
    frame=pd.DataFrame([dict(id='only',date='2026-09-01',kind='支出',category='交通',merchant='站',amount_cents=100)])
    class Model:
        def parse_question(self,question):return dict(metric='compare',category=None,period='all',compare_categories=['餐饮','教育'])
    result=answer_question(frame,'餐饮和教育各花多少？',model=Model())
    assert result['status']=='missing' and not result['evidence'] and '0.00' not in result['answer']
