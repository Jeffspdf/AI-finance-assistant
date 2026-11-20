"""Adapters between Qt forms and the existing ledger and savings APIs."""

from datetime import date, datetime
from functools import wraps
from pathlib import Path
import re

from finance.agent_runtime import plan_savings_agent
from finance.agent_tools import confirmed_inputs
from finance.analytics import yuan
from finance.categories import category_in_question, category_options
from finance.demo import initialize_demo
from finance.importer import money_to_cents, parse_statement
from finance.qa import answer_question, month_shift, parse_question
from finance.storage import Store
from finance.spending_presenter import basis_label
from .query_compiler import MONTH, compile_query, clarify
from .monthly_report import monthly_report
from .monthly_savings import monthly_savings
from .query_context import normalize_months, change_contributions, month_only_followup
from .query_errors import QueryFailure, classify_query_error, query_failure


MONTH_NUMBERS = {name: number for number, name in enumerate('一 二 三 四 五 六 七 八 九 十 十一 十二'.split(), 1)}
MONTH_TOKEN = r'(?:1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])'
YEAR_TOKEN = r'(?:20[0-9]{2}|二[〇零][〇零一二三四五六七八九]{2})'
YEAR_DIGITS = str.maketrans('〇零一二三四五六七八九', '00123456789')
DINING_TOTAL = re.compile(
    r'(?:我想知道|请问|把)?\s*(?P<period>20[0-9]{2}-[0-9]{2}|' + MONTH_TOKEN + r'\s*月(?:份)?)\s*'
    r'(?:的)?\s*(?:(?:我)?花在吃饭上的钱累计是多少|吃饭一共花掉多少钱|吃饭相关的小票加在一起[，, ]*总共花了多少钱)'
    r'[，,。？? ]*(?:(?:只告诉我金额|只要数字)[。？? ]*)?'
)
DINING_COLLOQUIAL = re.compile(
    r'(?:请问|帮我算算|帮我算一下|我想知道)?\s*'
    r'(?P<period>20[0-9]{2}-(?:0[1-9]|1[0-2]))\s*(?:里|份|的)?[，,\s]*'
    r'我?\s*(?:为了|在)?\s*(?:吃吃喝喝|吃饭喝饮料|吃喝|吃饭|餐饮)(?:上|这方面)?\s*'
    r'(?:一共|总共|总计|累计)?\s*(?:掏了|花了|花掉|花去|支出)\s*多少(?:钱|元)?[。？?！!\s]*'
)
DINING_QUICK = re.compile(
    r'(?P<period>20[0-9]{2}-(?:0[1-9]|1[0-2])|本月|这个月|这月|上个月|上月)\s*'
    r'(?:的)?\s*(?:吃饭|餐饮)(?:大概|约|大约)?\s*多少(?:钱|元)?[。？?！!\s]*'
)


def month_number(token):
    return MONTH_NUMBERS[token] if token in MONTH_NUMBERS else int(token)


class Workspace:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mode = 'demo'
        self.open('demo')

    def open(self, mode):
        if mode not in ('demo', 'personal'):
            raise ValueError('未知账本空间')
        store = Store(self.directory / f'{mode}.sqlite3')
        if mode == 'demo':
            initialize_demo(store)
        self.mode, self.store = mode, store


