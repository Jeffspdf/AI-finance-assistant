from datetime import date
import pandas as pd
import pytest
from finance.insights import make_insights,suggested_budgets
from finance.storage import Store
from finance.importer import parse_statement

def rows():
    return pd.DataFrame([dict(id=str(i),date=f'2026-{month:02d}-01 12:00:00',kind=kind,category=category,merchant=merchant,amount_cents=amount) for i,(month,kind,category,merchant,amount) in enumerate([
        (7,'收入','工资','单位',300000),(7,'支出','餐饮','食堂',10000),
        (8,'收入','工资','单位',300000),(8,'支出','餐饮','食堂',10000),
        (9,'收入','工资','单位',300000),(9,'支出','餐饮','食堂',20000)])])

def test_insight_math_and_empty():
    f=rows();cards=make_insights(f,'2026-09',today=date(2026,10,1))
    change=next(c for c in cards if c['kind']=='change')
    assert change['value']=='¥100.00' and '100.0%' in change['why'] and set(change['ids'])=={'3','5'}
    assert any(c['kind']=='unusual' and c['value']=='2.0 倍' for c in cards)
    assert make_insights(f.iloc[:0],'2026-09')==[]
    assert not make_insights(f[f.date.str.startswith('2026-09')],'2026-09',today=date(2026,10,1))
    assert suggested_budgets(f,date(2026,10,1))['餐饮']==12000

def test_goal_insight_is_conditional_and_traced():
    goal=dict(id=1,name='储蓄',target_cents=600000,saved_cents=0,start_date='2026-07-01',deadline='2027-01-01',income_cents=300000,budget_cents=100000)
    contributions=pd.DataFrame(columns=['date','amount_cents'])
    cards=make_insights(rows(),'2026-09',[goal],{1:contributions},date(2026,10,1))
    card=cards[0]
    assert card['kind']=='goal' and card['value']=='约 3 个月' and len(card['ids'])==6
    assert '不是保证' in card['why'] and '截止 2027-01-01' in card['why']

def test_manual_learning_copy_and_undo(tmp_path):
    s=Store(tmp_path/'db.sqlite3')
    row=s.add_manual_transaction('2026-09-01','陌生店',2500,'支出','餐饮','现金午餐')
    assert s.transactions().iloc[0].source_file=='手动记账'
    s.correct_categories([(row,'购物')],learn=True)
    new=parse_statement('交易ID,日期,商户,描述,金额,类型\nNEW,2026-09-02,陌生店,新商品,26,支出\n'.encode(),'file.csv').rows
    s.import_rows(new);added=s.transactions().query("external_id=='NEW'").iloc[0]
    assert added.category=='购物' and added.category_source=='商户学习'
    assert len(s.merchant_rules())==1
    s.delete_merchant_rule('陌生店','支出');assert s.merchant_rules().empty
    snapshot=s.batch_category([row,added.id],'娱乐')
    assert all(s.transactions().category=='娱乐')
    assert s.undo_category_batch(snapshot)==2
    assert all(s.transactions().category=='购物')
    s.save_budget('2026-08','餐饮',10000);s.save_budget('2026-09','餐饮',15000)
    s.copy_budgets('2026-08','2026-09',overwrite=False);assert s.budgets('2026-09')['餐饮']==15000
    s.copy_budgets('2026-08','2026-09',overwrite=True);assert s.budgets('2026-09')['餐饮']==10000
    with pytest.raises(ValueError):s.add_manual_transaction('bad','食堂',100,'支出','餐饮','')
