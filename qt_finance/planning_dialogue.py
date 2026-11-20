"""Draft planning conditions. Drafts are never InputFacts until the user confirms."""
import re
import unicodedata
from finance.categories import category_options
from .expense_draft import expense_amount

FIELDS = ('target_cents', 'months', 'saved_cents', 'income_cents', 'budget_cents')
SAVINGS_MONTH_LIMIT = 120
DIGITS = dict(zip('零一二三四五六七八九', range(10)))

def number(text):
    if text.isdigit():
        return int(text)
    if text == '两':
        return 2
    if text.count('十') == 1:
        a, b = text.split('十')
        return (DIGITS.get(a, 1) * 10) + DIGITS.get(b, 0)
    return DIGITS.get(text)

def draft_request(question, previous=None):
    text = ''.join(unicodedata.normalize('NFKC', question).split())
    reduction = re.search(
        r'每(?:个)?月(?P<approx>大约|大概|约|争取)?(?:能|可以|可)?(?:少花|少支出|省下?)(?:个)?'
        r'(?P<amount>[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千万]+)(?:元|块钱?)?', text,
    )
    if not reduction and re.search(r'比.+多存|多少|查一下|查询', text):
        return None
    if not (reduction or re.search('存|攒|储蓄', text)
            or previous and re.search('改|晚|延长|保留|不能|不想|预算|收入', text)):
        return None
    state = dict(previous or {})
    state.setdefault('target_cents', '')
    state.setdefault('months', '')
    if reduction:
        suffix = text[reduction.end():]
        uncertain = reduction['approx'] or re.match(r'多|左右|出头|到|至|[-~～—万千百角分\d.]|,\d', suffix)
        state['monthly_reduction'] = '' if uncertain else expense_amount(reduction['amount'])
        state.setdefault('budget_cents', '')
    target = re.search(r'(?:存|攒)(?:够|下|到)?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)(?:元|块)?', text)
    if target:
        state['target_cents'] = target[1]
    term = re.search(r'([0-9一二两三四五六七八九十]+)个?月', text)
    if '半年' in text:
        state['months'] = '6'
    elif term:
        n = number(term[1])
        if n is not None:
            if re.search(r'(?:晚|延长|推迟)' + re.escape(term[0]), text):
                n += int(state.get('months') or 0)
            state['months'] = str(n)
    protected = set(state.get('protected_categories', []))
    notes = []
    for clause in re.split('[，,。；;]', text):
        if not re.search('不能动|不动|保留|不想影响|别动|不要动|别减少|不能减少|不能减|不要减|不削减|不能碰|别砍|不能砍', clause):
            continue
        matches = [c for c in category_options('支出') if c in clause]
        if '房租' in clause:
            matches.append('居住')
            notes.append('房租暂按整个「居住」类别保护，请核对。')
        if re.search('聚餐|吃饭|外卖', clause):
            matches.append('餐饮')
            notes.append('账单分类不能可靠识别周末聚餐，暂保护整个「餐饮」类别；请确认或调整。')
        if re.search('饭钱|伙食', clause):
            matches.append('餐饮')
            notes.append('饭钱暂按整个「餐饮」类别保护，请核对。')
        protected.update(matches)
        if not matches:
            notes.append('尚不能对应分类的限制：' + clause + '。请在下方选择需要保护的分类。')
    state['protected_categories'] = sorted(protected)
    state['constraint_notes'] = notes or state.get('constraint_notes', [])
    direct_revision = re.fullmatch(
        r'(?:那|那就)?(?:改成|改为|晚|延长|推迟)[0-9一二两三四五六七八九十]+个?月'
        r'(?:[，,](?:周末)?聚餐(?:还是)?保留)?[。！!？?]*', text)
    retained_dining = '聚餐' not in text or '餐饮' in (previous or {}).get('protected_categories', [])
    state['needs_confirmation'] = not (previous and not previous.get('needs_confirmation')
        and direct_revision and retained_dining
        and protected == set(previous.get('protected_categories', [])))
    state['interpretation'] = '本地提取 · 待你确认'
    return state


def understand_request(question, previous, draft, client, cancelled):
    """Only question + current planning fields leave this boundary, never ledger rows."""
    state = dict(draft)
    if cancelled.is_set():
        return None
    try:
        response = client.understand_savings(dict(question=question,
            previous={k: v for k, v in (previous or {}).items() if k in (*FIELDS, 'protected_categories')}))
        if cancelled.is_set():
            return None
        allowed = {*FIELDS, 'protected_categories', 'clarification'}
        if not isinstance(response, dict) or set(response) - allowed:
            raise ValueError('unexpected planning fields')
        for key in FIELDS:
            if key not in response or response[key] is None:
                continue
            value = response[key]
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,2})?', value):
                raise ValueError('invalid input draft')
            if key == 'months' and (not value.isdigit() or not 1 <= int(value) <= SAVINGS_MONTH_LIMIT):
                raise ValueError('invalid horizon')
            state[key] = value
        protected = response.get('protected_categories', [])
        if not isinstance(protected, list) or any(c not in category_options('支出') for c in protected):
            raise ValueError('invalid constraint')
        # A model may add a proposed protection, but never silently remove an existing one.
        state['protected_categories'] = sorted(set(state.get('protected_categories', [])) | set(protected))
        clarification = response.get('clarification', '')
        if not isinstance(clarification, str) or len(clarification) > 300:
            raise ValueError('invalid clarification')
        if clarification:
            state['constraint_notes'] = [*state.get('constraint_notes', []), clarification]
        state['interpretation'] = '模型理解草稿 · 尚未计算，请确认'
    except Exception:
        state = dict(draft)
        state['interpretation'] = '模型未完成理解 · 已保留本地条件，请核对'
    return state
