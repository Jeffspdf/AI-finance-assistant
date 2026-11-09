"""Bounded model-directed savings loop; local calculations remain authoritative."""
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic

from .agent_tools import AgentTools, ToolValidationError, integer
from .agent_planner import plan_with_tools, planning_result, render_rules_plan
from .agent_protocol import TOOL_CATALOG
from .agent_verifier import ClaimValidationError, default_claims, render_claims, verify_claims
from .categories import category_options


@dataclass
class AggregateConsent:
    """Per-session authorization for BOTH planning and explanation payloads."""
    allowed: bool = False

    def revoke(self):
        self.allowed = False


class AgentStopped(ValueError):
    pass


def aggregate_payload(tools):
    """An explicit allowlist, never a recursive dump of tool envelopes."""
    facts = []
    for fact in tools.facts.values():
        data = fact['facts']
        if fact['tool'] == 'data_coverage':
            clean = {k: deepcopy(data[k]) for k in ('months', 'available_months', 'missing_months', 'completeness')}
        elif fact['tool'] == 'spending_overview':
            clean = {k: deepcopy(data[k]) for k in ('average_income_cents', 'average_expense_cents',
                                                  'category_spending_cents', 'requires_explicit_budget')}
            if not set(clean['category_spending_cents']) <= set(category_options('支出')):
                raise ToolValidationError('分类待复核，不能发送未知分类标签')
        elif fact['tool'] == 'scenario_simulate_batch':
            clean = deepcopy(data)
        else:
            raise ToolValidationError('未知事实类型')
        facts.append(dict(step_id=fact['step_id'], fact_id=fact['fact_id'], tool=fact['tool'],
                          status=fact['status'], facts=clean, period=deepcopy(fact['period']),
                          basis=fact['basis'], unit=fact['unit']))
    return dict(mode=tools.mode, basis=tools.basis, as_of=tools.today.isoformat(),
                inputs={name: item.value for name, item in tools.inputs.items()}, facts=facts)


def _action(response, *, first=False):
    if first:
        if not isinstance(response, dict) or set(response) != {'preview_steps', 'first_action'}:
            raise ToolValidationError('初始计划结构无效')
        preview = response['preview_steps']
        if not isinstance(preview, list) or len(preview) > 6:
            raise ToolValidationError('预览步骤无效')
        for step in preview:
            if not isinstance(step, dict) or set(step) != {'tool', 'args'} or not isinstance(step['tool'], str) or step['tool'] not in AgentTools.TOOLS or not isinstance(step['args'], dict):
                raise ToolValidationError('预览包含未知工具或字段')
        response = response['first_action']
    if not isinstance(response, dict) or not isinstance(response.get('action'), str):
        raise ToolValidationError('action 结构无效')
    if response['action'] == 'finish' and set(response) == {'action'}:
        return response
    if response['action'] != 'call_tool' or set(response) != {'action', 'tool', 'args'}:
        raise ToolValidationError('只允许储蓄工具调用或结束')
    if not isinstance(response['tool'], str) or response['tool'] not in AgentTools.TOOLS or not isinstance(response['args'], dict):
        raise ToolValidationError('工具调用无效')
    return response


def _execute(tools, action):
    name, args = action['tool'], action['args']
    existing = tools.facts
    if name == 'data_coverage':
        if tools.mode != 'historical' or args != {'months': 3}:
            raise ToolValidationError('本轮只检查三个历史月份')
    elif name == 'spending_overview':
        if not any(f['tool'] == 'data_coverage' and f['status'] == 'ok' for f in existing.values()):
            raise ToolValidationError('必须先检查历史覆盖')
    elif name == 'scenario_simulate_batch':
        scenarios = args.get('scenarios')
        if not isinstance(scenarios, list) or len(scenarios) != 3 or any(not isinstance(s, dict) for s in scenarios):
            raise ToolValidationError('必须一次模拟 A/B/C')
        expected = {'A': 'current', 'B': 'cut_categories', 'C': 'required_cut'}
        if any(not isinstance(s.get('label'), str) or s['label'] not in expected or s.get('strategy') != expected[s['label']] for s in scenarios) or {s['label'] for s in scenarios} != set(expected):
            raise ToolValidationError('方案标签与策略不匹配')
        cats = next(s for s in scenarios if s['label'] == 'B').get('categories')
        if not isinstance(cats, list) or any(not isinstance(c, str) or c not in category_options('支出') for c in cats):
            raise ToolValidationError('只允许已有支出分类')
        if tools.mode == 'historical' and 'category_spending_cents' not in args:
            raise ToolValidationError('历史方案必须引用分类依据')
    return tools.call(name, args)


