"""Conversational finance analysis over a minimal, read-only ledger summary."""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import re

from finance.analytics import yuan
from finance.categories import category_in_question, category_options
from finance.evidence import calculation, effective_frame
from finance.qa import month_shift
from finance.spending_presenter import basis_label
from finance.storage import Store


_FINANCE = re.compile(r'开销|消费|花销|花钱|餐饮|吃饭|伙食|支出|花费|财务|理财|预算|结余|收入|省钱|开支|超支|削减|存钱|备用金|应急金|记账|储蓄|零花钱|负债|财务目标')
_ADVICE = re.compile(
    r'你觉(?:得|着)|你感觉|怎么看|偏高|偏低|太[多大高少]|'
    r'有点[多大高少]|正常吗|合[理适]吗|合不合适|算多|算高|'
    r'算不算(?:超支|高|多|合理)|'
    r'是不是(?:合理|偏高|太多|超支)|是不是(?:应该)?(?:减少|控制|调整)|'
    r'(?:该|应该)(?:设|定|调|控制)[^，。！？?]{0,8}(?:多少|合适|怎么)|'
    r'预算(?:该|应该|应|要)?(?:设|定|调)[^，。！？?]{0,8}(?:多少|合适|怎么)|'
    r'要不要(?:减少|控制|调整)|'
    r'该不该|有没有必要|建议|划算|值得|是否过高|如何|怎么|'
    r'能不能|可以减少|可以省|优化|原因|为什么|怎么办|是什么|有哪些办法'
)
_LOCAL_ANALYSIS = re.compile(
    r'钱包有点遭不住|哪儿最费钱|哪里最费钱|'
    r'乱七八糟的地方花太多|钱都花到哪里|钱都去哪'
)
_CONTEXT_REFERENCE = re.compile(r'^(?:那(?:么)?|这(?:个|样)?|刚才|上面|前面|为什么会这样)')
_QUERY = re.compile(
    r'^(?:(?:那|请|帮我|我想)\s*)*(?:查|看|列出|统计|打开|切换|只看|只查|再查)|'
    r'哪一?笔|哪家|明细|的记录|多少钱|是多少|一共多少|花了多少|还剩多少|累计多少'
)
_WRITE_ONLY = re.compile(r'删掉|删除|撤销|改成|设为|帮我记|记一笔|导入账单|修改预算|调高预算|新增交易')
_MONTH_ONLY = re.compile(r'^(?:那|换成|改成)?\s*(?:20\d{2}-\d{1,2}|(?:20\d{2}年)?[0-9一二三四五六七八九十]{1,3}月(?:份)?|本月|这个月|上个月|上月)\s*(?:呢|的呢)?[？?。！!]*$')
_REPLY_CONTENT = re.compile(
    r'地铁|公交|公共交通|通勤|打车|自驾|步行|骑车|外卖|聚餐|自己做饭|预算|额度|上限|'
    r'经常|偶尔|平时|主要|每月|每周|周末|工作日|没有|有的|不太|'
    r'(?:[0-9一二两三四五六七八九十百千万]+(?:[.点]\d+)?\s*(?:元|块|次|左右))'
)
_RANK_QUESTION = re.compile(
    r'(?:那|请|请问|你(?:能不能|不能|可以|能)|能不能|帮我|给我|查一下|查看|看看|查|看|列出|的)*'
    r'(?:(?:(?:净)?支出|消费|开销)?占比(?:最大|最高)(?:的)?(?:分类|类别)(?:是(?:什么|哪(?:一)?类))?'
    r'|(?:哪(?:一|个|种)?类?(?:分类|类别)?|哪项花销|哪类花销|哪些分类)'
    r'(?:花的钱)?(?:(?:净)?支出|消费)?(?:占(?:当月|本月)?(?:净支出|支出|消费)?(?:比例|比)?)?'
    r'(?:最大|最高|最多)'
    r'|(?:分类|类别)(?:(?:净)?支出|消费)?占比(?:排名|排行)'
    r'|(?:我(?:的)?)?(?:开销|开支|花销|支出|消费)(?:最大|最高|最多)(?:的)?(?:是)?'
    r'哪(?:一|几|个|种)*类(?:型)?(?:的)?(?:支出|开销|消费)?)'
    r'(?:吗|呢|吧)?'
)
_RECENT_RANK = re.compile(r'这几个月|最近几个月|近几个月|最近三个月|近三个月|最近3个月|近3个月')
_RANK_MONTH = re.compile(r'(?:20\d{2}[-年]\d{1,2}月?(?:份)?|[0-9一二三四五六七八九十]+月(?:份)?|上上个月|上上月|上个月|上月|本月|这个月)')
ANALYSIS_CANCEL = frozenset(('取消', '取消分析', '不用了', '先不分析了', '不分析了'))
_ANALYSIS_CLOSE = re.compile(r'^(?:谢谢(?:你)?|好的?|明白了|知道了|收到)[。！!]*$')
_ADDITIONAL_DETAIL = re.compile(r'^(?:另外|补充一下|(?:周末|工作日)(?:我)?(?:偶尔|主要|通常|经常))')
_MAX_HISTORY = 4
_RELATIVE = {'本月': 0, '这个月': 0, '这月': 0, '上个月': -1, '上月': -1, '上上个月': -2, '上上月': -2}
_MONTH = re.compile(r'(20\d{2})[-年]\s*(0?[1-9]|1[0-2])(?:\s*月(?:份)?|(?!\d))')
_CHINESE_MONTHS = {word: idx for idx, word in enumerate('一 二 三 四 五 六 七 八 九 十 十一 十二'.split(), 1)}


