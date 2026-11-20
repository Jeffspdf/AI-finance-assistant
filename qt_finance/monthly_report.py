"""Monthly, read-only report queries with explicit row selection and evidence."""
import re

from finance.analytics import yuan
from finance.categories import category_options
from finance.evidence import calculation, count_records, effective_frame
from finance.qa import answer_question, month_shift
from finance.spending_presenter import basis_label
from .query_compiler import MONTH, clarify
from .query_errors import QueryFailure, add_error

NEGATION = r'除了|不包含|不包括|不含|排除|去掉|不算|除开|以外|之外'
CATEGORIES = sorted(category_options('支出'), key=len, reverse=True)
FIELDS = ('category', 'merchant', 'exclude_category', 'exclude_merchant')


def monthly_report(frame, text, previous, today, budgets):
    text = re.sub(r'\s+', '', text).strip('？?。')
    prior = (previous or {}).get('query_state', {})
    period = re.match(rf'^(?:那)?({MONTH}(?![-\d])|本月|这个月|上个月|上月)', text)
    scope = (previous or {}).get('scope', '')
    if period:
        token = period[1]
        scope = {'本月': month_shift(today,0), '这个月': month_shift(today,0),
                 '上月': month_shift(today,-1), '上个月': month_shift(today,-1)}.get(token,token)
        text = text[period.end():].removeprefix('份').removeprefix('的')
    filters = {}
    category = next((c for c in CATEGORIES if text.startswith(c)), None)
    if category:
        filters['category'] = category
        text = text[len(category):].removeprefix('里').removeprefix('的')
    mode = None
    list_question = text in ('交易明细有哪些','有哪些记录','交易明细','查看交易明细','把这几笔列给我','列出明细')
    if list_question:
        mode = 'list'
    elif text in ('钱主要花在哪些分类','钱主要花哪儿了','主要把钱花哪儿了','里我主要把钱花哪儿了','哪个分类花得最多','哪类花销最大','查看分类构成','消费去向'):
        mode = 'category_rank'
    elif text in ('哪家店最多','哪家店花得最多','哪个商户花销最大','哪个商户支出最高'):
        mode = 'merchant_rank'
    elif text in ('预算还剩多少','预算剩余多少') and not category:
        mode = 'month_budget'
    elif text in ('退款有多少','退款多少元','退款多少笔','退款几笔') and not category:
        mode = 'refund_summary'
    elif text in ('收入减去支出还剩多少','收入减支出剩多少','收入和支出各多少','收支各多少') and not category:
        mode = 'balance' if '减' in text else 'income_expense'
    elif text in (
        '占全部净支出多少', '占全部净支出多少比例', '占全部净支出比例多少',
        '占净支出多少', '占净支出比例多少', '占比多少', '占比是多少',
    ):
        mode = 'category_share'
    elif text in (
        '在支出分类里排第几', '在支出分类中排第几',
        '在支出分类里排名第几', '在支出分类中排名第几',
    ):
        mode = 'category_position'
    elif re.search(NEGATION,text):
        m = re.fullmatch(r'(?:除了|除开)(.+?)(?:以外|之外)?(?:还)?(?:花了多少|净支出多少)(?:钱)?',text)
        if not m:
            m = re.fullmatch(r'(?:花了多少|净支出多少)(?:钱)?[，,]?(?:不包含|不包括|不含|排除|去掉|不算)(.+)',text)
        if not m:
            return clarify('请明确一个要排除的分类或商户，以及要统计的月份。')
        target = m[1].removesuffix('以外').removesuffix('之外')
        if target == '这家':
            leaders = (previous or {}).get('merchant_leaders', [])
            if len(leaders) != 1:
                return clarify('请写出要排除的商户名称。')
            target = leaders[0]
        if target in CATEGORIES:
            filters['exclude_category'] = target
        elif target in set(frame.merchant):
            filters['exclude_merchant'] = target
        else:
            return clarify('没有找到唯一匹配的排除对象，请填写账本里的完整商户名或分类。')
        mode = 'expense'
    elif prior.get('report_mode') and period and text in ('呢','呢？'):
        mode = prior['report_mode']
        filters = {k:prior[k] for k in FIELDS if prior.get(k)}
    elif prior.get('exclude_merchant') or prior.get('exclude_category'):
        if re.match(r'^(只看|只查|这两个月|那么|那|换|改)', text):
            return clarify('这次追问仍涉及排除条件，请写明月份、要统计和要排除的对象。')
    if mode is None:
        return None
    if not re.fullmatch(MONTH, scope):
        return clarify(
            '请先指定要查询的月份，例如“十月”。',
            code=QueryFailure.MISSING_PERIOD,
        )
    if mode in ('category_share', 'category_position') and not category:
        return clarify(
            '请先明确要计算占比或排名的分类。',
            code=QueryFailure.UNSUPPORTED_CONDITION,
        )
    if not period and (list_question or mode in ('expense','merchant_rank')):
        filters = {**{k:prior[k] for k in FIELDS if prior.get(k)}, **filters}
    if any(filters.get(k) and filters[k] == filters.get('exclude_'+k) for k in ('category','merchant')):
        return clarify('同一个条件同时被选中和排除，请确认要保留哪一个。')
    if scope not in set(frame.attrs.get('source_months', ())) | set(frame.date.str[:7]):
        return add_error(
            dict(status='missing',parser='本地解析',answer=scope+' 没有已导入来源。',
                 evidence=[],scope=scope),
            QueryFailure.NO_DATA,
        )
    if mode == 'refund_summary':
        parts = [answer_question(frame,scope+q,today=today) for q in ('退款抵扣','待处理退款','待复核退款','已关联退款')]
        return dict(status='ok', parser='本地解析',scope=scope, answer='\n'.join(p['answer'] for p in parts),
                    evidence=parts[0]['evidence'],trace={'退款查询':[p['trace'] for p in parts]})
    tx = effective_frame(frame)
    tx = tx[tx.date.str.startswith(scope)]
    if mode not in ('balance','income_expense'):
        tx = tx[tx.kind == '支出']
    all_expense = tx.copy()
    for k in ('category','merchant'):
        if filters.get(k): tx = tx[tx[k] == filters[k]]
        if filters.get('exclude_'+k): tx = tx[tx[k] != filters['exclude_'+k]]
    amounts = []
    extras = []
    if mode == 'month_budget':
        limits = budgets.get(scope,{})
        if not limits:
            return add_error(
                dict(status='no_match',scope=scope,parser='本地解析',
                     answer=scope+' 尚未设置分类预算。',evidence=[]),
                QueryFailure.NO_MATCH,
            )
        tx = tx[tx.category.isin(limits)]
        limit = sum(limits.values())
        used = int(tx.amount_cents.sum())
        amounts = [('已设预算',limit),('对应净支出',used),('剩余',limit-used)]
        note = '计入已设预算的分类：'+'、'.join(sorted(limits))
        extras = [dict(type='Budget',id=scope+'/'+k,snapshot={'category':k,'amount_cents':v}) for k,v in limits.items()]
    elif mode in ('balance','income_expense'):
        income = int(tx.loc[tx.kind=='收入','amount_cents'].sum())
        spent = int(tx.loc[tx.kind=='支出','amount_cents'].sum())
        amounts = [('收入',income),('净支出',spent),('结余',income-spent)]
        note = '结余按收入减去净支出计算。'
    elif tx.empty:
        return add_error(
            dict(status='no_match',scope=scope,parser='本地解析',
                 answer=scope+' 没有符合条件的记录。',evidence=[]),
            QueryFailure.NO_MATCH,
        )
    elif mode in ('category_rank','merchant_rank'):
        key = 'category' if mode == 'category_rank' else 'merchant'
        grouped = tx.groupby(key,sort=True).amount_cents.sum().sort_values(ascending=False,kind='stable')
        amounts = [(str(k),int(v)) for k,v in grouped.head(3).items()]
        leaders = list(grouped[grouped == grouped.max()].index)
        note = '净支出最高：'+'、'.join(leaders)+'。按净额从高到低排列。'
    elif mode == 'category_share':
        total = int(all_expense.amount_cents.sum())
        cents = int(tx.amount_cents.sum())
        if total <= 0 or (all_expense.groupby('category').amount_cents.sum() < 0).any():
            return add_error(
                dict(status='no_match',scope=scope,parser='本地解析',
                     answer=f'{scope} 净额构成不适合计算分类占比。',evidence=[]),
                QueryFailure.NO_MATCH,
            )
        share = round(cents * 100 / total, 2)
        amounts = [(filters['category'], cents), ('总净支出', total)]
        note = f"{filters['category']}占总支出的 {share:.2f}%。"
    elif mode == 'category_position':
        grouped = all_expense.groupby('category').amount_cents.sum().sort_values(
            ascending=False, kind='stable')
        cents = int(grouped[filters['category']])
        position = 1 + int((grouped > cents).sum())
        tied = '并列' if int((grouped == cents).sum()) > 1 else ''
        amounts = [(f'{tied}第 {position} 位', cents)]
        note = f"{filters['category']}在支出分类中排{tied}第 {position} 位。"
    else:
        amounts = [('净支出',int(tx.amount_cents.sum()))]
        note = f'共 {count_records(tx)} 笔消费'
        for k,title in [('exclude_category','分类'),('exclude_merchant','商户')]:
            if filters.get(k): note += ' · 已排除'+title+'：'+filters[k]
    state = dict(
        period=scope,
        metric=('share' if mode == 'category_share' else
                'top' if mode == 'category_position' else
                'expense' if mode in ('expense','list','category_rank','merchant_rank') else mode),
        report_mode=mode,
        **filters,
    )
    rows = tx.sort_values('date',kind='stable').to_dict('records')
    result = dict(status='ok',parser='本地解析',scope=scope,query_state=state,basis=basis_label(frame.attrs['spending_basis']),
                  amounts=amounts,amount_note=note,answer='，'.join(f'{k} {yuan(v)} 元' for k,v in amounts)+'。\n'+note,
                  trace=calculation('answer','query.'+mode,all_expense if mode in ('category_share','category_position') else tx,state,extras),evidence=rows)
    if mode == 'category_rank':
        result['suggestions'] = [str(leaders[0])+'里哪家店最多？','查看交易明细']
    if mode == 'merchant_rank':
        result['merchant_leaders'] = leaders
        result['suggestions'] = ['除了这家还花了多少？'] if len(leaders)==1 else []
    if mode == 'expense':
        result['suggestions'] = ['把这几笔列给我']
    if mode == 'list':
        result['detail_rows'] = rows[:20]
        result['amount_note'] += f' · 显示 {min(len(rows),20)} / {len(rows)} 行（含退款抵扣）'
    return result
