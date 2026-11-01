from datetime import date
import pytest
from finance.storage import Store
from finance.demo import demo_bytes
from finance.importer import parse_statement
from finance.analytics import budget_report, estimate_from_records, goal_progress
from finance.qa import answer_question, validate_model_intent

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'old.sqlite3');s.import_rows(parse_statement(demo_bytes(date(2026,10,9)),'demo.csv').rows);return s

def test_budget_consistent(store):
    store.save_budget('2026-09','餐饮',10000)
    r=budget_report(store.transactions(),'2026-09',store.budgets('2026-09')).iloc[0]
    assert r['已支出']==145 and r['超支']==45
    a=answer_question(store.transactions(),'上个月餐饮超支多少？',date(2026,10,9),budgets={'2026-09':store.budgets('2026-09')})
    assert '45.00' in a['answer']

def test_estimate_complete_months_and_missing(store):
    r=estimate_from_records(store.transactions(),date(2026,10,9))
    assert r['months']==['2026-07','2026-08','2026-09'] and r['record_count']==100
    assert r['average_expense_cents']==340333
    with pytest.raises(ValueError):estimate_from_records(store.transactions().iloc[:0],date(2026,10,9))
    r=estimate_from_records(store.transactions()[store.transactions().date.str.startswith('2026-09')],date(2026,10,9))
    assert r['missing_months']==['2026-07','2026-08'] and len(r['months'])==1

def test_goal_contributions_migrate_and_cascade(store):
    plan=dict(name='目标',target_cents=600000,saved_cents=10000,start_date='2026-07-01',deadline='2027-01-01',income_cents=500000,budget_cents=400000)
    store.save_goal(plan);gid=int(store.goals().iloc[0].id)
    store.add_contribution(gid,'2026-08-01',50000,today=date(2026,10,9))
    store.add_contribution(gid,'2026-09-01',60000,today=date(2026,10,9))
    goal=store.goals().iloc[0].to_dict()
    r=goal_progress(goal,store.contributions(gid),date(2026,10,9))
    assert r['actual_saved']==120000 and r['expected_saved']==305002 and r['behind_cents']==185002
    assert Store(store.path).contributions(gid).amount_cents.sum()==110000
    with pytest.raises(ValueError):store.add_contribution(gid,'2027-01-01',100,today=date(2026,10,9))
    store.delete_goal(gid);assert store.contributions(gid).empty

def test_true_legacy_database_upgrade(tmp_path):
    import sqlite3
    path=tmp_path/'legacy.sqlite3'
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE goals(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,target_cents INTEGER NOT NULL,saved_cents INTEGER NOT NULL,start_date TEXT NOT NULL,deadline TEXT NOT NULL,income_cents INTEGER NOT NULL,budget_cents INTEGER NOT NULL)')
    db.execute("INSERT INTO goals VALUES(1,'旧目标',600000,5000,'2026-07-01','2027-01-01',500000,400000)")
    db.commit();db.close()
    upgraded=Store(path)
    assert upgraded.goals().iloc[0].saved_cents==5000 and upgraded.contributions(1).empty
    upgraded.add_contribution(1,'2026-08-01',1000,today=date(2026,10,9))
    assert upgraded.contributions(1).iloc[0].amount_cents==1000

def test_model_suggestions_protect_corrections(store):
    row=store.transactions().iloc[0]
    assert not store.apply_model_category(row.id,'娱乐')
    with store.connect() as db:db.execute('UPDATE transactions SET category_source=? WHERE id=?',('待确认',row.id))
    assert store.apply_model_category(row.id,'娱乐')
    store.correct_categories([(row.id,'餐饮')]);assert not store.apply_model_category(row.id,'娱乐')
    with pytest.raises(ValueError):store.apply_model_category(row.id,'假类别')

class Fake:
    def __init__(self,intent):self.intent=intent;self.calls=0
    def parse_question(self,q):self.calls+=1;return self.intent

def test_model_merchant_and_dates(store):
    model=Fake(dict(metric='expense',category=None,period='last_month',merchant='麦当劳'))
    r=answer_question(store.transactions(),'上个月在麦当劳花了多少？',date(2026,10,9),model=model)
    assert r['status']=='ok' and r['evidence'] and all(x['merchant']=='麦当劳' for x in r['evidence'])
    model=Fake(dict(metric='expense',category=None,period='all',date_from='2026-09-01',date_to='2026-09-10'))
    r=answer_question(store.transactions(),'2026-09-01到2026-09-10支出多少？',date(2026,10,9),model=model)
    assert r['status']=='ok' and all('2026-09-01'<=x['date'][:10]<='2026-09-10' for x in r['evidence'])

def test_compare_and_average(store):
    model=Fake(dict(metric='compare',category=None,period='last_month',compare_categories=['餐饮','交通']))
    r=answer_question(store.transactions(),'上个月餐饮和交通各花多少？',date(2026,10,9),model=model)
    assert r['status']=='ok' and '145.00' in r['answer'] and '餐饮' in r['answer'] and '交通' in r['answer']
    model=Fake(dict(metric='average_expense',category=None,period='last_3_complete_months'))
    r=answer_question(store.transactions(),'近三个月月均支出？',date(2026,10,9),model=model)
    assert r['status']=='ok' and '3,403.33' in r['answer']

def test_rule_precedence_and_drop_filter_safety(store):
    model=Fake(dict(metric='income',category=None,period='all'))
    assert answer_question(store.transactions(),'上个月餐饮花了多少？',date(2026,10,9),model=model)['status']=='ok'
    assert model.calls==0
    for q in ['上个月在麦当劳餐饮花了多少？','下个月股票收益多少？','昨天花了多少？','2026-09-01到2026-09-10支出多少？']:
        assert answer_question(store.transactions(),q,date(2026,10,9),model=Fake(dict(metric='expense',category=None,period='all')))['status']=='unsupported'

@pytest.mark.parametrize('changes',[{'date_from':'1999-01-01','date_to':'2026-01-01'},{'date_from':'2026-02-02','date_to':'2026-01-01'},{'category':'无效'},{'sql':'DELETE'},{'merchant':'x'*51}])
def test_bad_intent(changes):
    assert validate_model_intent('请汇总支出',{'metric':'expense','category':None,'period':'all',**changes}) is None
