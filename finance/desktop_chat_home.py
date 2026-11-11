"""Tk conversation home. The query worker only receives a read-only ledger path."""

from datetime import date
import json
from queue import Empty, Queue
import re
from threading import Event, Thread
import tkinter as tk
from tkinter import ttk

from .analytics import yuan
from .chat_widgets import CHAT_INPUT, SPACE_FILL, RoundedButton, RoundedPanel
from .desktop_agent import open_savings_planner
from .llm import model_status_text
from .qa import answer_question, month_shift
from .storage import Store
from .ui_scale import point_font, px, set_window_geometry
from .ui_theme import BORDER, INK, MUTED, PRIMARY


DINING_QUESTION = '上个月餐饮花了多少？'
SAVINGS_PROMPT = '帮我做一个储蓄计划'
PAGE_ACTIONS = {'records': '前往交易账本', 'budget_page': '前往预算与提醒', 'plan': '前往储蓄规划'}
EDIT_REQUEST = re.compile(
    r'^(?:(?:请问|请|帮我|能不能|能否|可以|我想|想要|如何|怎么)\s*)*'
    r'(?:记一笔|记账|入账|导入|删除|删掉|删一|撤销|恢复|归档|新增|添加|增加|录入|补记|修改|更新|更改|调整|设置|清空)'
    r'|(?:把|将).*(?:改|调|设|删除|删掉|撤销|归档|清空)|(?:改|调|设)(?:成|为|到)\s*\d'
)
SAVINGS_GOAL = re.compile(r'^(?:我)?(?:想|计划|打算)\s*(?:存(?:下|到|够)?|攒(?:够)?|储蓄)\s*[\d,.]+\s*(?:万)?\s*(?:元|块钱?)?$')
EDIT_HISTORY_QUESTION = re.compile(r'^(?:请问\s*)?(?:已|已经)?(?:删除|导入|设置|修改|调整|归档|恢复)(?:了|的|过|后)')
AMOUNT_HEADLINE = re.compile(r'^(?P<label>.+?)\s+(?P<amount>-?[\d,]+\.\d{2} 元)(?P<tail>[。，].*)$')


def _guidance(question):
    request = question.rstrip('。！？?!')
    if request == SAVINGS_PROMPT or SAVINGS_GOAL.fullmatch(request):
        return {'answer': '打开 A/B/C 规划器，填写储蓄目标和期限，即可比较方案。', 'route': 'planner'}
    if EDIT_HISTORY_QUESTION.match(question) or not EDIT_REQUEST.search(question):
        return None
    if '预算' in question:
        return {'answer': '请到预算与提醒页选择月份和分类，再确认要修改的预算。', 'route': 'budget_page'}
    if any(word in question for word in ('储蓄', '目标')):
        return {'answer': '请到储蓄规划页核对目标，再手动操作。', 'route': 'plan'}
    return {'answer': '请到交易账本定位相关记录，再手动操作。', 'route': 'records'}


def _read_answer(path, question, model, cancelled):
    if cancelled.is_set():
        return None
    store = Store(path, read_only=True)
    frame = store.spending_view()
    today = date.today()
    months = set(frame.date.str[:7]) | set(frame.attrs.get('source_months', []))
    months.update(month_shift(today, offset) for offset in (-1, 0))
    budgets = {month: store.budgets(month) for month in months}
    if cancelled.is_set():
        return None
    result = answer_question(frame, question, today=today, budgets=budgets, model=model)
    if result['status'] in ('ok', 'missing'):
        metric = (result.get('trace') or {}).get('formula_id')
        result['route'] = 'budget_page' if metric == 'query.budget' else 'records'
    return result


