from datetime import date
from io import BytesIO
from pathlib import Path
import json
import pandas as pd
import pytest
from finance.storage import Store
from finance.analytics import overview,estimate_from_records,budget_report
from finance.insights import suggested_budgets,make_insights
from finance.qa import answer_question
from finance.spending_presenter import spending_totals,refund_status_summary,refund_status_text,net_export_csv,answer_export_csv
from test_m2_1 import ledger,add_refund
from test_m2_2 import add_purchase,source_rows,audit_rows,expense
from test_m2_3 import imported


@pytest.mark.parametrize('consent',[False,None,1,'yes'])
def test_basis_switch_requires_explicit_true_confirmation(tmp_path,consent):
    store=ledger(tmp_path);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='确认'):store.set_spending_basis('original_purchase_month',confirmed=consent)
    assert Path(store.path).read_bytes()==before and store.spending_basis()=='cash'


def test_basis_switch_is_global_per_space_audited_and_source_immutable(tmp_path):
    store=ledger(tmp_path);store.confirm_refund_link('R-W','P-W');source=source_rows(store)
    other=Store(tmp_path/'personal.sqlite3')
    assert store.set_spending_basis('original_purchase_month',confirmed=True)
    frame=store.spending_view();assert frame.attrs['spending_basis']=='original_purchase_month'
    assert expense(frame,'2026-09')==7000 and expense(frame,'2026-10')==0
    assert source_rows(store)==source and other.spending_basis()=='cash'
    event=next(r for r in audit_rows(store) if r['event']=='spending_basis_changed')
    detail=json.loads(event['detail']);assert detail['old']=='cash' and detail['new']=='original_purchase_month' and detail['changed_at']
    before=Path(store.path).read_bytes();assert store.set_spending_basis('original_purchase_month',confirmed=True) is False
    assert Path(store.path).read_bytes()==before
    assert Store(store.path).spending_basis()=='original_purchase_month'
    assert store.set_spending_basis('cash',confirmed=True) and expense(store.spending_view(),'2026-10')==-3000


def test_basis_audit_failure_rolls_back_and_readonly_cannot_switch(tmp_path,monkeypatch):
    import finance.refunds as refunds
    store=ledger(tmp_path);before=Path(store.path).read_bytes()
    def fail(*args,**kwargs):raise OSError('synthetic audit write failure')
    monkeypatch.setattr(refunds,'audit',fail)
    with pytest.raises(OSError):store.set_spending_basis('original_purchase_month',confirmed=True)
    assert Path(store.path).read_bytes()==before and store.spending_basis()=='cash'
    with pytest.raises(ValueError,match='只读'):Store(store.path,read_only=True).set_spending_basis('original_purchase_month',confirmed=True)
    with pytest.raises(ValueError):store.set_spending_basis('gross',confirmed=True)


@pytest.mark.parametrize('basis,october',[('cash',-3000),('original_purchase_month',0)])
def test_totals_budget_qa_and_export_share_one_projection(tmp_path,basis,october):
    store=ledger(tmp_path);store.confirm_refund_link('R-W','P-W');store.set_spending_basis(basis,confirmed=True)
    frame=store.spending_view();selected=frame[frame.date.str.startswith('2026-10')].copy()
    totals=spending_totals(selected)
    assert totals['net_cents']==october and totals['gross_cents']==0 and totals['balance_cents']==-october
    assert budget_report(frame,'2026-10',{'餐饮':10000}).iloc[0]['已支出']==october/100
    answer=answer_question(frame,'2026-10 支出多少？')
    assert answer['status']=='ok' and f'{october/100:,.2f}' in answer['answer']
    assert answer['trace']['inputs']['spending_basis']==basis
    exported=pd.read_csv(BytesIO(net_export_csv(frame)))
    assert set(exported.basis)=={basis} and '计算时间' in exported and '口径' in exported
    assert int(exported.loc[exported.kind=='支出','amount_cents'].sum())==7000
    evidence=pd.read_csv(BytesIO(answer_export_csv(answer)))
    assert 'basis' in evidence and 'amount_basis' in evidence


def test_gross_query_is_explicit_and_derived_without_changing_net_source(tmp_path):
    store=ledger(tmp_path);store.confirm_refund_link('R-W','P-W');frame=store.spending_view('original_purchase_month')
    gross=answer_question(frame,'2026-09 消费总额多少？');net=answer_question(frame,'2026-09 净支出多少？')
    assert '100.00' in gross['answer'] and '消费总额' in gross['answer'] and gross['trace']['inputs']['amount_basis']=='gross'
    assert '70.00' in net['answer'] and net['trace']['inputs']['amount_basis']=='net'
    assert expense(frame)==7000 and expense(store.transactions())==10000


