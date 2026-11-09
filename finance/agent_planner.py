"""Deterministic savings workflow; no model calls, GUI changes or ledger writes."""
from .agent_tools import AgentTools


def plan_savings_rules(store=None, *, inputs=(), mode='historical', today=None,
                       categories=('餐饮', '交通'), cut_pct=10):
    """Run a NEW snapshot for each user-confirmed input revision.

    Inputs must already be confirmed by the caller. Missing values are questions,
    never model guesses. The result is a local preview, not a saved goal/budget.
    """
    tools = AgentTools(store, inputs=inputs, mode=mode, today=today)
    missing = [name for name in ('target_cents', 'months', 'saved_cents') if name not in tools.inputs]
    questions = {'target_cents': '请确认储蓄目标金额', 'months': '请确认规划期限（月）',
                 'saved_cents': '请确认已有储蓄金额（没有则确认 0）',
                 'income_cents': '请确认未来每月收入', 'budget_cents': '请确认未来每月预算'}

    def result(status, needed=(), simulation=None):
        return dict(status=status, planner='local_rules', planner_label='本地规则规划', mode=mode,
                    basis=tools.basis, as_of=tools.today.isoformat(),
                    inputs=[dict(fact_id='IN:' + item.name, kind='input', name=item.name,
                                 value=item.value, unit='months' if item.name == 'months' else 'CNY_cents',
                                 confirmed_by_user=True) for item in tools.inputs.values()],
                    questions=[dict(input=name, question=questions[name]) for name in needed],
                    facts=tools.facts, trace=tools.trace, simulation=simulation,
                    assumptions=['仅依据已导入记录，完整性未知'] if mode == 'historical' else
                                ['基于输入假设，未使用历史账单'], writes_ledger=False)

    args = {key: {'$input': key} for key in ('target_cents', 'months', 'saved_cents')}
    if mode == 'historical':
        coverage = tools.call('data_coverage', dict(months=3))
        if coverage['status'] != 'ok':
            return result('needs_assumptions', list(dict.fromkeys(missing + ['income_cents', 'budget_cents'])))
        overview = tools.call('spending_overview', dict(period='last_3_months'))
        if overview['facts']['requires_explicit_budget']:
            return result('needs_assumptions', list(dict.fromkeys(missing + ['income_cents', 'budget_cents'])))
        ref = overview['step_id'] + '.'
        args.update(base_income_cents={'$ref': ref + 'average_income_cents'},
                    base_spending_cents={'$ref': ref + 'average_expense_cents'},
                    category_spending_cents={'$ref': ref + 'category_spending_cents'})
    else:
        missing += [name for name in ('income_cents', 'budget_cents') if name not in tools.inputs]
        args.update(base_income_cents={'$input': 'income_cents'},
                    base_spending_cents={'$input': 'budget_cents'})
    if missing:
        return result('needs_input', missing)
    args['scenarios'] = [dict(label='A', strategy='current'),
                         dict(label='B', strategy='cut_categories', categories=list(categories), cut_pct=cut_pct),
                         dict(label='C', strategy='required_cut')]
    simulation = tools.call('scenario_simulate_batch', args)
    return result('ok', simulation=simulation)


def render_rules_plan(plan):
    """Small local preview renderer for service acceptance, not the future claims UI."""
    if plan['status'] != 'ok':
        return '本地规则规划：需要补充或确认输入。\n' + '\n'.join(q['question'] for q in plan['questions'])
    def money(value):
        sign = '-' if value < 0 else ''
        return f'{sign}¥{abs(value) // 100:,}.{abs(value) % 100:02d}'
    lines = [f"本地规则规划 · {'基于输入假设' if plan['mode'] == 'assumption' else '基于已导入记录'} · 口径：{plan['basis']}"]
    for row in plan['simulation']['facts']['scenarios']:
        if row['status'] != 'ok':
            lines.append(row['label'] + '：' + row['reason'])
        else:
            lines.append(f"{row['label']}：每月结余 {money(row['monthly_available_cents'])}，期末预计 {money(row['projected_cents'])}，缺口 {money(row['gap_cents'])}。")
    lines.append('方案依赖假设；不保证可执行，未保存或修改账本、预算或目标。')
    return '\n'.join(lines)


def main():
    """Local preview entrypoint. Existing databases are opened read-only."""
    import argparse
    import json
    import sys
    import sqlite3
    from .agent_tools import confirmed_inputs
    parser = argparse.ArgumentParser(description='本地规则储蓄规划预览；金额单位均为分，不保存账本')
    parser.add_argument('--ledger', help='历史账本路径（只读）')
    parser.add_argument('--target-cents', type=int, required=True)
    parser.add_argument('--months', type=int, required=True)
    parser.add_argument('--saved-cents', type=int, required=True)
    parser.add_argument('--income-cents', type=int)
    parser.add_argument('--budget-cents', type=int)
    parser.add_argument('--confirm-inputs', action='store_true', help='确认本次输入的金额和期限')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if not args.confirm_inputs:
        parser.error('请核对输入后加 --confirm-inputs')
    if args.ledger and (args.income_cents is not None or args.budget_cents is not None):
        parser.error('历史模式与输入假设模式请分开运行')
    values = dict(target_cents=args.target_cents, months=args.months, saved_cents=args.saved_cents)
    for key in ('income_cents', 'budget_cents'):
        if getattr(args, key) is not None:
            values[key] = getattr(args, key)
    try:
        store = None
        if args.ledger:
            from .storage import Store
            store = Store(args.ledger, read_only=True)
        plan = plan_savings_rules(store, inputs=confirmed_inputs(**values),
                                  mode='historical' if args.ledger else 'assumption')
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else render_rules_plan(plan))


if __name__ == '__main__':
    main()
