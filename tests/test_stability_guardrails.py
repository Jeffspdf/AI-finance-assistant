from pathlib import Path
import json
import subprocess
import sys
import pytest
from finance.storage import Store
from scripts.m2_ui_fixture import seed_ui_refunds

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('month,category,cents',[
    ('2026-13','餐饮',100),('2026-00','餐饮',100),('2026-1','餐饮',100),
    ('2026-01x','餐饮',100),(None,'餐饮',100),(202601,'餐饮',100),
    ('2026-11','工资',100),('2026-11','转账',100),('2026-11','不存在',100),
    ('2026-11',None,100),('2026-11','餐饮',True),('2026-11','餐饮',False),
    ('2026-11','餐饮',100.9),('2026-11','餐饮',float('nan')),
    ('2026-11','餐饮',float('inf')),('2026-11','餐饮',None),
    ('2026-11','餐饮','100'),('2026-11','餐饮',-1),('2026-11','餐饮',10**12+1),
])
def test_invalid_budget_never_writes(tmp_path,month,category,cents):
    store=Store(tmp_path/'ledger.sqlite3');before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.save_budget(month,category,cents)
    assert Path(store.path).read_bytes()==before


def test_valid_zero_budget_and_read_only_refusal(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3')
    store.save_budget('2026-11','餐饮',0)
    assert store.budgets('2026-11')=={'餐饮':0}
    readonly=Store(store.path,read_only=True);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='只读'):readonly.save_budget('2026-11','餐饮',10)
    assert Path(store.path).read_bytes()==before


def test_copy_legacy_invalid_budget_is_atomic(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');store.save_budget('2026-11','餐饮',100)
    with store.connect() as db:
        db.execute("INSERT INTO budgets VALUES ('2026-10','餐饮',500)")
        db.execute("INSERT INTO budgets VALUES ('2026-10','工资',1000)")
    before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.copy_budgets('2026-10','2026-11',overwrite=True)
    assert Path(store.path).read_bytes()==before


def test_edit_and_rule_failure_roll_back_relations_and_source(tmp_path):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    store.confirm_refund_link(ids['R-W'],ids['P-W'])
    values=store.source_transactions().set_index('id').loc[ids['P-W']].to_dict()
    values['amount_cents']=8000
    with store.connect() as db:
        db.execute("CREATE TRIGGER stability_reject_rule BEFORE INSERT ON merchant_rules BEGIN SELECT RAISE(ABORT,'synthetic rule failure'); END")
    before=Path(store.path).read_bytes()
    with pytest.raises(Exception,match='synthetic rule failure'):store.edit_transaction(ids['P-W'],values,learn=True)
    assert Path(store.path).read_bytes()==before
    assert store.refund_links()[0]['status']=='confirmed'
    assert int(store.spending_view().query("kind == '支出'").amount_cents.sum())==7000


def test_stale_edit_cannot_change_deleted_source_or_rules(tmp_path):
    store,ids=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    values=store.source_transactions().set_index('id').loc[ids['P-W']].to_dict()
    store.delete_import_batch(values['import_batch_id'])
    before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='删除'):store.edit_transaction(ids['P-W'],dict(values,merchant='不应写入'),learn=True)
    assert Path(store.path).read_bytes()==before


def test_budget_suggestion_failure_rolls_back_every_category(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');store.save_budget('2026-11','餐饮',100)
    with store.connect() as db:
        db.execute("CREATE TRIGGER stability_reject_budget BEFORE INSERT ON budgets WHEN NEW.category='交通' BEGIN SELECT RAISE(ABORT,'synthetic budget failure'); END")
    before=Path(store.path).read_bytes()
    with pytest.raises(Exception,match='synthetic budget failure'):store.save_budgets('2026-11',{'餐饮':500,'交通':700})
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('case',['edit_atomic','import_space','budget_chat','budget_suggestions'])
def test_real_tk_stability_regression(case):
    result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_stability_ui.py'),case],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    report=json.loads(result.stdout)
    assert report['passed'] and not report['callback_errors']
