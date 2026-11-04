from datetime import date
import re
import pandas as pd
from .categories import category_in_question
from .analytics import yuan
from .categories import CATEGORIES
from .evidence import effective_frame,calculation,count_records

def month_shift(today, offset):
    index=today.year*12+today.month-1+offset
    y,m=divmod(index,12)
    return f'{y:04d}-{m+1:02d}'

def parse_question(question):
    if not question.strip() or len(question)>500: return None
    unsupported=['投资','股票','基金','收益率','明年','下个月','预测','贷款','房价','信用','昨天','今天','上周','本周','去年','今年','退款']
    if any(w in question for w in unsupported): return None
    # Day-level ranges, merchants and category comparisons need extra parsing.
    if re.search(r'\d{1,2}[日号]|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d+月|到|至|之间',question): return None
    if '上个月' in question or '上月' in question: period='last_month'
    elif '这个月' in question or '本月' in question: period='this_month'
    else:
        match=re.search(r'20\d{2}-(?:0[1-9]|1[0-2])(?!\d)',question)
        period=match.group(0) if match else 'all'
    category=category_in_question(question)
    if any(w in question for w in ['预算','超支']): metric='budget'
    elif any(w in question for w in ['占比','比例']): metric='share'
    elif any(w in question for w in ['最多','最高','最大']): metric='top'
    elif any(w in question for w in ['几笔','多少笔','笔数']): metric='count'
    elif any(w in question for w in ['结余','净收入','收支差']): metric='balance'
    elif any(w in question for w in ['收入','工资','生活费收入']): metric='income'
    elif any(w in question for w in ['支出','花了','花费','消费','开销','多少钱']): metric='expense'
    else: return None
    # Require known wording and reject unparsed qualifiers instead of answering a broader question.
    scrub=question
    tokens=['上个月','上月','这个月','本月','全部','所有','累计','总共','总','我的','我','请问','请','记录','一共','的','是','有','了','在','多少','几笔','笔数','多少笔','支出','花费','花了','消费','开销','多少钱','收入','工资','生活费','结余','净收入','收支差','预算','超支','占比','比例','最多','最高','最大','哪个','哪一类','分类','类别','餐饮','吃饭','饮食','伙食','交通','购物','居住','生活缴费','教育','医疗','娱乐','其他收入','其他支出','转账','金额','使用','情况','用了','还剩','剩余','目前','？','?','。',' ','，',',']
    for token in sorted(tokens,key=len,reverse=True):
        scrub=scrub.replace(token,'')
    scrub=re.sub(r'20\d{2}-(0[1-9]|1[0-2])','',scrub)
    if scrub.strip(): return None
    return {'metric':metric,'category':category,'period':period}

