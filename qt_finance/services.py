"""Adapters between Qt forms and the existing ledger and savings APIs."""

from datetime import date
from pathlib import Path
import re

from finance.agent_runtime import plan_savings_agent
from finance.agent_tools import confirmed_inputs
from finance.demo import initialize_demo
from finance.importer import money_to_cents, parse_statement
from finance.qa import answer_question, month_shift
from finance.storage import Store


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
    months.update(month_shift(today, offset) for offset in (-1, 0))
    budgets = {month: store.budgets(month) for month in months}
    if cancelled.is_set():
        return None
    return answer_question(frame, question, today=today, budgets=budgets, model=client)


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
