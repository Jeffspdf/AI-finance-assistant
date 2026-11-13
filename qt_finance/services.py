"""Adapters between Qt forms and the existing ledger and savings APIs."""

from datetime import date
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


MONTH_NUMBERS = {name: number for number, name in enumerate('一 二 三 四 五 六 七 八 九 十 十一 十二'.split(), 1)}
MONTH_TOKEN = r'(?:1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])'
YEAR_TOKEN = r'(?:20[0-9]{2}|二[〇零][〇零一二三四五六七八九]{2})'
YEAR_DIGITS = str.maketrans('〇零一二三四五六七八九', '00123456789')
DINING_TOTAL = re.compile(
    r'(?:我想知道|请问|把)?\s*(?P<period>20[0-9]{2}-[0-9]{2}|' + MONTH_TOKEN + r'\s*月(?:份)?)\s*'
    r'(?:的)?\s*(?:(?:我)?花在吃饭上的钱累计是多少|吃饭一共花掉多少钱|吃饭相关的小票加在一起[，, ]*总共花了多少钱)'
    r'[，,。？? ]*(?:(?:只告诉我金额|只要数字)[。？? ]*)?'
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


def query_ledger(path, question, client, cancelled):
    store = Store(path, read_only=True)
    frame = store.spending_view()
    today = date.today()
    months = set(frame.date.str[:7]) | set(frame.attrs.get('source_months', ()))
    budgets = {month: store.budgets(month) for month in months | {month_shift(today, i) for i in (-1, 0)}}
    if cancelled.is_set():
        return None
    normalized = re.sub(rf'(?<!\d)({YEAR_TOKEN})\s*年\s*({MONTH_TOKEN})\s*月(?:份)?(?!\s*(?:\d|[一二三四五六七八九十]+\s*[日号]))',
                        lambda match: f'{match[1].translate(YEAR_DIGITS)}-{month_number(match[2]):02}', question)
    dining = DINING_TOTAL.fullmatch(normalized)
    combined = re.fullmatch(r'(?:请问\s*)?(20[0-9]{2}-(?:0[1-9]|1[0-2]))\s*的?\s*收入(?:和|与|、)净支出分别(?:是)?多少(?:元)?[。？?]?', normalized)
    if dining:
        period = dining['period']
        if '-' not in period:
            number = month_number(re.match(MONTH_TOKEN, period)[0])
            matches = sorted(month for month in months if month.endswith(f'-{number:02}'))
            if len(matches) != 1:
                details = '、'.join(matches)
                message = f'账本中有多个 {number} 月（{details}），请补充年份。' if matches else f'尚无已导入的 {number} 月记录。请确认年份，或先导入该月账单。'
                return dict(answer=message, evidence=[], status='unsupported', parser='本地解析',
                            clarify=dict(period='', category='餐饮', merchant='', metric='expense'))
            period = matches[0]
        intent = dict(metric='expense', period=period, category='餐饮')
    elif combined:
        intent = dict(metric='balance', period=combined[1])
    else:
        intent = merchant_expense(normalized)
    result = answer_question(frame, normalized, today=today, budgets=budgets, model=client, local_intent=intent)
    if combined and result['status'] == 'ok':
        evidence = result['evidence']
        income = sum(row['amount_cents'] for row in evidence if row['kind'] == '收入')
        net = sum(row['amount_cents'] for row in evidence if row['kind'] == '支出')
        result['display_answer'] = f'收入 {yuan(income)} 元，净支出 {yuan(net)} 元。\n结余 {yuan(income - net)} 元。'
        result['details'] = result['answer'] = result['display_answer'] + '\n' + result['answer']
    executed = result.get('trace', {}).get('inputs', {})
    if executed:
        period = executed.get('period', '')
        period = {'this_month': month_shift(today, 0), 'last_month': month_shift(today, -1),
                  'all': '全部已导入记录', 'last_3_complete_months': '最近三个完整月份'}.get(period, period)
        result['scope'] = (f"{executed['date_from']} 至 {executed['date_to']}"
                           if executed.get('date_from') else period)
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
                       next(iter(re.findall(r'20\d{2}-(?:0[1-9]|1[0-2])(?![-\d])|上个月|上月|本月|这个月', normalized)), ''),
                category=category_in_question(normalized) or '', merchant=known[0] if known else '',
                metric=parsed['metric'] if parsed else 'expense' if any(x in normalized for x in ('支出', '花了', '花费', '消费')) else '',
            )
    return result


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
        for name, value in values.items() if name != 'months' and value.strip()
    })


def savings_plan(path, mode, inputs, client, consent, cancelled):
    store = Store(path, read_only=True) if mode == 'historical' else None
    return plan_savings_agent(
        store, question='按已确认的目标比较 A/B/C。B 假设餐饮和交通各压缩 10%，请说明缺口。',
        inputs=inputs, mode=mode, client=client, consent=consent, cancel_event=cancelled,
    )


def preview_statement(path, source, adapter, account_id):
    source = Path(source)
    parsed = parse_statement(source.read_bytes(), source.name, retain_source=True, adapter=adapter, account_id=account_id)
    return parsed, Store(path, read_only=True).preview_import(parsed)


def guidance(question):
    """Recognize deliberate navigation prompts and leave ordinary queries alone."""
    text = question.strip().rstrip('。！？?!')
    savings = re.fullmatch(r'(?:我想|想|计划)\s*(?:存|攒)\s*([0-9,.]+)\s*(?:元|块)?', text)
    if savings or text == '帮我做一个储蓄计划':
        return 'plan', '可以在储蓄规划里确认目标和期限，再比较 A/B/C。', savings[1] if savings else ''
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