def validate_model_intent(question, intent):
    """Validate executable fields and retain explicit user constraints."""
    expanded=question
    for old,new in [('上一个月','上个月'),('上一月','上月'),('当前月份','本月'),('总花销','支出'),('花销','支出'),('合计','总共'),('支用了','花了'),('余额变化','结余')]:
        expanded=expanded.replace(old,new)
    if not isinstance(intent,dict) or not question.strip() or len(question)>500:return None
    if any(w in question for w in ['预测','投资','股票','基金','收益率','下个月','明年','退款','贷款','房价','信用','昨天','今天','上周','本周','去年','今年']):return None
    allowed={'metric','category','period','merchant','date_from','date_to','compare_categories'}
    if set(intent)-allowed:return None
    metric=intent.get('metric');category=intent.get('category');period=intent.get('period')
    if metric not in ['expense','income','balance','count','top','share','budget','compare','average_expense','average_income']:return None
    if category not in [None,*CATEGORIES] or not isinstance(period,str):return None
    if period not in ['this_month','last_month','all','last_3_complete_months'] and not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',period):return None
    merchant=intent.get('merchant')
    if merchant is not None and (not isinstance(merchant,str) or not 1<=len(merchant.strip())<=50 or merchant not in question):return None
    if bool(intent.get('date_from'))!=bool(intent.get('date_to')):return None
    if intent.get('date_from'):
        try:
            start=date.fromisoformat(intent['date_from']);end=date.fromisoformat(intent['date_to'])
            if not 2000<=start.year<=end.year<=2100 or start>end:return None
        except (TypeError,ValueError):return None
        if period!='all':return None
    explicit_dates=re.findall(r'20\d{2}-\d{2}-\d{2}',question)
    if explicit_dates and (len(explicit_dates)!=2 or intent.get('date_from')!=explicit_dates[0] or intent.get('date_to')!=explicit_dates[1]):return None
    if intent.get('date_from') and not explicit_dates:return None
    explicit_months=re.findall(r'20\d{2}-(?:0[1-9]|1[0-2])(?![-\d])',question)
    if explicit_months and not explicit_dates and (len(set(explicit_months))!=1 or period!=explicit_months[0]):return None
    if ('上个月' in expanded or '上月' in expanded) and period!='last_month':return None
    if ('本月' in expanded or '这个月' in expanded) and period!='this_month':return None
    if '近三个月' in question or '最近三个月' in question:
        if period!='last_3_complete_months':return None
    if period=='last_3_complete_months' and not any(w in question for w in ['近三个月','最近三个月']):return None
    merchant_match=re.search(r'在(.+?)(?:花|消费|支出|餐饮|的)',question)
    if merchant_match and intent.get('merchant')!=merchant_match.group(1):return None
    categories=[c for c in CATEGORIES if c in question]
    compare=intent.get('compare_categories')
    if compare is not None:
        if not isinstance(compare,list) or len(compare)!=2 or len(set(compare))!=2 or any(c not in CATEGORIES or c not in question for c in compare):return None
        if metric!='compare' or category is not None:return None
    if metric=='compare' and not compare:return None
    if len(categories)>1 and (not compare or set(compare)!=set(categories)):return None
    if len(categories)==1 and not compare and category!=categories[0]:return None
    expected=parse_question(expanded)
    if expected is not None and any(intent.get(k)!=expected[k] for k in ['metric','category','period']):return None
    if metric=='budget' and (merchant or explicit_dates or compare):return None
    return intent

def _refund_answer(frame,question,today):
    """Bounded local refund queries; amounts never come from a model."""
    if frame.attrs.get('projection')!='spending_view':return None
    matched=re.fullmatch(r'(本月|这个月|上个月|上月|20\d{2}-(?:0[1-9]|1[0-2])|全部|累计)?(已确认退款抵扣|退款抵扣|退款待处理|待处理退款|退款待复核|待复核退款|退款已关联|已关联退款)(多少元|多少笔|几笔|笔数|多少|金额)?[？?。]?',re.sub(r'\s+','',question))
    if not matched:return None
    period,metric,quantity=matched.groups()
    month=month_shift(today,0 if period in ('本月','这个月') else -1) if period in ('本月','这个月','上月','上个月') else period
    all_months=month in (None,'全部','累计');scope='全部已导入记录' if all_months else month
    if not all_months and month not in frame.attrs.get('source_months',[]):
        return dict(answer=scope+'没有已导入来源，无法可靠给出退款金额。',evidence=[],status='missing',parser='本地退款查询')
    from .spending_presenter import basis_label
    adjustments=frame[frame.projection_kind=='refund_adjustment']
    if not all_months:adjustments=adjustments[adjustments.date.str.startswith(month)]
    extra=[];kind='confirmed_adjustment' if '抵扣' in metric else 'source_refund'
    if kind=='confirmed_adjustment':
        amount=-int(adjustments.amount_cents.sum());count=len(adjustments)
        text=f'{scope}按当前口径已确认退款抵扣 {yuan(amount)} 元，{count} 笔。退款去重后只抵扣一次。'
    else:
        state='needs_review' if '复核' in metric else 'pending' if '处理' in metric else 'linked'
        events=[e for e in frame.attrs['refund_sources'] if e['association_status']==state and (all_months or e['date'].startswith(month))]
        amount=sum(e['amount_cents'] for e in events);count=len(events)
        title={'needs_review':'待复核退款','pending':'待处理退款','linked':'已关联退款'}[state]
        text=f'{scope}按实际退款发生月，{title} {count} 笔，完整退款金额 {yuan(amount)} 元。流水发生月可能与净额抵扣月份不同。'
        extra=[dict(type='RefundSourceStatus',id=e['representative_id'],snapshot=e) for e in events]
        adjustments=frame.iloc[:0].copy()
    if not extra and adjustments.empty:
        extra=[dict(type='SourceCoverage',id=scope,snapshot=dict(source_months=frame.attrs['source_months'],scope='仅已导入来源；未导入数据未知'))]
    trace=calculation('answer','query.refund',adjustments,inputs=dict(period=period,metric=metric,amount_basis=kind),extra=extra)
    text+=' '+basis_label(frame.attrs['spending_basis'])+'。不代表完整账单或账户余额。'
    return dict(answer=text,evidence=adjustments[['id','external_id','date','amount_cents','kind','category','merchant','source_file','source_row']].to_dict('records'),
                trace=trace,status='ok',parser='本地退款查询')


