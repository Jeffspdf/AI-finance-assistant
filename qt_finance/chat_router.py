"""Deterministic conversation routing independent of the Qt page."""
from dataclasses import dataclass
import re
from datetime import date
from typing import Any

from finance.categories import category_in_question

from .expense_draft import cancels_expense, expense_draft, is_expense_candidate, revise_expense
from .finance_dialogue import ANALYSIS_CANCEL, independent_query, is_finance_knowledge, rank_question, wants_analysis
from .monthly_savings import matched_savings_request
from .planning_dialogue import draft_request
from .query_context import analysis_context_from_query, last_analysis, last_query, month_only_followup
from .query_errors import QueryFailure, query_failure
from .services import guidance, savings_follow_up, savings_request


@dataclass(frozen=True)
class ChatRoute:
    kind: str
    payload: Any = None


_QUERY_PATCH = re.compile(r'^(?:那|只|这|换|改|仅|把这|我在|在|然后|再看|顺便|另外|除了|查看|列出)|哪家店|哪个商户')
_DRAFT_REFERENCE = re.compile(r'^(?:把)?(?:刚(?:才|刚)那笔|这笔)|不记|取消记账')
_REACTION = re.compile(r'^(?:这(?:个|样)?|那(?:个|样)?)[，, ]*(?:算|是)?(?:正常|合理|偏高|高|太多)吗[？?。]*$')


@dataclass(frozen=True)
class TurnDecision:
    route: ChatRoute
    text: str
    query: dict | None = None
    analysis: dict | None = None
    savings: dict | None = None


def resolve_turn(text, history, generation, *, expense=None, selected_query=None):
    adjacent = history[-1][1] if history and history[-1][0] == 'assistant' else {}
    query = selected_query if selected_query and selected_query.get('query_state') else last_query(history, generation)
    pending = selected_query if selected_query is not None else adjacent
    clarification = pending.get('pending_turn') if pending.get('ledger_generation') == generation else None
    if clarification and clarification['missing'] == 'period':
        month = month_only_followup(text)
        if text.rstrip('。！!') in ANALYSIS_CANCEL:
            return TurnDecision(ChatRoute('cancel_query'), text)
        if month:
            question = clarification['question'].rstrip('。？！?!')
            period = month['period']
            if clarification['action'] == 'query':
                category = category_in_question(question)
                if category and re.fullmatch(re.escape(category) + r'(?:这(?:一)?块|这方面)?(?:呢|吗)?', question):
                    question = category + '净支出多少？'
                return TurnDecision(ChatRoute('query'), f'{period} {question}')
            return TurnDecision(ChatRoute('analysis'), f'{period} {question}')
    choices = pending.get('choices', {}) if pending.get('ledger_generation') == generation else {}
    if text == '取消' and choices and expense is None:
        return TurnDecision(ChatRoute('cancel_query'), text)
    if re.fullmatch(r'(?:(?:好|就这样吧)[，, ]*)?(?:今天)?先聊到这(?:里|儿)?(?:吧)?[。！!]*', text):
        answer = '好的，今天先聊到这里。'
        if expense is not None:
            answer += '刚才的记账草稿仍待确认，尚未保存。'
        return TurnDecision(ChatRoute('clarify', dict(answer=answer, analysis_closed=True)), text)
    if choices and re.fullmatch(r'(?:去年|今年|前年)(?:那(?:个|月)|的)?[。！!]*', text):
        year = date.today().year - (2 if text.startswith('前年') else 1 if text.startswith('去年') else 0)
        matching = [key for key in choices if key.startswith(f'{year}-')]
        if len(matching) == 1:
            text = matching[0]
    text = choices.get(text, text)
    if pending.get('awaiting_merchant') and pending.get('ledger_generation') == generation and query:
        merchant = re.fullmatch(r'(?:商户|店名)(?:是|为|[:：])[ ]*([\w·（）() -]{1,30})[。]?', text)
        if merchant:
            text = '只看' + merchant[1]
        elif text.rstrip('。') in choices:
            text = choices[text.rstrip('。')]

    if expense is not None:
        if cancels_expense(text):
            return TurnDecision(ChatRoute('cancel_expense'), text)
        revision = revise_expense(expense, text)
        if revision is not None:
            return TurnDecision(ChatRoute('revise_expense', revision), text)
        if expense['multiple'] and re.fullmatch(r'(?:金额)?(?:改成|改为|换成).+', text):
            return TurnDecision(ChatRoute('clarify', dict(
                answer='草稿涉及多笔消费。请先确认要更正哪一笔，并提供该笔的日期或商户。'
            )), text)
        if _DRAFT_REFERENCE.search(text):
            return TurnDecision(ChatRoute('clarify', {
                'answer': '草稿仍在。你要修改金额、日期还是支付方式？也可以说「不记了」。',
            }), text)

    previous_analysis = adjacent.get('analysis_context') or last_analysis(history)
    if _REACTION.fullmatch(text) and adjacent is query:
        previous_analysis = analysis_context_from_query(query, generation)
    elif previous_analysis is None and adjacent is query:
        previous_analysis = analysis_context_from_query(query, generation)

    rank, explicit_month = rank_question(text)
    revising_rank = adjacent.get('report_type') == 'category_rank' and month_only_followup(text)
    if rank or revising_rank:
        return TurnDecision(ChatRoute('rank', bool(explicit_month or revising_rank)), text,
                            analysis=previous_analysis)
    if previous_analysis and text.rstrip('。！!') in ANALYSIS_CANCEL:
        return TurnDecision(ChatRoute('cancel_analysis'), text)
    if _REACTION.fullmatch(text) and previous_analysis is None:
        return TurnDecision(ChatRoute('clarify', query_failure(
            QueryFailure.UNSUPPORTED_CONDITION, '你想评价哪项开销？请告诉我月份和分类。',
        )), text)

    text = re.sub(r'^顺便(?:帮我)?查(?:一下|下|查)?[，, ]*(?=20\d{2}|[0-9一二三四五六七八九十]+月|上月|上个月|本月|这个月|这月)', '', text)
    savings = adjacent.get('savings_state') if adjacent.get('ledger_generation', generation) == generation else None
    resume = re.match(r'^(?:那)?(?:回到|继续)(?:刚才的)?(?:储蓄)?计划[，, ]*', text)
    if resume:
        savings = next((payload['savings_state'] for role, payload in reversed(history)
                        if role == 'assistant' and payload.get('savings_state')
                        and payload.get('ledger_generation') == generation), None)
        if savings is None:
            return TurnDecision(ChatRoute('clarify', dict(answer='当前会话没有可继续的储蓄计划。')), text)
        text = text[resume.end():] or '继续储蓄'
    if savings and 'monthly_reduction' in savings and not savings['monthly_reduction'] and re.fullmatch(
            r'[0-9零一二两三四五六七八九十百千万.]+(?:元|块)?[。！!]*', text):
        amount = text.rstrip('。！!')
        return TurnDecision(ChatRoute('savings', draft_request('每月少花' + amount, savings)), text, savings=savings)
    if savings and 'monthly_reduction' in savings and re.fullmatch(
            r'(?:那)?(?:改成|改为|用|按)?[0-9零一二两三四五六七八九十]+个?月(?:呢|吧)?[。？?]*', text):
        term = re.sub(r'^(?:那)?(?:改成|改为|用|按)?', '', text)
        return TurnDecision(ChatRoute('savings', draft_request('改成' + term, savings)), text, savings=savings)
    route = classify_chat_route(text, savings, previous_analysis)
    if expense is not None and (route.kind == 'expense' or
                                route.kind in ('query', 'navigation') and is_expense_candidate(text)):
        return TurnDecision(ChatRoute('clarify', {
            'answer': '还有一笔草稿等你确认。请先处理或放弃它，再记下一笔。',
        }), text)
    followup = query if (selected_query is not None or choices or month_only_followup(text)
                         or _QUERY_PATCH.search(text)
                         or re.match(r'^(?:不是那个|就刚才|比之前|剩下的)', text)) else None
    category = category_in_question(text)
    if route.kind == 'query' and not followup and category and re.fullmatch(
            re.escape(category) + r'(?:这(?:一)?块|这方面)(?:呢|吗)?[。？?]*', text):
        return TurnDecision(ChatRoute('clarify', dict(
            answer=f'你想查哪个月的{category}净支出？',
            pending_turn=dict(question=text, action='query', missing='period'),
            ledger_generation=generation,
        )), text)
    if (route.kind == 'query' and not followup and not independent_query(text)
            and not matched_savings_request(text) and not re.search(
                r'花了|花费|净支出|消费总额|退款|收入|结余|笔数|预算|占比|排名|多少|哪一|哪几|删除|撤销|新增|导入|改成|设为',
                text)):
        route = ChatRoute('interpret')
    return TurnDecision(route, text, followup, previous_analysis, savings)


