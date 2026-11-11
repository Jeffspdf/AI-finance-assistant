"""Tk conversation home. The query worker only receives a read-only ledger path."""

from datetime import date
import json
from queue import Empty, Queue
import re
from threading import Event, Thread
import tkinter as tk
from tkinter import ttk

from .analytics import yuan
from .desktop_agent import open_savings_planner
from .llm import model_status_text
from .qa import answer_question, month_shift
from .storage import Store
from .ui_scale import point_font, px, set_window_geometry
from .ui_theme import BG, BORDER, MUTED, PRIMARY


DINING_QUESTION = '上个月餐饮花了多少？'
SAVINGS_PROMPT = '帮我做一个储蓄计划'
PAGE_ACTIONS = {'records': '前往交易账本', 'budget_page': '前往预算与提醒', 'plan': '前往储蓄规划'}
EDIT_REQUEST = re.compile(
    r'^(?:请\s*)?(?:帮我\s*)?(?:记一笔|记账|入账|导入|删除|删掉|删一|撤销|恢复|归档|新增|添加|增加|录入|补记|修改|更新|更改|调整|设置|清空)'
    r'|(?:把|将).*(?:改|调|设|删除|删掉|撤销|归档|清空)|(?:改|调|设)(?:成|为|到)\s*\d'
)


