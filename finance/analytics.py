from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_CEILING
import pandas as pd

def yuan(cents):
    return f'{int(cents)/100:,.2f}'

def month_records(frame, month):
    return frame[frame['date'].str.startswith(month)].copy()

def overview(frame):
    if frame.empty: return pd.DataFrame(columns=['月份','收入','支出','结余']), pd.DataFrame(columns=['分类','支出','占比'])
    tx=frame.copy(); tx['月份']=tx['date'].str[:7]
    rows=[]
    for month,group in tx.groupby('月份'):
        income=int(group.loc[group.kind=='收入','amount_cents'].sum())
        expense=int(group.loc[group.kind=='支出','amount_cents'].sum())
        rows.append({'月份':month,'收入':income/100,'支出':expense/100,'结余':(income-expense)/100})
    costs=tx[tx.kind=='支出'].groupby('category')['amount_cents'].sum()
    total=int(costs.sum())
    categories=pd.DataFrame([{'分类':k,'支出':int(v)/100,'占比':round(int(v)/total*100,2) if total else 0} for k,v in costs.items()])
    if not categories.empty: categories=categories.sort_values('支出',ascending=False)
    return pd.DataFrame(rows),categories

def budget_report(frame, month, budgets):
    tx=month_records(frame,month)
    costs=tx[tx.kind=='支出'].groupby('category')['amount_cents'].sum().to_dict()
    rows=[]
    for category,limit in budgets.items():
        spent=int(costs.get(category,0)); over=max(0,spent-limit)
        rows.append({'分类':category,'预算':limit/100,'已支出':spent/100,'剩余':(limit-spent)/100,'使用率':round(spent/limit*100,2) if limit else None,'超支':over/100})
    return pd.DataFrame(rows,columns=['分类','预算','已支出','剩余','使用率','超支'])

def plan_savings(target_cents, saved_cents, start_date, deadline, income_cents, budget_cents):
    start=date.fromisoformat(str(start_date)); end=date.fromisoformat(str(deadline))
    if end<=start: raise ValueError('截止日期必须晚于开始日期')
    if any(x<0 for x in [target_cents,saved_cents,income_cents,budget_cents]) or target_cents<=0:
        raise ValueError('目标金额须大于零，其他金额不能为负数')
    # Contributions happen on the monthly anniversary of the start date.
    # Short months use their final day, so Jan 31 -> Feb 28 -> Mar 31.
    contribution_dates=[]
    offset=1
    while True:
        index=start.year*12+start.month-1+offset
        y,m=divmod(index,12); m+=1
        contribution=date(y,m,min(start.day,monthrange(y,m)[1]))
        if contribution>end: break
        contribution_dates.append(contribution.isoformat()); offset+=1
    n=len(contribution_dates)
    available=max(0,income_cents-budget_cents)
    remaining=max(0,target_cents-saved_cents)
    required=int((Decimal(remaining)/n).to_integral_value(rounding=ROUND_CEILING)) if n else None
    projected=saved_cents+n*available
    deficit=max(0,target_cents-projected)
    return dict(months=n,monthly_available=available,required_monthly=required,projected=projected,deficit=deficit,feasible=projected>=target_cents,negative_cashflow=income_cents<budget_cents,contribution_dates=contribution_dates)
