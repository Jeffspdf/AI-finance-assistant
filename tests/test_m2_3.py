"""M2-3 maintenance contracts across write services, persistence and pure readers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import json
import pytest
from finance.storage import Store
from finance.refunds import RefundReviewRequired
from finance.importer import parse_statement
from finance.demo_generator import DemoOptions,generate_demo
from finance.demo import initialize_demo
from test_m2_1 import ledger,add_refund,snapshot
from test_m2_2 import expense,audit_rows,add_purchase


def observe(store):
    """Cross-interface invariants; reading every surface must preserve bytes AND audit."""
    before=Path(store.path).read_bytes();audit=audit_rows(store)
    gross=store.transactions();net=store.spending_view();sources=store.refund_sources();links=store.refund_links()
    active=[r for r in links if r['status']=='confirmed' and not r['repair_pending']]
    assert expense(net)==expense(gross)-sum(r['amount_cents'] for r in active)
    assert set(net.loc[net.projection_kind=='refund_adjustment','refund_link_id'])=={r['id'] for r in active}
    assert {r['id'] for r in net.attrs['repair_pending']}=={r['id'] for r in links if r['repair_pending']}
    assert {r['id'] for r in store.refund_confirmation_blockers()}=={r['id'] for r in links if r['repair_pending']}
    pending=[r for r in sources if r['association_status']!='linked']
    assert store.pending_refunds()==dict(count=len(pending),amount_cents=sum(r['amount_cents'] for r in pending))
    assert net.attrs['refund_pending']==store.pending_refunds()==gross.attrs['refund_pending']
    for event in sources:
        associated=any(r['refund_txn_id'] in event['member_ids'] for r in active)
        assert (event['association_status']=='linked')==associated
        if event['association_status']=='needs_review':assert event['reasons']
    for link in links:
        evidence=store.refund_link_evidence(link['id'])
        assert evidence['valid']==(link in active) and evidence['repair_pending']==link['repair_pending']
        if link['status']=='needs_review':assert evidence['reasons'] and link['review_reasons']
    store.source_transactions();store.reconciliation_groups()
    assert audit_rows(store)==audit and Path(store.path).read_bytes()==before
    return dict(gross=expense(gross),net=expense(net),links=links,sources=sources)


def confirmed(tmp_path):
    store=ledger(tmp_path);decision=store.confirm_refund_link('R-W','P-W')
    return store,decision


def values(store,identifier):
    with store.connect(read_only=True) as db:return dict(db.execute('SELECT * FROM transactions WHERE id=?',(identifier,)).fetchone())


def imported(tmp_path,name='ledger.sqlite3'):
    """Separate purchase/refund batches let either endpoint be deleted independently."""
    store=Store(tmp_path/name)
    header='交易ID,日期,商户,描述,金额,类型,平台,币种,状态\n'
    for identifier,when,amount,kind,status in [('P','2026-09-01',100,'支出','支付成功'),('R','2026-10-01',30,'收入','退款成功')]:
        parsed=parse_statement((header+f'{identifier},{when},合成餐厅,合成来源,{amount},{kind},微信,CNY,{status}\n').encode(),identifier+'.csv',retain_source=True)
        store.import_statement(parsed)
    source=store.source_transactions();byext={r['external_id']:r for r in source.to_dict('records')}
    decision=store.confirm_refund_link(byext['R']['id'],byext['P']['id'])
    return store,byext,decision


@pytest.mark.parametrize('original,reason',[('P-W','重新确认同一关联'),('P-W','修改核对说明'),('P-2','换一个原消费')])
def test_contract_b_valid_confirmation_must_be_revoked_first(tmp_path,original,reason):
    store,link=confirmed(tmp_path);add_purchase(store);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='必须先撤销'):store.confirm_refund_link('R-W',original,reason=reason)
    assert Path(store.path).read_bytes()==before and store.refund_links()[0]['reason']==link['reason']
    store.revoke_refund_link(link['id']);new=store.confirm_refund_link('R-W',original,reason=reason)
    assert new['id']==link['id'] and new['reason']==reason
    proof=store.refund_link_evidence(new['id'])
    assert [r['event'] for r in proof['audit'] if r['entity_id']==new['id']]==['refund_confirmed','refund_revoked','refund_confirmed']
    assert json.loads(proof['audit'][-1]['detail'])['old']['status']=='rejected'
    observe(store)


@pytest.mark.parametrize('source_id',['P-W','P-B','R-W'])
@pytest.mark.parametrize('route',['edit','single_category','batch_category','model_category','undo_batch'])
def test_every_classification_and_edit_route_invalidates_and_persists(tmp_path,source_id,route):
    store=ledger(tmp_path);category='工资' if source_id=='R-W' else '购物'
    if route=='model_category':
        with store.connect() as db:db.execute("UPDATE transactions SET category_source='待确认' WHERE id=?",(source_id,))
    link=store.confirm_refund_link('R-W','P-W');expected_context=link['context_json']
    if route=='edit':store.edit_transaction(source_id,dict(values(store,source_id),description='人工修改合成描述'))
    elif route=='single_category':store.correct_categories([(source_id,category)],learn=True)
    elif route=='batch_category':store.batch_category([source_id],category)
    elif route=='model_category':assert store.apply_model_category(source_id,category) is True
    else:
        undo=store.batch_category([source_id],category)
        observe(store)
        # Confirm after the first explicit edit; undo must invalidate that new decision too.
        renewed=store.confirm_refund_link('R-W','P-B' if source_id=='P-B' else 'P-W')
        expected_context=renewed['context_json'];store.undo_category_batch(undo)
    result=observe(store)
    assert result['net']==result['gross'] and all(r['stored_status']=='needs_review' for r in result['links'])
    assert result['links'][0]['context_json']==expected_context
    assert store.refund_sources()[0]['association_status']=='needs_review'
    with store.connect(read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='refund_invalidated' AND entity_id=?",(link['id'],)).fetchone()[0]>=1
    reopened=Store(store.path);assert reopened.refund_links()[0]['stored_status']=='needs_review';observe(reopened)


def test_protected_model_label_and_noop_edits_do_not_invalidate_valid_money(tmp_path):
    store,link=confirmed(tmp_path);before=Path(store.path).read_bytes()
    assert store.apply_model_category('P-W','购物') is False
    assert Path(store.path).read_bytes()==before
    store.correct_categories([('P-W','餐饮')]);store.batch_category(['R-W'],'其他收入')
    store.edit_transaction('P-W',values(store,'P-W'))
    assert store.refund_links()[0]['stored_status']=='confirmed' and expense(store.spending_view())==7000
    observe(store)


@pytest.mark.parametrize('source_id',['P-W','R-W'])
@pytest.mark.parametrize('field',['amount_cents','date','merchant','description','category','kind'])
def test_each_edit_field_invalidates_with_before_and_after_evidence(tmp_path,source_id,field):
    store,link=confirmed(tmp_path);old=values(store,source_id);new=dict(old)
    new[field]={'amount_cents':2000,'date':'2026-11-01','merchant':'新合成商户','description':'新合成描述',
                'category':'工资' if source_id=='R-W' else '购物','kind':'支出' if source_id=='R-W' else '收入'}[field]
    if field=='kind':new['category']='购物' if source_id=='R-W' else '其他收入'
    store.edit_transaction(source_id,new);state=observe(store);proof=store.refund_link_evidence(link['id'])
    assert state['links'][0]['stored_status']=='needs_review' and state['net']==state['gross']
    assert proof['link']['context_json']==link['context_json'] and proof['effective_status']=='needs_review'
    assert json.loads(proof['edits'][-1]['old_values'])[field]==old[field]
    assert next(r for r in proof['sources'] if r['id']==source_id)[field]==new[field]


@pytest.mark.parametrize('route',['single','batch','undo'])
def test_later_invalid_classification_rolls_back_earlier_source_and_refund_updates(tmp_path,route):
    store,_=confirmed(tmp_path);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):
        if route=='single':store.correct_categories([('P-W','购物'),('R-W','购物')])
        elif route=='batch':store.batch_category(['P-W','R-W'],'购物')
        else:
            store.undo_category_batch([dict(id='P-W',category='购物',source='规则',new='餐饮'),
                                       dict(id='R-W',category='餐饮',source='规则',new='其他收入')])
    assert Path(store.path).read_bytes()==before;observe(store)


@pytest.mark.parametrize('endpoint',['P','R'])
def test_delete_either_independent_import_batch_preserves_evidence_and_disables_refund(tmp_path,endpoint):
    store,rows,link=imported(tmp_path);batch=rows[endpoint]['import_batch_id']
    store.delete_import_batch(batch);result=observe(store)
    assert result['links'][0]['stored_status']=='needs_review' and result['net']==(0 if endpoint=='P' else 10000)
    proof=store.refund_link_evidence(link['id'])
    assert len(proof['sources'])==2 and any(r['status']=='deleted' for r in proof['sources'])
    assert all(r['raw_json'] and r['sha256'] for r in proof['sources'])
    assert any(r['event']=='batch_deleted' for r in proof['audit'])
    assert any(r['event']=='refund_invalidated' for r in proof['audit'])


@pytest.mark.parametrize('kind',['purchase','refund'])
@pytest.mark.parametrize('action',['confirm','split','reopen'])
def test_all_group_decisions_require_explicit_money_reconfirmation(tmp_path,kind,action):
    store=ledger(tmp_path)
    if kind=='purchase':
        group='GP'
        if action in ('confirm','reopen'):store.decide_group(group,'split')
    else:
        add_refund(store,'R-B',3000,channel='银行')
        group=store.create_refund_group(['R-W','R-B'],reason='同退款合成证据')
        if action=='split':store.decide_group(group,'confirmed')
    link=store.confirm_refund_link('R-W','P-W')
    if action=='confirm':
        if kind=='purchase':store.reopen_group(group)
        store.decide_group(group,'confirmed')
    elif action=='split':store.decide_group(group,'split')
    else:store.reopen_group(group)
    result=observe(store)
    assert result['links'][0]['stored_status']=='needs_review' and result['net']==result['gross']
    assert result['links'][0]['context_json']==link['context_json']


def test_demo_month_replacement_invalidates_refund_and_preserves_raw_proof(tmp_path):
    store,rows,link=imported(tmp_path,name='demo.sqlite3')
    generate_demo(store,DemoOptions('2026-09',months=1,expenses_per_month=2,refunds=False,replace_months=True),space='demo',seed=7)
    result=observe(store)
    assert result['links'][0]['stored_status']=='needs_review' and result['net']==result['gross']
    proof=store.refund_link_evidence(link['id'])
    assert any(r['id']==rows['P']['id'] and r['status']=='deleted' for r in proof['sources'])
    assert all(r['raw_json'] for r in proof['sources'])


def test_demo_cleanup_uses_batch_deletion_and_does_not_reseed(tmp_path):
    store,rows,link=imported(tmp_path,name='demo.sqlite3')
    initialize_demo(store)
    for batch in store.import_batches():store.delete_import_batch(batch['id']);observe(store)
    before=Path(store.path).read_bytes();assert initialize_demo(store) is False
    assert store.source_transactions().empty and expense(store.spending_view())==0
    assert Path(store.path).read_bytes()==before and store.refund_links()[0]['stored_status']=='needs_review'


def test_reimporting_deleted_source_cannot_reactivate_a_stale_confirmation(tmp_path):
    store,link=confirmed(tmp_path);old=values(store,'R-W')
    with store.connect() as db:db.execute("UPDATE transactions SET status='deleted' WHERE id='R-W'")
    assert store.refund_links()[0]['repair_pending']
    assert store.import_rows([old],restore_deleted=True)[0]==1
    assert store.refund_links()[0]['stored_status']=='needs_review' and expense(store.spending_view())==10000
    assert store.refund_links()[0]['context_json']==link['context_json'];observe(store)


@pytest.mark.parametrize('endpoint',['P','R'])
def test_real_import_after_batch_deletion_preserves_identity_without_reconfirming(tmp_path,endpoint):
    store,rows,link=imported(tmp_path);row=rows[endpoint];source=store.transaction_source(row['id'])
    header='交易ID,日期,商户,描述,金额,类型,平台,币种,状态\n'
    amount=100 if endpoint=='P' else 30;kind='支出' if endpoint=='P' else '收入';status='支付成功' if endpoint=='P' else '退款成功'
    when='2026-09-01' if endpoint=='P' else '2026-10-01'
    data=(header+f'{endpoint},{when},合成餐厅,合成来源,{amount},{kind},微信,CNY,{status}\n').encode()
    store.delete_import_batch(row['import_batch_id'])
    result=store.import_statement(parse_statement(data,endpoint+'.csv',retain_source=True),restore_deleted=True)
    assert result[0]==1 and store.transaction_source(row['id'])['sha256']==source['sha256']
    assert store.refund_links()[0]['stored_status']=='needs_review' and expense(store.spending_view())==10000
    assert store.refund_links()[0]['context_json']==link['context_json'];observe(store)


def two_invalid_components(store):
    first=store.confirm_refund_link('R-W','P-W');add_refund(store,'R-20',2000)
    related=store.confirm_refund_link('R-20','P-W');add_purchase(store);add_refund(store,'R-25',2500)
    second=store.confirm_refund_link('R-25','P-2')
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-11-01' WHERE id IN ('R-W','R-25')")
    return first,related,second


def test_global_blockers_have_actionable_details_and_targeted_repair_is_component_scoped(tmp_path):
    store=ledger(tmp_path);first,related,other=two_invalid_components(store)
    observe(store);before=Path(store.path).read_bytes()
    blockers=store.refund_confirmation_blockers();assert {r['id'] for r in blockers}=={first['id'],related['id'],other['id']}
    assert all(r['reasons'] and 'repair_refund_links' in r['actions'] and 'revoke_refund_link' in r['actions'] for r in blockers)
    with pytest.raises(RefundReviewRequired) as error:store.confirm_refund_link('R-20','P-2')
    assert error.value.blockers==blockers and Path(store.path).read_bytes()==before
    assert store.repair_refund_links(identifier=first['id'],source='user_selected')==2
    byid={r['id']:r for r in store.refund_links()}
    assert byid[first['id']]['stored_status']==byid[related['id']]['stored_status']=='needs_review'
    assert byid[other['id']]['stored_status']=='confirmed' and byid[other['id']]['repair_pending']
    assert {r['id'] for r in store.refund_confirmation_blockers()}=={other['id']}
    after=Path(store.path).read_bytes();assert store.repair_refund_links(identifier=first['id'])==0
    assert Path(store.path).read_bytes()==after
    assert store.repair_refund_links(identifier=other['id'])==1
    assert store.refund_confirmation_blockers()==[];observe(store)
    after=Path(store.path).read_bytes();assert store.repair_refund_links()==0 and Path(store.path).read_bytes()==after


def test_persisted_review_has_reasons_after_restart_and_no_automatic_reconfirmation(tmp_path):
    store,link=confirmed(tmp_path)
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
    assert store.repair_refund_links(identifier=link['id'])==1
    context=store.refund_links()[0]['context_json'];before=Path(store.path).read_bytes()
    reopened=Store(store.path);assert Path(store.path).read_bytes()==before
    assert reopened.refund_links()[0]['review_reasons'] and reopened.refund_sources()[0]['reasons']
    assert reopened.refund_sources()[0]['association_status']=='needs_review'
    assert reopened.refund_links()[0]['context_json']==context and expense(reopened.spending_view())==10000
    observe(reopened)


@pytest.mark.parametrize('bad_context',['not-json','{"purchase":null}','{"refund":[]}'])
def test_evidence_and_repair_tolerate_malformed_confirmation_context(tmp_path,bad_context):
    store,link=confirmed(tmp_path)
    with store.connect() as db:db.execute('UPDATE refund_links SET context_json=? WHERE id=?',(bad_context,link['id']))
    assert store.refund_link_evidence(link['id'])['repair_pending'];observe(store)
    assert store.repair_refund_links(identifier=link['id'])==1;observe(store)


def test_review_without_audit_still_has_a_generic_reason(tmp_path):
    store,link=confirmed(tmp_path)
    with store.connect() as db:db.execute("UPDATE refund_links SET status='needs_review' WHERE id=?",(link['id'],))
    assert store.refund_sources()[0]['association_status']=='needs_review' and store.refund_sources()[0]['reasons']
    observe(store)


@pytest.mark.parametrize('damage',['refund_date','purchase_group','refund_group','category_domain'])
def test_writable_startup_maintains_groups_and_refunds_then_is_byte_idempotent(tmp_path,damage):
    store=ledger(tmp_path)
    if damage=='refund_group':
        add_refund(store,'R-B',3000,channel='银行')
        group=store.create_refund_group(['R-W','R-B'],reason='合成跨源退款');store.decide_group(group,'confirmed')
    link=store.confirm_refund_link('R-W','P-W')
    with store.connect() as db:
        if damage=='refund_date':db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
        elif damage=='purchase_group':db.execute("UPDATE transactions SET amount_cents=8000 WHERE id='P-B'")
        elif damage=='refund_group':db.execute("UPDATE transactions SET amount_cents=1000 WHERE id='R-B'")
        else:db.execute("UPDATE transactions SET category='工资' WHERE id='P-W'")
    before=Path(store.path).read_bytes();readonly=Store(store.path,read_only=True);observe(readonly)
    assert Path(store.path).read_bytes()==before and readonly.refund_links()[0]['repair_pending']
    reopened=Store(store.path);assert reopened.refund_links()[0]['stored_status']=='needs_review';observe(reopened)
    if damage=='category_domain':assert values(reopened,'P-W')['category']=='工资'  # Discovery, not guessed correction.
    after=Path(store.path).read_bytes();Store(store.path);assert Path(store.path).read_bytes()==after


def test_explicit_refund_group_repair_does_not_touch_an_unrelated_component(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    group=store.create_refund_group(['R-W','R-B'],reason='同退款');store.decide_group(group,'confirmed')
    first=store.confirm_refund_link('R-W','P-W');add_purchase(store);add_refund(store,'R-25',2500)
    other=store.confirm_refund_link('R-25','P-2')
    with store.connect() as db:
        db.execute("UPDATE transactions SET amount_cents=1000 WHERE id='R-B'")
        db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-25'")
    assert next(g for g in store.reconciliation_groups() if g['id']==group)['repair_pending']
    assert store.repair_invalid_groups(identifier=group)==1
    byid={r['id']:r for r in store.refund_links()}
    assert byid[first['id']]['stored_status']=='needs_review' and byid[other['id']]['repair_pending']
    after=Path(store.path).read_bytes();assert store.repair_invalid_groups(identifier=group)==0
    assert Path(store.path).read_bytes()==after;observe(store)


def test_invalid_purchase_role_is_visible_before_explicit_group_repair(tmp_path):
    store,link=confirmed(tmp_path)
    with store.connect() as db:db.execute("UPDATE link_members SET role='refund' WHERE group_id='GP' AND transaction_id='P-B'")
    before=Path(store.path).read_bytes();state=observe(store)
    assert state['gross']==state['net']==20000 and Path(store.path).read_bytes()==before
    assert next(g for g in store.reconciliation_groups() if g['id']=='GP')['repair_pending']
    assert store.repair_invalid_groups(identifier='GP')==1
    assert store.refund_links()[0]['stored_status']=='needs_review';observe(store)


@pytest.mark.parametrize('kind',['purchase','refund'])
def test_targeted_group_repair_finds_saved_decisions_even_after_all_members_disappear(tmp_path,kind):
    store=ledger(tmp_path);group='GP'
    if kind=='refund':
        add_refund(store,'R-B',3000,channel='银行')
        group=store.create_refund_group(['R-W','R-B'],reason='同一退款');store.decide_group(group,'confirmed')
    link=store.confirm_refund_link('R-W','P-W')
    with store.connect() as db:db.execute('DELETE FROM link_members WHERE group_id=?',(group,))
    observe(store);assert store.refund_links()[0]['repair_pending']
    assert store.repair_invalid_groups(identifier=group)==1
    assert store.refund_links()[0]['stored_status']=='needs_review' and store.refund_links()[0]['context_json']==link['context_json']
    observe(store)


def test_explicit_candidate_scan_persists_refund_review_without_confirming_money(tmp_path):
    store,link=confirmed(tmp_path)
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
    assert store.refund_links()[0]['repair_pending'];store.generate_candidates()
    assert store.refund_links()[0]['stored_status']=='needs_review' and store.refund_confirmation_blockers()==[]
    assert expense(store.spending_view())==10000;observe(store)


def test_restore_valid_snapshot_and_invalid_snapshot_maintain_consistent_decisions(tmp_path):
    store,link=confirmed(tmp_path);valid=store.backup();valid_bytes=valid.read_bytes()
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
    invalid=store.backup();invalid_bytes=invalid.read_bytes()
    store.restore(valid,confirmed=True);assert expense(store.spending_view())==7000;observe(store)
    before=snapshot(store.path);recovery=store.restore(invalid,confirmed=True)
    assert snapshot(recovery)==before and store.refund_links()[0]['stored_status']=='needs_review'
    assert expense(store.spending_view())==10000;observe(store)
    assert valid.read_bytes()==valid_bytes and invalid.read_bytes()==invalid_bytes


def test_restore_maintenance_failure_restores_pre_restore_data_and_audit(tmp_path,monkeypatch):
    import finance.refunds as refunds
    store,link=confirmed(tmp_path)
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
    invalid=store.backup();store.repair_refund_links();store.confirm_refund_link('R-W','P-W')
    before=snapshot(store.path)
    def fail(*args,**kwargs):raise RuntimeError('synthetic maintenance audit failure')
    monkeypatch.setattr(refunds,'audit',fail)
    with pytest.raises(RuntimeError,match='synthetic'):store.restore(invalid,confirmed=True)
    assert snapshot(store.path)==before;observe(store)


@pytest.mark.parametrize('operation',['explicit_repair','startup'])
def test_multiple_repairs_roll_back_together_if_later_audit_fails(tmp_path,monkeypatch,operation):
    import finance.refunds as refunds
    store=ledger(tmp_path);two_invalid_components(store);before=Path(store.path).read_bytes()
    original=refunds.audit;calls=[]
    def fail_later(*args,**kwargs):
        calls.append(args[1])
        if len(calls)==2:raise RuntimeError('synthetic second audit failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(refunds,'audit',fail_later)
    with pytest.raises(RuntimeError,match='synthetic'):
        if operation=='explicit_repair':store.repair_refund_links()
        else:Store(store.path)
    assert len(calls)==2 and Path(store.path).read_bytes()==before;observe(store)


def test_group_and_refund_maintenance_are_one_transaction_on_startup_failure(tmp_path,monkeypatch):
    import finance.refunds as refunds
    store,link=confirmed(tmp_path);add_purchase(store);add_refund(store,'R-25',2500)
    store.confirm_refund_link('R-25','P-2')
    with store.connect() as db:
        db.execute("UPDATE transactions SET amount_cents=8000 WHERE id='P-B'")
        db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-25'")
    before=Path(store.path).read_bytes();original=refunds.audit
    def fail_after_group(db,event,*args,**kwargs):
        if event=='refund_repaired':raise RuntimeError('synthetic failure after group maintenance')
        return original(db,event,*args,**kwargs)
    monkeypatch.setattr(refunds,'audit',fail_after_group)
    with pytest.raises(RuntimeError,match='after group'):Store(store.path)
    assert Path(store.path).read_bytes()==before
    assert next(g for g in store.reconciliation_groups() if g['id']=='GP')['stored_status']=='confirmed';observe(store)


@pytest.mark.parametrize('route',['edit','single','batch','model','undo','delete','demo_replace'])
def test_source_changes_and_invalidation_roll_back_on_audit_failure(tmp_path,monkeypatch,route):
    import finance.refunds as refunds
    import finance.demo_generator as generator
    store,rows,link=imported(tmp_path,name='demo.sqlite3' if route=='demo_replace' else 'ledger.sqlite3')
    source_id=rows['P']['id']
    if route=='model':
        with store.connect() as db:db.execute("UPDATE transactions SET category_source='待确认' WHERE id=?",(source_id,))
    undo=None
    if route=='undo':
        undo=store.batch_category([source_id],'购物')
        store.confirm_refund_link(rows['R']['id'],source_id)
    before=Path(store.path).read_bytes()
    def fail(*args,**kwargs):raise RuntimeError('synthetic maintenance audit failure')
    # Fail late for demo replacement, after soft deletion and new batch import.
    monkeypatch.setattr(generator if route=='demo_replace' else refunds,'audit',fail)
    with pytest.raises(RuntimeError,match='synthetic'):
        if route=='edit':store.edit_transaction(source_id,dict(values(store,source_id),amount_cents=8000))
        elif route=='single':store.correct_categories([(source_id,'购物')])
        elif route=='batch':store.batch_category([source_id],'购物')
        elif route=='model':store.apply_model_category(source_id,'购物')
        elif route=='undo':store.undo_category_batch(undo)
        elif route=='delete':store.delete_import_batch(rows['P']['import_batch_id'])
        else:generate_demo(store,DemoOptions('2026-09',months=1,expenses_per_month=2,replace_months=True),space='demo',seed=3)
    assert Path(store.path).read_bytes()==before;observe(store)


def test_duplicate_candidate_creation_is_idempotent_and_scan_preserves_refund_candidates(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    group=store.create_refund_group(['R-W','R-B'],reason='第一次提出候选');before=Path(store.path).read_bytes()
    assert store.create_refund_group(['R-B','R-W'],reason='重试提交')==group and Path(store.path).read_bytes()==before
    store.generate_candidates()
    assert next(g for g in store.reconciliation_groups() if g['id']==group)['status']=='candidate'
    with store.connect() as db:
        db.execute("INSERT INTO link_groups(id,kind,status,reason,confidence,algorithm_version) VALUES ('OLD-DUP','refund_duplicate','candidate','合成历史重复','B','m2-refund-1')")
        db.executemany("INSERT INTO link_members VALUES ('OLD-DUP',?,'refund')",[('R-W',),('R-B',)])
    result=store.maintain_links();assert result['deduplicated_candidates']==1
    groups=[g for g in store.reconciliation_groups() if g['kind']=='refund_duplicate']
    assert sorted(g['status'] for g in groups)==['candidate','stale']
    assert len(groups)==2 and any(r['event']=='refund_candidate_deduplicated' for r in audit_rows(store))
    after=Path(store.path).read_bytes();assert store.maintain_links()['deduplicated_candidates']==0
    assert Path(store.path).read_bytes()==after;observe(store)


def test_readonly_write_guards_unknown_target_and_valid_repair_are_byte_safe(tmp_path):
    store,link=confirmed(tmp_path);before=Path(store.path).read_bytes()
    assert store.repair_refund_links(identifier=link['id'])==0
    with pytest.raises(ValueError,match='不存在'):store.repair_refund_links(identifier='missing')
    with pytest.raises(ValueError,match='不存在'):store.refund_link_evidence('missing')
    readonly=Store(store.path,read_only=True)
    for call in [readonly.maintain_links,readonly.repair_refund_links,readonly.repair_invalid_groups]:
        with pytest.raises(ValueError,match='只读'):call()
    assert Path(store.path).read_bytes()==before;observe(readonly)


def test_two_connections_repair_once_and_repeat_is_noop(tmp_path):
    store,link=confirmed(tmp_path);stores=[Store(store.path),Store(store.path)]
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
    barrier=Barrier(2)
    def repair(i):barrier.wait(timeout=5);return stores[i].repair_refund_links(identifier=link['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(repair,range(2)))
    assert sorted(results)==[0,1]
    assert sum(r['event']=='refund_repaired' for r in audit_rows(store))==1;observe(store)


def test_two_connections_create_only_one_identical_refund_candidate(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行');stores=[Store(store.path),Store(store.path)]
    barrier=Barrier(2)
    def create(i):
        barrier.wait(timeout=5)
        return stores[i].create_refund_group(['R-W','R-B'] if i==0 else ['R-B','R-W'],reason='合成同成员提交')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create,range(2)))
    assert results[0]==results[1]
    assert len([g for g in store.reconciliation_groups() if g['kind']=='refund_duplicate'])==1
    assert sum(r['event']=='refund_group_proposed' for r in audit_rows(store))==1;observe(store)
