"""Whole-sentence rendering of a closed set of savings claims.

Validation checks the complete task coverage, not just that a fact ID exists.
Free model text is never accepted as a financial statement or a transition.
"""
from copy import deepcopy
import json
import re


class ClaimValidationError(ValueError):
    pass


SUGGESTIONS = {'none': '', 'adjust_assumptions': '可以调整假设后重新计算。',
               'inspect_evidence': '可以查看本次计算依据。'}


def default_claims(plan):
    if plan['status'] != 'ok':
        return []
    claims = []

    def add(fact, statement, **extra):
        claims.append(dict(claim_id='C' + str(len(claims) + 1), fact_id=fact['fact_id'],
                           statement=statement, scope=dict(period=deepcopy(fact['period']),
                                                          basis=fact['basis'], **extra)))

    simulation = plan['simulation']
    add(simulation, 'savings_target')
    if plan['mode'] == 'historical':
        for fact in plan['facts'].values():
            if fact['tool'] == 'spending_overview' and fact['status'] == 'ok':
                add(fact, 'income_expense')
                break
        for fact in plan['facts'].values():
            if fact['tool'] == 'data_coverage' and fact['status'] == 'ok':
                add(fact, 'data_coverage')
                break
    else:
        add(simulation, 'assumption_baseline')
    for label in ('A', 'B', 'C'):
        add(simulation, 'scenario_result', scenario=label)
    return claims


def verify_claims(plan, payload):
    if not isinstance(payload, dict) or not {'claims'} <= payload.keys() <= {'claims', 'suggestion'}:
        raise ClaimValidationError('回答结构不在白名单内')
    claims, suggestion = payload['claims'], payload.get('suggestion', 'none')
    if not isinstance(suggestion, str) or suggestion not in SUGGESTIONS:
        raise ClaimValidationError('建议只能选择本地模板')
    expected = default_claims(plan)
    if not expected or not isinstance(claims, list) or len(claims) != len(expected):
        raise ClaimValidationError('回答未完整覆盖储蓄目标、依据和 A/B/C')
    def signature(claim):
        return json.dumps({k: claim[k] for k in ('fact_id', 'statement', 'scope')},
                          ensure_ascii=False, sort_keys=True, allow_nan=False)
    permitted = {signature(c) for c in expected}
    registry = {fact['fact_id']: fact for fact in plan['facts'].values()}
    seen, ids = set(), set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {'claim_id', 'fact_id', 'statement', 'scope'}:
            raise ClaimValidationError('claim 含有未知字段或自由文字')
        identifier = claim['claim_id']
        if not isinstance(identifier, str) or not re.fullmatch(r'C[1-9][0-9]*', identifier) or identifier in ids:
            raise ClaimValidationError('claim 标识无效或重复')
        ids.add(identifier)
        if not isinstance(claim['fact_id'], str):
            raise ClaimValidationError('fact 标识必须是字符串')
        fact = registry.get(claim['fact_id'])
        if fact is None or fact['unit'] != 'CNY_cents' or fact['basis'] != plan['basis']:
            raise ClaimValidationError('事实、单位或口径不匹配')
        try:
            key = signature(claim)
        except (TypeError, ValueError) as exc:
            raise ClaimValidationError('claim 不能编码为合法 JSON') from exc
        if key not in permitted or key in seen:
            raise ClaimValidationError('事实类型、期间、方案或任务范围不匹配')
        seen.add(key)
    if seen != permitted:
        raise ClaimValidationError('缺少必要事实')
    return deepcopy(dict(claims=claims, suggestion=suggestion))


def _money(value):
    if type(value) is not int:
        raise ClaimValidationError('金额必须为整数分')
    return ('-' if value < 0 else '') + f'¥{abs(value) // 100:,}.{abs(value) % 100:02d}'


def render_claims(plan, payload):
    verified = verify_claims(plan, payload)
    registry = {fact['fact_id']: fact for fact in plan['facts'].values()}
    lines = [plan['planner_label']]
    for claim in verified['claims']:
        fact = registry[claim['fact_id']]
        facts, args, statement = fact['facts'], fact['args'], claim['statement']
        if statement == 'savings_target':
            lines.append(f"目标：{args['months']} 个月后达到 {_money(args['target_cents'])}，已有储蓄 {_money(args['saved_cents'])}；每月需存 {_money(facts['required_monthly_cents'])}。")
        elif statement == 'income_expense':
            lines.append(f"已导入记录的月均收入 {_money(facts['average_income_cents'])}、月均净支出 {_money(facts['average_expense_cents'])}。")
        elif statement == 'assumption_baseline':
            lines.append(f"输入假设：每月收入 {_money(args['base_income_cents'])}、未来预算 {_money(args['base_spending_cents'])}；未使用历史账单。")
        elif statement == 'data_coverage':
            lines.append('已导入记录覆盖：' + '、'.join(facts['months']) + '；无法保证账单完整。')
        elif statement == 'scenario_result':
            label = claim['scope']['scenario']
            row = next((r for r in facts['scenarios'] if r['label'] == label), None)
            if row is None:
                raise ClaimValidationError('方案不存在')
            if row['status'] != 'ok':
                lines.append(label + '：缺少分类预算依据，不能推算分类节省金额。')
                continue
            policy = {'A': '保持当前收支假设', 'B': '按所选分类和比例压缩', 'C': '按达标所需金额压缩'}[label]
            if label == 'B':
                rule = next(s for s in args['scenarios'] if s['label'] == 'B')
                policy = '假设' + '、'.join(rule['categories']) + f"各压缩 {rule['cut_pct']}%"
            lines.append(f"{label}（{policy}）：每月压缩 {_money(row['spending_cut_cents'])}，每月结余 {_money(row['monthly_available_cents'])}，期末预计 {_money(row['projected_cents'])}，缺口 {_money(row['gap_cents'])}。")
            if row['negative_cashflow']:
                lines.append(label + ' 存在负现金流，不能作为可行储蓄方案。')
            if row['requested_cut_cents'] > row['spending_cut_cents'] and label == 'C':
                lines.append('C 已达到可压缩预算上限，仍需调整收入或目标。')
    lines.append('口径：' + plan['basis'] + '。模拟依赖假设，不保证能执行或达成；未保存账本、预算或目标。')
    if SUGGESTIONS[verified['suggestion']]:
        lines.append(SUGGESTIONS[verified['suggestion']])
    return '\n'.join(lines)
