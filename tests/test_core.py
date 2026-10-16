from datetime import date
from io import BytesIO
from pathlib import Path
import csv
import pandas as pd
import pytest
from finance.storage import Store
from finance.importer import parse_statement, money_to_cents
from finance.analytics import overview, budget_report, plan_savings
from finance.qa import answer_question, parse_question
from finance.demo import demo_bytes, demo_rows
from finance.llm import ModelClient

TODAY=date(2026,10,9)

@pytest.fixture
def store(tmp_path):
    db=Store(tmp_path/'test.sqlite3')
    db.import_rows(parse_statement(demo_bytes(TODAY),'test.csv').rows)
    return db

def csv_bytes(rows):
    from io import StringIO
    s=StringIO(); w=csv.writer(s)
    w.writerow(['交易ID','日期','商户','描述','金额','类型','平台','币种']); w.writerows(rows)
    return s.getvalue().encode('utf-8-sig')

def test_labeled_100_accuracy():
    parsed=parse_statement(demo_bytes(TODAY),'labeled.csv')
    expected={r[0]:r[-1] for r in demo_rows(TODAY)}
    correct=sum(r['category']==expected[r['external_id']] for r in parsed.rows)
    assert len(parsed.rows)==100 and not parsed.issues
    assert correct/100>=.9

def test_import_errors_and_dedup(tmp_path):
    result=parse_statement(demo_bytes(TODAY,errors=True),'errors.csv')
    assert len(result.rows)==102 and len(result.issues)==4
    db=Store(tmp_path/'db.sqlite3')
    assert db.import_rows(result.rows)[:2]==(100,2)
    assert db.import_rows(result.rows)[:2]==(0,102)
    assert len(db.transactions())==100

def test_conflicting_id_does_not_overwrite(store):
    row=parse_statement(csv_bytes([['DEMO-0001','2026-07-01 12:00:00','麦当劳','午餐','99','支出','样例','CNY']]),'new.csv').rows
    added,dupes,conflicts=store.import_rows(row)
    assert (added,dupes)==(0,0) and len(conflicts)==1
    assert store.transactions().set_index('external_id').loc['DEMO-0001','amount_cents']==4500

@pytest.mark.parametrize('value',['NaN','Inf','1.001','abc','0','10000000000000000'])
def test_bad_money(value):
    with pytest.raises(ValueError): money_to_cents(value)

def test_exact_money_and_signs():
    assert money_to_cents('￥1,234.56')==123456
    assert money_to_cents('-0.01')==-1
    assert money_to_cents(0,True)==0
    data=b'date,amount,merchant\n2026-09-01,-12.50,Shop\n2026-09-02,500,Work\n'
    parsed=parse_statement(data,'signed.csv')
    assert [(r['kind'],r['amount_cents']) for r in parsed.rows]==[('支出',1250),('收入',50000)]
    assert parsed.warnings

def test_missing_columns():
    with pytest.raises(ValueError,match='缺少'): parse_statement(b'x,y\n1,2','bad.csv')

def test_excel_and_platform_preamble():
    frame=pd.DataFrame({'交易时间':['2026-09-01 12:00:00'],'交易对方':['食堂'],'商品':['午餐'],'金额(元)':['12.50'],'收/支':['支出']})
    stream=BytesIO()
    with pd.ExcelWriter(stream,engine='openpyxl') as writer: frame.to_excel(writer,index=False,startrow=3)
    result=parse_statement(stream.getvalue(),'wx.xlsx')
    assert result.rows[0]['source_row']==5 and result.rows[0]['amount_cents']==1250
    csv_data='微信账单导出\n用户：示例\n'+frame.to_csv(index=False)
    assert len(parse_statement(csv_data.encode('gb18030'),'wx.csv').rows)==1

def test_invalid_dates_and_currency():
    r=parse_statement(csv_bytes([
        ['1','2026-99-01','店','消费','20','支出','p','CNY'],
        ['2','45600','店','消费','20','支出','p','CNY'],
        ['3','2026-09-01','店','消费','20','支出','p','USD'],
    ]),'bad.csv')
    assert len(r.issues)==3 and not r.rows

def test_summary_budget_and_transfer(store):
    frame=store.transactions(); trend,categories=overview(frame)
    # Independent totals from the labeled input, never derived via production aggregations.
    source=demo_rows(TODAY)
    for row in trend.to_dict('records'):
        income=sum(r[4] and float(r[4]) for r in source if r[1].startswith(row['月份']) and r[5]=='收入')
        expense=sum(float(r[4]) for r in source if r[1].startswith(row['月份']) and r[5]=='支出')
        assert row['收入']==income and row['支出']==expense and row['结余']==income-expense
    assert round(categories['占比'].sum(),1)==100
    report=budget_report(frame,'2026-09',{'餐饮':10000,'教育':0}).set_index('分类')
    assert report.loc['餐饮','已支出']==145 and report.loc['餐饮','超支']==45
    assert report.loc['教育','使用率'] is None or pd.isna(report.loc['教育','使用率'])

def test_category_override_persists(tmp_path):
    p=tmp_path/'store.sqlite3'; db=Store(p)
    rows=parse_statement(csv_bytes([['1','2026-09-01','未知店','消费','30','支出','p','CNY']]),'source.csv').rows
    db.import_rows(rows); db.correct_categories([(rows[0]['id'],'教育')])
    db.import_rows(rows)
    refreshed=Store(p).transactions().iloc[0]
    assert refreshed.category=='教育' and refreshed.category_source=='用户修正'

