"""Savings-only desktop preview. Workers never call Tk; nothing saves a goal here."""
from datetime import date
import json
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk

from .agent_runtime import AggregateConsent, plan_savings_agent
from .agent_planner import scenario_goal_values
from .agent_tools import confirmed_inputs
from .agent_verifier import default_claims
from .importer import money_to_cents
from .llm import model_status_text
from .storage import Store
from .ui_scale import px, point_font, set_window_geometry
from .ui_theme import BG, INK, MUTED, PRIMARY, DANGER, scrollable_form


def money(cents):
    return ('-' if cents < 0 else '') + f'¥{abs(cents) // 100:,}.{abs(cents) % 100:02d}'


def ledger_stamp(path):
    result = []
    for name in (str(path), str(path) + '-wal'):
        try:
            stat = Path(name).stat(); result.append((stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            result.append(None)
    return tuple(result)


def prefill_values(plan, scenario):
    """Generate form strings, never a Store write or a saved goal."""
    values = scenario_goal_values(plan, scenario)
    def amount(value): return f'{value // 100}.{value % 100:02d}'
    return {'目标金额 / 元': amount(values['target_cents']), '已有储蓄 / 元': amount(values['saved_cents']),
            '每月收入 / 元': amount(values['income_cents']),
            '方案 A 月预算 / 元': amount(values['baseline_budget_cents']),
            '方案 B 月预算 / 元': amount(values['budget_cents']),
            '截止日期 YYYY-MM-DD': values['deadline']}


class SavingsPlannerWindow(tk.Toplevel):
    def __init__(self, owner, *, runner=plan_savings_agent, today=None, hidden=False):
        super().__init__(owner)
        self.withdraw()
        self.owner, self.runner, self.today = owner, runner, today
        self.origin_path = str(Path(owner.store.path).resolve())
        self.busy = False; self.generation = 0; self.last_result = None
        self.cancel_event = None; self.consent = None; self.poll_id = None; self.watch_id = None
        self.result_stamp = None; self.job_stamp = None; self.job_client = None
        self.title('AI 储蓄规划 · AI理财小助手'); self.configure(bg=BG)
        set_window_geometry(self, 1060, 790, minimum=(760, 540))
        footer = tk.Frame(self, bg=BG, padx=px(18), pady=px(8)); footer.pack(side='bottom', fill='x')
        self.generate_button = self._button(footer, '确认并生成 / 重新计算', self.start, primary=True)
        self.cancel_button = self._button(footer, '取消任务', self.cancel); self.cancel_button.config(state='disabled')
        self._button(footer, '关闭', self.close)
        body = scrollable_form(self, padx=18, pady=12)
        self.body = body
        self._label(body, '把储蓄目标，变成有依据的 A/B/C 方案', 15, bold=True)
        self._label(body, '仅作储蓄情景模拟。所有金额在本机计算，生成结果不会保存预算或目标。', 10, MUTED)
        self._label(body, '当前空间：' + ('我的账本' if getattr(owner, 'mode', 'demo') == 'personal' else '演示空间'), 10, MUTED)
        self.model_label = self._label(body, model_status_text(getattr(owner, 'model_client', None)), 10, PRIMARY)
        form = tk.Frame(body, bg='white', padx=px(12), pady=px(8)); form.pack(fill='x', pady=px(8))
        for col in range(3): form.columnconfigure(col, weight=1, uniform='input')
        self.values = {}
        specs = [('目标金额 / 元', 'target_cents', '6000'), ('期限 / 整数个月', 'months', '6'),
                 ('已有储蓄 / 元', 'saved_cents', '0'), ('假设每月收入 / 元', 'income_cents', ''),
                 ('假设每月预算 / 元', 'budget_cents', '')]
        for index, (title, name, value) in enumerate(specs):
            field = tk.Frame(form, bg='white'); field.grid(row=index // 3, column=index % 3, sticky='ew', padx=px(8), pady=px(4))
            self._label(field, title, 10, MUTED)
            variable = tk.StringVar(self, value=value)
            ttk.Entry(field, textvariable=variable, width=16).pack(fill='x')
            self.values[name] = variable
        mode_field = tk.Frame(form, bg='white'); mode_field.grid(row=1, column=2, sticky='ew', padx=px(8), pady=px(4))
        self._label(mode_field, '数据依据', 10, MUTED)
        self.mode_var = tk.StringVar(self, value='历史账单')
        ttk.Combobox(mode_field, textvariable=self.mode_var, state='readonly', values=['历史账单', '输入假设'], width=16).pack(fill='x')
        self.mode_note = self._label(body, '历史模式使用前三个完整日历月的已导入净额；收入/预算输入仅在假设模式使用。', 10, MUTED)
        self.confirm_var = tk.BooleanVar(self, value=False)
        tk.Checkbutton(body, text='我已核对并确认本次输入的目标、期限和金额', variable=self.confirm_var,
                       bg=BG, anchor='w', font=point_font()).pack(fill='x')
        self.allow_model = tk.BooleanVar(self, value=False)
        consent_text = '允许将本次确认输入与脱敏汇总发送给已配置的模型，用于规划与解释（不发送账单明细、商户名或来源 ID）'
        self.consent_widget = tk.Checkbutton(body, text=consent_text, variable=self.allow_model, command=self.change_consent,
                                            bg=BG, anchor='w', justify='left', font=point_font(), wraplength=px(900))
        self.consent_widget.pack(fill='x')
        self.status = self._label(body, '准备就绪 · 默认使用本地规则规划；授权后才调用已配置的模型。', 10, PRIMARY)
        self.switch_button = self._button(body, '改用输入假设继续', self.use_assumptions)
        self.switch_button.pack_forget()
        self.summary = self._label(body, '', 11)
        cards = tk.Frame(body, bg=BG); cards.pack(fill='x', pady=px(8))
        self.card_labels = {}
        for column, name in enumerate(('A', 'B', 'C')):
            cards.columnconfigure(column, weight=1, uniform='scenario')
            card = tk.Frame(cards, bg='white', padx=px(12), pady=px(10))
            card.grid(row=0, column=column, sticky='nsew', padx=px((0, 7)))
            label = self._label(card, f'方案 {name}\n确认输入后生成', 10)
            label.config(wraplength=px(265)); self.card_labels[name] = label
            card.bind('<Configure>', lambda event, widget=label: widget.config(wraplength=max(px(80), event.width-px(24))))
        actions = tk.Frame(body, bg=BG); actions.pack(fill='x')
        self.trace_button = self._button(actions, '工具轨迹', lambda: self.details('trace'))
        self.evidence_button = self._button(actions, '查看 claims 依据', lambda: self.details('evidence'))
        self.trace_button.config(state='disabled'); self.evidence_button.config(state='disabled')
        transfer = tk.Frame(body, bg=BG); transfer.pack(fill='x')
        self.selected_scenario = tk.StringVar(self, value='B')
        ttk.Combobox(transfer, textvariable=self.selected_scenario, values=['A', 'B', 'C'], state='readonly', width=5).pack(side='left', padx=px((0, 8)))
        self.prefill_button = self._button(transfer, '所选方案 → 原规划表 B（不保存）', self.prefill)
        self.prefill_button.config(state='disabled')
        self._label(body, '输入或账本发生变化后需重新计算。正在进行的网络请求按超时结束；取消或撤销授权后，不接受迟到的模型结果。', 9, MUTED)
        for variable in [*self.values.values(), self.mode_var]: variable.trace_add('write', self.inputs_changed)
        body.bind('<Configure>', self.rewrap, add='+')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.watch_id = self.after(250, self.watch_context)
        if not hidden and owner.state() != 'withdrawn': self.deiconify()

    def _label(self, parent, text, size=10, color=INK, bold=False):
        label = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color, anchor='w', justify='left',
                         font=point_font(size, bold), wraplength=px(920))
        label.pack(fill='x', pady=px(3)); return label

    def _button(self, parent, text, command, primary=False):
        button = tk.Button(parent, text=text, command=command, bg=PRIMARY if primary else 'white',
                           fg='white' if primary else PRIMARY, relief='flat', font=point_font(),
                           padx=px(10), pady=px(7), cursor='hand2')
        button.pack(side='left' if parent is not getattr(self, 'body', None) else 'top', padx=px((0, 7)), pady=px(4))
        return button

    def rewrap(self, event):
        width = max(px(180), event.width)
        for widget in self.body.winfo_children():
            if widget.winfo_class() in ('Label', 'Checkbutton'):
                widget.config(wraplength=width-px(8))

    def same_workspace(self):
        return str(Path(self.owner.store.path).resolve()) == self.origin_path

    def inputs_changed(self, *_):
        self.confirm_var.set(False); self.generation += 1
        if self.cancel_event: self.cancel_event.set()
        if self.consent: self.consent.revoke()
        self.last_result = None; self.prefill_button.config(state='disabled')
        self.trace_button.config(state='disabled'); self.evidence_button.config(state='disabled')
        self.switch_button.pack_forget(); self.summary.config(text='')
        for name, label in self.card_labels.items(): label.config(text=f'方案 {name}\n输入已变化，请重新计算', fg=INK)
        self.status.config(text='请核对新输入并重新勾选确认，再生成规划。', fg=MUTED)

    def change_consent(self):
        if not self.allow_model.get() and self.consent:
            self.consent.revoke()
            if self.busy: self.status.config(text='已撤销授权；等待当前请求结束，随后使用本地规则结果。')

    def use_assumptions(self):
        self.mode_var.set('输入假设')
        self.status.config(text='已切换输入假设。请补齐每月收入/预算，确认后重新生成；不会使用历史账单。')

    def collect_inputs(self):
        if not self.confirm_var.get(): raise ValueError('请先核对输入并勾选确认。')
        values = {}
        for name, variable in self.values.items():
            text = variable.get().strip()
            if not text: continue
            if name == 'months':
                if not re.fullmatch(r'[0-9]{1,3}', text): raise ValueError('期限请填 1–120 的整数个月，不使用模糊日期。')
                values[name] = int(text)
            else:
                values[name] = money_to_cents(text, allow_zero=True)
        return confirmed_inputs(**values)

    def start(self):
        if self.busy: return
        if not self.same_workspace(): self.close(); return
        try: inputs = self.collect_inputs()
        except ValueError as exc: self.status.config(text=str(exc), fg=DANGER); return
        self.last_result = None; self.prefill_button.config(state='disabled')
        self.evidence_button.config(state='disabled'); self.trace_button.config(state='disabled')
        self.summary.config(text='')
        for name, label in self.card_labels.items(): label.config(text=f'方案 {name}\n正在生成…', fg=MUTED)
        self.switch_button.pack_forget()
        self.busy = True; self.generation += 1; generation = self.generation
        self.generate_button.config(state='disabled'); self.cancel_button.config(state='normal')
        self.cancel_event = threading.Event(); self.consent = AggregateConsent(self.allow_model.get())
        event, consent = self.cancel_event, self.consent
        self.job_stamp = ledger_stamp(self.origin_path)
        self.job_client = getattr(self.owner, 'model_client', None)
        client = self.job_client; mode = 'historical' if self.mode_var.get() == '历史账单' else 'assumption'
        self.model_label.config(text=model_status_text(client))
        self.status.config(text='正在检查输入、读取快照并生成规划… 可以取消。', fg=PRIMARY)
        mailbox = queue.Queue()
        origin_path, runner, today = self.origin_path, self.runner, self.today
        def worker():
            try:
                store = Store(origin_path, read_only=True) if mode == 'historical' else None
                result = runner(store, question='按已确认的储蓄目标规划，方案 B 假设餐饮和交通各压缩10%；比较 A/B/C 并说明缺口。',
                                     inputs=inputs, mode=mode, today=today, client=client,
                                     consent=consent, cancel_event=event)
                mailbox.put((True, result))
            except Exception:
                mailbox.put((False, '本次规划未完成，请检查输入或稍后重试；未写入账本。'))
        threading.Thread(target=worker, daemon=True).start()
        self.poll_id = self.after(60, lambda: self.poll(mailbox, generation))

    def poll(self, mailbox, generation):
        self.poll_id = None
        try: ok, result = mailbox.get_nowait()
        except queue.Empty:
            self.poll_id = self.after(60, lambda: self.poll(mailbox, generation)); return
        self.busy = False; self.generate_button.config(state='normal'); self.cancel_button.config(state='disabled')
        if generation != self.generation or not self.same_workspace(): return
        if ledger_stamp(self.origin_path) != self.job_stamp:
            self.inputs_changed(); self.status.config(text='账本已变化，请重新确认并计算。'); return
        if not ok:
            self.status.config(text=result, fg=DANGER); return
        self.last_result = result; self.result_stamp = self.job_stamp
        self.render_result(result)

    def render_result(self, result):
        messages = {'not_configured': '未配置模型', 'consent_required': '未授权发送汇总',
                    'inputs_required': '请补充确认输入', 'timed_out': '模型超时，已转本地规则',
                    'round_limit': '模型达到轮数上限，已转本地规则', 'invalid_action': '模型工具计划无效，已转本地规则',
                    'invalid_claims': '模型回答未通过校验，已转本地规则', 'model_failure': '模型调用失败，已转本地规则',
                    'consent_revoked': '授权已撤销，已转本地规则', 'cancelled': '已取消模型调用'}
        state = result.get('model_status', '')
        self.status.config(text=result['planner_label'] + (' · ' + messages.get(state, state) if state != 'verified' else '') +
                           ('\n' + result['model_message'] if result.get('model_message') else ''), fg=PRIMARY)
        self.model_label.config(text=model_status_text(getattr(self.owner, 'model_client', None)))
        if result['can_use_assumptions']:
            self.switch_button.pack(fill='x', before=self.summary, pady=px(4))
        self.trace_button.config(state='normal'); self.evidence_button.config(state='normal')
        if result['status'] != 'ok':
            self.summary.config(text=result['answer'])
            for name, label in self.card_labels.items(): label.config(text=f'方案 {name}\n等待输入或数据依据', fg=MUTED)
            return
        lines = result['answer'].splitlines()
        self.summary.config(text='\n'.join(lines[1:3]) + '\n口径：' + result['basis'] + ' · 情景模拟，不保证达成。')
        for row in result['simulation']['facts']['scenarios']:
            name = row['label']
            if row['status'] != 'ok':
                text = f'方案 {name}\n缺少分类预算依据\n该方案暂不能计算'
            else:
                text = f"方案 {name}\n期末预计 {money(row['projected_cents'])}\n目标缺口 {money(row['gap_cents'])}\n每月结余 {money(row['monthly_available_cents'])}\n每月压缩 {money(row['spending_cut_cents'])}"
                if name == 'B':
                    policy = next(s for s in result['simulation']['args']['scenarios'] if s['label'] == 'B')
                    text += '\n假设' + '、'.join(policy['categories']) + f"各压缩 {policy['cut_pct']}%"
                if row['negative_cashflow']: text += '\n负现金流，不能作为可行方案'
                elif row['gap_cents']: text += '\n按此假设仍有缺口'
                else: text += '\n仅在假设成立时达到目标'
            self.card_labels[name].config(text=text, fg=DANGER if row.get('negative_cashflow') else INK)
        self.prefill_button.config(state='normal')

    def details(self, kind, *, hidden=False):
        if self.last_result is None: return
        result = self.last_result
        payload = dict(local_result_trace=result['trace'], model_attempt_trace=result.get('model_trace', [])) if kind == 'trace' else dict(
            inputs=result['inputs'], assumptions=result['assumptions'], basis=result['basis'],
            claims=result.get('claims', dict(claims=default_claims(result))), facts=result['facts'])
        window = tk.Toplevel(self); window.withdraw(); window.title('工具轨迹' if kind == 'trace' else 'claims 与计算依据（金额单位：分）')
        set_window_geometry(window, 840, 620, minimum=(600, 400))
        box = tk.Text(window, wrap='word', font=point_font(), padx=px(12), pady=px(10))
        scroll = ttk.Scrollbar(window, command=box.yview); scroll.pack(side='right', fill='y')
        box.config(yscrollcommand=scroll.set); box.pack(fill='both', expand=True)
        box.insert('1.0', json.dumps(payload, ensure_ascii=False, indent=2)); box.config(state='disabled')
        if not hidden and self.state() != 'withdrawn': window.deiconify()
        return window

    def prefill(self):
        if not self.last_result: return
        if not self.same_workspace() or ledger_stamp(self.origin_path) != self.result_stamp:
            self.inputs_changed(); self.status.config(text='空间或账本已变化，请重新计算。'); return
        try: values = prefill_values(self.last_result, self.selected_scenario.get())
        except ValueError as exc: self.status.config(text=str(exc), fg=DANGER); return
        self.owner.show('plan')
        self.owner.plan_start = self.last_result['as_of']
        for name, value in values.items():
            entry = self.owner.inputs[name]; entry.delete(0, 'end'); entry.insert(0, value)
        self.owner.calculate()
        self.status.config(text=f'已将所选方案 {self.selected_scenario.get()} 填入原表的方案 B，尚未保存。请回原页面核对后决定是否保存。')

    def cancel(self):
        self.generation += 1
        if self.cancel_event: self.cancel_event.set()
        if self.consent: self.consent.revoke()
        self.last_result = None; self.prefill_button.config(state='disabled')
        self.trace_button.config(state='disabled'); self.evidence_button.config(state='disabled')
        self.summary.config(text='')
        for name, label in self.card_labels.items(): label.config(text=f'方案 {name}\n本次任务已取消', fg=MUTED)
        self.status.config(text='已取消；等待当前请求结束，不接受迟到结果，不写入账本。', fg=MUTED)

    def watch_context(self):
        self.watch_id = None
        if not self.same_workspace(): self.close(); return
        self.model_label.config(text=model_status_text(getattr(self.owner, 'model_client', None)))
        if self.busy and getattr(self.owner, 'model_client', None) is not self.job_client:
            self.cancel(); self.allow_model.set(False)
            self.status.config(text='模型设置已变化；本次任务已取消，请重新确认授权。')
        if self.last_result and ledger_stamp(self.origin_path) != self.result_stamp:
            self.inputs_changed(); self.status.config(text='账本已变化，旧结果已失效，请重新计算。')
        self.watch_id = self.after(250, self.watch_context)

    def on_destroy(self, event):
        if event.widget is not self: return
        if self.cancel_event: self.cancel_event.set()
        if self.consent: self.consent.revoke()
        for job in (self.poll_id, self.watch_id):
            if job:
                try: self.after_cancel(job)
                except tk.TclError: pass

    def close(self):
        self.cancel(); self.destroy()


def open_savings_planner(owner):
    current = getattr(owner, '_savings_agent_window', None)
    if current is not None and current.winfo_exists():
        if current.same_workspace():
            if owner.state() != 'withdrawn': current.deiconify(); current.lift()
            return current
        current.close()
    owner._savings_agent_window = SavingsPlannerWindow(owner)
    return owner._savings_agent_window
