"""Read-only, integer-cent tools for the savings-planner MVP.

An instance is one immutable ledger/input snapshot. It is deliberately independent
of ModelClient and never sends its facts or evidence to an external service.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
import re
from inspect import signature
from types import MappingProxyType

from .qa import month_shift


class ToolValidationError(ValueError):
    pass


def integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ToolValidationError(f'{name} 必须是范围内的整数，金额单位为分')
    return value


@dataclass(frozen=True)
class InputFact:
    """Only create after the UI/caller has obtained explicit user confirmation."""
    name: str
    value: int
    confirmed_by_user: bool

    def __post_init__(self):
        allowed = {'target_cents', 'months', 'saved_cents', 'income_cents', 'budget_cents'}
        if self.name not in allowed or self.confirmed_by_user is not True:
            raise ToolValidationError('输入名称无效，或尚未经用户确认')
        integer(self.value, self.name, 1 if self.name in ('target_cents', 'months') else 0,
                120 if self.name == 'months' else None)


def confirmed_inputs(**values):
    """Adapter for already-confirmed form values; not a natural-language parser."""
    return [InputFact(name, value, True) for name, value in values.items()]


def _rounded_average(total, count):
    return int((Decimal(total) / count).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class AgentTools:
    TOOLS = {'data_coverage', 'spending_overview', 'scenario_simulate_batch'}
    MAX_CALLS = 6

    def __init__(self, store=None, *, inputs=(), today=None, mode='historical'):
        if mode not in ('historical', 'assumption'):
            raise ToolValidationError('未知规划模式')
        self.mode = mode
        self.today = today or date.today()
        self.inputs = {}
        for item in inputs:
            if not isinstance(item, InputFact) or item.name in self.inputs:
                raise ToolValidationError('需要唯一且经过确认的 InputFact')
            self.inputs[item.name] = item
        self.inputs = MappingProxyType(self.inputs)
        self._results = {}
        self._cache = {}
        self._trace = []
        self._frame = None
        if mode == 'historical':
            if store is None:
                raise ToolValidationError('历史模式需要账本')
            self._frame = store.spending_view().copy(deep=True)
            if self._frame.attrs.get('projection') != 'spending_view':
                raise ToolValidationError('必须使用净额投影')
            self.basis = self._frame.attrs['spending_basis']
        else:
            self.basis = 'input_assumption'

    @property
    def trace(self):
        return deepcopy(self._trace)

    @property
    def facts(self):
        return deepcopy(self._results)

    def _resolve(self, value):
        if isinstance(value, dict):
            if '$input' in value:
                if set(value) != {'$input'} or not isinstance(value['$input'], str):
                    raise ToolValidationError('无效输入引用')
                item = self.inputs.get(value['$input'])
                if item is None:
                    raise ToolValidationError('缺少已确认输入：' + value['$input'])
                return item.value
            if '$ref' in value:
                ref = value['$ref']
                if set(value) != {'$ref'} or not isinstance(ref, str) or not re.fullmatch(r'S[1-6]\.[A-Za-z_]+', ref):
                    raise ToolValidationError('无效事实引用')
                step, field = ref.split('.')
                result = self._results.get(step)
                if result is None or result['status'] != 'ok' or field not in result['facts']:
                    raise ToolValidationError('引用不存在、未执行或不可用的事实：' + ref)
                return deepcopy(result['facts'][field])
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def call(self, tool, args):
        if tool not in self.TOOLS or not isinstance(args, dict):
            raise ToolValidationError('工具或参数不在白名单内')
        # Monetary arguments must retain provenance even when their value is zero.
        if tool == 'scenario_simulate_batch':
            for key in ('target_cents', 'months', 'saved_cents'):
                if args.get(key) != {'$input': key}:
                    raise ToolValidationError(key + ' 必须引用对应的已确认输入')
            for key, input_name in (('base_income_cents', 'income_cents'), ('base_spending_cents', 'budget_cents')):
                expected = {'$input': input_name} if self.mode == 'assumption' else {
                    '$ref': self._overview_ref('average_income_cents' if input_name == 'income_cents' else 'average_expense_cents')}
                if args.get(key) != expected:
                    raise ToolValidationError(key + ' 必须引用当前模式的收入/预算事实')
            if 'category_spending_cents' in args:
                if self.mode != 'historical' or args['category_spending_cents'] != {'$ref': self._overview_ref('category_spending_cents')}:
                    raise ToolValidationError('分类金额必须引用同一收支概览')
        resolved = self._resolve(args)
        key = json.dumps([tool, resolved], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if key in self._cache:
            result = deepcopy(self._cache[key])
            self._trace.append(dict(tool=tool, step_id=result['step_id'], cached=True))
            return result
        if len(self._results) >= self.MAX_CALLS:
            raise ToolValidationError('已达到六次工具调用上限')
        handler = getattr(self, '_' + tool)
        try:
            signature(handler).bind(**resolved)
        except TypeError as exc:
            raise ToolValidationError('工具参数缺失或包含未知字段') from exc
        facts, status, warnings, periods, evidence = handler(**resolved)
        if tool == 'scenario_simulate_batch':
            # Preserve source provenance through the calculation boundary.
            for previous in self._results.values():
                if previous['tool'] == 'spending_overview':
                    evidence = sorted(set(evidence) | set(previous['evidence_ids']))
        step = 'S' + str(len(self._results) + 1)
        result = dict(step_id=step, fact_id='F' + step[1:], tool=tool, args=deepcopy(resolved),
                      status=status, facts=facts, category=None, period=periods, basis=self.basis,
                      unit='CNY_cents', evidence_ids=evidence,
                      assumptions=['仅统计已导入记录，无法保证账单完整'] if self.mode == 'historical' else
                                  ['基于用户确认的输入假设，未使用历史账单'], warnings=warnings)
        result['input_fact_ids'] = ['IN:' + name for name in self.inputs]
        self._results[step] = deepcopy(result)
        self._cache[key] = deepcopy(result)
        self._trace.append(dict(tool=tool, step_id=step, cached=False))
        return deepcopy(result)

    def _overview_ref(self, field):
        for step, result in self._results.items():
            if result['tool'] == 'spending_overview' and result['status'] == 'ok':
                return step + '.' + field
        raise ToolValidationError('请先执行可用的收支概览')

    def _months(self, months):
        integer(months, '历史月份数', 1, 12)
        return [month_shift(self.today, -i) for i in range(months, 0, -1)]

    def _historical(self):
        if self._frame is None:
            raise ToolValidationError('假设模式不读取历史账本')
        return self._frame

    def _data_coverage(self, months):
        frame = self._historical()
        periods = self._months(months)
        present = set(frame.date.str[:7])
        missing = [m for m in periods if m not in present]
        selected = frame[frame.date.str[:7].isin(periods)]
        return (dict(months=periods, available_months=[m for m in periods if m in present],
                     missing_months=missing, completeness='unknown',
                     sources=sorted(set(selected.source_file.dropna())) if 'source_file' in selected else []),
                'insufficient_data' if missing else 'ok',
                ['部分月份没有可统计记录，请导入或提供收入和预算假设'] if missing else [],
                periods, self._evidence(selected))

    @staticmethod
    def _evidence(frame):
        ids = set(str(v) for v in frame.id)
        for column in ('original_txn_id', 'refund_txn_id', 'refund_link_id'):
            if column in frame:
                ids.update(str(v) for v in frame[column].dropna())
        for decision in frame.attrs.get('link_decisions', []):
            if ids.intersection(decision['transaction_ids']):
                ids.add(str(decision['id']))
        return sorted(ids)

    def _spending_overview(self, period):
        if period != 'last_3_months':
            raise ToolValidationError('MVP 仅支持前三个完整日历月')
        frame = self._historical()
        periods = self._months(3)
        selected = frame[frame.date.str[:7].isin(periods)]
        missing = [m for m in periods if not selected.date.str.startswith(m).any()]
        if missing:
            return (dict(missing_months=missing), 'insufficient_data',
                    ['历史月份不足，不将缺失月份当作零'], periods, self._evidence(selected))
        income = int(selected.loc[selected.kind == '收入', 'amount_cents'].sum())
        expenses = selected[selected.kind == '支出']
        expense = int(expenses.amount_cents.sum())
        categories = {str(k): _rounded_average(int(v), 3)
                      for k, v in expenses.groupby('category').amount_cents.sum().items()}
        avg_income, avg_expense = _rounded_average(income, 3), _rounded_average(expense, 3)
        return (dict(average_income_cents=avg_income, average_expense_cents=avg_expense,
                     category_spending_cents=categories,
                     requires_explicit_budget=avg_expense < 0 or avg_income < 0), 'ok',
                ['历史均值为负，请确认非负的未来收入和预算假设'] if avg_expense < 0 or avg_income < 0 else [],
                periods, self._evidence(selected))

    def _scenario_simulate_batch(self, target_cents, months, saved_cents,
                                 base_income_cents, base_spending_cents, scenarios,
                                 category_spending_cents=None):
        integer(target_cents, '目标', 1)
        integer(months, '期限', 1, 120)
        for name, value in (('已有储蓄', saved_cents), ('收入', base_income_cents), ('预算', base_spending_cents)):
            integer(value, name)
        if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 3:
            raise ToolValidationError('只允许一至三个方案')
        remaining = max(0, target_cents - saved_cents)
        required = (remaining + months - 1) // months
        available = base_income_cents - base_spending_cents
        results = []
        labels = set()
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                raise ToolValidationError('方案必须是对象')
            label, strategy = scenario.get('label'), scenario.get('strategy')
            if label not in ('A', 'B', 'C') or label in labels:
                raise ToolValidationError('方案标识无效或重复')
            labels.add(label)
            allowed = {'label', 'strategy'} | ({'categories', 'cut_pct'} if strategy == 'cut_categories' else set())
            if set(scenario) != allowed:
                raise ToolValidationError('方案参数不匹配，不允许模型填金额')
            requested = cut = 0
            if strategy == 'cut_categories':
                cats = scenario['categories']
                if not isinstance(cats, list) or not cats or any(not isinstance(c, str) for c in cats) or len(set(cats)) != len(cats):
                    raise ToolValidationError('分类必须唯一且非空')
                pct = integer(scenario['cut_pct'], '压缩比例', 0, 100)
                if category_spending_cents is None or any(c not in category_spending_cents for c in cats):
                    results.append(dict(label=label, strategy=strategy, status='insufficient_data',
                                        reason='缺少所选分类的预算依据，不能推算分类节省金额'))
                    continue
                requested = sum(int((Decimal(max(0, category_spending_cents[c])) * pct / 100)
                                    .quantize(Decimal('1'), rounding=ROUND_HALF_UP)) for c in cats)
                cut = min(requested, base_spending_cents)
            elif strategy == 'required_cut':
                requested = max(0, required - available)
                cut = min(requested, base_spending_cents)
            elif strategy != 'current':
                raise ToolValidationError('未知模拟策略')
            monthly = available + cut
            projected = saved_cents + months * monthly
            results.append(dict(label=label, strategy=strategy, status='ok',
                                spending_cut_cents=cut, requested_cut_cents=requested,
                                monthly_available_cents=monthly, projected_cents=projected,
                                gap_cents=max(0, target_cents - projected),
                                goal_met=projected >= target_cents,
                                feasible=projected >= target_cents and monthly >= 0,
                                negative_cashflow=monthly < 0,
                                conditional=strategy != 'current'))
        return (dict(required_monthly_cents=required, scenarios=results), 'ok',
                ['模拟仅反映输入假设，不保证能够执行或实现目标'],
                dict(start=self.today.isoformat(), months=months), [])