def _guidance(question):
    if question.rstrip('。！？?!') == SAVINGS_PROMPT:
        return {'answer': '打开 A/B/C 规划器，填写储蓄目标和期限，即可比较方案。', 'route': 'planner'}
    if not EDIT_REQUEST.search(question):
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
        super().__init__(parent, bg=BG)
        self.owner = owner
        self.history = history
        self.query_slots = query_slots
        self.generation = 0
        self.poll_id = None
        self.scroll_id = None
        self.cancelled = None
        self.bind('<Destroy>', self._on_destroy, add='+')

        header = tk.Frame(self, bg=BG)
        header.pack(fill='x', pady=px((0, 10)))
        owner.label(header, 'AI 对话', 20, bold=True).pack(side='left', padx=px((0, 18)))
        owner.pack_space_picker(header)
        ttk.Button(header, text='新对话', command=self.clear).pack(side='right')
        self.model_label = owner.label(self, model_status_text(owner.model_client), 10, MUTED)
        if owner.model_client:
            owner.label(self, '需要模型解析时，仅发送本次问题到已配置的接口。', 9, MUTED)

        shortcuts = tk.Frame(self, bg=BG)
        shortcuts.pack(fill='x', pady=px((3, 12)))
        self.dining_button = ttk.Button(shortcuts, text=DINING_QUESTION, command=lambda: self.submit(DINING_QUESTION))
        self.dining_button.pack(side='left', padx=px((0, 8)))
        self.planner_button = ttk.Button(shortcuts, text=SAVINGS_PROMPT, command=lambda: open_savings_planner(owner))
        self.planner_button.pack(side='left')

        self.composer = tk.Frame(self, bg='white', padx=px(12), pady=px(9))
        self.composer.pack(side='bottom', fill='x', pady=px((10, 0)))
        self.status = owner.label(self.composer, '只读查询 · 回车发送', 9, MUTED)
        input_row = tk.Frame(self.composer, bg='white')
        input_row.pack(fill='x')
        self.question = ttk.Entry(input_row)
        self.question.pack(side='left', fill='x', expand=True, ipady=px(5), padx=px((0, 8)))
        self.question.bind('<Return>', self._enter)
        self.send_button = tk.Button(input_row, text='发送', command=self.submit, bg=PRIMARY, fg='white',
                                     activebackground='#115e59', activeforeground='white', relief='flat', padx=px(15))
        self.send_button.pack(side='left', fill='y')
        self.stop_button = ttk.Button(input_row, text='停止', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=px((8, 0)))

        viewport = tk.Frame(self, bg='white', highlightthickness=1, highlightbackground=BORDER)
        viewport.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(viewport, bg='white', highlightthickness=0, width=1, height=1)
        scroll = ttk.Scrollbar(viewport, command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.messages = tk.Frame(self.canvas, bg='white', padx=px(16), pady=px(12))
        self.message_window = self.canvas.create_window(0, 0, window=self.messages, anchor='n')
        self.canvas.bind('<Configure>', self._resize_messages)
        self.messages.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self._render()

    def _resize_messages(self, event):
        self.canvas.coords(self.message_window, event.width / 2, 0)
        self.canvas.itemconfigure(self.message_window, width=min(event.width, px(840)))

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
            welcome = tk.Frame(self.messages, bg='white')
            welcome.pack(pady=px(32))
            self.owner.label(welcome, '今天想看看哪笔账？', 20, bold=True)
            self.owner.label(welcome, '按月份或分类查收支，也可以打开储蓄规划比较 A/B/C 方案。', 10, MUTED)
        for role, message in self.history:
            mine = role == 'user'
            card = tk.Frame(self.messages, bg='#e6f4f1' if mine else '#f6f8f7', padx=px(12), pady=px(9))
            card.pack(fill='x', padx=px((60, 0) if mine else (0, 48)), pady=px(5))
            title = '你' if mine else message.get('parser') or '小财灵'
            self.owner.label(card, title, 9, PRIMARY, bold=True)
            text = self.owner.label(card, message.get('display_answer', message['answer']), 11)
            card.bind('<Configure>', lambda event, label=text: label.configure(wraplength=max(px(80), event.width - px(25))))
            route = message.get('route')
            actions = tk.Frame(card, bg=card['bg'])
            if message.get('evidence') or message.get('trace') or message.get('details'):
                ttk.Button(actions, text='查看依据', command=lambda data=message: self.show_evidence(data)).pack(side='left', padx=px((0, 8)))
            if route == 'planner':
                ttk.Button(actions, text='打开 A/B/C 储蓄规划', command=lambda: open_savings_planner(self.owner)).pack(side='left')
            elif route:
                ttk.Button(actions, text=PAGE_ACTIONS[route], command=lambda page=route: self.owner.show(page)).pack(side='left')
            if actions.winfo_children():
                actions.pack(fill='x', pady=px((5, 0)))
            else:
                actions.destroy()
        if self.scroll_id is not None:
            self.after_cancel(self.scroll_id)
        self.scroll_id = self.after_idle(self._scroll_to_latest)

    def _scroll_to_latest(self):
        self.scroll_id = None
        self.canvas.yview_moveto(1)

    def show_evidence(self, result):
        window = tk.Toplevel(self)
        window.title('查看依据 · 回答时的快照')
        set_window_geometry(window, 850, 580)
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

    def _enter(self, _event):
        self.submit()
        return 'break'

    def submit(self, question=None):
        if self.cancelled is not None:
            return
        question = (question if question is not None else self.question.get()).strip()
        if not question:
            return
        if len(question) > 500:
            self.status.configure(text='问题过长，请缩短到 500 字以内。')
            return
        guidance = _guidance(question)
        if guidance is None and not self.query_slots.acquire(blocking=False):
            self.status.configure(text='先前请求仍在结束，请稍后重试。')
            return
        self.question.delete(0, 'end')
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
                result = _read_answer(path, question, model, cancelled)
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
        self._set_busy(False, '只读查询 · 回车发送')
        self.model_label.configure(text=model_status_text(self.owner.model_client))
        self._render()

    def _set_busy(self, busy, status):
        self.send_button.configure(state='disabled' if busy else 'normal')
        self.dining_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
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
        self.question.delete(0, 'end')
        for child in self.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
        self._set_busy(False, '只读查询 · 回车发送')
        self._render()

    def _on_destroy(self, event):
        if event.widget is self:
            self._invalidate()
            if self.scroll_id is not None:
                self.after_cancel(self.scroll_id)
                self.scroll_id = None
