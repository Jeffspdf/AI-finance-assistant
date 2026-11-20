"""Build and revise an unconfirmed expense without accessing the ledger."""
from datetime import date, timedelta
import re
import unicodedata

from finance.categories import classify
from finance.importer import money_to_cents


CHANNELS = ('微信', '支付宝', '银行卡', '现金', '其他')
_AMOUNT = re.compile(r'(?<![\d.])(?:¥\s*)?[+-]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:块钱?\d?(?!\d)|元|人民币)')
_CHINESE_AMOUNT = re.compile(
    r'(?<![零〇一二两三四五六七八九十百千万])'
    r'[零〇一二两三四五六七八九十百千万]+'
)
_NON_MONEY_MEASURE = re.compile(r'^(?:个|位|杯|份|碗|瓶|顿|次|天|年|月|日|号|岁|人|张|件|只|斤|公里)')
_EXPLICIT = re.compile(r'记一笔|记笔|帮我记(?:一下|账)?|记录(?:一下|一笔)|一起记[了上]?|先记(?:着|[\d一二两三四五六七八九十百千万])')
_APPROXIMATE = re.compile(r'大概|差不多|好像|可能|暂估|估计|左右|不到|出头|来块|多[块元]|\d+[块元]多|[十百千]多|小[一二两三四五六七八九]千|[几十]几[十百千块元]|几十|几百|几千|[一二两三四五六七八九]{2,3}[十百千]')
_CANCEL = re.compile(r'(?:算了[，, ]*)?(?:取消(?:记账|这笔)?|不记了|不记(?:这笔)?了?|别记了)(?:吧)?')
_ENTRY = re.compile(r'花了|付了|支付了|消费了|买了|(?:^|\s)支出')
_LOOKUP_COMMAND = re.compile(r'^(?:(?:请|麻烦|帮我|我想|能否|能不能|可以|再|顺便)\s*)*(?:查|看一下|找一下)')
_LOOKUP = re.compile(r'多少|哪一?笔|哪家|哪些|查[询看找]?一下|查[询看找]|找一下|看看|明细|的记录|统计|合计|累计|结余|是否|是不是|有没有')
_UNSUPPORTED = re.compile(r'每[天周月年]|平均|预算|计划|预计|假如|如果|打算|准备|明天|下个月|退款|转账|报销|收入|没花|没付|不想|不要|别记|删除|删掉|修改|撤销|怎么|为什么|上限|美元|日元|港元|欧元|美金')
_EXTRA_AMOUNT = re.compile(r'(?:另加|再加|加了|加上|另付|外加|还付了|再付了|还有)\s*[¥\d零〇一二两三四五六七八九十百千万]')
_CHANNEL_NEGATION = re.compile(r'(?:没有?|未|没带|不带|不用|不是|并非)\s*(?:用|使用|带)?\s*(微信|支付宝|银行卡|现金)')
_DATE = re.compile(r'\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?|(?:(?:前年|去年|今年|明年)\s*)?(?<!\d)\d{1,2}月\d{1,2}[日号]|大前天|前天|昨晚|昨天|今天|今早|今晚|刚才')
_AGREED_AMOUNT = re.compile(r'算([零〇一二两三四五六七八九十百千万]+)吧[，, ]*记一笔')
_CORRECTION = re.compile(
    r'(?:不对[，, ]*(?:应该是|实际(?:是|付了)?|是)?|不是[^，,。]+[，, ]+是|'
    r'搞错了[，, ]*是|而是|实际(?:是|付了)?|应该是|实付|更正为)\s*'
    r'(\d+(?:,\d{3})*(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)'
    r'(?:元|块钱?|人民币)?(?=$|[，,。！!\s]|(?:用)?(?:微信|支付宝|银行卡|现金))'
)
_AMOUNT_CONFLICT = re.compile(r'别(?:按|算|记)|不要(?:按|算|记)|不对|更正|[,，]实际|[,，]实付|而是|不是[\d零〇一二两三四五六七八九十]')
_CHINESE = dict(zip('零一二三四五六七八九', range(10)), 两=2, 〇=0)
_UNITS = dict(十=10, 百=100, 千=1000, 万=10000)


def is_expense_candidate(text):
    text = unicodedata.normalize('NFKC', text)
    explicit = bool(_EXPLICIT.search(text))
    if not explicit and (re.search(r'(?:那|这)笔|[?？]|吗|么[呀呢]?$', text)):
        return False
    return (not (_LOOKUP_COMMAND.search(text) or _LOOKUP.search(text) or _UNSUPPORTED.search(text))
            and bool(_EXPLICIT.search(text) or _ENTRY.search(text)
                     or re.search(r'打车|买(?:点)?菜', text) and _APPROXIMATE.search(text)
                     or re.search(r'结的|付款|付的|结账|刷卡|买单|买了|我那杯|我那份|我付', text)))


