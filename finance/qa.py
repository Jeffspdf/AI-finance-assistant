from datetime import date
import re
import pandas as pd
from .categories import category_in_question
from .analytics import yuan

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
    """Never let an unsupported qualifier silently disappear in model parsing."""
    expanded=question
    for old,new in [('上一个月','上个月'),('上一月','上月'),('当前月份','本月'),('总花销','支出'),('花销','支出'),('合计','总共'),('支用了','花了'),('余额变化','结余')]:
        expanded=expanded.replace(old,new)
    expected=parse_question(expanded)
    if expected is None or intent is None: return None
    return intent if all(intent.get(k)==expected[k] for k in ['metric','category','period']) else None

def answer_question(frame, question, today=None, budgets=None, model=None):
    today=today or date.today(); budgets=budgets or {}
    intent=parse_question(question)
    source='本地解析'
    if intent is None and model is not None:
        # Hard boundaries are shared by local and model paths.
        if any(w in question for w in ['预测','投资','股票','下个月','明年','退款']):
            return {'answer':'这个问题不能由已导入的交易记录可靠回答。请询问某月的支出、收入、分类占比或预算。','evidence':[],'status':'unsupported','parser':source}
        try:
            intent=validate_model_intent(question,model.parse_question(question)); source='模型理解 + 代码计算'
        except Exception:
            source='模型不可用，回退本地解析'
    if intent is None:
        return {'answer':'目前无法可靠理解这个问题。支持本月、上个月或 YYYY-MM 的收入、支出、结余、笔数、最高支出分类、分类占比和预算。请明确月份与分类；不推断未提供的交易或未来收益。','evidence':[],'status':'unsupported','parser':source}
    period=intent['period']; metric=intent['metric']; category=intent.get('category')
    month=month_shift(today,0 if period=='this_month' else -1) if period in ['this_month','last_month'] else period
    tx=frame.copy() if month=='all' else frame[frame.date.str.startswith(month)].copy()
    label='全部已导入记录' if month=='all' else month+' 已导入记录'
    full=tx.copy()
    if tx.empty:
        return {'answer':f'没有 {label}，无法给出金额。请先导入对应账单；没有记录不代表没有消费。','evidence':[],'status':'missing','parser':source}
    if metric in ['expense','share','top','budget']: tx=tx[tx.kind=='支出']
    elif metric=='income': tx=tx[tx.kind=='收入']
    elif metric=='balance': tx=tx[tx.kind!='转账']
    if category: tx=tx[tx.category==category]
    if tx.empty:
        return {'answer':f'{label}中没有符合条件的{category or "相关"}记录，无法提供有源记录支持的金额。','evidence':[],'status':'missing','parser':source}
    total=int(tx.amount_cents.sum()); item=category or ''
    if metric in ['expense','income']:
        description='支出' if metric=='expense' else '收入'
        answer=f'{label}的{item}{description}合计 {yuan(total)} 元，共 {len(tx)} 笔。计算方式：逐笔金额相加。'
    elif metric=='balance':
        income=int(tx.loc[tx.kind=='收入','amount_cents'].sum()); expense=int(tx.loc[tx.kind=='支出','amount_cents'].sum())
        answer=f'{label}的{item}结余为 {yuan(income-expense)} 元：收入 {yuan(income)} 元减支出 {yuan(expense)} 元。转账不计入收支。'
    elif metric=='count': answer=f'{label}的{item}交易共 {len(tx)} 笔。'
    elif metric=='top':
        grouped=tx.groupby('category').amount_cents.sum(); winners=grouped[grouped==grouped.max()]
        names='、'.join(winners.index)
        answer=f'{label}中支出最高的分类为 {names}，每类 {yuan(int(grouped.max()))} 元。计算方式：按分类汇总并比较。'
        # Include all compared expense rows so the ranking can be verified.
    elif metric=='share':
        if not category:
            return {'answer':'请指定分类，例如“上个月餐饮占比是多少？”','evidence':[],'status':'unsupported','parser':source}
        all_expenses=full[full.kind=='支出']; denominator=int(all_expenses.amount_cents.sum())
        answer=f'{label}的{category}占总支出的 {total/denominator*100:.2f}%：{yuan(total)} 元 ÷ {yuan(denominator)} 元 × 100%。'
        tx=all_expenses  # Both numerator and denominator need evidence.
    else:
        if month=='all' or not category:
            return {'answer':'预算问题请指定月份和分类，例如“本月餐饮超支多少？”','evidence':[],'status':'unsupported','parser':source}
        budget=budgets.get(month,{}).get(category)
        if budget is None:
            return {'answer':f'没有设置 {month} 的{category}预算，请先设置。','evidence':[],'status':'missing','parser':source}
        over=max(0,total-budget)
        answer=f'{label}的{category}支出 {yuan(total)} 元，已设置预算 {yuan(budget)} 元，超支 {yuan(over)} 元，剩余 {yuan(budget-total)} 元。预算是用户设置值，支出是源记录汇总值。'
    answer+=' 仅覆盖导入记录，不能代表未导入的交易。下方列出全部计算依据。'
    evidence=tx[['id','external_id','date','amount_cents','kind','category','merchant','source_file','source_row']].to_dict('records')
    return {'answer':answer,'evidence':evidence,'status':'ok','parser':source}
