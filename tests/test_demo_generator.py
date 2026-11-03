from dataclasses import replace
from datetime import datetime
from unittest.mock import patch
import pytest
from finance.demo_generator import DemoOptions,statement,generate_demo
from finance.storage import Store
from finance.qa import answer_question


def test_selected_months_leap_dates_exact_money_and_refund_semantics(tmp_path):
    store=Store(tmp_path/'demo.sqlite3')
    config=DemoOptions('2024-02',2,40,1234,1234,300000,refunds=True)
    result=generate_demo(store,config,space='demo',seed=3)
    rows=store.source_transactions()
    assert result['months']==['2024-02','2024-03'] and result['added']==86
    assert set(rows.date.str[:7])=={'2024-02','2024-03'}
    assert all(datetime.fromisoformat(s).month in (2,3) for s in rows.date)
    expenses=rows[rows.kind=='支出'];assert len(expenses)==80 and set(expenses.amount_cents)=={1234}
    assert len(rows[rows.kind=='转账'])==2 and store.pending_refunds()['amount_cents']==1234
    projected=store.transactions();assert projected.loc[projected.kind=='收入','amount_cents'].sum()==600000
    answer=answer_question(projected,'2024-02 支出多少？')
    assert answer['status']=='ok' and '493.60' in answer['answer'] and '未扣退款' in answer['answer']
    assert len(store.import_batches())==1 and all(rows.raw_row_id.notna())


def test_append_runs_have_distinct_ids_and_zero_income_option(tmp_path):
    store=Store(tmp_path/'demo.sqlite3');config=DemoOptions('2026-12',1,3,100,200,0,transfers=False)
    generate_demo(store,config,space='demo',seed=5);ids=set(store.transactions().id)
    generate_demo(store,config,space='demo',seed=5)
    rows=store.transactions();assert len(rows)==6 and len(set(rows.id))==6 and len(set(rows.id)-ids)==3
    assert set(rows.kind)=={'支出'} and len(store.import_batches())==2


@pytest.mark.parametrize('options',[
    DemoOptions('2026-13'),DemoOptions('2100-12',2),DemoOptions('1999-12'),DemoOptions('2026-1'),
    DemoOptions('2026-01',0),DemoOptions('2026-01',13),DemoOptions('2026-01',expenses_per_month=501),
    DemoOptions('2026-01',expense_min_cents=0),DemoOptions('2026-01',expense_min_cents=200,expense_max_cents=100),
    DemoOptions('2026-01',income_cents=-1),
])
def test_invalid_options_never_write(tmp_path,options):
    store=Store(tmp_path/'demo.sqlite3')
    with pytest.raises(ValueError):generate_demo(store,options,space='demo')
    assert store.transactions().empty and not store.import_batches()


def test_personal_ledger_cannot_be_target_even_with_wrong_space_argument(tmp_path):
    store=Store(tmp_path/'personal.sqlite3');store.add_manual_transaction('2026-09-01','个人记录',100,'支出','餐饮')
    original=store.transactions().to_dict('records')
    for mode in ('personal','demo'):
        with pytest.raises(ValueError,match='演示空间'):generate_demo(store,DemoOptions('2026-09'),space=mode)
    assert store.transactions().to_dict('records')==original and not store.import_batches()


def test_replace_only_selected_months_preserves_settings_and_evidence(tmp_path):
    store=Store(tmp_path/'demo.sqlite3');config=DemoOptions('2026-09',2,3,100,200,0,transfers=False)
    generate_demo(store,config,space='demo');old=store.transactions();old_september=set(old.loc[old.date.str.startswith('2026-09'),'id'])
    october=set(old.loc[old.date.str.startswith('2026-10'),'id']);store.save_budget('2026-09','餐饮',10000)
    store.save_goal(dict(name='保留目标',target_cents=10000,saved_cents=0,start_date='2026-01-01',deadline='2027-01-01',income_cents=300000,budget_cents=100000))
    outcome=generate_demo(store,replace(config,months=1,expenses_per_month=5,replace_months=True),space='demo')
    rows=store.transactions();assert outcome['removed']==3 and outcome['added']==5 and len(rows)==8
    assert not set(rows.id)&old_september and october<=set(rows.id)
    assert store.budgets('2026-09')=={'餐饮':10000} and len(store.goals())==1
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM raw_rows').fetchone()[0]==11
        assert db.execute("SELECT COUNT(*) FROM transactions WHERE status='deleted'").fetchone()[0]==3


def test_replace_failure_rolls_back_old_rows_and_new_batch(tmp_path):
    store=Store(tmp_path/'demo.sqlite3');config=DemoOptions('2026-09',1,3,100,200,0,transfers=False)
    generate_demo(store,config,space='demo');old=store.source_transactions().to_dict('records')
    with patch.object(store,'_insert_rows',side_effect=RuntimeError('injected failure')):
        with pytest.raises(RuntimeError):generate_demo(store,replace(config,replace_months=True),space='demo')
    assert store.source_transactions().to_dict('records')==old and len(store.import_batches())==1


def test_replace_invalidates_confirmed_reconciliation(tmp_path):
    from finance.m1a_demo import seed_six
    store=Store(tmp_path/'demo.sqlite3');seed_six(store);store.generate_candidates()
    groups=store.reconciliation_groups()
    for group in groups:store.decide_group(group['id'],'confirmed')
    outcome=generate_demo(store,DemoOptions('2026-09',1,3,100,100,0,transfers=False,replace_months=True),space='demo')
    assert outcome['removed']==6 and len(store.transactions())==3
    assert all(g['status']=='needs_review' for g in store.reconciliation_groups())
