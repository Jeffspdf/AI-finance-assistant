"""Read-only comparison of a savings target with a specific month's ledger."""

from datetime import date
import re

from finance.analytics import yuan
from finance.evidence import calculation
from finance.importer import money_to_cents
from finance.spending_presenter import basis_label, spending_totals


MONTH = r'(?:20[0-9]{2}年)?(?:1[0-2]|0?[1-9])月'
REQUEST = re.compile(
    rf'(?:我)?(?:想|计划|打算)?(?:在)?(?P<target>{MONTH})比(?P<base>{MONTH})'
    r'(?:多存|多攒|多储蓄)(?P<amount>[0-9]+(?:\.[0-9]{1,2})?)元[，,。]?'
    r'(?:在)?收入(?:保持)?不变(?:的情况下|时|的话)?[，,。]?'
    r'(?:(?:我的)?(?:开支|支出|消费)(?:里面|中)?有哪些部分(?:可以)?(?:再)?优化'
    r'|(?:我)?(?:可以|该|应该)?(?:从)?哪些(?:开支|支出|消费)(?:入手|可以省|可以优化|下手)?'
    r'|(?:我)?(?:该|应该|可以)?怎么(?:省|缩减开支|优化支出))[？?。！!]*'
)


def monthly_savings(frame, question, today):
    query = re.sub(r'\s+', '', question)
    request = REQUEST.fullmatch(query)
    if not request:
        if re.search(r'多(?:存|攒|储蓄)|提高储蓄|减少开支', query) and re.search(r'开支|支出|消费|收入', query):
            return dict(answer='这是储蓄规划问题。请说明基准月份、目标月份、希望多存的金额和收入是否变化。'
                        '例如：“我想在11月比10月多存1000元，收入不变的情况下，怎么省？”',
                        route='plan', parser='本地规划提示')
        return None

    def prompt(message):
        return dict(answer=message, route='plan', parser='本地规划提示')

    try:
        extra = money_to_cents(request['amount'])
    except ValueError:
        return prompt('请填写大于零且在支持范围内的额外储蓄金额。')
    base_year, _, base_month = request['base'].rstrip('月').rpartition('年')
    target_year, _, target_month = request['target'].rstrip('月').rpartition('年')
    base_number, target_number = int(base_month), int(target_month)
    sources = set(frame.attrs.get('source_months', ()))
    if not base_year:
        if target_year:
            base_year = str(int(target_year) - (target_number < base_number))
        else:
            candidates = sorted(period for period in sources if period.endswith(f'-{base_number:02}'))
            if len(candidates) > 1:
                return prompt(f'账本覆盖了多个年份的 {base_number} 月，请写明基准月份的年份。')
            base_year = candidates[0][:4] if candidates else str(today.year)
    base = f'{base_year}-{base_number:02}'
    target_year = int(target_year) if target_year else int(base_year) + (target_number < base_number)
    target = f'{target_year:04d}-{target_number:02}'
    if target <= base:
        return prompt('目标月份需要晚于基准月份，请确认是否写反或补充年份。')
    if base not in sources:
        return prompt(f'{base} 没有已导入记录，无法给出分类节省方案。请先导入该月账单，或到储蓄规划填写收入和开支假设。')

    month = frame.loc[frame.date.str.startswith(base)]
    totals = spending_totals(month)
    income, net = totals['income_cents'], totals['net_cents']
    ceiling = net - extra
    expenses = month.loc[month.kind == '支出']
    buckets = expenses.groupby('category').amount_cents.sum().sort_values(ascending=False)
    discretionary = [(category, int(cents)) for category, cents in buckets.items()
                     if cents > 0 and category in ('购物', '娱乐', '餐饮', '交通', '其他支出')]
    lines = [
        f'收入不变时，{target} 要比 {base} 多存 {yuan(extra)} 元，净支出就得减少 {yuan(extra)} 元。',
        f'{base} 已记录收入 {yuan(income)} 元、净支出 {yuan(net)} 元、结余 {yuan(income - net)} 元。',
    ]
    if ceiling < 0:
        lines.append('目标要求净支出为负。仅靠压缩普通开支无法保证达成，建议调整额外储蓄目标或另行考虑收入变化。')
    else:
        lines.append(f'{target} 的净支出上限为 {yuan(ceiling)} 元，目标结余为 {yuan(income - ceiling)} 元。')
        if discretionary:
            listed = discretionary[:3]
            potential = sum(cents // 10 for _, cents in listed)
            examples = '、'.join(f'{category} {yuan(cents)} 元' for category, cents in listed)
            lines.append(f'优先核对这几类里可选的消费：{examples}。')
            if potential:
                lines.append(f'情景试算：若这几类各少花 10%，可少支出约 {yuan(potential)} 元。'
                             + (f'距离目标还差 {yuan(extra - potential)} 元。' if potential < extra else '在这个假设下可达到目标。'))
        else:
            lines.append('基准月没有可供优先核对的正额分类。请先检查账单覆盖范围。')
    lines.append('这些是基于已导入记录的假设。先标出各类里可取消或替代的项目，保留必要开支。')
    answer = '\n'.join(lines)
    return dict(answer=answer, display_answer=answer, route='plan', status='ok',
                parser='本地储蓄试算', scope=f'{base} → {target}',
                evidence=expenses.to_dict('records'),
                details=answer + '\n统计口径：' + basis_label(totals['basis']) + '。',
                trace=calculation('savings', 'monthly_savings.same_income', month,
                                  inputs=dict(base_month=base, target_month=target, extra_cents=extra,
                                              income_cents=income, net_cents=net, hypothetical_cut_percent=10)))