def expense_date(text, today):
    match = _DATE.search(text)
    if match:
        value = match[0]
        numbers = re.findall(r'\d+', value)
        try:
            if len(numbers) == 3:
                return date(*map(int, numbers)).isoformat()
            if len(numbers) == 2:
                offset = {'前年': -2, '去年': -1, '明年': 1}.get(value[:2], 0)
                return date(today.year + offset, *map(int, numbers)).isoformat()
        except ValueError:
            return ''
        days = {'大前天': 3, '前天': 2, '昨晚': 1, '昨天': 1}
        return (today - timedelta(days=days.get(value, 0))).isoformat()
    if re.search(r'上周|上个?月|上星期|本月|这[周月]|最近|这些天|星期|周[一二三四五六日天]|\d+月|\d+号|[\d一二三四五六七八九十]+天前|前几天', text):
        return ''
    return today.isoformat()


def payment_channel(text):
    affirmative = _CHANNEL_NEGATION.sub('', text)
    found = [name for name in CHANNELS if name in affirmative]
    return found[0] if len(found) == 1 else ''


def expense_amount(value):
    colloquial = re.fullmatch(r'(\d+)块(?:钱)?(\d)', value.strip())
    if colloquial:
        value = f'{colloquial[1]}.{colloquial[2]}'
    value = re.sub(r'\s*(?:元人民币|人民币|块钱|块|元)$', '', value.strip())
    if re.fullmatch(r'[零〇一二两三四五六七八九十百千万]+', value):
        # Reject colloquial unit omissions such as “一百二” as ambiguous.
        if re.search(r'[百千万][一二两三四五六七八九]$', value):
            return ''
        total, section, digit, previous_unit = 0, 0, None, 10000
        for char in value:
            if char in _CHINESE:
                if digit not in (None, 0):
                    return ''
                digit = _CHINESE[char]
            else:
                unit = _UNITS[char]
                if unit == 10000 and not total:
                    total, section, digit, previous_unit = (section + (digit or 0)) * unit, 0, None, 10000
                    if not total:
                        return ''
                elif unit >= previous_unit or digit == 0 or (digit is None and not (unit == 10 and not section)):
                    return ''
                else:
                    section += (1 if digit is None else digit) * unit
                    previous_unit, digit = unit, None
        value = str(total + section + (digit or 0))
    elif not re.fullmatch(r'¥?\s*\d+(?:,\d{3})*(?:\.\d+)?', value):
        return ''
    try:
        cents = money_to_cents(value)
    except ValueError:
        return ''
    return f'{cents // 100}.{cents % 100:02d}' if cents > 0 else ''


def full_amount_candidates(text):
    """Return maximal literal amount expressions, never inner numeral fragments."""
    candidates = [match[0] for match in _AMOUNT.finditer(text)]
    for match in _CHINESE_AMOUNT.finditer(text):
        if not _NON_MONEY_MEASURE.match(text[match.end():]):
            candidates.append(match[0])
    return candidates


def is_full_amount(value, candidates):
    parsed = expense_amount(value)
    return bool(parsed) and any(parsed == expense_amount(candidate) for candidate in candidates)


def merchant_name(phrase):
    name = phrase.strip(' ，。！？,.')
    # A drink or meal quantity belongs to the purchase, not the merchant.
    return re.sub(r'^(?:\d+|[一二两三四五六七八九十]+)?\s*[杯份碗瓶顿]\s*(?=\S)', '', name, count=1)


def expense_draft(question, today=None):
    text = unicodedata.normalize('NFKC', question).strip()
    if not is_expense_candidate(text):
        return None
    explicit = bool(_EXPLICIT.search(text))
    approximate = amount_is_approximate(text)
    if not explicit and (re.search(r'[?？]|吗|么[呀呢]?$', text) or not (_ENTRY.search(text) or approximate)):
        return None
    matches = list(_AMOUNT.finditer(text))
    corrected = _CORRECTION.search(text)
    if not matches and not explicit and not approximate and not corrected:
        return None
    agreed = _AGREED_AMOUNT.search(text)
    original = text[:corrected.start()] if corrected else text
    purchase_clauses = [part for part in re.split('[，,。]', original) if _ENTRY.search(part)]
    distinct_purchases = sum(bool(_AMOUNT.search(part) or _CHINESE_AMOUNT.search(part))
                             for part in purchase_clauses)
    original_amounts = sum(match.start() < len(original) for match in matches)
    multiple = (len(_DATE.findall(text)) > 1 or '一起记' in text
                or bool(_EXTRA_AMOUNT.search(text)) or original_amounts > 1 or distinct_purchases > 1)
    merchant = ''
    if len(matches) == 1:
        place = re.search(r'在([^，。！？,]{1,30}?)(?:用(?:微信|支付宝|现金|银行卡)|花了|消费|支付|付了|买了)', text)
        after = re.match(r'\s*(?:去|在|买了?|喝了?|吃了?)\s*([^，。！？,]+)', text[matches[0].end():])
        if place or after:
            merchant = re.split(r'(?:用|通过)(?:微信|支付宝|现金|银行卡)', (place or after)[1])[0]
            merchant = merchant_name(merchant) if not place else merchant.strip(' 。！')
    amount = ''
    if not multiple:
        if corrected:
            amount = expense_amount(corrected[1])
        elif not _AMOUNT_CONFLICT.search(text):
            if agreed:
                amount = expense_amount(agreed[1])
            elif len(matches) == 1 and not approximate:
                amount = expense_amount(matches[0][0])
    category = classify(merchant, text, '支出')[0]
    if category == '其他支出' and re.search(r'撸串|烧烤', text):
        category = '餐饮'
    return dict(date=expense_date(text, today or date.today()), merchant=merchant, description=question,
                amount_cents=amount, category=category, channel=payment_channel(text), multiple=multiple)


