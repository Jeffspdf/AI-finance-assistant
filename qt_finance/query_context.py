"""Local query context; a clarification never overwrites a verified query."""
import re
from urllib.parse import urlsplit

from finance.categories import category_options


class QueryModelReceipt:
    """Record actual query-intent calls without copying prompts, keys or responses."""
    def __init__(self, client):
        self.client = client
        self.calls = []

    def parse_question(self, question):
        self.calls.append('本条问题文本')
        return self.client.parse_question(question)

    def plan_ledger_follow_up(self, payload):
        self.calls.append('本条问题文本、已确认月份与统计类型')
        return self.client.plan_ledger_follow_up(payload)

    def summary(self):
        if not self.calls:
            return '本次账本查询未调用模型。'
        host = urlsplit(getattr(self.client, 'endpoint', '')).hostname or '已配置的模型服务'
        return (f'本次向 {host} 发起 {len(self.calls)} 次条件理解请求：' + '；'.join(dict.fromkeys(self.calls)) +
                '。问题文本可能含商户和金额；未发送账单明细。金额由本地计算。')


def last_query(history, generation):
    for role, payload in reversed(history):
        if role != 'assistant':
            continue
        if payload.get('savings_state') or payload.get('route'):
            return None
        if (payload.get('status') == 'ok' and payload.get('query_state')
                and payload.get('ledger_generation') == generation):
            return payload
    return None


def last_analysis(history):
    """Keep the last analysis while the user inspects read-only ledger evidence."""
    for role, payload in reversed(history):
        if role != 'assistant':
            continue
        if payload.get('analysis_closed') or payload.get('expense_receipt') or payload.get('savings_state'):
            return None
        context = payload.get('analysis_context')
        if context and (context.get('turns') or context.get('question')):
            return context
    return None


def month_only_followup(text):
    """Identify a period replacement as a whole utterance, not a prefix match."""
    return re.fullmatch(
        r'(?:(?:那(?:么)?|单单|单看|仅看|只看|只算|只查|只统计|换成|改成|单独看)\s*)?'
        r'(?P<period>20\d{2}-(?:0[1-9]|1[0-2])|(?:1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])月(?:份)?'
        r'|本月|这个月|上个月|上月)\s*(?:的)?(?:呢|吗)?[？?。！!]*',
        text.strip(),
    )


def analysis_context_from_query(payload, generation):
    """Bridge one successful single-month query into a later analysis task."""
    if not payload or payload.get('ledger_generation') != generation:
        return None
    state = payload.get('query_state') or {}
    scope = payload.get('scope', '')
    if not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', scope):
        return None
    category = state.get('category')
    if category not in category_options('支出'):
        category = None
    return dict(
        category=category,
        periods=[scope],
        question=payload.get('query_text', ''),
        follow_up_question='',
        turns=[],
        ledger_generation=generation,
    )


def normalize_months(text, months, previous, number, token):
    """Resolve bare calendar months without ever widening them to all dates."""
    error = None
    def replace(match):
        nonlocal error
        month = number(match[1])
        choices = sorted(m for m in months if m.endswith(f'-{month:02}'))
        if len(choices) == 1:
            return choices[0]
        # A follow-up can refer to an uncovered month in the established year.
        scope = (previous or {}).get('scope', '')
        if not choices and re.fullmatch(r'20\d{2}-\d{2}', scope):
            return f'{scope[:4]}-{month:02}'
        error = (f'发现多个年份的 {month} 月：{"、".join(choices)}，请补充年份。' if choices else
                 f'账本没有可确定年份的 {month} 月记录，请补充年份；不会改查全部月份。')
        return match[0]
    normalized = re.sub(rf'(?<![\d年月])({token})\s*月(?:份)?(?!\s*(?:\d|[一二三四五六七八九十]+\s*[日号]))', replace, text)
    return normalized, error


def change_contributions(results):
    """Partition projected rows once; refund adjustments retain their signed amount."""
    buckets = {}
    for index, result in enumerate(results):
        for row in result['evidence']:
            key = (row['category'], row['merchant'])
            buckets.setdefault(key, [0, 0])[index] += int(row['amount_cents'])
    return [dict(category=key[0], merchant=key[1], before=values[0], after=values[1],
                 delta=values[1] - values[0]) for key, values in
            sorted(buckets.items(), key=lambda pair: (-abs(pair[1][1] - pair[1][0]), pair[0]))]