def classified_query(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return classify_query_error(function(*args, **kwargs))
    return wrapper


def parse_ledger_turn(text, previous, months, today):
    """Normalize a spoken ledger turn without reading or writing the ledger."""
    normalized = re.sub(
        rf'(?<!\d)({YEAR_TOKEN})\s*年\s*({MONTH_TOKEN})\s*月(?:份)?'
        r'(?!\s*(?:\d|[一二三四五六七八九十]+\s*[日号]))',
        lambda match: f'{match[1].translate(YEAR_DIGITS)}-{month_number(match[2]):02}',
        text,
    )
    normalized, temporal_error = normalize_months(
        normalized, months, previous, month_number, MONTH_TOKEN,
    )
    month_patch = month_only_followup(normalized) if previous else None
    return dict(
        question=text,
        previous=previous,
        months=months,
        today=today,
        normalized=normalized,
        temporal_error=temporal_error,
        month_patch=month_patch,
        report_text=f"那{month_patch['period']}呢" if month_patch else normalized,
        explicit_months=set(re.findall(r'20\d{2}-(?:0[1-9]|1[0-2])(?![-\d])', normalized)),
    )


def validate_ledger_turn(plan, frame, budgets, months):
    """Apply local boundaries and resolve only deterministic period ambiguity."""
    question = plan['question']
    normalized = plan['normalized']
    previous = plan['previous']
    if re.fullmatch(r'地铁公交七七八八加起来多少[？?。！!]*', question.strip()):
        response = query_failure(
            QueryFailure.MISSING_PERIOD,
            '你想算哪个月的地铁公交支出？它们会按交通分类统计。',
        )
    elif re.search(r'删除|记一笔|帮我记|新增交易|修改预算|写入|调高预算|撤销导入', question):
        response = clarify(
            '当前对话只查询账单。记账或修改请前往相应页面。',
            code=QueryFailure.UNSUPPORTED_CONDITION,
        )
    elif re.search(r'(?:那|这)(?:家店|家商户|个商户|家店铺)', question):
        known = (previous or {}).get('query_state', {})
        scope = (previous or {}).get('scope', '')
        candidates = sorted({
            row['merchant'] for row in (previous or {}).get('evidence', []) if row.get('merchant')
        })[:3]
        context = ' '.join(filter(None, (scope, known.get('category'))))
        response = query_failure(
            QueryFailure.UNSUPPORTED_CONDITION,
            (f'已保留 {context}。' if context else '') + '你指哪家商户？请补充店名。',
            choices=[(name, f'只看{name}') for name in candidates] if re.fullmatch(MONTH, scope) else (),
            clarify=dict(period=scope if re.fullmatch(MONTH, scope) else '',
                         category=known.get('category', ''), merchant='', metric=known.get('metric', 'expense')),
        )
        response['awaiting_merchant'] = bool(previous)
    elif re.fullmatch(r'(?:那)?(?:比之前|剩下的|那个)呢?[？?。]*', question):
        response = clarify('你想和哪个月比较？' if '之前' in question else '你指哪个分类或商户？')
    elif re.search(r'平均一天|日均', question) and not re.search(r'月|\d{4}-\d{2}|这周|上周', question):
        response = clarify('你想算哪个月或哪段日期的日均支出？', code=QueryFailure.MISSING_PERIOD)
    elif plan['temporal_error']:
        temporal_error = plan['temporal_error']
        choices = re.findall(r'20\d{2}-\d{2}', temporal_error)
        if choices:
            pattern = rf'({MONTH_TOKEN})\s*月(?:份)?'
            options = []
            for month in choices:
                candidate = re.sub(
                    pattern,
                    lambda match: month if month_number(match[1]) == int(month[-2:]) else match[0],
                    normalized,
                )
                bounds = re.search(rf'({MONTH})\s*(?:到|至)\s*({MONTH})', candidate)
                if not bounds or bounds[1] <= bounds[2]:
                    options.append((month, candidate))
            response = clarify(temporal_error, options, code=QueryFailure.AMBIGUOUS_PERIOD)
        elif re.search(r'比较|哪个月|对比', question):
            response = clarify(
                temporal_error + ' 请分别查询两个单月并核对账单覆盖。',
                code=QueryFailure.AMBIGUOUS_PERIOD,
            )
        else:
            response = clarify(temporal_error, code=QueryFailure.NO_DATA)
    elif ('吃喝玩乐' in normalized
          and re.search(r'20\d{2}-\d{2}|本月|这个月|上个月|上月', normalized)):
        response = clarify(
            '“吃喝玩乐”可能跨餐饮、娱乐等多个分类。请指定要统计的分类，'
            '例如“餐饮”或“娱乐”；当前不会合并成单一分类。',
            code=QueryFailure.UNSUPPORTED_CONDITION,
        )
    else:
        response = None
    return dict(plan, response=response)


def run_ledger_turn(frame, plan, budgets, client, cancelled):
    """Execute a validated local query and return its raw calculation."""
    question = plan['question']
    normalized = plan['normalized']
    previous = plan['previous']
    months = plan['months']
    today = plan['today']
    report = monthly_report(frame, plan['report_text'], previous, today, budgets)
    if report is not None:
        report['calculated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
        if report.get('status') == 'ok':
            report['query_text'] = question
        return dict(result=report, final=True)
    compiled, prompt = ((None, None) if plan['month_patch'] else
                        compile_query(normalized, previous, months, today, frame.merchant.unique()))
    if prompt:
        return dict(result=prompt, final=True)
    follow_up, model_follow_up = (
        (compiled, False) if compiled else
        follow_up_query(normalized, previous, today, client, cancelled, original_question=question)
    )
    if cancelled.is_set():
        return None
    if follow_up and follow_up.get('clarify_comparison'):
        return dict(result=dict(
            answer='请先查一个月的净支出，再说“那九月呢”切换到另一个月，就能比较这两个月。',
            status='unsupported', parser='本地解析', evidence=[],
        ), final=True)
    if follow_up and follow_up.get('clarify_takeout'):
        return dict(result=query_failure(
            QueryFailure.UNSUPPORTED_CONDITION,
            f'已保留 {previous["scope"]}。外卖是指某个商户，还是想查这一月的餐饮合计？',
            clarify=dict(period=previous['scope'], category='餐饮', merchant='', metric='expense'),
        ), final=True)
    if not follow_up and re.match(
            r'^(?:那|只看|只查|这两个月|这2个月|换|改看|仅看|只想看|把这)', normalized):
        return dict(result=dict(
            answer='请明确查询月份和条件，例如“2026-09 餐饮净支出多少？”。'
                   '如果要延续查询，请先查一个明确的月份。',
            status='unsupported', parser='本地解析', evidence=[],
        ), final=True)
    if follow_up and follow_up.get('compare_months'):
        return dict(result=compare_months(frame, normalized, follow_up, today), final=True)
    dining = (DINING_COLLOQUIAL.fullmatch(normalized) or DINING_TOTAL.fullmatch(normalized)
              or DINING_QUICK.fullmatch(normalized))
    combined = re.fullmatch(
        r'(?:请问\s*)?(20[0-9]{2}-(?:0[1-9]|1[0-2]))\s*的?\s*'
        r'收入(?:和|与|、)净支出分别(?:是)?多少(?:元)?[。？?]?',
        normalized,
    )
    overrun = re.fullmatch(
        rf'({MONTH}|上个月|上月|这个月|本月)\s*(?:的)?([\u4e00-\u9fff]{{0,12}}?)'
        r'(?:好像|是不是)?(?:花超了|超支了?|超了)[，, ]*(?:具体|到底)?(?:是)?多少(?:元)?[。？?]?',
        normalized,
    )
    if overrun:
        period = {'本月': month_shift(today, 0), '这个月': month_shift(today, 0),
                  '上月': month_shift(today, -1), '上个月': month_shift(today, -1)}.get(overrun[1], overrun[1])
        category = category_in_question(overrun[2])
        if overrun[2] and not category:
            return dict(result=clarify('请告诉我想核对哪个分类的预算。'), final=True)
        intent = dict(metric='expense', period=period, category=category)
    elif dining:
        period = dining['period']
        period = {'本月': month_shift(today, 0), '这个月': month_shift(today, 0),
                  '这月': month_shift(today, 0), '上月': month_shift(today, -1),
                  '上个月': month_shift(today, -1)}.get(period, period)
        if '-' not in period:
            number = month_number(re.match(MONTH_TOKEN, period)[0])
            matches = sorted(month for month in months if month.endswith(f'-{number:02}'))
            if len(matches) != 1:
                details = '、'.join(matches)
                message = (f'账本中有多个 {number} 月（{details}），请补充年份。'
                           if matches else f'尚无已导入的 {number} 月记录。请确认年份，或先导入该月账单。')
                return dict(result=dict(
                    answer=message, evidence=[], status='unsupported', parser='本地解析',
                    clarify=dict(period='', category='餐饮', merchant='', metric='expense'),
                ), final=True)
            period = matches[0]
        intent = dict(metric='expense', period=period, category='餐饮')
    elif combined:
        intent = dict(metric='balance', period=combined[1])
    else:
        intent = follow_up or category_breakdown(normalized) or merchant_expense(normalized)
    parsed = intent or parse_question(normalized)
    if (parsed and parsed.get('period') == 'all' and not parsed.get('date_from')
            and re.search(r'月|年|日|号|昨天|今天|上周|本周|最近|季度', normalized)):
        return dict(result=dict(
            answer='尚未确定你指定的时间范围，请使用明确月份或 YYYY-MM-DD 起止日期；不会扩大到全部账单。',
            status='unsupported', parser='本地解析', evidence=[],
            clarify=dict(period='', category=category_in_question(question) or '', merchant='', metric='expense'),
        ), final=True)
    unresolved_time = re.sub(
        r'20\d{2}-\d{2}(?:-\d{2})?|上个月|上月|这个月|本月', '', normalized,
    )
    if not compiled and re.search(
            rf'{YEAR_TOKEN}\s*年|{MONTH_TOKEN}\s*月|\d+\s*[日号]|昨天|今天|上周|本周|'
            r'季度|今年|去年|最近\s*[一二三四五六七八九十半\d]+',
            unresolved_time,
    ):
        return dict(result=dict(
            answer='这段时间条件还不能可靠转换，请填写 YYYY-MM 或明确的起止日期；不会忽略日期条件计算。',
            status='unsupported', parser='本地解析', evidence=[],
            clarify=dict(period='', category=category_in_question(question) or '', merchant='', metric='expense'),
        ), final=True)
    result = answer_question(frame, normalized, today=today, budgets=budgets, model=client, local_intent=intent)
    return dict(
        result=result,
        final=False,
        parsed=parsed,
        follow_up=follow_up,
        model_follow_up=model_follow_up,
        combined=combined,
        overrun=overrun,
    )


def present_ledger_turn(frame, execution, plan, budgets):
    """Attach scope, state and follow-up metadata to a completed calculation."""
    result = execution['result']
    if execution['final']:
        return result
    question = plan['question']
    normalized = plan['normalized']
    previous = plan['previous']
    months = plan['months']
    today = plan['today']
    parsed = execution['parsed']
    follow_up = execution['follow_up']
    combined = execution['combined']
    overrun = execution['overrun']
    if execution['model_follow_up'] and result['status'] == 'ok':
        result['parser'] = '模型理解追问 + 代码计算'
    if combined and result['status'] == 'ok':
        evidence = result['evidence']
        income = sum(row['amount_cents'] for row in evidence if row['kind'] == '收入')
        net = sum(row['amount_cents'] for row in evidence if row['kind'] == '支出')
        result['display_answer'] = f'收入 {yuan(income)} 元，净支出 {yuan(net)} 元。\n结余 {yuan(income - net)} 元。'
        result.update(amounts=[('收入', income), ('净支出', net)], amount_note=f'结余 {yuan(income - net)} 元。')
        result['details'] = result['answer'] = result['display_answer'] + '\n' + result['answer']
    if re.search(r'(?:花)?超(?:了|支)', question) and result.get('status') == 'ok' and parsed:
        period = parsed.get('period')
        period = {'this_month': month_shift(today, 0), 'last_month': month_shift(today, -1)}.get(period, period)
        limit = budgets.get(period, {}).get(parsed.get('category'))
        if parsed.get('metric') == 'expense' and (overrun or limit is None):
            if limit is None:
                note = '没有对应的预算额度，无法判断是否超支或超出多少。'
            else:
                spent = sum(row['amount_cents'] for row in result['evidence'] if row['kind'] == '支出')
                note = (f'分类预算 ¥{yuan(limit)}，超出 ¥{yuan(spent - limit)}。'
                        if spent > limit else f'分类预算 ¥{yuan(limit)}，未超支。')
            result['answer'] += ' ' + note
            if result.get('display_answer'):
                result['display_answer'] += '\n' + note
    executed = result.get('trace', {}).get('inputs', {})
    if not executed and result['status'] == 'missing' and parsed:
        period = parsed.get('period', '')
        result['scope'] = {'this_month': month_shift(today, 0),
                           'last_month': month_shift(today, -1)}.get(period, period)
    if (result['status'] == 'ok' and len(plan['explicit_months']) == 1 and executed
            and executed.get('period') not in plan['explicit_months'] and not executed.get('date_from')):
        return dict(answer='理解出的查询范围与你指定的月份不一致，已停止计算。请确认月份后重试。',
                    status='unsupported', parser='条件校验未通过', evidence=[])
    if executed:
        period = executed.get('period', '')
        period = {'this_month': month_shift(today, 0), 'last_month': month_shift(today, -1),
                  'all': '全部已导入记录', 'last_3_complete_months': '最近三个完整月份'}.get(period, period)
        result['scope'] = (f"{executed['date_from']} 至 {executed['date_to']}"
                           if executed.get('date_from') else period)
    if result['status'] == 'ok' and re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', result.get('scope', '')):
        old_scope = previous.get('scope') if follow_up and previous else None
        if old_scope and not re.fullmatch(MONTH, old_scope):
            old_scope = None
        if old_scope and previous.get('query_state', {}).get('metric') != executed.get('metric'):
            old_scope = None
            follow_pair = False
        else:
            follow_pair = bool(follow_up and previous)
        scope = result['scope']
        result['query_months'] = ([old_scope, scope] if old_scope and old_scope != scope else
                                  previous.get('query_months', [scope]) if follow_pair else [scope])
    if result['status'] == 'ok' and executed:
        result['query_state'] = dict(executed)
        result['query_text'] = question
        result['calculated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
        result['basis'] = ('消费总额（未扣退款）' if executed.get('amount_basis') == 'gross'
                           else basis_label(frame.attrs['spending_basis']))
        result['condition_changes'] = [key for key in ('period', 'metric', 'category', 'merchant')
                                       if previous and follow_up and executed.get(key) !=
                                       (previous or {}).get('query_state', {}).get(key)]
        scope = result.get('scope', '')
        suggestions = []
        if re.fullmatch(r'20\d{2}-\d{2}', scope):
            earlier = sorted(m for m in months if m < scope)
            if earlier and executed.get('amount_basis') != 'gross':
                suggestions.append(f'那{earlier[-1]}呢？')
            merchants = sorted({row['merchant'] for row in result.get('evidence', []) if row.get('merchant')})
            if (merchants and executed.get('metric') == 'expense'
                    and executed.get('category') and not executed.get('merchant')):
                totals = {}
                for row in result['evidence']:
                    name = row.get('merchant')
                    if name:
                        totals[name] = totals.get(name, 0) + row['amount_cents']
                merchant = max(merchants, key=lambda name: totals[name])
                suggestions.append(f'只看{merchant}')
                result['suggestion_labels'] = {suggestions[-1]: f'{scope} · 只看{merchant}'}
            elif (executed.get('metric') == 'expense'
                  and not executed.get('category') and not executed.get('merchant')):
                suggestions.extend(['查看分类构成', '查看交易明细'])
            if (len(result.get('query_months', [])) == 2
                    and executed.get('metric') in ('expense', 'compare', 'breakdown')):
                suggestions.append('这两个月差多少？')
        result['suggestions'] = suggestions[:3]
    if result.get('query_state'):
        result['query_state']['date_anchor'] = today.isoformat()
    if result['status'] == 'unsupported':
        if result['parser'] == '模型理解 + 代码计算':
            result['parser'] = '模型未完成可计算的查询'
        result['answer'] += ' 支持按月份或日期区间查询已导入收支、分类及商户。例如“2026-08 餐饮净支出多少？”'
        if not any(word in normalized for word in ('投资', '预测', '贷款', '退款', '下个月', '明年')):
            known = sorted({name for name in frame.merchant if name and name.casefold() in normalized.casefold()},
                           key=lambda name: (-len(name), name))
            parsed = parse_question(normalized)
            result['clarify'] = dict(
                period=' 至 '.join(re.findall(r'(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)', normalized)) or
                       next(iter(re.findall(
                           r'20\d{2}-(?:0[1-9]|1[0-2])(?![-\d])|上个月|上月|本月|这个月', normalized,
                       )), ''),
                category=category_in_question(normalized) or '', merchant=known[0] if known else '',
                metric=(parsed['metric'] if parsed else
                        'expense' if any(x in normalized for x in ('支出', '花了', '花费', '消费')) else ''),
            )
    return result


@classified_query
def query_ledger(path, question, client, cancelled, *, previous=None):
    """Orchestrate parsing, validation, execution and presentation."""
    store = Store(path, read_only=True)
    frame = store.spending_view()
    today = date.today()
    if cancelled.is_set():
        return None
    savings = monthly_savings(frame, question, today)
    if savings:
        return savings
    months = set(frame.date.str[:7]) | set(frame.attrs.get('source_months', ()))
    budgets = {month: store.budgets(month) for month in months | {month_shift(today, i) for i in (-1, 0)}}
    if cancelled.is_set():
        return None
    plan = validate_ledger_turn(
        parse_ledger_turn(question, previous, months, today),
        frame, budgets, months,
    )
    if plan['response'] is not None:
        return plan['response']
    if cancelled.is_set():
        return None
    execution = run_ledger_turn(frame, plan, budgets, client, cancelled)
    if execution is None or cancelled.is_set():
        return None
    return present_ledger_turn(frame, execution, plan, budgets)


def category_breakdown(question):
    names = '|'.join(map(re.escape, category_options('支出')))
    separator = r'\s*[、，,和与及]\s*'
    match = re.fullmatch(
        rf'(?:请问\s*)?(上个月|上月|本月|这个月|20\d{{2}}-(?:0[1-9]|1[0-2]))\s*的?\s*'
        rf'((?:{names})(?:{separator}(?:{names}))+)\s*'
        r'(?:各自?|分别)(?:花了?|净支出|支出|是)?(?:是)?多少(?:钱|元)?[？?。]?', question)
    if not match:
        return None
    categories = list(dict.fromkeys(re.split(separator, match[2])))
    period = {'上个月': 'last_month', '上月': 'last_month', '本月': 'this_month', '这个月': 'this_month'}.get(match[1], match[1])
    if len(categories) == 2:
        return dict(metric='compare', period=period, compare_categories=categories)
    return dict(metric='breakdown', period=period, categories=categories)


def follow_up_query(question, previous, today, client, cancelled, *, original_question=None):
    """Resolve one change against the last successful query in this conversation."""
    if not previous or previous.get('status') != 'ok':
        return None, False
    original = previous.get('query_state') or previous.get('query_intent') or previous.get('trace', {}).get('inputs', {})
    intent = {key: original[key] for key in ('metric', 'period', 'category', 'categories',
              'compare_categories', 'merchant', 'date_from', 'date_to', 'amount_basis') if key in original}
    scope = previous.get('scope', '')
    month = month_only_followup(question)
    if (not month and not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', scope)) or intent.get('metric') not in (
        'breakdown', 'expense', 'income', 'balance', 'count', 'top', 'share', 'budget', 'compare',
    ):
        return None, False
    intent['period'] = scope
    if not month and original.get('amount_basis') == 'gross':
        return None, False
    text = question.rstrip('？?。！!').strip()
    names = '|'.join(map(re.escape, category_options('支出')))
    category = re.fullmatch(rf'(?:那|那么|只看|只查)\s*({names})\s*呢?', text)
    category = category or re.fullmatch(rf'不是(?:那个|这类|这个)[，, ]*是\s*({names})', text)
    merchant = re.fullmatch(r'(?:那[，, ]*)?只(?:看|查)\s*(?:商户[:：]\s*)?([^，,；;。！？?]{1,50})', text)
    compare = re.fullmatch(r'(?:那)?这(?:两|2)个月(?:的)?(?:支出)?(?:相比)?(?:差多少|相差多少|对比一下)', text)
    used_model = False
    if month:
        action, value = 'month', month['period']
        intent.pop('date_from', None)
        intent.pop('date_to', None)
    elif category:
        action, value = 'category', category[1]
    elif re.fullmatch(r'就刚才那个分类[，, ]*再算一遍', text) and intent.get('category'):
        return intent, False
    elif re.fullmatch(r'那外卖呢?', text):
        return {'clarify_takeout': True}, False
    elif merchant:
        action, value = 'merchant', merchant[1].strip()
    elif compare:
        action, value = 'compare_months', ''
    elif (client is not None and not cancelled.is_set()
          and hasattr(client, 'plan_ledger_follow_up')
          and re.search(r'^(?:那|那么|换|改|仅看|只想看|把这|这两个月)', text)
          and not re.search(r'预测|下个月|投资|删除|备份|修改|新增|退款|不要|排除|不含|除了|除去|去掉|以来|之前|之后|全部|所有', text)):
        try:
            text = (original_question or question).rstrip('？?。！!').strip()
            plan = client.plan_ledger_follow_up({'question': original_question or question, 'period': scope, 'metric': intent['metric']})
        except Exception:
            return None, False
        if cancelled.is_set() or not isinstance(plan, dict) or set(plan) != {'action', 'value'}:
            return None, False
        action, value = plan['action'], plan['value']
        if not isinstance(action, str) or not isinstance(value, str) or not value or value not in text:
            return None, False
        # Reject compound requests rather than silently dropping conditions.
        remainder = text.replace(value, '', 1)
        if re.search(r'\d|[，,、；;]|商户|相差|排名|占比|笔数|预算|收入|结余|净支出|餐饮|交通|购物|居住', remainder):
            return None, False
        used_model = True
    else:
        return None, False
    if action == 'month':
        value = {'本月': month_shift(today, 0), '这个月': month_shift(today, 0), '上个月': month_shift(today, -1),
                 '上月': month_shift(today, -1)}.get(value, value)
        if re.fullmatch(rf'{MONTH_TOKEN}月', value) and re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', scope):
            value = f'{scope[:4]}-{month_number(value[:-1]):02d}'
        if not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', value):
            return None, False
        intent['period'] = value
    elif action == 'category' and value in category_options('支出') and intent['metric'] in (
        'expense', 'compare', 'breakdown', 'count', 'share', 'budget',
    ):
        intent.pop('categories', None)
        intent.pop('compare_categories', None)
        if intent['metric'] in ('compare', 'breakdown'):
            intent['metric'] = 'expense'
        intent['category'] = value
    elif action == 'merchant' and intent['metric'] in ('expense', 'compare', 'breakdown', 'count') and re.fullmatch(
        r'[\w &·.（）()\-]{1,50}', value
    ) and not re.search(r'和|以及|或者|以外|除外|花了|多少|不要|排除|不含|除了|全部|所有|分类|退款', value):
        intent['merchant'] = value
    elif action == 'compare_months' and not used_model:
        periods = previous.get('query_months', [])
        if len(periods) != 2 or intent['metric'] not in ('expense', 'breakdown', 'compare'):
            return {'clarify_comparison': True}, False
        intent['compare_months'] = periods
    else:
        return None, False
    return intent, used_model


def compare_months(frame, question, intent, today):
    intent = dict(intent)
    periods = intent.pop('compare_months')
    result = []
    for period in periods:
        scoped = dict(intent, period=period)
        rows = answer_question(frame, question, today=today, local_intent=scoped)
        if rows['status'] != 'ok':
            return dict(answer=f'{period} 缺少可用于本次比较的记录，请核对账单覆盖和筛选条件。',
                        status=rows['status'], parser='本地解析', evidence=[])
        result.append(rows)
    names = intent.get('categories') or intent.get('compare_categories') or [intent.get('category') or '全部支出']
    evidence = [row for item in result for row in item['evidence']]
    findings = []
    for name in names:
        totals = [sum(row['amount_cents'] for row in item['evidence'] if
                      name == '全部支出' or row['category'] == name) for item in result]
        findings.append(f'{name}：{periods[0]} {yuan(totals[0])} 元，{periods[1]} {yuan(totals[1])} 元，'
                        f'后一个月减前一个月为 {yuan(totals[1] - totals[0])} 元。')
    basis = basis_label(frame.attrs['spending_basis'])
    answer = '\n'.join([*findings, f'按{basis}比较已导入记录。'])
    contributions = change_contributions(result)
    totals = [sum(int(row['amount_cents']) for row in item['evidence']) for item in result]
    delta = totals[1] - totals[0]
    return dict(answer=answer, evidence=evidence, status='ok', parser='本地解析',
                scope=intent['period'], query_intent=intent, query_months=periods,
                scope_display=f'{periods[0]} 与 {periods[1]}',
                amounts=[(periods[0], totals[0]), (periods[1], totals[1]), ('变化（后者 − 前者）', delta)],
                amount_note=f'{periods[1]} 比 {periods[0]} ' +
                            (f'增加 {yuan(delta)} 元。' if delta > 0 else f'减少 {yuan(-delta)} 元。' if delta < 0 else '没有变化。'),
                query_state=intent, basis=basis, contributions=contributions,
                calculated_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                trace={'两月计算': [item['trace'] for item in result]})


def merchant_expense(question):
    """Bounded local form for an explicit period followed by a merchant expense."""
    pattern = (r'(?:请问\s*)?(?:从|在)?\s*'
               r'(?P<period>20\d{2}-\d{2}-\d{2}\s*(?:到|至|—|~)\s*20\d{2}-\d{2}-\d{2}(?:之间|期间)?'
               r'|20\d{2}-(?:0[1-9]|1[0-2])|上个月|上月|本月|这个月)\s*'
               r'(?:在|商户[:：]?\s*)?(?P<merchant>[\w &·.（）()\-]{1,50}?)\s*(?:的)?'
               r'(?:分类[:：](?P<category>[^\s:：]{1,10})\s+)?'
               r'(?:花了多少(?:钱|元)?|(?:净支出|消费总额|支出|花费|消费)(?:是多少|多少)(?:元)?)\s*[？?。]?')
    match = re.fullmatch(pattern, question)
    if not match or parse_question(question):
        return None
    merchant = match['merchant'].strip()
    if not merchant or category_in_question(merchant) or any(word in merchant for word in (
        '除了', '不含', '排除', '退款', '对比', '比较', '多少', '分别', '和', '或者', '以前', '之后', '之前', '以上', '以下',
    )):
        return None
    period = match['period']
    dates = re.findall(r'20\d{2}-\d{2}-\d{2}', period)
    category = match['category']
    if category and category not in category_options('支出'):
        return None
    intent = dict(metric='expense', category=category, merchant=merchant,
                  period={'上月': 'last_month', '上个月': 'last_month', '本月': 'this_month', '这个月': 'this_month'}.get(period, period))
    if dates:
        try:
            if date.fromisoformat(dates[0]) > date.fromisoformat(dates[1]):
                return None
        except ValueError:
            return None
        intent.update(period='all', date_from=dates[0], date_to=dates[1])
    return intent


def ledger_coverage(store):
    frame = store.source_transactions()
    months = sorted(set(frame.date.str[:7]))
    latest = next((row.get('finished_at') for row in store.import_batches()
                   if row['status'] == 'committed' and row.get('finished_at')), None)
    return dict(months=months, count=len(frame), latest=latest)


def savings_inputs(values):
    months = values['months'].strip()
    if not re.fullmatch(r'[0-9]{1,3}', months):
        raise ValueError('请填写整数月数，范围为 1–120。')
    return confirmed_inputs(months=int(months), **{
        name: money_to_cents(value.strip(), allow_zero=True)
        for name, value in values.items() if name in ('target_cents', 'saved_cents', 'income_cents', 'budget_cents') and value.strip()
    })


def savings_plan(path, mode, inputs, client, consent, cancelled, *, protected_categories=()):
    store = Store(path, read_only=True) if mode == 'historical' else None
    return plan_savings_agent(
        store, question=('按已确认目标比较 A/B/C。不得削减保护分类：' + '、'.join(protected_categories) +
                         '。B 仅对有数据的非保护分类假设压缩 10%。C 是达标所需条件，不保证可执行。')
                         if protected_categories else '按已确认的目标比较 A/B/C。B 假设餐饮和交通各压缩 10%，请说明缺口。',
        inputs=inputs, mode=mode, client=client, consent=consent, cancel_event=cancelled, protected_categories=protected_categories,
    )


def preview_statement(path, source, adapter, account_id):
    source = Path(source)
    parsed = parse_statement(source.read_bytes(), source.name, retain_source=True, adapter=adapter, account_id=account_id)
    return parsed, Store(path, read_only=True).preview_import(parsed)


PLAN_PROMPT = re.compile(
    r'(?:请)?(?:帮我|我想|我需要)?(?:做|写|制定|生成|安排|开始)(?:一个|个|一份)?'
    r'(?:储蓄|存钱|攒钱)(?:计划|规划|方案|安排)'
)
PLAN_GOAL = r'(?:存下?|攒下?)(?P<target>[0-9,.]+)(?:元|块)?'
PLAN_TERM = r'(?P<months>[0-9]{1,3})个?月(?:内)?'
PLAN_INTRO = r'(?:我想|想|计划|打算|希望)?'


def savings_request(question):
    text = ''.join(question.split()).rstrip('。！？?!')
    if PLAN_PROMPT.fullmatch(text):
        return dict(target_cents='', months='')
    for pattern in (
        rf'{PLAN_INTRO}(?:用|在)?{PLAN_TERM}{PLAN_GOAL}',
        rf'{PLAN_INTRO}{PLAN_GOAL}(?:[，,、]?(?:期限|用时)?{PLAN_TERM})?',
    ):
        match = re.fullmatch(pattern, text)
        if match and (match['months'] or re.match(r'(?:我想|想|计划|打算|希望)', text)):
            return dict(target_cents=match['target'], months=match['months'] or '')
    return None


def savings_follow_up(question, previous):
    """Read only bounded revisions to the active plan; let other queries go through normally."""
    text = ''.join(question.split()).rstrip('。！？?!')
    if text in ('改用手动假设', '用手动假设', '按账单重算', '用历史账单'):
        if text in ('按账单重算', '用历史账单'):
            return {key: value for key, value in previous.items() if key not in ('income_cents', 'budget_cents')}
        return dict(previous, income_cents=previous.get('income_cents', ''), budget_cents=previous.get('budget_cents', ''))
    revisions = {}
    for part in re.split('[，、；;]|(?<![0-9]),|,(?![0-9])', text):
        match = re.fullmatch(r'(?:把)?(?:(?:期限|时间)(?:改为|改成|延长到|缩短到|设为)?|改为|改成|换成)?'
                             r'([0-9]{1,3})个?月(?:呢)?', part)
        if match:
            revisions['months'] = match[1]
            continue
        match = re.fullmatch(r'(?:(?P<name>目标(?:金额)?|已有储蓄|已有存款|已经存了|已存|已有|每月收入|月收入|收入|每月预算|月预算|预算|每月开支)'
                             r'(?:改为|改成|调到|是|为)?|(?:改为|改成|换成))?(?P<value>[0-9,.]+)元(?:呢)?', part)
        if not match:
            return None
        name = match['name'] or ''
        if name in ('每月收入', '月收入', '收入'):
            field = 'income_cents'
        elif name in ('每月预算', '月预算', '预算', '每月开支'):
            field = 'budget_cents'
        elif name in ('已有储蓄', '已有存款', '已经存了', '已存', '已有'):
            field = 'saved_cents'
        else:
            field = 'target_cents'
        if field in revisions:
            return None
        revisions[field] = match['value']
    if not revisions:
        return None
    state = dict(previous, **revisions)
    if {'income_cents', 'budget_cents'} & revisions.keys():
        state.setdefault('income_cents', '')
        state.setdefault('budget_cents', '')
    return state


def guidance(question):
    """Recognize deliberate navigation prompts and leave ordinary queries alone."""
    text = question.strip().rstrip('。！？?!')
    if (re.search(r'随礼|随了|人情往来', text)
            and re.search(r'算什么|算啥|分类', text)):
        return ('ledger',
                '人情往来一般可归为“其他支出”；如需入账，请到记账草稿并选择分类。',
                '')
    request = savings_request(text)
    if request is not None:
        return 'plan', '先确认想存多少钱、打算用几个月，再比较三种方案。', request['target_cents']
    command = re.match(r'^(?:(?:请问|请|能不能|能否|可以|帮我|我想|如何|怎么)\s*)*'
                       r'(?:管理|删除|删掉|添加|新增|调整|修改|设置|导入|恢复|备份|记账|记一笔)', text)
    explicit_change = re.search(r'(?:把|将).*(?:改成|改为|调到|设为|删除|删掉|归档)', text)
    if not command and not explicit_change and not re.search(r'(?:退款|对账).*(?:怎么|如何)处理', text):
        return None
    if re.search(r'^(?:已|已经)?(?:删除|导入|设置|修改|调整|恢复)(?:了|的|过|后)', text):
        return None
    for page, words, answer in (
        ('backup', ('备份',), '请到备份与恢复查看文件，确认后再恢复。'),
        ('accounts', ('银行卡', '账户', '钱包', '绑定'), '请到资金账户核对资料，再手动操作。'),
        ('review', ('退款', '对账'), '请到退款与对账查看来源，再决定如何处理。'),
        ('budget', ('预算',), '请到预算与提醒选择月份与分类，再手动调整。'),
        ('plan', ('储蓄', '目标'), '请到储蓄规划核对目标与收支。'),
    ):
        if any(word in text for word in words):
            return page, answer, ''
    return 'ledger', '请到交易账本定位记录，再确认要执行的操作。', ''