def interpret_turn(client, text, *, has_context, pending_question, context=None):
    request = dict(text=text, has_context=has_context, pending_question=pending_question)
    if context:
        request['current_result'] = dict(
            task='analysis', category=context.get('category'),
            periods=context.get('periods'), follow_up_question=context.get('follow_up_question'),
        )
    result = client.interpret_finance_turn(request)
    if (not isinstance(result, dict) or set(result) != {'action', 'reference', 'missing'}
            or result['action'] not in ('analysis', 'expense', 'query', 'clarify')
            or result['reference'] not in ('current', 'none')
            or result['missing'] not in ('intent', 'period', 'object', 'amount', 'none')
            or result['reference'] == 'current' and not has_context):
        return None
    return result


def classify_chat_route(text, previous=None, analysis_context=None):
    """Resolve a user turn before any model call or ledger access."""
    draft = expense_draft(text)
    if draft is not None:
        return ChatRoute('expense', draft)

    savings = savings_request(text)
    if savings is not None and previous is not None:
        savings = dict(previous, **{key: value for key, value in savings.items() if value})
    if savings is None and previous is not None:
        savings = savings_follow_up(text, previous)
    if savings is None and matched_savings_request(text):
        return ChatRoute('query')
    if savings is None and (is_finance_knowledge(text) or wants_analysis(text, analysis_context)
                            or re.match(r'^分析[ ]+20\d{2}-\d{2}', text)):
        return ChatRoute('analysis')
    if savings is None:
        savings = draft_request(text, previous)
    if savings is not None:
        return ChatRoute('savings', savings)

    navigation = guidance(text)
    if navigation is not None:
        return ChatRoute('navigation', navigation)
    return ChatRoute('query')