def independent_query(text):
    return bool(_QUERY.search(text) or _MONTH_ONLY.fullmatch(text.strip()))


def wants_analysis(text, previous=None):
    if _WRITE_ONLY.search(text):
        return False
    if _LOCAL_ANALYSIS.search(text):
        return True
    if re.search(r'钱不够用|少花一点|太败家|别让我乱花|工资.{0,9}没影|钱咋就没了|'
                 r'有点月光|只能吃土|越花越慌|喝得有点上头|是不是买多了|'
                 r'配不配谈存钱|存钱好难|手头紧|花多少合适', text):
        return True
    if independent_query(text):
        return False
    if _ADVICE.search(text) and (_FINANCE.search(text) or previous and _CONTEXT_REFERENCE.search(text)):
        return True
    return is_analysis_reply(text, previous) or bool(previous and _ADDITIONAL_DETAIL.search(text))


def is_analysis_reply(text, previous=None):
    """Recognize a plausible direct reply to the model's last financial question."""
    if not previous or not previous.get('follow_up_question'):
        return False
    compact = ' '.join(text.split()).strip()
    return bool(compact and len(compact) <= 120
                and compact.rstrip('。！!') not in ANALYSIS_CANCEL
                and not (_ANALYSIS_CLOSE.fullmatch(compact) or _WRITE_ONLY.search(compact)
                         or independent_query(compact) or re.search(r'[?？]|多少|几笔|什么时候|哪个月|讲个笑话', compact))
                and (_REPLY_CONTENT.search(compact) or re.fullmatch(r'\d+(?:\.\d+)?[。！!]*', compact)))


def depends_on_analysis_context(text, previous=None):
    if not previous or independent_query(text) or _WRITE_ONLY.search(text):
        return False
    if is_analysis_reply(text, previous) or _ADDITIONAL_DETAIL.search(text):
        return True
    explicit_scope = category_in_question(text) and (
        _MONTH.search(text) or any(month in text for month in _RELATIVE)
        or re.search(r'[0-9一二三四五六七八九十]+月', text))
    return bool(_ADVICE.search(text) and _CONTEXT_REFERENCE.search(text) and not explicit_scope)


