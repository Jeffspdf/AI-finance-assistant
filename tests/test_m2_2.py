"""M2-2 business acceptance: real write APIs, signed projection and adversarial DB states."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier
import json
import pytest
from finance.storage import Store
from finance.refunds import RefundReviewRequired
from finance.analytics import overview,budget_report,month_records,estimate_from_records,plan_savings
from finance.evidence import calculation,count_records
from finance.qa import answer_question
from test_m2_1 import ledger,add_refund,save_confirmed


def expense(frame,month=None):
    if month:frame=month_records(frame,month)
    return int(frame.loc[frame.kind=='支出','amount_cents'].sum())


def source_rows(store):
    with store.connect(read_only=True) as db:
        return [tuple(r) for r in db.execute('SELECT * FROM transactions ORDER BY id')]


def audit_rows(store):
    with store.connect(read_only=True) as db:
        return [dict(r) for r in db.execute('SELECT * FROM audit_events ORDER BY id')]


def add_purchase(store,identifier='P-2',amount=10000):
    with store.connect() as db:
        row=dict(db.execute("SELECT * FROM transactions WHERE id='P-W'").fetchone())
        row.update(id=identifier,external_id='EXT-'+identifier,signature='SIG-'+identifier,
                   amount_cents=amount,account_id=None,link_group_id=None,source_document_id=None,
                   raw_row_id=None,import_batch_id=None,merchant='另一合成商店',category='购物')
        db.execute('INSERT INTO transactions('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
    return identifier


@pytest.mark.parametrize('amount,net',[(10000,0),(3000,7000)])
def test_full_and_partial_confirmation_preserves_sources_and_gross(tmp_path,amount,net):
    store=ledger(tmp_path)
    with store.connect() as db:db.execute("UPDATE transactions SET amount_cents=? WHERE id='R-W'",(amount,))
    before=source_rows(store);gross=store.transactions().to_json()
    decision=store.confirm_refund_link('R-W','P-B')
    assert decision['status']=='confirmed' and decision['original_txn_id']=='P-W'
    assert source_rows(store)==before and store.transactions().to_json()==gross
    assert expense(store.transactions())==10000 and expense(store.spending_view())==net
    assert store.pending_refunds()==dict(count=0,amount_cents=0)
    assert store.refund_sources()[0]['source_status']=='refund_pending'
    assert store.refund_sources()[0]['association_status']=='linked'
    entry=next(r for r in audit_rows(store) if r['event']=='refund_confirmed')
    detail=json.loads(entry['detail']);assert detail['old'] is None and detail['new']['id']==decision['id']


def test_multiple_partial_refunds_and_exact_limit(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-20',2000)
    store.confirm_refund_link('R-W','P-W');store.confirm_refund_link('R-20','P-B')
    assert expense(store.spending_view())==5000
    add_refund(store,'R-50',5000);store.confirm_refund_link('R-50','P-W')
    assert expense(store.spending_view())==0
    add_refund(store,'R-1',1);before=audit_rows(store)
    with pytest.raises(ValueError,match='累计退款超过'):store.confirm_refund_link('R-1','P-B')
    assert audit_rows(store)==before and len(store.refund_links())==3


def test_confirmation_revalidates_after_a_stale_preview(tmp_path):
    store=ledger(tmp_path);preview=store.validate_refund_link('R-W','P-W')
    assert preview['remaining_after_cents']==7000
    add_refund(store,'R-80',8000);store.confirm_refund_link('R-80','P-B')
    before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='累计退款超过'):store.confirm_refund_link('R-W','P-W')
    assert Path(store.path).read_bytes()==before and expense(store.spending_view())==2000


@pytest.mark.parametrize('identifier,category',[('P-W','工资'),('P-B','工资'),('R-W','餐饮')])
def test_invalid_legacy_category_domain_is_rejected_without_auto_correction(tmp_path,identifier,category):
    store=ledger(tmp_path)
    with store.connect() as db:db.execute('UPDATE transactions SET category=? WHERE id=?',(category,identifier))
    before=Path(store.path).read_bytes()
    for operation in (store.validate_refund_link,store.confirm_refund_link):
        with pytest.raises(ValueError,match='历史分类待修正'):operation('R-W','P-W')
    assert Path(store.path).read_bytes()==before
    assert identifier in set(store.category_conflicts().id)


def test_revoke_and_reconfirm_keeps_one_row_and_complete_audit(tmp_path):
    store=ledger(tmp_path);before=source_rows(store)
    first=store.confirm_refund_link('R-W','P-W');store.revoke_refund_link(first['id'])
    assert expense(store.spending_view())==10000 and store.pending_refunds()==dict(count=1,amount_cents=3000)
    second=store.confirm_refund_link('R-W','P-B')
    assert second['id']==first['id'] and len(store.refund_links())==1
    assert expense(store.spending_view())==7000 and source_rows(store)==before
    events=[r['event'] for r in audit_rows(store)]
    assert events.count('refund_confirmed')==2 and events.count('refund_revoked')==1
    assert json.loads(audit_rows(store)[-1]['detail'])['old']['status']=='rejected'


def test_confirmation_and_audit_fail_atomically(tmp_path,monkeypatch):
    import finance.refunds as refunds
    store=ledger(tmp_path);before=Path(store.path).read_bytes()
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(refunds,'audit',fail)
    with pytest.raises(RuntimeError,match='synthetic'):store.confirm_refund_link('R-W','P-W')
    assert Path(store.path).read_bytes()==before and store.refund_links()==[]


@pytest.mark.parametrize('damage',['early_refund','currency','ordinary_income','refund_transfer','missing_flow'])
def test_confirmation_rejects_unsupported_source_roles_and_dates(tmp_path,damage):
    store=ledger(tmp_path)
    with store.connect() as db:
        if damage=='early_refund':db.execute("UPDATE transactions SET date='2026-08-01' WHERE id='R-W'")
        elif damage=='currency':db.execute("UPDATE transactions SET currency='USD' WHERE id='R-W'")
        elif damage=='ordinary_income':db.execute("UPDATE transactions SET status='normal' WHERE id='R-W'")
        elif damage=='refund_transfer':db.execute("UPDATE transactions SET kind='转账',category='转账' WHERE id='R-W'")
        else:db.execute("UPDATE transactions SET status='deleted' WHERE id='R-W'")
    before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.confirm_refund_link('R-W','P-W')
    assert Path(store.path).read_bytes()==before and store.refund_links()==[]


def test_refund_duplicate_confirmation_split_and_reconfirmation(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    first=store.confirm_refund_link('R-W','P-W');second=store.confirm_refund_link('R-B','P-W')
    assert expense(store.spending_view())==4000
    contexts={r['id']:r['context_json'] for r in store.refund_links()}
    group=store.create_refund_group(['R-W','R-B'],reason='人工核对两个来源为同一退款')
    assert expense(store.spending_view())==4000  # A proposal alone makes no financial decision.
    store.decide_group(group,'confirmed')
    assert {r['status'] for r in store.refund_links()}=={'needs_review'}
    assert {r['id']:r['context_json'] for r in store.refund_links()}==contexts
    assert store.pending_refunds()==dict(count=1,amount_cents=3000) and expense(store.spending_view())==10000
    renewed=store.confirm_refund_link('R-B','P-B')
    assert renewed['id']==first['id'] and renewed['refund_txn_id']=='R-W'
    assert expense(store.spending_view())==7000 and store.pending_refunds()['count']==0
    with pytest.raises(ValueError,match='同一逻辑退款事件'):store.confirm_refund_link('R-B','P-W')
    store.decide_group(group,'split')
    assert expense(store.spending_view())==10000 and store.pending_refunds()==dict(count=2,amount_cents=6000)
    assert {r['id'] for r in store.refund_links()}=={first['id'],second['id']}
    store.reopen_group(group);store.decide_group(group,'confirmed')
    assert expense(store.spending_view())==10000  # Reconfirming a group never reconfirms money.
    store.confirm_refund_link('R-W','P-W');assert expense(store.spending_view())==7000


@pytest.mark.parametrize('damage',['two_wallets','amount','category','same_id','ordinary_income'])
def test_refund_group_rejects_invalid_sources_without_writes(tmp_path,damage):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    with store.connect() as db:
        if damage=='two_wallets':db.execute("UPDATE transactions SET channel='支付宝' WHERE id='R-B'")
        elif damage=='amount':db.execute("UPDATE transactions SET amount_cents=1 WHERE id='R-B'")
        elif damage=='category':db.execute("UPDATE transactions SET category='餐饮' WHERE id='R-B'")
        elif damage=='ordinary_income':db.execute("UPDATE transactions SET status='normal' WHERE id='R-B'")
    before=Path(store.path).read_bytes();ids=['R-W','R-W'] if damage=='same_id' else ['R-W','R-B']
    with pytest.raises(ValueError):store.create_refund_group(ids,reason='合成来源核对')
    assert Path(store.path).read_bytes()==before


def test_refund_group_needs_separate_explicit_non_wallet_acknowledgement(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B1',3000,channel='银行');add_refund(store,'R-B2',3000,channel='银行')
    group=store.create_refund_group(['R-B1','R-B2'],reason='核对两家银行中的同一退款')
    with pytest.raises(ValueError,match='没有钱包'):store.decide_group(group,'confirmed')
    store.decide_group(group,'confirmed',acknowledge_no_wallet=True)
    assert len([r for r in store.refund_sources() if r['event_key'].endswith(group)])==1


@pytest.mark.parametrize('basis,sep,oct',[('cash',10000,-3000),('original_purchase_month',7000,0)])
def test_cross_month_projection_has_one_adjustment_and_both_real_dates(tmp_path,basis,sep,oct):
    store=ledger(tmp_path);decision=store.confirm_refund_link('R-W','P-W');before=Path(store.path).read_bytes()
    frame=store.spending_view(basis);adjustment=frame[frame.projection_kind=='refund_adjustment'].iloc[0]
    assert expense(frame,'2026-09')==sep and expense(frame,'2026-10')==oct
    assert expense(frame)==7000 and adjustment.record_count==0
    assert adjustment.original_date.startswith('2026-09') and adjustment.refund_date.startswith('2026-10')
    assert adjustment.refund_link_id==decision['id'] and adjustment.original_txn_id=='P-W' and adjustment.refund_txn_id=='R-W'
    assert adjustment.category=='餐饮' and adjustment.merchant=='合成餐厅'
    assert frame.attrs['spending_basis']==basis and Path(store.path).read_bytes()==before
    assert expense(store.transactions())==10000


def test_monthly_negative_net_budget_remaining_and_nonpositive_share(tmp_path):
    store=ledger(tmp_path);store.confirm_refund_link('R-W','P-W');frame=store.spending_view()
    october=month_records(frame,'2026-10');trend,cats=overview(october)
    assert trend.iloc[0]['支出']==-30 and cats['占比'].isna().all()
    budget=budget_report(frame,'2026-10',{'餐饮':10000}).iloc[0]
    assert budget['已支出']==-30 and budget['剩余']==130 and budget['超支']==0
    answer=answer_question(frame,'2026-10 餐饮占比是多少？')
    assert answer['status']=='ok' and '不适用' in answer['answer']
    assert '现金口径' in answer['answer'] and len([e for e in answer['trace']['evidence'] if e['type']=='RefundDecision'])==1
    assert count_records(october[october.kind=='支出'])==0
    estimate=estimate_from_records(october,date(2026,11,1))
    assert estimate['average_expense_cents']==-3000 and estimate['requires_explicit_budget'] is True
    with pytest.raises(ValueError,match='不能为负数'):
        plan_savings(10000,0,'2026-11-01','2027-01-01',10000,estimate['average_expense_cents'])


def test_full_refund_zero_denominator_and_adjustments_not_counted_as_purchases(tmp_path):
    store=ledger(tmp_path)
    with store.connect() as db:db.execute("UPDATE transactions SET amount_cents=10000 WHERE id='R-W'")
    store.confirm_refund_link('R-W','P-W');frame=store.spending_view('original_purchase_month')
    _,cats=overview(frame);assert cats['占比'].isna().all()
    assert '不适用' in answer_question(frame,'2026-09 餐饮占比是多少？')['answer']
    assert '共 1 笔' in answer_question(frame,'2026-09 支出多少？')['answer']
    assert '共 1 笔' in answer_question(frame,'2026-09 记录几笔？')['answer']
    assert count_records(frame)==len(store.transactions())


def test_projection_evidence_is_typed_and_preserves_both_event_groups(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    group=store.create_refund_group(['R-W','R-B'],reason='人工核对退款编号');store.decide_group(group,'confirmed')
    link=store.confirm_refund_link('R-B','P-B');frame=store.spending_view()
    trace=calculation('test','spending.net',frame)
    item=next(e for e in trace['evidence'] if e['type']=='RefundDecision')
    assert item['id']==link['id'] and item['snapshot']['purchase']['member_ids']==['P-B','P-W']
    assert item['snapshot']['refund']['member_ids']==['R-B','R-W']
    assert item['snapshot']['purchase']['group']['kind']=='purchase_duplicate'
    assert item['snapshot']['refund']['group']['kind']=='refund_duplicate'
    assert trace['inputs']['spending_basis']=='cash' and trace['algorithm_version']=='m2-refund-1'
    assert sum(e['type']=='RefundAdjustment' for e in trace['evidence'])==1
    october=month_records(frame,'2026-10');assert len(calculation('test','net',october)['evidence'])==3  # transfer + adjustment + decision


@pytest.mark.parametrize('damage',['amount','refund_date','refund_category','split','delete_purchase','delete_refund'])
def test_external_mutation_is_ignored_by_readonly_projection_and_global_confirm_barrier(tmp_path,damage):
    store=ledger(tmp_path);store.confirm_refund_link('R-W','P-W')
    with store.connect() as db:
        if damage=='amount':db.execute("UPDATE transactions SET amount_cents=2000 WHERE id IN ('P-W','P-B')")
        elif damage=='refund_date':db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
        elif damage=='refund_category':db.execute("UPDATE transactions SET category='餐饮' WHERE id='R-W'")
        elif damage=='split':db.execute("UPDATE link_groups SET status='split' WHERE id='GP'")
        elif damage=='delete_purchase':db.execute("UPDATE transactions SET status='deleted' WHERE id IN ('P-W','P-B')")
        else:db.execute("UPDATE transactions SET status='deleted' WHERE id='R-W'")
    add_purchase(store);add_refund(store,'R-10',1000)
    before=Path(store.path).read_bytes();readonly=Store(store.path,read_only=True)
    frame=readonly.spending_view();assert not (frame.projection_kind=='refund_adjustment').any()
    assert frame.attrs['repair_pending'] and all(r['repair_pending'] for r in readonly.refund_links())
    with pytest.raises(RefundReviewRequired) as error:store.confirm_refund_link('R-10','P-2')
    assert error.value.status=='needs_review' and error.value.link_ids
    assert Path(store.path).read_bytes()==before


def test_global_duplicate_event_to_two_purchases_is_not_rescued_by_local_scope(tmp_path):
    store=ledger(tmp_path);add_purchase(store);add_purchase(store,'P-3');add_refund(store,'R-B',3000,channel='银行')
    group=store.create_refund_group(['R-W','R-B'],reason='同退款的跨源证据');store.decide_group(group,'confirmed')
    a=store.validate_refund_link('R-W','P-W');b=store.validate_refund_link('R-B','P-2')
    save_confirmed(store,a,'BAD-A');save_confirmed(store,b,'BAD-B',refund_id='R-B')
    add_refund(store,'R-10',1000);before=Path(store.path).read_bytes()
    assert len(store.spending_view().attrs['repair_pending'])==2
    with pytest.raises(RefundReviewRequired,match='同一逻辑退款事件'):store.confirm_refund_link('R-10','P-3')
    assert Path(store.path).read_bytes()==before


def test_global_preexisting_overrefund_marks_every_related_decision_for_review(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-80',8000);add_purchase(store);add_refund(store,'R-10',1000)
    a=store.validate_refund_link('R-W','P-W');b=store.validate_refund_link('R-80','P-B')
    save_confirmed(store,a,'BAD-30');save_confirmed(store,b,'BAD-80')
    before=Path(store.path).read_bytes();frame=store.spending_view()
    assert expense(frame)==20000 and len(frame.attrs['repair_pending'])==2
    with pytest.raises(RefundReviewRequired,match='累计退款超过'):store.confirm_refund_link('R-10','P-2')
    assert Path(store.path).read_bytes()==before
    store.revoke_refund_link('BAD-80')
    assert next(r for r in store.refund_links() if r['id']=='BAD-30')['stored_status']=='needs_review'
    assert expense(store.spending_view())==20000  # Never silently keep the other decision.
    store.confirm_refund_link('R-W','P-W');assert expense(store.spending_view())==17000


def test_invalid_component_does_not_hide_an_unrelated_valid_refund(tmp_path):
    store=ledger(tmp_path);add_purchase(store);add_refund(store,'R-80',8000);add_refund(store,'R-10',1000)
    proposals=[store.validate_refund_link(r,p) for r,p in [('R-W','P-W'),('R-80','P-W'),('R-10','P-2')]]
    for result,identifier in zip(proposals,['BAD-30','BAD-80','GOOD-10']):save_confirmed(store,result,identifier)
    frame=store.spending_view()
    assert expense(frame)==19000 and {r['id'] for r in frame.attrs['repair_pending']}=={'BAD-30','BAD-80'}
    assert [r['id'] for r in frame.attrs['refund_decisions']]==['GOOD-10']


def test_full_refund_then_original_shrinks_requires_reconfirmation_not_negative_expense(tmp_path):
    store=ledger(tmp_path);add_purchase(store)
    with store.connect() as db:db.execute("UPDATE transactions SET amount_cents=10000 WHERE id='R-W'")
    store.confirm_refund_link('R-W','P-2');assert expense(store.spending_view())==10000
    with store.connect(read_only=True) as db:values=dict(db.execute("SELECT * FROM transactions WHERE id='P-2'").fetchone())
    values['amount_cents']=8000;store.edit_transaction('P-2',values)
    assert expense(store.spending_view())==18000 and store.refund_links()[0]['stored_status']=='needs_review'
    with pytest.raises(ValueError,match='累计退款超过'):store.confirm_refund_link('R-W','P-2')


@pytest.mark.parametrize('action',['revoke','group_confirm','group_split'])
def test_decision_and_invalidation_roll_back_when_audit_write_fails(tmp_path,monkeypatch,action):
    import finance.refunds as refunds
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    link=store.confirm_refund_link('R-W','P-W')
    group=store.create_refund_group(['R-W','R-B'],reason='跨源退款核对')
    if action=='group_split':store.decide_group(group,'confirmed');store.confirm_refund_link('R-W','P-W')
    before=Path(store.path).read_bytes()
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(refunds,'audit',fail)
    with pytest.raises(RuntimeError,match='synthetic'):
        if action=='revoke':store.revoke_refund_link(link['id'])
        else:store.decide_group(group,'split' if action=='group_split' else 'confirmed')
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('action',['shrink_purchase','edit_refund','delete_wallet','delete_bank','split_purchase'])
def test_normal_write_paths_conservatively_invalidate_all_related_decisions(tmp_path,action):
    store=ledger(tmp_path);add_refund(store,'R-20',2000)
    store.confirm_refund_link('R-W','P-W');store.confirm_refund_link('R-20','P-B')
    if action in ('shrink_purchase','edit_refund'):
        identifier='P-W' if action=='shrink_purchase' else 'R-W'
        with store.connect(read_only=True) as db:values=dict(db.execute('SELECT * FROM transactions WHERE id=?',(identifier,)).fetchone())
        values['amount_cents']=4000 if action=='shrink_purchase' else 2500
        store.edit_transaction(identifier,values)
    elif action.startswith('delete_'):store.delete_import_batch('B-W' if action=='delete_wallet' else 'B-B')
    else:store.decide_group('GP','split')
    links=store.refund_links();assert all(r['stored_status']=='needs_review' and not r['repair_pending'] for r in links)
    frame=store.spending_view();assert not (frame.projection_kind=='refund_adjustment').any()
    assert expense(frame)==expense(store.transactions())
    assert sum(r['event']=='refund_invalidated' for r in audit_rows(store))==2


@pytest.mark.parametrize('race',['same_refund','same_event_two_purchases','different_refunds_over_limit','different_refunds_within_limit'])
def test_two_independent_connections_confirm_atomically(tmp_path,race):
    store=ledger(tmp_path);add_refund(store,'R-2',8000 if race=='different_refunds_over_limit' else 3000)
    if race=='different_refunds_over_limit':
        with store.connect() as db:db.execute("UPDATE transactions SET amount_cents=8000 WHERE id='R-W'")
    if race=='same_event_two_purchases':
        add_purchase(store)
        with store.connect() as db:db.execute("UPDATE transactions SET channel='银行' WHERE id='R-2'")
        group=store.create_refund_group(['R-W','R-2'],reason='跨源同退款核对');store.decide_group(group,'confirmed')
    stores=[Store(store.path),Store(store.path)];barrier=Barrier(2)
    requests=[('R-W','P-W'),('R-W' if race=='same_refund' else 'R-2','P-2' if race=='same_event_two_purchases' else 'P-B')]
    def confirm(i):
        barrier.wait(timeout=5)
        try:return stores[i].confirm_refund_link(*requests[i])
        except ValueError as exc:return exc
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(confirm,range(2)))
    expected=2 if race=='different_refunds_within_limit' else 1
    assert sum(isinstance(r,dict) for r in results)==expected
    assert sum(r['stored_status']=='confirmed' for r in store.refund_links())==expected
    assert store.spending_view().attrs['repair_pending']==[]
    assert expense(store.spending_view())>=0


def test_empty_default_basis_invalid_basis_and_readonly_service(tmp_path):
    store=Store(tmp_path/'empty.sqlite3');frame=store.spending_view()
    assert frame.empty and frame.attrs['spending_basis']=='cash'
    assert overview(frame)[0].empty and budget_report(frame,'2026-10',{'餐饮':1000}).iloc[0]['已支出']==0
    with pytest.raises(ValueError,match='口径'):store.spending_view('gross')
    (tmp_path/'populated').mkdir()
    store=ledger(tmp_path/'populated');readonly=Store(store.path,read_only=True);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='只读'):readonly.confirm_refund_link('R-W','P-W')
    assert Path(store.path).read_bytes()==before


def test_original_refunded_status_without_independent_flow_is_not_an_adjustment(tmp_path):
    store=ledger(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE transactions SET status='deleted' WHERE id='R-W'")
        db.execute("UPDATE transactions SET description='已退款' WHERE id='P-W'")
    assert expense(store.spending_view())==10000 and store.spending_view().attrs['refund_decisions']==[]
    with pytest.raises(ValueError):store.confirm_refund_link('P-W','P-W')


def test_both_supported_import_adapters_run_the_full_refund_business_path(tmp_path):
    from scripts.reproduce_m2_refunds import verify_refunds
    report=verify_refunds(tmp_path)
    assert report['data']=='synthetic only'
    assert [r['net_cents'] for r in report['scenarios']]==[0,7000,5000]
    assert [r['source_rows'] for r in report['scenarios']]==[4,4,6]