def answer_question(frame, question, today=None, budgets=None, model=None):
    frame=effective_frame(frame)
    refund_answer=_refund_answer(frame,question,today or date.today())
    if refund_answer is not None:return refund_answer
    gross_requested='消费总额' in question
    question=question.replace('消费总额','支出').replace('净支出','支出')
    if gross_requested and frame.attrs.get('projection')=='spending_view':
        frame=frame[frame.projection_kind!='refund_adjustment'].copy()
    today=today or date.today(); budgets=budgets or {}
    intent=parse_question(question)
    source='本地解析'
    if intent is None and model is not None:
        # Hard boundaries are shared by local and model paths.
        if any(w in question for w in ['预测','投资','股票','下个月','明年','退款']):
            return {'answer':'这个问题不能由已导入的交易记录可靠回答。请询问某月的支出、收入、分类占比或预算。','evidence':[],'status':'unsupported','parser':source}
        try:
            intent=validate_model_intent(question,model.parse_question(question)); source='模型理解 + 代码计算'
            if intent is not None:
                mentioned=[m for m in frame.merchant.unique() if m and m in question]
                if mentioned and not intent.get('merchant'):intent=None
        except Exception:
            source='模型不可用，回退本地解析'
    if intent is None:
        return {'answer':'目前无法可靠理解这个问题或条件不足。请明确月份与分类；不推断未提供交易或未来收益。可改问：“上个月餐饮花了多少？”或“本月结余是多少？”。模型启用后另支持明确商户、日期区间和两个分类比较。','evidence':[],'status':'unsupported','parser':source}
    period=intent['period']; metric=intent['metric']; category=intent.get('category')
    month=month_shift(today,0 if period=='this_month' else -1) if period in ['this_month','last_month'] else period
    if period=='last_3_complete_months':
        months=[month_shift(today,-i) for i in [3,2,1]]
        tx=frame[frame.date.str[:7].isin(months)].copy()
    else:tx=frame.copy() if month=='all' else frame[frame.date.str.startswith(month)].copy()
    label='全部已导入记录' if month=='all' else month+' 已导入记录'
    if period=='last_3_complete_months':label='最近三个完整月份（'+ '、'.join(months)+'）'
    if intent.get('date_from'):
        tx=tx[(tx.date.str[:10]>=intent['date_from'])&(tx.date.str[:10]<=intent['date_to'])]
        label=intent['date_from']+' 至 '+intent['date_to']+' 已导入记录'
    if intent.get('merchant'):
        tx=tx[tx.merchant.str.contains(intent['merchant'],regex=False,case=False)]
        label+='，商户包含“'+intent['merchant']+'”'
    full=tx.copy()
    known_net_scope=(frame.attrs.get('projection')=='spending_view' and not intent.get('merchant') and
        not intent.get('date_from') and (month in frame.attrs.get('source_months',[]) or month=='all' and bool(frame.attrs.get('source_months'))))
    if tx.empty and not known_net_scope:
        return {'answer':f'没有 {label}，无法给出金额。请先导入对应账单；没有记录不代表没有消费。','evidence':[],'status':'missing','parser':source}
    if metric in ['expense','share','top','budget','compare','average_expense']: tx=tx[tx.kind=='支出']
    elif metric in ['income','average_income']: tx=tx[tx.kind=='收入']
    elif metric=='balance': tx=tx[tx.kind!='转账']
    if category: tx=tx[tx.category==category]
    if tx.empty and not known_net_scope:
        return {'answer':f'{label}中没有符合条件的{category or "相关"}记录，无法提供有源记录支持的金额。','evidence':[],'status':'missing','parser':source}
    total=int(tx.amount_cents.sum()); item=category or ''
    if tx.empty and metric in ('top','compare','average_expense','average_income'):
        return dict(answer='该口径下没有足够计入记录进行排名、比较或均值计算。',evidence=[],status='missing',parser=source)
    if metric=='compare':
        names=intent['compare_categories'];tx=tx[tx.category.isin(names)]
        if tx.empty:return {'answer':'所比较分类没有可追溯的记录，无法提供金额，请先导入对应账单。','evidence':[],'status':'missing','parser':source}
        totals={c:int(tx.loc[tx.category==c,'amount_cents'].sum()) for c in names}
        answer=f'{label}：'+ '；'.join(c+' '+yuan(totals[c])+' 元' for c in names)+f'。差额（{names[0]} − {names[1]}）为 {yuan(totals[names[0]]-totals[names[1]])} 元。计算方式：分别逐笔汇总再相减；零值仅代表已导入匹配记录的汇总。'
    elif metric in ['average_expense','average_income']:
        if period!='last_3_complete_months':return {'answer':'平均问题请指定“近三个月”。例如“近三个月月均支出是多少？”','evidence':[],'status':'unsupported','parser':source}
        observed=sorted(full.date.str[:7].unique());n=len(observed)
        missing=[m for m in months if m not in observed]
        answer=f'{label}的月均'+('支出' if metric=='average_expense' else '收入')+f'为 {total/100/n:,.2f} 元。计算式：{yuan(total)} 元 ÷ {n} 个有记录月份。缺失月份：'+('、'.join(missing) if missing else '无')+'。缺失月不视为零消费。'
    elif metric in ['expense','income']:
        description=('消费总额（未扣退款）' if gross_requested else '净支出' if frame.attrs.get('projection')=='spending_view' else '支出') if metric=='expense' else '收入'
        answer=f'{label}的{item}{description}合计 {yuan(total)} 元，共 {count_records(tx)} 笔。计算方式：逐笔金额相加。'
    elif metric=='balance':
        income=int(tx.loc[tx.kind=='收入','amount_cents'].sum()); expense=int(tx.loc[tx.kind=='支出','amount_cents'].sum())
        answer=f'{label}的{item}结余为 {yuan(income-expense)} 元：收入 {yuan(income)} 元减支出 {yuan(expense)} 元。转账不计入收支。'
    elif metric=='count': answer=f'{label}的{item}交易共 {count_records(tx)} 笔。计算式：匹配记录逐笔计数。'
    elif metric=='top':
        grouped=tx.groupby('category').amount_cents.sum(); winners=grouped[grouped==grouped.max()]
        names='、'.join(winners.index)
        answer=f'{label}中支出最高的分类为 {names}，每类 {yuan(int(grouped.max()))} 元。计算方式：按分类汇总并比较。'
        # Include all compared expense rows so the ranking can be verified.
    elif metric=='share':
        if not category:
            return {'answer':'请指定分类，例如“上个月餐饮占比是多少？”','evidence':[],'status':'unsupported','parser':source}
        all_expenses=full[full.kind=='支出']; denominator=int(all_expenses.amount_cents.sum())
        answer=(f'{label}的{category}占比不适用：总支出为 {yuan(denominator)} 元，分母不大于零。' if denominator<=0 else
                f'{label}的{category}占总支出的 {total/denominator*100:.2f}%：{yuan(total)} 元 ÷ {yuan(denominator)} 元 × 100%。')
        tx=all_expenses  # Both numerator and denominator need evidence.
    else:
        if month=='all' or not category:
            return {'answer':'预算问题请指定月份和分类，例如“本月餐饮超支多少？”','evidence':[],'status':'unsupported','parser':source}
        budget=budgets.get(month,{}).get(category)
        if budget is None:
            return {'answer':f'没有设置 {month} 的{category}预算，请先设置。','evidence':[],'status':'missing','parser':source}
        over=max(0,total-budget)
        answer=f'{label}的{category}支出 {yuan(total)} 元，已设置预算 {yuan(budget)} 元，超支 {yuan(over)} 元，剩余 {yuan(budget-total)} 元。预算是用户设置值，支出是源记录汇总值。'
    answer+=' 数据范围：'+label+'。假设：导入记录与人工修正有效。缺失信息：无法识别未导入交易，不能代表完整账户余额。下方列出全部计算依据。'
    evidence=tx[['id','external_id','date','amount_cents','kind','category','merchant','source_file','source_row']].to_dict('records')
    extra=[]
    if metric=='budget':extra=[dict(type='Budget',id=month+'/'+category,snapshot=dict(month=month,category=category,amount_cents=budget))]
    if tx.empty and known_net_scope:
        extra.extend(dict(type='ExcludedSource',id=r['id'],snapshot=r) for r in frame.attrs.get('excluded_sources',[]) if month=='all' or r['date'].startswith(month))
        for d in frame.attrs.get('refund_decisions',[]):
            representative=next(r for r in d['refund']['members'] if r['id']==d['refund']['representative_id'])
            if month=='all' or representative['date'].startswith(month):extra.append(dict(type='RefundDecision',id=d['id'],snapshot=d))
        answer+=' 此口径本期无计入明细；退款可能归入原消费月，零值不代表完整账单或余额。'
    trace=calculation('answer','query.'+metric,tx,inputs=dict(intent,amount_basis='gross' if gross_requested else 'net' if frame.attrs.get('projection')=='spending_view' else 'gross'),extra=extra)
    if trace['evidence'] and any(e['type']=='LinkDecision' for e in trace['evidence']):
        answer+=' 已按人工确认的关联组排除重复资金腿；关联决定可在对账中撤销。'
    refund=frame.attrs.get('refund_pending',{})
    if frame.attrs.get('projection')=='spending_view':
        from .spending_presenter import basis_label,refund_status_summary,refund_status_text
        answer+=' 当前空间口径：'+basis_label(frame.attrs['spending_basis'])+'。'
        events=frame.attrs.get('refund_sources',[])
        if period=='last_3_complete_months':events=[r for r in events if r['date'][:7] in months]
        if intent.get('date_from'):events=[r for r in events if intent['date_from']<=r['date'][:10]<=intent['date_to']]
        summary=refund_status_summary(events,None if month=='all' else month if month in frame.attrs.get('source_months',[]) else None)
        answer+=' 退款流水状态（按实际发生月，可能与抵扣月份不同）：'+refund_status_text(summary)+'。'
    elif refund.get('count'):
        answer+=f' 退款仍待处理（{refund["count"]} 笔），当前支出未扣退款，净支出待定。'
    return {'answer':answer,'evidence':evidence,'trace':trace,'status':'ok','parser':source}
