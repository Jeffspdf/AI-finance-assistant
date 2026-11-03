"""Deterministic, evidence-linked observations; never asserts complete finances."""
from datetime import date
from decimal import Decimal
from math import ceil
from .analytics import estimate_from_records,goal_progress,yuan,plan_savings
from .qa import month_shift
from .evidence import effective_frame,calculation

def make_insights(frame,month,goals=None,contributions=None,today=None):
    frame=effective_frame(frame)
    today=today or date.today();goals=goals or [];contributions=contributions or {}
    if month=='全部月份' or frame.empty:return []
    target=date.fromisoformat(month+'-01');previous=month_shift(target,-1)
    current=frame[(frame.date.str.startswith(month))&(frame.kind=='支出')]
    old=frame[(frame.date.str.startswith(previous))&(frame.kind=='支出')]
    cards=[]
    if not current.empty and not old.empty:
        current_costs=current.groupby('category').amount_cents.sum();old_costs=old.groupby('category').amount_cents.sum()
        changes=[(c,int(v)-int(old_costs.get(c,0)),int(v),int(old_costs.get(c,0))) for c,v in current_costs.items()]
        for category,delta,value,base in sorted(changes,key=lambda x:abs(x[1]),reverse=True)[:2]:
            if not delta:continue
            now=current[current.category==category];before=old[old.category==category]
            merchant_changes={m:int(now.loc[now.merchant==m,'amount_cents'].sum())-int(before.loc[before.merchant==m,'amount_cents'].sum()) for m in set(now.merchant)|set(before.merchant)}
            biggest=max(merchant_changes,key=lambda m:abs(merchant_changes[m]))
            percent=f'（{delta/base:+.1%}）' if base else '（上月该类无匹配支出）'
            cards.append(dict(kind='change',title=category+'较上月'+('增加' if delta>0 else '减少'),value=f'¥{yuan(abs(delta))}',
                              why=f'{month} ¥{yuan(value)} − {previous} ¥{yuan(base)}{percent}。其中商户“{biggest}”变化 ¥{yuan(merchant_changes[biggest])}。仅描述记录差异，不推断原因。',
                              category=category,month=month,ids=now.id.tolist()+before.id.tolist()))
    prior=[month_shift(target,-i) for i in [3,2,1]]
    historical=frame[frame.date.str[:7].isin(prior)&(frame.kind=='支出')]
    if not current.empty and not historical.empty:
        used=sorted(set(frame.loc[frame.date.str[:7].isin(prior),'date'].str[:7]))
        for category,total in current.groupby('category').amount_cents.sum().sort_values(ascending=False).items():
            baseline=int(historical.loc[historical.category==category,'amount_cents'].sum())/len(used)
            if baseline>0 and total>baseline*1.5:
                rows=current[current.category==category]
                cards.append(dict(kind='unusual',title=category+'高于历史均值',value=f'{total/baseline:.1f} 倍',why=f'本期 ¥{yuan(total)} ÷ 前 {len(used)} 个有记录月份均值 ¥{baseline/100:,.2f}。对比月份：'+ '、'.join(used)+'。本月可能尚未结束，账单可能不完整。',category=category,month=month,ids=rows.id.tolist()+historical.loc[historical.category==category,'id'].tolist()))
                break
    try:estimate=estimate_from_records(frame,today)
    except ValueError:estimate=None
    if estimate:
        available=estimate['average_income_cents']-estimate['average_expense_cents']
        for goal in goals:
            entries=contributions.get(goal['id'])
            if entries is None:continue
            p=goal_progress(goal,entries,today)
            if not p['remaining']:continue
            if available>0:
                months=ceil(p['remaining']/available)
                explanation=f'剩余 ¥{yuan(p["remaining"])} ÷ 近 {len(estimate["months"])} 个有记录完整月平均结余 ¥{yuan(available)}，向上取整。假设未来结余固定且全部用于该目标；不是保证。'
                deadline=date.fromisoformat(goal['deadline'])
                if deadline>today:
                    future=plan_savings(goal['target_cents'],p['actual_saved'],today,deadline,max(0,available),0)
                    explanation+=f' 截止 {deadline.isoformat()} 尚有 {future["months"]} 次月度存入，按此情景缺口 ¥{yuan(future["deficit"])}。'
                else:explanation+=' 原截止日期已到，请核对进度并调整目标。'
                value=f'约 {months} 个月'
            else:value='当前结余不足';explanation='近三个完整月的平均结余不为正，无法据此推算达成时间。'
            cards.insert(0,dict(kind='goal',title=goal['name']+' · 情景估算',value=value,why=explanation,category=None,month='全部月份',ids=frame.loc[frame.date.str[:7].isin(estimate['months']),'id'].tolist(),goal_id=goal['id']))
            break
    for card in cards:
        evidence_frame=frame[frame.id.isin(card['ids'])]
        extra=[]
        if card['kind']=='goal':
            goal=next(g for g in goals if g['id']==card['goal_id'])
            extra.append(dict(type='Goal',id=str(goal['id']),snapshot=dict(goal)))
            extra.extend(dict(type='Contribution',id=str(e.get('id',i)),snapshot=e) for i,e in enumerate(contributions[goal['id']].to_dict('records')))
        card['trace']=calculation('insight','insight.'+card['kind'],evidence_frame,
                                  inputs=dict(month=month,today=today.isoformat(),assumption='仅已导入记录；未扣待处理退款'),extra=extra)
    return [c for c in cards[:3] if c['trace']['evidence']]

def suggested_budgets(frame,today=None):
    frame=effective_frame(frame)
    today=today or date.today();estimate=estimate_from_records(frame,today)
    expenses=frame[frame.date.str[:7].isin(estimate['months'])&(frame.kind=='支出')]
    return {c:int((Decimal(int(total))/len(estimate['months'])*Decimal('.9')).quantize(Decimal('1'))) for c,total in expenses.groupby('category').amount_cents.sum().items()}