def amount_is_approximate(text):
    return bool(_APPROXIMATE.search(text) and
                not re.search(r'算[一二两三四五六七八九十百千万]+吧[，, ]*记一笔', text))


def understand_expense(client, question, *, today=None):
    """Accept only literal source phrases. Convert dates and money locally."""
    text = unicodedata.normalize('NFKC', question).strip()
    result = client.understand_expense(text)
    if not isinstance(result, dict):
        raise ValueError('模型返回的记账格式不完整')
    intent = result.get('intent', result.get('type'))
    if intent in ('query', 'unsupported'):
        return None
    if (set(result) - {'intent', 'type', 'amount_text', 'merchant', 'multiple'}
            or intent != 'expense' or ('type' in result and result['type'] != intent)
            or type(result.get('multiple')) is not bool):
        raise ValueError('模型返回的记账格式不完整')
    amount_text, merchant = result.get('amount_text'), result.get('merchant')
    amount_text = '' if amount_text is None else amount_text
    merchant = '' if merchant is None else merchant
    if any(not isinstance(value, str) or len(value) > 60 or (value and value not in text)
           for value in (amount_text, merchant)):
        return None
    local = expense_draft(question, today)
    multiple = bool(local and local['multiple']) or (
        not (local and _CORRECTION.search(text))
        and (result['multiple'] or len(list(_AMOUNT.finditer(text))) > 1 or bool(_EXTRA_AMOUNT.search(text)))
    )
    amount = ''
    if amount_text and not multiple and not _AMOUNT_CONFLICT.search(text) and not amount_is_approximate(text) and is_full_amount(
            amount_text, full_amount_candidates(text)):
        amount = expense_amount(amount_text)
    if local and local['amount_cents'] and not multiple:
        amount = local['amount_cents']
    merchant = merchant_name(merchant) or (local or {}).get('merchant', '')
    return dict(date=expense_date(text, today or date.today()), merchant=merchant,
                description=question, amount_cents=amount, category=classify(merchant, text, '支出')[0],
                channel=payment_channel(text), multiple=multiple)


def revise_expense(draft, question, *, today=None):
    text = unicodedata.normalize('NFKC', question).strip().rstrip('。！!').removesuffix('吧')
    text = re.sub(r'^(?:把)?(?:(?:刚才|刚刚)那笔|这笔)[，, ]*', '', text)
    if _LOOKUP_COMMAND.search(text) or _LOOKUP.search(text) and not re.match(r'^(?:合计|一共|总共)', text):
        return None
    corrected = _CORRECTION.search(text)
    amount = expense_amount(corrected[1]) if corrected else expense_amount(re.sub(r'^(?:(?:就|一共|合计|实际付了|实付|我付了|总共|按|金额(?:改成|改为)?|改成)\s*)', '', text))
    if amount and (not draft['multiple'] or re.match(r'^(?:合计|一共|总共)', text)):
        return dict(draft, amount_cents=amount, multiple=False,
                    channel=payment_channel(text) or draft['channel'])
    if re.fullmatch(r'(?:(?:改成|换成|支付方式改成|支付方式是|我用|用)\s*)?(?:微信|支付宝|银行卡|现金|其他)(?:付(?:的)?|支付)?', text):
        return dict(draft, channel=payment_channel(text))
    when = text.removeprefix('日期改成').removeprefix('改成').strip()
    if _DATE.fullmatch(when):
        return dict(draft, date=expense_date(when, today or date.today()))
    return None


def cancels_expense(question):
    text = unicodedata.normalize('NFKC', question).strip().rstrip('。！!')
    return bool(_CANCEL.fullmatch(text))


def duplicate_expenses(store, data, payment):
    rows = store.source_transactions()
    if rows.empty:
        return []
    match = ((rows.date.str[:10] == data['date'][:10]) & (rows.amount_cents == data['amount_cents'])
             & (rows.kind == '支出') & (rows.channel == payment)
             & ~rows.status.isin(('deleted', 'refund_pending')))
    if data['merchant']:
        match &= rows.merchant.str.strip().str.casefold() == data['merchant'].casefold()
    else:
        match &= rows.description.str.strip().str.casefold() == data['description'].casefold()
    return rows[match].to_dict('records')
