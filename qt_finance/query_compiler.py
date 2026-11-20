"""Compile bounded spoken ledger queries before the legacy question parser."""
import calendar
import re
from finance.qa import month_shift, parse_question
from finance.categories import category_options
from .query_errors import QueryFailure, query_failure

MONTH = r'20\d{2}-(?:0[1-9]|1[0-2])'
PREFIX = r'^(?:那么|那个(?=月)|然后|再看看|再看|顺便|另外|换成|改看|那)'
ALIASES = {word: category for category, words in {
    '居住': '房租 租金 月供', '餐饮': '外卖 奶茶 吃饭 食堂', '交通': '打车 公交 地铁',
    '购物': '日用品 买东西', '教育': '学费 书 课程', '医疗': '看病 药',
    '生活缴费': '水电 话费 网费'}.items() for word in words.split()}


def clarify(message, choices=(), code=QueryFailure.UNSUPPORTED_CONDITION):
    return query_failure(code, message, choices=choices)


def compile_query(text, previous, months, today, merchants):
    """Return a verified intent or a clarification. Unmatched forms use existing parsers."""
    source = text.strip(' ？?。')
    body = re.sub(PREFIX, '', source, count=1).strip()
    prior = (previous or {}).get('query_state', {})
    scope = (previous or {}).get('scope', '')
    merchant = next((m for m in sorted(filter(None, merchants), key=len, reverse=True) if m in body), None)
    if merchant is None:
        explicit = re.search(r'在([\w·]{1,40}?)(?:上)?(?:一共|总共)?(?:花了|消费了)(?:多少|几)', body)
        if explicit:
            merchant = explicit[1]
    if merchant and body in ('只看'+merchant, '只查'+merchant):
        return None, None
    # Match merchants as whole literal spans before replacing category vocabulary.
    for word, category in ALIASES.items():
        if word in body and not (merchant and word in merchant and
                                 (word != merchant or '在'+merchant in body)):
            body = body.replace(word, category)
    if merchant and merchant in body:
        body = body.replace(merchant, '商户条件', 1)
    else:
        merchant = None
    body = re.sub(r'上上个月|前两个月', month_shift(today, -2), body)
    body = body.replace('前一个月', '上个月').replace('赚了', '收入').replace('进账', '收入').replace('到账', '收入')
    body = body.replace('剩余额度', '预算剩余')
    recent = re.match(r'(?:最近|近)(?:三|3)个?月', body)
    if recent and re.search(r'上个月|上月|本月|这个月',body):
        return None, clarify(
            '最近三个完整月和单月同时出现。请指定一个时间范围。',
            code=QueryFailure.AMBIGUOUS_PERIOD,
        )
    if recent:
        body = body[recent.end():]
    periods = re.findall(MONTH + r'(?![-\d])', body)
    between = re.search(rf'({MONTH})\s*(?:到|至|—|~)\s*({MONTH})(?![-\d])', body)
    comparison = re.search(rf'({MONTH})\s*(?:和|与|、|跟)\s*({MONTH})(?![-\d])', body)
    if periods and re.search(r'本月|上月|这个月|上个月', body):
        return None, clarify(
            '同时识别到明确月份和相对月份，请确认要查哪个月。',
            code=QueryFailure.AMBIGUOUS_PERIOD,
        )
    if len(periods) > 1 and not (len(periods) == 2 and (between or comparison)):
        return None, clarify(
            '请明确这两个月要合计还是比较。',
            code=QueryFailure.AMBIGUOUS_PERIOD,
        )
    extra = {}
    if between:
        start, end = between.groups()
        if start > end:
            return None, clarify(
                '起始月份晚于结束月份，请重新确认起止月份。',
                code=QueryFailure.AMBIGUOUS_PERIOD,
            )
        body = body.replace(between[0], '')
        year, month = map(int, end.split('-'))
        extra.update(date_from=start+'-01', date_to=f'{end}-{calendar.monthrange(year, month)[1]}')
    elif comparison:
        body = body.replace(comparison[0], '')
        if not re.search(r'差多少|对比|比较', body):
            return None, clarify(
                '要比较这两个月的差额，还是合计两个月的支出？',
                code=QueryFailure.AMBIGUOUS_PERIOD,
            )
        body = re.sub(r'相差多少|差多少|对比|比较', '支出多少', body)
        extra.update(compare_months=list(comparison.groups()))
    if merchant:
        body = re.sub(r'(?:我)?(?:在)?商户条件(?:上)?', '', body)
    body = re.sub(r'^我(?=20\d\d-)', '', body)
    average = '月均' in body
    body = body.replace('月均', '').replace('净支出', '支出').strip()
    if len([c for c in category_options('支出') if c in body]) > 1:
        return None, None
    if re.search(r'只(?:看|查)(?:' + '|'.join(category_options('支出')) + r')$', source):
        return None, None
    parsed = parse_question(body)
    if not parsed:
        if between or comparison:
            return None, clarify(
                '请明确两个月份共用的统计指标、分类或商户。',
                code=QueryFailure.UNSUPPORTED_CONDITION,
            )
        return None, None
    if parsed.get('category') in category_options('支出') and parsed['metric'] == 'income':
        return None, clarify('收入和支出分类同时出现。你想查收入，还是这个分类的支出？')
    if not (extra or recent or merchant or body != source):
        return None, None
    if recent:
        if parsed['period'] != 'all' or parsed.get('date_from') or extra:
            return None, clarify('检测到多个时间范围，请选择其中一个。')
        parsed['period'] = 'last_3_complete_months'
    if average:
        if not recent or parsed['metric'] not in ('expense','income'):
            return None, clarify('月均查询请指定最近三个完整月的收入或支出。')
        parsed['metric'] = 'average_' + parsed['metric']
    if comparison:
        if parsed['metric'] != 'expense' or '消费总额' in source:
            return None, clarify('两月差额目前支持同一分类、商户下的净支出。')
        if periods[0] == periods[1]:
            return None, clarify('比较需要两个不同的月份。')
        if not set(periods) <= months:
            return None, clarify('缺少可比较的月份，请分别查询两个单月并核对账单覆盖。')
        parsed['period'] = periods[-1]
    if merchant:
        parsed['merchant'] = merchant
    follow = bool(re.match(PREFIX, source))
    if parsed['period'] == 'all' and not extra and (merchant or follow) and not parsed.get('date_from'):
        if re.fullmatch(MONTH, scope):
            parsed['period'] = scope
        else:
            return None, clarify(
                '要查询哪个月？',
                [(m, m+' '+source) for m in sorted(months, reverse=True)],
                code=QueryFailure.MISSING_PERIOD,
            )
    if follow and prior.get('amount_basis') == 'gross' and parsed['metric'] == 'expense' and '净支出' not in source and '消费总额' not in source:
        return None, clarify('上一问使用消费总额，请明确这次使用净支出还是消费总额。')
    if follow and prior and parsed['metric'] == prior.get('metric'):
        for key in ('category','merchant'):
            if not parsed.get(key) and prior.get(key):
                parsed[key] = prior[key]
    return dict(parsed, **extra), None