def analysis_periods(question, covered, today, previous):
    time_conditions = re.sub(r'(?:发薪|发工资|领工资)(?:后|后的)第[一二两三四五六七八九十\d]+天', '', question)
    if re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d+日|[一二两三四五六七八九十\d]+天|上周|本周|季度|今年|去年|下月|下个月|未来|全年', time_conditions):
        return None
    explicit = {f'{int(year):04d}-{int(month):02d}' for year, month in _MONTH.findall(question)}
    other = _MONTH.sub('', question)
    if re.search(r'20\d{2}\s*(?:年|-)', other):
        return None
    for name in sorted(_RELATIVE, key=len, reverse=True):
        if name in other:
            explicit.add(month_shift(today, _RELATIVE[name]))
            other = other.replace(name, '')
    for token in re.findall(r'(?<!\d)(1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])\s*月', other):
        month = int(token) if token.isdigit() else _CHINESE_MONTHS[token]
        candidates = [value for value in covered if int(value[5:7]) == month]
        if len(candidates) != 1:
            return None
        explicit.add(candidates[0])
    if len(explicit) > 3 or len(explicit) > 1 and re.search(r'到|至', question):
        return None
    if explicit:
        return sorted(explicit)
    if previous and previous.get('periods'):
        return previous['periods']
    current = today.strftime('%Y-%m')
    return [month for month in covered if month < current][-3:] or [current]


def rank_question(text):
    """Return recognition and whether the user supplied a month."""
    compact = re.sub(r'\s+', '', text).strip('？?。！!')
    if re.search(r'除了|除开|不算|不含|排除', compact):
        return False, bool(_RANK_MONTH.findall(compact))
    recent = _RECENT_RANK.search(compact)
    compact = _RECENT_RANK.sub('', compact)
    months = _RANK_MONTH.findall(compact)
    return bool(_RANK_QUESTION.fullmatch(_RANK_MONTH.sub('', compact))), bool(months or recent)


def category_distribution(expenses, total):
    groups = expenses.groupby('category').amount_cents.sum().sort_index().sort_values(ascending=False, kind='stable')
    show_share = total > 0 and bool((groups >= 0).all())
    return [
        dict(category=str(category), cents=int(cents),
             share=str((Decimal(int(cents) * 100) / total).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))
                   if show_share else None)
        for category, cents in groups.items() if cents > 0
    ]


def leading_categories(items):
    if not items:
        return []
    cutoff = items[min(3, len(items)) - 1]['cents']
    return [item for item in items if item['cents'] >= cutoff]


def finance_summary(store, question, today, previous=None):
    frame = store.spending_view()
    covered = sorted(set(frame.attrs.get('source_months', ())) | set(frame.date.str[:7]))
    periods = analysis_periods(question, covered, today, previous)
    if periods is None:
        return None
    category = (category_in_question(question)
                or ('购物' if re.search(r'淘宝|网购', question) else None)
                or ('餐饮' if re.search(r'奶茶|咖啡|外卖', question) else None)
                or (previous or {}).get('category'))
    if category not in category_options('支出'):
        category = None
    expenses = effective_frame(frame)
    expenses = expenses[expenses.kind == '支出']
    facts = []
    for month in periods:
        fact = dict(month=month, available=month in covered, in_progress=month == today.strftime('%Y-%m'))
        if month in covered:
            month_rows = expenses[expenses.date.str.startswith(month)]
            total = int(month_rows.amount_cents.sum())
            fact['net_expense_cents'] = total
            fact['category_distribution'] = category_distribution(month_rows, total)
            if category:
                selected = month_rows[month_rows.category == category]
                cents = int(selected.amount_cents.sum())
                fact['category_net_cents'] = cents
                fact['category_has_activity'] = not selected.empty
                fact['category_budget_cents'] = store.budgets(month).get(category)
                if total > 0 and 0 <= cents <= total:
                    share = (Decimal(cents * 100) / total).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
                    fact['category_share_pct'] = str(share)
        facts.append(fact)
    return dict(category=category, basis=basis_label(frame.attrs['spending_basis']), months=facts)