def test_savings_two_scenarios_and_anniversaries():
    args=dict(target_cents=600000,saved_cents=0,start_date='2026-10-09',deadline='2027-04-09',income_cents=500000)
    a=plan_savings(**args,budget_cents=420000); b=plan_savings(**args,budget_cents=380000)
    assert a['months']==6 and a['projected']==480000 and a['deficit']==120000 and not a['feasible']
    assert b['projected']==720000 and b['feasible'] and b['required_monthly']==100000
    short=plan_savings(10000,0,'2026-01-31','2026-03-30',30000,20000)
    assert short['contribution_dates']==['2026-02-28']
    rounded=plan_savings(10001,0,'2026-01-01','2026-04-01',30000,20000)
    assert rounded['required_monthly']==3334
    with pytest.raises(ValueError): plan_savings(100,0,'2026-10-09','2026-10-09',100,0)

def test_goals_and_budget_persist(tmp_path):
    p=tmp_path/'db.sqlite3'; db=Store(p)
    db.save_budget('2026-09','餐饮',12345)
    db.save_goal(dict(name='目标',target_cents=600000,saved_cents=0,start_date='2026-10-09',deadline='2027-04-09',income_cents=500000,budget_cents=380000))
    other=Store(p); assert other.budgets('2026-09')=={'餐饮':12345}; assert len(other.goals())==1

QUESTIONS=[
 '上个月餐饮花了多少？','上个月交通支出多少？','上个月购物支出多少？','上个月教育支出多少？',
 '上个月娱乐支出多少？','上个月医疗支出多少？','上个月居住支出多少？','上个月生活缴费支出多少？',
 '上个月收入多少？','上个月支出多少？','上个月结余多少？','上个月多少笔？',
 '上个月哪个分类支出最多？','上个月餐饮占比是多少？','上个月餐饮超支多少？',
 '2026-08 餐饮支出多少？','2026-07 收入多少？','全部支出多少？','全部收入多少？','全部结余多少？',
]

@pytest.mark.parametrize('question',QUESTIONS)
def test_twenty_grounded_questions(store,question):
    result=answer_question(store.transactions(),question,TODAY,{'2026-09':{'餐饮':10000}})
    assert result['status']=='ok', (question,result)
    assert result['evidence']
    stored=store.transactions().set_index('id')
    for row in result['evidence']:
        assert row['date']==stored.loc[row['id'],'date']
        assert row['amount_cents']==stored.loc[row['id'],'amount_cents']
    if question=='上个月餐饮花了多少？':
        assert '145.00' in result['answer']
        assert sum(r['amount_cents'] for r in result['evidence'])==14500
    if question=='上个月餐饮超支多少？': assert '45.00' in result['answer']

@pytest.mark.parametrize('question',['下个月股票收益多少？','昨天餐饮花了多少？','2026-09-01 餐饮支出多少？','上个月餐饮和交通各花了多少？','上个月在麦当劳花了多少？','2025-01 餐饮花了多少？','2026-09-01到2026-09-10支出多少？'])
def test_unsupported_or_missing_never_fabricates(store,question):
    result=answer_question(store.transactions(),question,TODAY)
    assert result['status'] in ['missing','unsupported'] and not result['evidence']

def test_no_external_requests_by_default(store,monkeypatch):
    import socket
    def blocked(*args,**kwargs): raise AssertionError('Unexpected network request')
    monkeypatch.setattr(socket,'create_connection',blocked)
    monkeypatch.setattr(socket.socket,'connect',blocked)
    parse_statement(demo_bytes(TODAY),'offline.csv')
    overview(store.transactions())
    assert answer_question(store.transactions(),'上个月餐饮花了多少？',TODAY)['status']=='ok'

def test_llm_validation_and_fallback(store,monkeypatch):
    with pytest.raises(ValueError): ModelClient('https://example.com/v1','model')
    with pytest.raises(ValueError): ModelClient('http://example.com/v1','model',enabled=True)
    client=ModelClient('http://localhost:11434/v1','test',enabled=True)
    monkeypatch.setattr(client,'_json',lambda *args:{'category':'不存在'})
    with pytest.raises(ValueError): client.suggest_category('店','消费','支出')
    monkeypatch.setattr(client,'_json',lambda *args:{'metric':'expense','category':None,'period':'2026-99'})
    assert client.parse_question('问题') is None
    monkeypatch.setattr(client,'_json',lambda *args:(_ for _ in ()).throw(OSError('offline')))
    result=answer_question(store.transactions(),'请分析这个账单',TODAY,model=client)
    assert result['status']=='unsupported'

def test_model_cannot_drop_merchant_or_date_filter(store):
    class FakeModel:
        def parse_question(self,question): return {'metric':'expense','category':'餐饮','period':'last_month'}
    for question in ['上个月在麦当劳餐饮花了多少？','上个月餐饮和交通各花了多少？','昨天餐饮花了多少？']:
        result=answer_question(store.transactions(),question,TODAY,model=FakeModel())
        assert result['status']=='unsupported' and not result['evidence']
    result=answer_question(store.transactions(),'上一个月餐饮花销多少？',TODAY,model=FakeModel())
    assert result['status']=='ok' and '145.00' in result['answer']