class ChatHome(tk.Frame):
    def __init__(self, parent, owner, history, query_slots):
        super().__init__(parent, bg='white')
        self.owner = owner
        self.history = history
        self.query_slots = query_slots
        self.generation = 0
        self.poll_id = None
        self.scroll_id = None
        self.cancelled = None
        self.evidence_window = None
        self.bind('<Destroy>', self._on_destroy, add='+')
        header = tk.Frame(self, bg='white')
        header.pack(fill='x', padx=px(24), pady=px((4, 12)))
        title = tk.Frame(header, bg='white')
        title.pack(side='left', fill='x', expand=True)
        owner.label(title, 'AI 对话', 15, bold=True)
        self.model_label = owner.label(title, model_status_text(owner.model_client), 9, MUTED)
        controls = tk.Frame(header, bg='white')
        controls.pack(side='right')
        space = RoundedPanel(controls, fill=SPACE_FILL, radius=12, inset=(6, 3))
        space.pack(side='left', padx=px((0, 12)))
        owner.pack_space_picker(space.body)
        owner.space_picker.configure(style='ChatSpace.TCombobox')
        owner.space_picker.pack_configure(padx=0)
        self.planner_button = RoundedButton(controls, text='储蓄规划', command=self._open_planner)
        self.planner_button.pack(side='left')
        tk.Frame(self, bg=BORDER, height=px(1)).pack(fill='x')

        footer = tk.Frame(self, bg='white')
        footer.pack(side='bottom', fill='x', padx=px(26), pady=px((6, 12)))
        self.composer = RoundedPanel(footer, fill=CHAT_INPUT, radius=20, inset=(14, 9), outline='#d6e1dc')
        self.composer.pack()
        self.question = tk.Text(self.composer.body, height=3, width=64, wrap='word', undo=True, relief='flat',
                                borderwidth=0, highlightthickness=0, bg=CHAT_INPUT, fg=INK,
                                insertbackground=INK, font=point_font(11), padx=px(4), pady=px(3))
        self.question.pack(fill='x')
        self.question.bind('<Return>', self._enter)
        self.question.bind('<Shift-Return>', self._newline)
        actions = tk.Frame(self.composer.body, bg=CHAT_INPUT)
        actions.pack(fill='x')
        self.status = owner.label(actions, 'Enter 发送 · Shift+Enter 换行', 9, MUTED)
        self.status.pack(side='left')
        self.send_button = ttk.Button(actions, text='发送', command=self.submit, style='ChatSend.TButton', cursor='hand2')
        self.send_button.pack(side='right')
        self.stop_button = RoundedButton(actions, text='停止', command=self.stop)
        notice = '金额仅基于已导入账单。'
        if owner.model_client:
            notice += '需要模型解析时，会发送本次问题到已配置的接口。'
        tk.Label(footer, text=notice, bg='white', fg=MUTED, font=point_font(9)).pack(pady=px((6, 0)))

        viewport = tk.Frame(self, bg='white')
        viewport.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(viewport, bg='white', highlightthickness=0, width=1, height=1)
        self.scrollbar = ttk.Scrollbar(viewport, command=self.canvas.yview)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=self._show_scrollbar)
        self.messages = tk.Frame(self.canvas, bg='white', padx=px(20), pady=px(14))
        self.message_window = self.canvas.create_window(0, 0, window=self.messages, anchor='n')
        self.canvas.bind('<Configure>', self._resize_messages)
        self.messages.bind('<Configure>', self._fit_scrollregion)
        self._render()
        self.question.focus_set()

    def _open_planner(self):
        return open_savings_planner(self.owner)

    def _resize_messages(self, event):
        self.canvas.coords(self.message_window, event.width / 2, 0)
        self.canvas.itemconfigure(self.message_window, width=min(event.width, px(790)))
        self._fit_scrollregion()

    def _fit_scrollregion(self, _event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.configure(scrollregion=(0, 0, width, max(height, self.messages.winfo_height())))

    def _show_scrollbar(self, start, end):
        self.scrollbar.set(start, end)
        if self.canvas.winfo_height() <= 1:
            return
        overflow = float(start) > 0 or float(end) < 1
        placed = self.scrollbar.winfo_manager() == 'place'
        if placed and not overflow:
            self.scrollbar.place_forget()
        elif overflow and not placed:
            self.scrollbar.place(relx=1, rely=0, relheight=1, anchor='ne')

    def scroll_messages(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self.canvas:
                self.canvas.yview_scroll(-int(event.delta / 120), 'units')
                return 'break'
            widget = widget.master

    def _render(self):
        for child in self.messages.winfo_children():
            child.destroy()
        if not self.history:
            self._render_welcome()
        for role, message in self.history:
            self._render_message(role, message)
        if self.scroll_id is not None:
            self.after_cancel(self.scroll_id)
        self.scroll_id = self.after_idle(self._scroll_to_latest)

    def _render_welcome(self):
        welcome = tk.Frame(self.messages, bg='white')
        welcome.pack(fill='x', pady=px((35, 16)))
        tk.Label(welcome, text='今天想看看哪笔账？', bg='white', fg=INK, font=point_font(27, True)).pack()
        tk.Label(welcome, text='查收支、看预算，或把储蓄目标变成可比较的计划。',
                 bg='white', fg=MUTED, font=point_font(11)).pack(pady=px((9, 22)))
        suggestions = tk.Frame(welcome, bg='white')
        suggestions.pack(fill='x', padx=px(10))
        suggestions.columnconfigure((0, 1), weight=1, uniform='suggestions')
        prompts = [
            ('消费明细', DINING_QUESTION, lambda: self.submit(DINING_QUESTION)),
            ('收支结余', '上个月结余是多少？', lambda: self.submit('上个月结余是多少？')),
            ('消费排行', '上个月哪个分类支出最高？', lambda: self.submit('上个月哪个分类支出最高？')),
            ('储蓄规划', SAVINGS_PROMPT, self._open_planner),
        ]
        for index, (topic, prompt, command) in enumerate(prompts):
            card = RoundedButton(suggestions, text=topic+'\n'+prompt, command=command, look='card')
            card.grid(row=index//2, column=index%2, sticky='ew', padx=px(5), pady=px(5))
            if index == 0:
                self.dining_button = card

    def _render_message(self, role, message):
        mine = role == 'user'
        row = tk.Frame(self.messages, bg='white')
        row.pack(fill='x', pady=px((7, 11)))
        if mine:
            bubble = RoundedPanel(row, fill='#eaf4ef', radius=16, inset=(14, 8))
            bubble.pack(anchor='e', padx=px((44, 0)))
            card = bubble.body
        else:
            card = tk.Frame(row, bg='white', padx=px(14), pady=px(8))
            card.pack(anchor='w', padx=px((0, 30)))
        labels = []

        def line(text, size=11, color=INK, bold=False):
            labels.append(self.owner.label(card, text, size, color, bold))

        if mine:
            line(message['answer'])
        else:
            line('小财灵', 10, PRIMARY, True)
            headline, _, insight = message.get('display_answer', message['answer']).partition('\n')
            formula = (message.get('trace') or {}).get('formula_id', '')
            single_amount = formula in {'query.expense', 'query.income', 'query.balance', 'query.top',
                                        'query.average_income', 'query.average_expense'}
            amount = AMOUNT_HEADLINE.fullmatch(headline) if single_amount else None
            if amount:
                line(amount['label'], 11, INK, True)
                line(amount['amount'], 25, PRIMARY, True)
                if note := amount['tail'].strip('。， '):
                    line(note, 10, MUTED)
            else:
                line(headline)
            if insight:
                line(insight, 10, MUTED)
            if message.get('parser'):
                line(message['parser'], 9, MUTED)
            self._render_actions(card, message)

        def wrap(event):
            limit = max(px(100), event.width - px(82 if mine else 58))
            for label in labels:
                label.configure(wraplength=limit)

        row.bind('<Configure>', wrap)

    def _render_actions(self, card, message):
        route = message.get('route')
        has_evidence = message.get('evidence') or message.get('trace') or message.get('details')
        if not (route or has_evidence):
            return
        actions = tk.Frame(card, bg=card['bg'])
        actions.pack(fill='x', pady=px((5, 0)))
        if has_evidence:
            RoundedButton(actions, text='查看依据', command=lambda: self.show_evidence(message)).pack(side='left', padx=px((0, 8)))
        if route == 'planner':
            RoundedButton(actions, text='打开 A/B/C 储蓄规划', command=self._open_planner).pack(side='left')
        elif route:
            RoundedButton(actions, text=PAGE_ACTIONS[route], command=lambda: self.owner.show(route)).pack(side='left')

    def _scroll_to_latest(self):
        self.scroll_id = None
        self.canvas.yview_moveto(1)

    def show_evidence(self, result):
        window = self.evidence_window
        if window is None or not window.winfo_exists():
            window = self.evidence_window = tk.Toplevel(self)
            window.title('查看依据 · 回答时的快照')
            set_window_geometry(window, 850, 580)
        else:
            for child in window.winfo_children():
                child.destroy()
        tabs = ttk.Notebook(window)
        tabs.pack(fill='both', expand=True, padx=px(12), pady=px(12))

        def text_tab(title, content):
            page = tk.Frame(tabs, bg='white')
            tabs.add(page, text=title)
            box = tk.Text(page, wrap='word', font=point_font(), padx=px(12), pady=px(10))
            scroll = ttk.Scrollbar(page, command=box.yview)
            scroll.pack(side='right', fill='y')
            box.pack(fill='both', expand=True)
            box.configure(yscrollcommand=scroll.set)
            box.insert('1.0', content)
            box.configure(state='disabled')

        text_tab('计算说明', result.get('details', result['answer']))
        if result.get('evidence'):
            page = tk.Frame(tabs, bg='white')
            tabs.add(page, text='源记录')
            rows = [[row['date'][:10], row['merchant'], yuan(row['amount_cents']), row['category'], row['source_file'], row['source_row']]
                    for row in result['evidence']]
            self.owner.table(page, ['日期', '商户', '金额 / 元', '分类', '源文件', '行号'], rows)
        if result.get('trace'):
            text_tab('完整证据', json.dumps(result['trace'], ensure_ascii=False, indent=2))
        window.deiconify()
        window.lift()

    def _enter(self, _event):
        self.submit()
        return 'break'

    def _newline(self, _event):
        self.question.insert('insert', '\n')
        return 'break'

    def submit(self, question=None):
        if self.cancelled is not None:
            return
        question = (question if question is not None else self.question.get('1.0', 'end-1c')).strip()
        if not question:
            return
        if len(question) > 500:
            self.status.configure(text='问题过长，请缩短到 500 字以内。')
            return
        request = ' '.join(question.split())
        guidance = _guidance(request)
        if guidance is None and not self.query_slots.acquire(blocking=False):
            self.status.configure(text='先前请求仍在结束，请稍后重试。')
            return
        self.question.delete('1.0', 'end')
        self._append('user', {'answer': question})
        if guidance is not None:
            self._reply(guidance)
            return

        self._render()
        self.generation += 1
        self.cancelled = cancelled = Event()
        mailbox = Queue(maxsize=1)
        mode, path, model = self.owner.mode, self.owner.store.path, self.owner.model_client
        slots = self.query_slots
        self._set_busy(True, '正在核对账单…')

        def worker():
            try:
                result = _read_answer(path, request, model, cancelled)
            except Exception:
                result = {'answer': '账单查询暂未完成，请重试或前往交易账本核对。', 'route': 'records'}
            finally:
                slots.release()
            if not cancelled.is_set():
                mailbox.put_nowait(result)

        try:
            Thread(target=worker, daemon=True).start()
        except RuntimeError:
            slots.release()
            self.cancelled = None
            self._reply({'answer': '查询暂时无法启动，请稍后重试。'})
            return
        self._poll(mailbox, self.generation, mode, path)

    def _poll(self, mailbox, generation, mode, path):
        if generation != self.generation:
            return
        self.poll_id = None
        if self.owner.mode != mode or self.owner.store.path != path:
            self.stop()
            return
        try:
            result = mailbox.get_nowait()
        except Empty:
            self.poll_id = self.after(50, self._poll, mailbox, generation, mode, path)
            return
        self.cancelled = None
        self._reply(result)

    def _append(self, role, message):
        self.history.append((role, message))
        del self.history[:-20]

    def _reply(self, result):
        self._append('assistant', result)
        self._set_busy(False, 'Enter 发送 · Shift+Enter 换行')
        self.model_label.configure(text=model_status_text(self.owner.model_client))
        self._render()

    def _set_busy(self, busy, status):
        if busy:
            self.send_button.pack_forget()
            self.stop_button.pack(side='right')
        else:
            self.stop_button.pack_forget()
            self.send_button.pack(side='right')
        self.status.configure(text=status)

    def _invalidate(self):
        self.generation += 1
        if self.cancelled is not None:
            self.cancelled.set()
            self.cancelled = None
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
            self.poll_id = None

    def stop(self):
        self._invalidate()
        self._set_busy(False, '已停止等待。')

    def clear(self):
        self._invalidate()
        self.history.clear()
        self.question.delete('1.0', 'end')
        if self.evidence_window is not None and self.evidence_window.winfo_exists():
            self.evidence_window.destroy()
        self.evidence_window = None
        self._set_busy(False, 'Enter 发送 · Shift+Enter 换行')
        self._render()
        self.question.focus_set()

    def _on_destroy(self, event):
        if event.widget is self:
            self._invalidate()
            if self.scroll_id is not None:
                self.after_cancel(self.scroll_id)
                self.scroll_id = None