def describe_summary(summary):
    text = []
    for fact in summary['months']:
        month = fact['month'] + ('（进行中）' if fact['in_progress'] else '')
        if not fact['available']:
            text.append(month + ' · 无已导入记录')
            continue
        values = [month, '总净支出 ¥' + yuan(fact['net_expense_cents'])]
        if summary['category']:
            values.append(summary['category'] + '净支出 ¥' + yuan(fact['category_net_cents']))
            if 'category_share_pct' in fact:
                values.append('占比 ' + fact['category_share_pct'] + '%')
            budget = fact['category_budget_cents']
            values.append('分类预算未设' if budget is None else '分类预算 ¥' + yuan(budget))
        text.append(' · '.join(values))
        if not summary['category']:
            leaders = leading_categories(fact['category_distribution'])
            if leaders:
                text.append('支出分类排名 · ' + '、'.join(
                    f"{item['category']} ¥{yuan(item['cents'])}" + (f"（{item['share']}%）" if item['share'] else '')
                    for item in leaders))
    return '\n'.join(text)


def analysis_signals(summary):
    """Give the model comparisons; keep exact amounts and percentages in the UI."""
    signals = dict(category=summary['category'], basis=summary['basis'], periods=[])
    previous = None
    for fact in summary['months']:
        current = dict(month=fact['month'], available=fact['available'], in_progress=fact['in_progress'])
        if fact['available']:
            leaders = leading_categories(fact['category_distribution'])
            if leaders and 'category_net_cents' not in fact:
                highest = leaders[0]['cents']
                current['highest_expense_categories'] = [item['category'] for item in leaders if item['cents'] == highest]
                current['other_high_expense_categories'] = [item['category'] for item in leaders if item['cents'] != highest]
                current['shares_applicable'] = leaders[0]['share'] is not None
            if 'category_net_cents' in fact:
                amount = fact['category_net_cents']
                budget = fact['category_budget_cents']
                current['budget_status'] = ('未设置' if budget is None else '已超过' if amount > budget
                                            else '刚好用完' if amount == budget else '尚有剩余')
                current['category_has_activity'] = fact['category_has_activity']
                share = fact.get('category_share_pct')
                if share is not None:
                    current['share_description'] = '占总净支出多数' if Decimal(share) > 50 else (
                        '占总净支出小部分' if Decimal(share) < 20 else '占总净支出相当一部分')
                if previous and 'category_net_cents' in previous:
                    earlier = previous['category_net_cents']
                    current['category_change'] = '比上个可比月份偏高' if amount > earlier else (
                        '比上个可比月份偏低' if amount < earlier else '与上个可比月份相同')
            elif previous and previous['available']:
                earlier = previous['net_expense_cents']
                total = fact['net_expense_cents']
                current['expense_change'] = '比上一观察月份偏高' if total > earlier else (
                    '比上一观察月份偏低' if total < earlier else '与上一观察月份相同')
        signals['periods'].append(current)
        previous = fact
    return signals