def plan_savings_agent(store=None, *, question, inputs=(), mode='historical', today=None,
                       client=None, consent=None, max_rounds=10, time_budget=90,
                       cancel_event=None, clock=monotonic):
    """No outgoing request without current consent; no model amounts are executed.

    Call again with mode='assumption' ONLY after the user selects the returned
    use_assumptions action. Missing historical data never silently changes mode.
    """
    integer(max_rounds, '总模型轮数', 2, 12)
    if type(time_budget) not in (int, float) or not 0 < time_budget <= 180:
        raise ToolValidationError('规划时间预算必须在 0 到 180 秒之间')
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ToolValidationError('请输入范围内的储蓄规划问题')
    tools = AgentTools(store, inputs=inputs, today=today, mode=mode)
    baseline = plan_with_tools(tools.fresh_session(share_cache=True))
    model_trace, model_calls = [], 0
    started = clock()

    def fallback(reason):
        plan = deepcopy(baseline)
        plan.update(model_status=reason, model_calls=model_calls, model_trace=deepcopy(model_trace),
                    total_tool_computations=tools.computation_count,
                    aggregate_authorized=bool(consent is not None and consent.allowed is True))
        plan['answer'] = render_claims(plan, dict(claims=default_claims(plan), suggestion='none')) if plan['status'] == 'ok' else render_rules_plan(plan)
        return plan

    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise AgentStopped('cancelled')
        if consent is None or consent.allowed is not True:
            raise AgentStopped('consent_revoked')
        remaining = time_budget - (clock() - started)
        if remaining <= 0:
            raise AgentStopped('timed_out')
        return min(15, remaining)

    if baseline['status'] != 'ok':
        return fallback('inputs_required')
    if client is None:
        return fallback('not_configured')
    if consent is None or consent.allowed is not True:
        return fallback('consent_required')
    timeout = min(15, time_budget)
    try:
        for round_index in range(max_rounds - 1):  # Reserve a round for claims.
            timeout = check()
            payload = aggregate_payload(tools)
            payload.update(question=question, tools=deepcopy(TOOL_CATALOG), first_turn=round_index == 0,
                           remaining_tool_calls=6 - len(tools.facts))
            model_calls += 1
            response = client.plan_savings_action(payload, timeout=timeout)
            check()  # Discard late responses and responses after revocation.
            action = _action(response, first=round_index == 0)
            if action['action'] == 'finish':
                simulations = [f for f in tools.facts.values() if f['tool'] == 'scenario_simulate_batch' and f['status'] == 'ok']
                if not simulations:
                    raise ToolValidationError('未模拟方案就结束')
                plan = planning_result(tools, 'ok', simulation=simulations[-1])
                plan.update(planner='model_tools', planner_label='模型理解 + 代码计算')
                timeout = check()
                payload = aggregate_payload(tools)
                payload['allowed_claims'] = default_claims(plan)
                model_calls += 1
                claims = client.explain_savings_claims(payload, timeout=timeout)
                check()
                plan['claims'] = verify_claims(plan, claims)
                plan['answer'] = render_claims(plan, plan['claims'])
                plan.update(model_status='verified', model_calls=model_calls,
                            total_tool_computations=tools.computation_count,
                            model_trace=deepcopy(model_trace), aggregate_authorized=True)
                return plan
            result = _execute(tools, action)
            model_trace.append(dict(action='call_tool', tool=action['tool'], step_id=result['step_id'],
                                    cached=tools.trace[-1]['cached'], status=result['status']))
        return fallback('round_limit')
    except AgentStopped as exc:
        return fallback(str(exc))
    except ClaimValidationError:
        return fallback('invalid_claims')
    except ToolValidationError:
        return fallback('invalid_action')
    except Exception as exc:
        # Do not expose API errors, endpoint URLs, model prose or raw exceptions.
        from .llm import connection_error_message
        result = fallback('model_failure')
        result['model_message'] = connection_error_message(exc, timeout_seconds=timeout)
        return result