@pytest.mark.parametrize('basis,expected',[('cash','30.00'),('original_purchase_month','0.00')])
def test_explicit_refund_queries_distinguish_adjustment_basis_from_source_month(tmp_path,basis,expected):
    store=ledger(tmp_path);store.confirm_refund_link('R-W','P-W');frame=store.spending_view(basis)
    amount=answer_question(frame,'2026-10 退款抵扣多少？')
    assert amount['status']=='ok' and expected in amount['answer'] and amount['trace']['inputs']['spending_basis']==basis
    sources=answer_question(frame,'2026-10 已关联退款多少？')
    assert sources['status']=='ok' and '30.00' in sources['answer'] and '按实际退款发生月' in sources['answer']
    assert sources['trace']['inputs']['amount_basis']=='source_refund'


def test_local_refund_status_queries_do_not_call_a_provider(tmp_path):
    store=ledger(tmp_path)
    class Forbidden:
        def parse_question(self,*args):raise AssertionError('model must not be called')
    answer=answer_question(store.spending_view(),'2026-10 待处理退款几笔？',model=Forbidden())
    assert answer['status']=='ok' and '1 笔' in answer['answer']
    assert answer_question(store.spending_view(),'2099-10 退款抵扣多少？')['status']=='missing'


def test_refund_only_original_month_zero_has_source_evidence_and_no_false_missing(tmp_path):
    store,rows,link=imported(tmp_path);frame=store.spending_view('original_purchase_month')
    answer=answer_question(frame,'2026-10 支出多少？')
    assert answer['status']=='ok' and '0.00' in answer['answer'] and '无计入明细' in answer['answer']
    assert any(e['type']=='RefundDecision' for e in answer['trace']['evidence'])
    assert any(e['type']=='ExcludedSource' for e in answer['trace']['evidence'])


def test_pending_review_and_linked_labels_are_distinct_everywhere(tmp_path):
    store=ledger(tmp_path);summary=refund_status_summary(store.refund_sources())
    assert '退款待处理 1 笔' in refund_status_text(summary)
    link=store.confirm_refund_link('R-W','P-W')
    text=refund_status_text(refund_status_summary(store.refund_sources()))
    assert '退款已关联 1 笔' in text and '待处理' not in text
    assert '已关联' in store.source_transactions().set_index('id').loc['R-W','counting_note']
    store.correct_categories([('R-W','工资')])
    text=refund_status_text(refund_status_summary(store.refund_sources()))
    assert '退款待复核 1 笔' in text and '待处理' not in text
    assert '待复核' in answer_question(store.spending_view(),'全部支出多少？')['answer']
    assert store.transactions().attrs['refund_pending']==store.pending_refunds()==dict(count=1,amount_cents=3000)


def test_invalid_refund_groups_are_merged_with_relation_blockers_and_reads_are_pure(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    group=store.create_refund_group(['R-W','R-B'],reason='合成同一退款');store.decide_group(group,'confirmed')
    link=store.confirm_refund_link('R-W','P-W')
    with store.connect() as db:db.execute("UPDATE transactions SET amount_cents=2000 WHERE id='R-B'")
    before=Path(store.path).read_bytes();work=store.refund_work_items()
    assert {(r['kind'],r['id']) for r in work}=={('link',link['id']),('group',group)}
    assert all(r['reasons'] for r in work) and Path(store.path).read_bytes()==before


def test_damaged_context_propagates_generic_review_to_all_current_refund_members(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    group=store.create_refund_group(['R-W','R-B'],reason='合成退款组');store.decide_group(group,'confirmed')
    link=store.confirm_refund_link('R-W','P-W')
    with store.connect() as db:
        db.execute("UPDATE refund_links SET context_json='not-json' WHERE id=?",(link['id'],))
        db.execute("UPDATE link_groups SET status='needs_review' WHERE id=?",(group,))
    events=store.refund_sources()
    assert len(events)==2 and all(r['association_status']=='needs_review' and r['reasons'] for r in events)


def test_explicit_purchase_role_does_not_fabricate_a_refund_or_change_source_money(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3')
    identifier=store.add_manual_transaction('2026-09-01','合成餐厅',10000,'支出','餐饮','已退款状态需核对')
    with store.connect() as db:db.execute("UPDATE transactions SET status='review' WHERE id=?",(identifier,))
    before=store.transaction_source(identifier)
    store.confirm_purchase_role(identifier,reason='核对来源为原消费，尚无独立退款流水')
    after=store.transaction_source(identifier)
    assert all(before[k]==after[k] for k in ('date','kind','amount_cents','category'))
    assert after['status']=='normal' and expense(store.spending_view())==10000 and store.refund_sources()==[]
    with pytest.raises(ValueError):store.confirm_purchase_role(identifier,reason='重复确认角色')


def test_negative_history_requires_explicit_future_budget_without_creating_goal_predictions(tmp_path):
    store=ledger(tmp_path)
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-05-01' WHERE id IN ('P-W','P-B')")
    store.confirm_refund_link('R-W','P-W');frame=store.spending_view()
    estimate=estimate_from_records(frame,date(2026,11,4))
    assert estimate['average_expense_cents']==-3000 and estimate['requires_explicit_budget']
    with pytest.raises(ValueError,match='非负未来预算'):suggested_budgets(frame,date(2026,11,4))
    with pytest.raises(ValueError):spending_totals(store.transactions())