def rank_categories(path, question, cancelled, previous=None):
    if cancelled.is_set():
        return None
    store = Store(path, read_only=True)
    frame = store.spending_view()
    covered = sorted(set(frame.attrs.get('source_months', ())) | set(frame.date.str[:7]))
    today = date.today()
    recent = _RECENT_RANK.search(question)
    implied = None if recent else previous
    periods = analysis_periods(question, covered, today, implied)
    if periods is None:
        return dict(answer='请明确要看哪个月份的分类占比，例如「2026-10 哪个类别占比最高？」。', parser='本地分类统计')
    expenses = effective_frame(frame)
    expenses = expenses.loc[expenses.kind == '支出']
    facts = []
    answer = []
    groups = [(month, [month]) for month in periods]
    if recent and len(periods) > 1:
        groups = [('、'.join(periods) + ' 合计', periods)]
    for month, span in groups:
        rows = expenses.loc[expenses.date.str[:7].isin(span)]
        total = int(rows.amount_cents.sum())
        fact = dict(month=month, available=any(p in covered for p in span), in_progress=today.strftime('%Y-%m') in span,
                    net_expense_cents=total, category_distribution=category_distribution(rows, total))
        facts.append(fact)
        leaders = fact['category_distribution']
        if not fact['available']:
            answer.append(f'{month} 没有已导入记录。')
        elif not leaders:
            answer.append(f'{month} 没有正额净支出分类。')
        else:
            highest = '、'.join(item['category'] for item in leaders if item['cents'] == leaders[0]['cents'])
            if leaders[0]['share'] is None:
                answer.append(f'{month} 净支出最高：{highest}。本月净额构成不适合计算占比。')
            else:
                answer.append(f"{month} 占比最高：{highest}，占 {leaders[0]['share']}%。")
    summary = dict(category=None, months=facts)
    if recent:
        answer.append('「这几个月」按最近最多三个有记录的已结束月份统计；如尚无已结束月份，则显示本月记录。')
    if re.search(r'哪几|哪些分类', question):
        answer.append('完整的前三类（含并列项）见下方分类排名。')
    inherited = dict(previous or {})
    analysis_context = dict(
        category=(inherited.get('category')
                  if inherited.get('category') in category_options('支出') else None),
        periods=(inherited.get('periods') or periods) if inherited.get('turns') else periods,
        question=inherited.get('question'),
        follow_up_question=inherited.get('follow_up_question'),
        turns=list(inherited.get('turns', ())),
    )
    return dict(answer='\n'.join(answer), analysis_facts=describe_summary(summary),
                analysis_context=analysis_context, report_type='category_rank',
                parser='本地分类统计', status='ok' if any(f['category_distribution'] for f in facts) else 'no_match', scope='、'.join(periods) + ' · ' + basis_label(frame.attrs['spending_basis']),
                trace=calculation('answer', 'query.category_share_rank',
                                  expenses.loc[expenses.date.str[:7].isin(periods)], dict(periods=periods)),
                privacy_receipt='本次分类排名由本机账本计算，未调用模型。')


def is_finance_knowledge(question):
    text = re.sub(r'^(?:请问|我想(?:知道|了解|问)|想(?:知道|了解|问))[，, ：:]*', '', question.strip())
    return (not re.search(r'我|自己|账本|账单|最近开销|本月|上月|收入为|支出为', text)
            and bool(_FINANCE.search(question) or re.search(r'应急金|复利|现金流', question))
            and bool(re.search(r'是什么|什么是|什么意思|有什么区别', text)
                     or re.match(r'^(?:一般(?:来说|而言)?|通常|作为常识)[，, ]*', text)
                     or re.match(r'^(?:家庭)?预算(?:一般|通常)?(?:应该|应当|该)?怎么(?:做|制定|设置)', text)))


def analyze_finances(path, question, client, cancelled, previous=None):
    if cancelled.is_set():
        return None
    knowledge = is_finance_knowledge(question)
    if knowledge:
        previous = None
    personal = not knowledge and bool(previous or wants_analysis(question) or re.search(
        r'我|本月|这[月个笔]|上(?:上)?个?月|20\d{2}|(?:[\d一二三四五六七八九十]+)月|^每到发薪',
        question,
    ))
    store = Store(path, read_only=True) if personal else None
    summary = finance_summary(store, question, date.today(), previous) if personal else None
    if personal and summary is None:
        bare_month = re.search(r'(?<![\d年])(?P<month>1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])月(?:份)?', question)
        if bare_month and not _MONTH.search(question):
            token = bare_month['month']
            number = int(token) if token.isdigit() else _CHINESE_MONTHS[token]
            frame = store.spending_view()
            covered = set(frame.attrs.get('source_months', ())) | set(frame.date.str[:7])
            alternatives = sorted(m for m in covered if m.endswith(f'-{number:02}'))
            if len(alternatives) > 1:
                choices = {m: '分析 ' + question[:bare_month.start()] + m + question[bare_month.end():]
                           for m in alternatives}
                return dict(answer='账本有多个年份的这个月份。你想分析哪一个？',
                            parser='理财分析 · 补充时间', choices=choices)
        return dict(answer='这个时间范围还需要确认。可以告诉我要分析哪个月份，例如「分析 2026-10 餐饮开销是否偏高」。',
                    parser='理财分析 · 补充时间')
    scope = [item['month'] for item in summary['months']] if summary else []
    context = dict(category=summary['category'], periods=scope) if summary else {}
    prior_turns = list((previous or {}).get('turns', ()))
    if previous and not prior_turns and previous.get('question'):
        prior_turns = [dict(question=previous['question'])]
    context['turns'] = [*prior_turns[-(_MAX_HISTORY - 1):], dict(question=question)]
    result = dict(analysis_context=context, calculated_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    if summary:
        result['analysis_facts'] = describe_summary(summary)
        result['scope'] = '、'.join(scope) + ' · ' + summary['basis'] + ' · 以已导入记录为准'
    if client is None:
        if knowledge and '应急金' in question:
            answer = '应急金是为失业、突发医疗或必要维修等意外状况预留的钱，通常放在能及时取用的地方。'
        elif summary and not any(fact['available'] for fact in summary['months']):
            answer = (f"{result.get('scope', '所选范围')} 没有已导入记录；"
                      '没有记录不等于没有消费。可以导入相应月份账单后再分析。')
        else:
            answer = ('开启「本次会话联网理解」后，我可以结合下面的账本汇总分析是否偏高，并给出具体调整建议。'
                      if summary else '开启「本次会话联网理解」后，可以和我聊这个理财问题。')
        result.update(answer=answer, parser='本地账本汇总' if summary else '理财分析')
        return result
    if cancelled.is_set():
        return None
    payload = {'question': question, 'ledger_summary': analysis_signals(summary) if summary else None}
    if prior_turns:
        payload['analysis_history'] = prior_turns[-_MAX_HISTORY:]
    if is_analysis_reply(question, previous):
        payload.update(previous_follow_up_question=previous['follow_up_question'],
                       follow_up_answer=question)
    sent = ['本次输入']
    if prior_turns:
        sent.append('本轮分析中最多四条近期提问或补充条件')
    if payload.get('previous_follow_up_question'):
        sent.append('模型上一条追问')
    if summary:
        sent.append('分类排名、预算状态与净支出变化等按月比较')
    result['privacy_receipt'] = '已向模型发送' + '、'.join(sent) + '。账本交易明细留在本机。'
    try:
        response = client.analyze_finances(payload)
        answer = response.get('analysis') if isinstance(response, dict) else None
        follow_up_question = (response.get('follow_up_question') or '') if isinstance(response, dict) else ''
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 1800:
            raise ValueError('理财分析回复格式无效')
        if not isinstance(follow_up_question, str) or len(follow_up_question) > 240:
            raise ValueError('理财追问格式无效')
        # All factual numbers render in the local ledger summary. Keep model prose qualitative.
        if re.search(r'\d|[一二两三四五六七八九十百千万]+\s*(?:元|块钱|%|成|个百分点)', answer):
            raise ValueError('模型分析包含未核对的金额或比例')
        if follow_up_question.strip() and not re.search(r'\d|[一二两三四五六七八九十百千万]+\s*(?:元|块钱|%|成|个百分点)', follow_up_question):
            context['follow_up_question'] = follow_up_question.strip()
            context['turns'][-1]['follow_up_question'] = context['follow_up_question']
        result.update(answer=answer.strip(), parser='模型理财分析 · 本机账本汇总' if summary else '模型理财分析')
    except Exception:
        result.update(answer='这次没有拿到可展示的模型分析。你可以重试。', parser='理财分析暂未生成')
    return None if cancelled.is_set() else result
