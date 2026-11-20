"""Read-only conversational view and reusable evidence dialog."""

from datetime import date
import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
    QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from finance.agent_planner import scenario_goal_values
from finance.agent_runtime import AggregateConsent
from finance.analytics import yuan
from finance.importer import money_to_cents
from finance.qa import month_shift
from finance.spending_presenter import basis_label, spending_totals
from finance.categories import category_options
from .services import ledger_coverage, query_ledger, savings_inputs, savings_plan
from .chat_router import interpret_turn, resolve_turn
from .finance_dialogue import analyze_finances, depends_on_analysis_context, rank_categories
from .expense_draft import is_expense_candidate, understand_expense
from .planning_dialogue import SAVINGS_MONTH_LIMIT, understand_request
from .query_context import QueryModelReceipt
from .query_errors import QueryFailure, query_failure
from .session_history import MAX_MESSAGES, MAX_SESSIONS, MAX_TITLE, SessionHistory
from .ui import ComboBox, Switch, button, card, confirm, dialog_buttons, label, readonly_text, table


DINING = '上个月餐饮花了多少？'
PROMPTS = (
    ('消费明细', DINING),
    ('收支结余', '上个月结余是多少？'),
    ('消费排行', '上个月哪个分类支出最高？'),
    ('储蓄规划', '帮我做一个储蓄计划'),
)
EVIDENCE_COLUMNS = (
    ('日期', lambda row: display_date(row['date'])), ('商户', 'merchant'), ('金额 / 元', lambda row: yuan(row['amount_cents'])),
    ('类型', 'kind'), ('分类', 'category'), ('来源', 'source_file'), ('行号', 'source_row'),
)


def display_date(value):
    text = str(value)
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].*)?', text):
        return text[:10]
    return text


class Composer(QPlainTextEdit):
    submitted = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('prompt')
        self.setFixedHeight(72)
        self.setPlaceholderText('问问账本，或记一笔：今天我花了 100 元喝星巴克…')
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)
                and not self.composing):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Evidence(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('查看依据 · 回答时的快照')
        self.resize(850, 550)
        self.tabs = QTabWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(button('关闭', self.close), alignment=Qt.AlignmentFlag.AlignRight)

    def present(self, result):
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()
        self.tabs.addTab(readonly_text(result.get('details', result['answer'])), '计算说明')
        if result.get('evidence'):
            view, _ = table(EVIDENCE_COLUMNS, result['evidence'])
            self.tabs.addTab(view, '源记录')
        if result.get('trace'):
            self.tabs.addTab(readonly_text(result['trace']), '完整证据')
        self.show()
        self.raise_()
        self.activateWindow()


class ChatPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.sessions = {'demo': [[]], 'personal': [[]]}
        self.session_titles = {'demo': [''], 'personal': ['']}
        self.active = {'demo': 0, 'personal': 0}
        self.saved = {mode: SessionHistory(owner.workspace.directory, mode) for mode in self.sessions}
        self.loaded = set()
        self.history_errors = {}
        self.evidence = Evidence(self)
        self.last_result = None
        self.scroll_epoch = 0
        self.follow_range_epoch = None
        self.busy = False
        self.async_epoch = 0
        self.savings_consent = None
        self.ledger_generation = 0
        self.has_sources = False
        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 10, 20, 12)
        column = QWidget()
        column.setMaximumWidth(820)
        outer.addStretch(1)
        outer.addWidget(column, 100)
        outer.addStretch(1)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.coverage = label('', 'muted')
        coverage_row = QHBoxLayout()
        coverage_row.addWidget(self.coverage, 1)
        coverage_row.addWidget(button('数据覆盖详情', self.show_coverage))
        layout.addLayout(coverage_row)
        self.snapshot, summary = card()
        summary.setContentsMargins(14, 7, 14, 8)
        summary.setSpacing(4)
        snapshot_header = QHBoxLayout()
        self.snapshot_title = label('', 'muted')
        snapshot_header.addWidget(self.snapshot_title, 1)
        snapshot_header.addWidget(button('打开财务概览', self.open_snapshot))
        summary.addLayout(snapshot_header)
        metrics = QHBoxLayout()
        self.snapshot_values = {}
        for key, title in (('income_cents', '收入'), ('net_cents', '净支出'),
                           ('balance_cents', '结余'), ('budget_cents', '预算剩余')):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(label(title, 'muted'))
            value = self.snapshot_values[key] = label('—', 'snapshotAmount')
            cell.addWidget(value)
            metrics.addLayout(cell, 1)
        summary.addLayout(metrics)
        layout.addWidget(self.snapshot)
        self.history_warning = label('', 'warning')
        self.history_warning.hide()
        layout.addWidget(self.history_warning)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName('messageContent')
        self.messages = QVBoxLayout(content)
        self.messages.setContentsMargins(6, 10, 6, 10)
        self.messages.addStretch(1)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        self.latest = button('回到最新消息 ↓', self.follow)
        layout.addWidget(self.latest, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.latest.hide()
        self.scroll.verticalScrollBar().valueChanged.connect(self.update_latest)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.update_latest)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.follow_new_range)

        composer = QFrame()
        composer.setObjectName('composer')
        form = QVBoxLayout(composer)
        form.setContentsMargins(12, 8, 12, 8)
        self.input = Composer()
        self.input.submitted.connect(self.submit_input)
        self.input.textChanged.connect(self.sync_controls)
        form.addWidget(self.input)
        actions = QHBoxLayout()
        self.expense_pending = None
        self.expense_generation = None
        self.use_model = Switch('本次会话联网理解')
        self.use_model.setToolTip('联网时可能发送问题文本和必要的查询条件。新对话、切换会话或账本时关闭。')
        self.use_model.clicked.connect(self.change_online_consent)
        actions.addWidget(self.use_model)
        actions.addStretch(1)
        self.send = button('↑', self.submit_input, primary=True)
        self.send.setObjectName('send')
        self.send.setAccessibleName('发送')
        self.send.setToolTip('发送 · Enter')
        self.send.setFixedSize(38, 38)
        self.stop_button = button('停止等待', self.stop)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.send)
        form.addLayout(actions)
        layout.addWidget(composer)
        self.status = label('Enter 发送 · Shift+Enter 换行 · 金额依据已导入记录', 'muted')
        layout.addWidget(self.status)
        self.reload()

    def active_history(self, mode=None):
        mode = mode or self.owner.workspace.mode
        return self.sessions[mode][self.active[mode]]

    def refresh(self):
        store = self.owner.workspace.store
        coverage = self.coverage_data = ledger_coverage(store)
        has_sources = bool(coverage['count'])
        if has_sources != self.has_sources:
            self.has_sources = has_sources
            if not self.active_history():
                self.empty.hide()
                self.empty.deleteLater()
                self.welcome()
        self.snapshot.setVisible(has_sources and not self.active_history())
        months = coverage['months']
        shown = '、'.join(months[-4:]) or '尚无账单'
        if len(months) > 4:
            shown = f'{len(months)} 个月 · 最近 {shown}'
        self.coverage.setText(f"账本覆盖 · {shown} · {coverage['count']} 条来源记录")
        self.coverage.setToolTip('、'.join(months) + '\n有记录的月份仍可能存在未导入交易。')
        frame = store.spending_view()
        month = self.snapshot_month = date.today().strftime('%Y-%m')
        data = frame.loc[frame.date.str.startswith(month)].copy()
        covered = month in frame.attrs.get('source_months', ()) or not data.empty
        totals = spending_totals(data)
        budgets = store.budgets(month) if covered else {}
        expenses = data.loc[(data.kind == '支出') & data.category.isin(budgets), 'amount_cents']
        totals['budget_cents'] = sum(budgets.values()) - int(expenses.sum()) if budgets else None
        for key, value in self.snapshot_values.items():
            cents = totals[key] if covered else None
            value.setText('¥' + yuan(cents) if cents is not None else '—')
            if key == 'budget_cents':
                hint = '本月未设置预算' if cents is None else '只汇总已设预算分类的预算及净支出。'
            else:
                hint = ''
            value.setToolTip(hint if covered else '本月尚无已导入记录')
        coverage_label = '' if covered else ' · 本月暂无账单'
        self.snapshot_title.setText(f'{month} · {basis_label(totals["basis"])}{coverage_label}')

    def open_snapshot(self):
        self.owner.navigate('overview')
        picker = self.owner.pages['overview'].month
        picker.setCurrentIndex(picker.findData(self.snapshot_month))

    def show_coverage(self):
        coverage = self.coverage_data
        months = coverage['months']
        missing = []
        if months:
            start = date.fromisoformat(months[0] + '-01')
            end = date.fromisoformat(months[-1] + '-01')
            observed = set(months)
            intervals = (end.year - start.year) * 12 + end.month - start.month + 1
            missing = [month_shift(start, offset) for offset in range(intervals)
                       if month_shift(start, offset) not in observed]
        dialog = QDialog(self)
        dialog.setWindowTitle('账本覆盖详情')
        dialog.resize(510, 340)
        layout = QVBoxLayout(dialog)
        text = (f"当前空间  {self.owner.space.currentText()}\n"
                f"来源记录  {coverage['count']} 条\n"
                f"最近导入（UTC）  {coverage['latest'] or '暂无批次'}\n\n"
                f"有记录月份\n{'、'.join(months) or '尚无账单'}\n\n"
                f"首末月之间无记录的月份\n{'、'.join(missing) or '—'}\n\n"
                '有记录的月份仍可能存在未导入的交易。')
        layout.addWidget(readonly_text(text))
        layout.addWidget(button('打开交易账本', dialog.accept), alignment=Qt.AlignmentFlag.AlignRight)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.owner.navigate('ledger')

    def sync_sessions(self):
        mode = self.owner.workspace.mode
        count = sum(bool(rows) for rows in self.sessions[mode])
        self.owner.chat_sessions.setText(f'历史会话（{count}）')

    def open_history(self):
        mode = self.owner.workspace.mode

        dialog = QDialog(self.owner)
        dialog.setWindowTitle('历史会话')
        dialog.resize(510, 410)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f'{self.owner.space.currentText()} · 仅显示当前空间', 'muted'))
        search = QLineEdit()
        search.setPlaceholderText('搜索会话标题或消息')
        search.setAccessibleName('搜索会话')
        layout.addWidget(search)
        sessions = QListWidget()
        sessions.setObjectName('sessionList')
        sessions.setAccessibleName('历史会话列表')
        layout.addWidget(sessions, 1)
        empty = label('', 'muted')
        layout.addWidget(empty)
        error = label('', 'error')
        error.hide()
        layout.addWidget(error)
        controls = dialog_buttons(QDialogButtonBox.StandardButton.Open, action='打开会话')
        open_button = controls.button(QDialogButtonBox.StandardButton.Open)
        rename = controls.addButton('重命名', QDialogButtonBox.ButtonRole.ActionRole)
        delete = controls.addButton('删除会话', QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(controls)

        def refresh():
            keyword = search.text().strip().casefold()
            sessions.clear()
            for index in reversed(range(len(self.sessions[mode]))):
                rows = self.sessions[mode][index]
                messages = [' '.join((value if role == 'user' else value.get('display_answer', value['answer'])).split())
                            for role, value in rows]
                title = self.session_titles[mode][index] or next(
                    (text for (role, _), text in zip(rows, messages) if role == 'user'), '新对话')
                if keyword and not any(keyword in text.casefold() for text in (title, *messages)):
                    continue
                preview = messages[-1] if len(messages) > 1 else ('等待回复' if messages else '尚无消息')
                item = QListWidgetItem(f'{title[:25]}{"…" if len(title) > 25 else ""}'
                                       f'{" · 当前会话" if index == self.active[mode] else ""}\n'
                                       f'{preview[:36]}{"…" if len(preview) > 36 else ""}')
                item.setData(Qt.ItemDataRole.UserRole, index)
                item.setToolTip(title)
                sessions.addItem(item)
                if index == self.active[mode]:
                    sessions.setCurrentItem(item)
            if sessions.count() and not sessions.currentItem():
                sessions.setCurrentRow(0)
            empty.setText('没有符合搜索条件的会话。' if keyword else '还没有历史会话。')
            empty.setVisible(not sessions.count())

        def open_selected():
            if sessions.currentItem():
                dialog.accept()

        def update_actions(current, _):
            open_button.setEnabled(current is not None)
            index = current.data(Qt.ItemDataRole.UserRole) if current else None
            has_messages = index is not None and bool(self.sessions[mode][index])
            rename.setEnabled(has_messages)
            delete.setEnabled(has_messages)

        def change_history(remove=False):
            item = sessions.currentItem()
            if item is None:
                return
            index = item.data(Qt.ItemDataRole.UserRole)
            title = item.toolTip()
            if remove:
                if not confirm(dialog, '删除会话', f'删除「{title}」及其中的消息？',
                               accept='确认删除', decline='暂不删除'):
                    return
            else:
                editor = QDialog(dialog)
                editor.setWindowTitle('重命名会话')
                editor_layout = QVBoxLayout(editor)
                editor_layout.addWidget(label('会话名称'))
                name = QLineEdit(title[:MAX_TITLE])
                name.setMaxLength(MAX_TITLE)
                name.selectAll()
                editor_layout.addWidget(name)
                actions = dialog_buttons(action='保存名称')
                save = actions.button(QDialogButtonBox.StandardButton.Ok)
                name.textChanged.connect(lambda value: save.setEnabled(bool(value.strip())))
                actions.accepted.connect(editor.accept)
                actions.rejected.connect(editor.reject)
                editor_layout.addWidget(actions)
                if editor.exec() != QDialog.DialogCode.Accepted or not name.text().strip():
                    return
                title = ' '.join(name.text().split())
            try:
                self.change_session(index, None if remove else title)
            except (OSError, ValueError) as exc:
                error.setText(str(exc) if isinstance(exc, ValueError) else '本机历史无法写入，会话已保留。')
                error.show()
                return
            error.hide()
            refresh()

        rename.clicked.connect(lambda: change_history())
        delete.clicked.connect(lambda: change_history(remove=True))
        sessions.currentItemChanged.connect(update_actions)
        sessions.itemActivated.connect(open_selected)
        search.textChanged.connect(refresh)
        search.returnPressed.connect(open_selected)
        controls.accepted.connect(open_selected)
        controls.rejected.connect(dialog.reject)
        refresh()
        update_actions(sessions.currentItem(), None)
        search.setFocus()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.owner.select_chat(sessions.currentItem().data(Qt.ItemDataRole.UserRole))

    def load_sessions(self):
        mode = self.owner.workspace.mode
        if mode in self.loaded:
            return
        self.loaded.add(mode)
        try:
            self.sessions[mode], self.active[mode], self.session_titles[mode] = self.saved[mode].load_with_titles()
        except (OSError, ValueError) as exc:
            self.history_errors[mode] = str(exc) if isinstance(exc, ValueError) else '无法读取本机对话历史。'

    def sync_history_options(self):
        mode = self.owner.workspace.mode
        checkbox = self.owner.save_chat_history
        checkbox.blockSignals(True)
        checkbox.setChecked(self.saved[mode].previous is not None)
        checkbox.blockSignals(False)
        warning = self.history_errors.get(mode, '')
        self.history_warning.setText(warning)
        self.history_warning.setVisible(bool(warning))

    def save_sessions(self):
        mode = self.owner.workspace.mode
        if self.saved[mode].previous is None or mode in self.history_errors:
            return
        try:
            self.saved[mode].save(self.sessions[mode], self.active[mode], self.session_titles[mode])
        except (OSError, ValueError) as exc:
            self.history_errors[mode] = str(exc) if isinstance(exc, ValueError) else '本机对话历史写入失败。'
            self.sync_history_options()

    def toggle_history(self, enabled):
        mode = self.owner.workspace.mode
        title = '本机保存对话' if enabled else '停止本机保存'
        message = ('为当前空间保存最近的对话文本？文本可能包含你问过的商户及回答金额。'
                   '计算依据、源文件和账本快照仍只在内存中。'
                   if enabled else '删除当前空间的本机历史文件？本次打开的对话仍可查看。')
        if confirm(self, title, message):
            try:
                if enabled:
                    self.saved[mode].save(self.sessions[mode], self.active[mode], self.session_titles[mode])
                else:
                    self.saved[mode].erase()
                self.history_errors.pop(mode, None)
            except (OSError, ValueError) as exc:
                self.history_errors[mode] = str(exc) if isinstance(exc, ValueError) else '未能更新本机对话历史。'
        self.sync_history_options()

    def select_session(self, index):
        mode = self.owner.workspace.mode
        if 0 <= index < len(self.sessions[mode]) and index != self.active[mode]:
            self.active[mode] = index
            self.reload()
            self.save_sessions()

    def reset_sessions(self):
        mode = self.owner.workspace.mode
        self.sessions[mode] = [[]]
        self.session_titles[mode] = ['']
        self.active[mode] = 0
        self.reload()
        self.save_sessions()

    def change_session(self, index, title):
        mode = self.owner.workspace.mode
        if mode in self.history_errors and self.saved[mode].previous is not None:
            raise ValueError('本机历史目前无法更新，请重启并检查历史文件后再操作。')
        rows = self.sessions[mode].copy()
        titles = self.session_titles[mode].copy()
        active = self.active[mode]
        if title is None:
            rows.pop(index)
            titles.pop(index)
            if not rows:
                rows, titles = [[]], ['']
            new_active = min(active - int(index < active), len(rows) - 1)
        else:
            titles[index] = title
            new_active = active
        if self.saved[mode].previous is not None:
            self.saved[mode].save(rows, new_active, titles)
        self.sessions[mode], self.active[mode], self.session_titles[mode] = rows, new_active, titles
        if title is None and index == active:
            self.reload()
        else:
            self.sync_sessions()

    def near_bottom(self):
        bar = self.scroll.verticalScrollBar()
        return bar.maximum() - bar.value() < 72

    def update_latest(self, *_):
        self.latest.setVisible(not self.near_bottom())

    def follow(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def scroll_after_layout(self, follow, previous=0):
        self.scroll_epoch += 1
        epoch = self.scroll_epoch

        def restore():
            if epoch == self.scroll_epoch:
                bar = self.scroll.verticalScrollBar()
                bar.setValue(bar.maximum() if follow else previous)

        QTimer.singleShot(0, self, restore)

    def follow_new_range(self, *_):
        if self.follow_range_epoch == self.scroll_epoch:
            epoch = self.scroll_epoch
            QTimer.singleShot(0, self, lambda: self.follow() if self.follow_range_epoch == self.scroll_epoch == epoch else None)

    def change_online_consent(self, enabled):
        if enabled:
            if not confirm(self, '开启本次会话联网理解',
                           '复杂问题会把原文及必要的查询条件发送给你配置的模型。'
                           '记账时，本条消费原文会发送给模型。理财分析会发送当前问题、本轮分析的近期提问、补充条件和按月比较（例如分类支出的增减、是否超过预算）。'
                           '账单明细和计算保留在本机。你可以随时关闭。',
                           accept='开启'):
                self.use_model.setChecked(False)
        elif self.busy:
            self.stop()

    def begin_async(self):
        self.async_epoch += 1
        return self.async_epoch

    def async_current(self, epoch):
        return epoch == self.async_epoch

    def sync_controls(self, companion=None):
        self.use_model.setEnabled(self.owner.client is not None and (not self.busy or self.use_model.isChecked()))
        self.send.setVisible(not self.busy)
        self.send.setEnabled(bool(self.input.toPlainText().strip()))
        self.stop_button.setVisible(self.busy)
        state = companion or ('writing' if self.owner.mascot.state == 'writing' else 'thinking')
        if self.busy or self.owner.query_feedback is None:
            self.owner.set_companion_state(state if self.busy else 'idle')

    def reload(self):
        self.clear_expense()
        self.stop(quiet=True)
        self.load_sessions()
        self.last_result = None
        self.evidence.close()
        self.input.clear()
        self.use_model.setChecked(False)
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        rows = self.active_history()
        self.snapshot.setVisible(self.has_sources and not rows)
        self.last_result = next((value for role, value in reversed(rows) if role == 'assistant'), None)
        if rows:
            for role, payload in rows:
                self.add_bubble(role, payload)
        else:
            self.welcome()
        self.sync_controls()
        self.sync_sessions()
        self.sync_history_options()
        self.scroll_after_layout(True)

    def welcome(self):
        self.empty = QWidget()
        layout = QVBoxLayout(self.empty)
        layout.setContentsMargins(10, 12, 10, 8)
        headline = label('今天想了解哪笔账？' if self.has_sources else '开始记录你的账单', 'hero')
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(headline)
        caption = label('查收支、核对来源，或给一个储蓄目标做 A/B/C 规划。' if self.has_sources
                        else '导入已有账单，或手动记下第一笔。演示账单和个人账单分开保存。', 'muted')
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)
        layout.addSpacing(9)
        grid = QGridLayout()
        grid.setSpacing(10)
        if self.has_sources:
            for i, (title, prompt) in enumerate(PROMPTS):
                item = button(f'{title}\n{prompt}', lambda checked=False, q=prompt: self.submit(q))
                item.setProperty('suggestion', True)
                item.setMinimumHeight(56)
                grid.addWidget(item, i // 2, i % 2)
        else:
            grid.addWidget(button('导入账单', self.open_import, primary=True), 0, 0)
            grid.addWidget(button('手动记一笔', self.owner.quick_entry), 0, 1)
            if self.owner.workspace.mode == 'personal':
                grid.addWidget(button('看看演示账单', lambda: self.owner.space.setCurrentIndex(
                    self.owner.space.findData('demo'))), 1, 0, 1, 2)
        layout.addLayout(grid)
        self.messages.insertWidget(0, self.empty)

    def open_import(self):
        self.owner.navigate('ledger')
        self.owner.perform(self.owner.pages['ledger'].choose_import)

    def add_bubble(self, role, payload):
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 4, 0, 4)
        bubble = QFrame()
        bubble.setMaximumWidth(740 if role == 'assistant' else 550)
        bubble.setObjectName('assistantBubble' if role == 'assistant' else 'userBubble')
        body = QVBoxLayout(bubble)
        body.setContentsMargins(16, 10, 16, 10)
        if role == 'user':
            line.addStretch(1)
            line.addWidget(bubble)
            body.addWidget(label(payload))
        elif 'savings_form' in payload or 'savings_result' in payload:
            bubble.setObjectName('savingsReply')
            bubble.setMaximumWidth(540)
            line.addWidget(bubble, 4)
            line.addStretch(1)
            if 'savings_form' in payload:
                self.savings_card(body, payload['savings_form'], payload['answer'])
            else:
                self.savings_result_card(body, payload)
        else:
            line.addWidget(bubble, 1)
            line.addSpacing(30)
            metadata = QHBoxLayout()
            metadata.addWidget(label('小财灵'))
            metadata.addStretch(1)
            if payload.get('parser'):
                style = 'warning' if payload.get('status') == 'unsupported' else 'parser'
                metadata.addWidget(label(payload['parser'], style))
            body.addLayout(metadata)
            answer = payload.get('display_answer', payload['answer'])
            headline, _, explanation = answer.partition('\n')
            amount = re.fullmatch(r'(.+?)\s+(-?[\d,]+\.\d{2} 元)([。，].*)', headline)
            if payload.get('analysis_context') is not None:
                body.addWidget(label(answer))
                explanation = ''
                context = payload['analysis_context']
                if context.get('reused') and context.get('periods'):
                    inherited = '、'.join(context['periods'])
                    body.addWidget(label(f"当前分析：{context.get('category') or '全部支出'} · {inherited}", 'muted'))
                if context.get('follow_up_question'):
                    body.addWidget(label(context['follow_up_question']))
                if payload.get('analysis_facts'):
                    body.addWidget(label('本机账本汇总', 'muted'))
                    body.addWidget(label(payload['analysis_facts'], 'muted'))
            elif payload.get('breakdown'):
                values = QGridLayout()
                for column, title in enumerate(('分类', '净支出', '笔数', '占本期净支出')):
                    values.addWidget(label(title, 'muted'), 0, column)
                for index, item in enumerate(payload['breakdown'], 1):
                    for column, value in enumerate((item['category'], yuan(item['cents']) + ' 元',
                                                    str(item['count']), item['share'])):
                        values.addWidget(label(value), index, column)
                body.addLayout(values)
                explanation = payload['amount_note']
            elif payload.get('amounts'):
                values = QGridLayout()
                for column, (title, cents) in enumerate(payload['amounts']):
                    values.addWidget(label(title), 0, column)
                    values.addWidget(label(f'{yuan(cents)} 元', 'amount'), 1, column)
                    values.setColumnStretch(column, 1)
                body.addLayout(values)
                explanation = payload.get('amount_note', '')
            elif amount:
                body.addWidget(label(amount[1]))
                amount_line = QHBoxLayout()
                amount_line.addWidget(label(amount[2].removesuffix(' 元'), 'amount'))
                amount_line.addWidget(label('元', 'muted'))
                amount_line.addStretch(1)
                body.addLayout(amount_line)
                if amount[3].strip('。， '):
                    body.addWidget(label(amount[3].strip('。， '), 'muted'))
            else:
                body.addWidget(label(headline))
            if explanation:
                body.addWidget(label(explanation, 'muted'))
            error = payload.get('error') or {}
            if error.get('hint'):
                body.addWidget(label(error['hint'], 'muted'))
            if payload.get('detail_rows'):
                records, _ = table((('日期', lambda r: display_date(r['date'])), ('商户','merchant'),
                                    ('金额 / 元', lambda r: yuan(r['amount_cents'])),
                                    ('性质', lambda r: '退款抵扣' if r.get('projection_kind') == 'refund_adjustment' else r['kind'])), payload['detail_rows'])
                records.setMinimumHeight(160)
                body.addWidget(records)
            if payload.get('scope'):
                body.addWidget(label('查询范围 · ' + payload.get('scope_display', payload['scope']), 'muted'))
            if payload.get('query_state'):
                state = payload['query_state']
                filters = ' · '.join(str(value) for value in (
                    state.get('category') or '、'.join(state.get('categories') or state.get('compare_categories') or []) or '全部分类',
                    state.get('merchant') or '全部商户',
                    '已排除：'+(state.get('exclude_category') or state.get('exclude_merchant')) if state.get('exclude_category') or state.get('exclude_merchant') else '', payload.get('basis', ''), payload.get('workspace_label', ''),
                ) if value)
                body.addWidget(label(filters, 'muted'))
                names = {'metric': '统计指标', 'period': '月份', 'category': '分类', 'merchant': '商户'}
                if payload.get('condition_changes'):
                    body.addWidget(label('本次调整：' + '、'.join(names[key] for key in payload['condition_changes']) + '；其余条件沿用。', 'muted'))
            if payload.get('contributions'):
                body.addWidget(label('变化来自哪些记录'))
                periods = payload['query_months']
                body.addWidget(label(f'{periods[1]} − {periods[0]}；按分类和商户拆分，含已确认退款抵扣。金额变化不等于消费原因。', 'muted'))
                values = QGridLayout()
                for index, item in enumerate(payload['contributions'][:5]):
                    values.addWidget(label(item['category'] + ' · ' + item['merchant']), index, 0)
                    values.addWidget(label(f"{yuan(item['delta'])} 元"), index, 1)
                remainder = sum(item['delta'] for item in payload['contributions'][5:])
                if len(payload['contributions']) > 5:
                    values.addWidget(label('其余记录合计'), 5, 0)
                    values.addWidget(label(f'{yuan(remainder)} 元'), 5, 1)
                body.addLayout(values)
            if payload.get('archived_text'):
                body.addWidget(label('历史文本 · 重新提问可查看本次计算依据', 'muted'))
            actions = QHBoxLayout()
            if payload.get('evidence') or payload.get('trace'):
                actions.addWidget(button('查看依据', lambda: self.evidence.present(payload)))
            if payload.get('privacy_receipt'):
                actions.addWidget(button('本次数据使用', lambda: self.show_data_use(payload)))
            if payload.get('expense_draft') is self.expense_pending and self.expense_pending is not None:
                review = button('核对并记账', lambda: self.review_expense(payload['expense_draft']))
                review.setObjectName('expenseReview')
                review.setProperty('reviewExpense', True)
                review.setVisible(not self.expense_pending['multiple'])
                review.setProperty('draftToken', str(id(payload['expense_draft'])))
                actions.addWidget(review)
                cancel = button('放弃草稿', lambda: self.cancel_expense(payload['expense_draft']))
                cancel.setObjectName('expenseReview')
                cancel.setProperty('draftToken', str(id(payload['expense_draft'])))
                actions.addWidget(cancel)
            if payload.get('expense_receipt'):
                if payload['expense_receipt']['undone']:
                    body.addWidget(label('这笔记账已撤销。', 'muted'))
                else:
                    undo = button('撤销这笔', lambda: self.undo_expense(payload['expense_receipt'], undo))
                    actions.addWidget(undo)
            if 'route' in payload:
                route = payload['route']
                actions.addWidget(button('打开' + self.owner.page_labels[route],
                                         lambda: self.owner.navigate(route, payload.get('target', ''))))
            if payload.get('clarify'):
                conditions = payload['clarify']
                names = {'period': '日期', 'category': '分类', 'merchant': '商户', 'metric': '统计'}
                shown = dict(conditions)
                if shown['metric'] == 'expense':
                    shown['metric'] = '净支出'
                found = ' · '.join(f'{names[key]} {value}' for key, value in shown.items() if value)
                if found:
                    body.addWidget(label(f'已识别 {found}', 'muted'))
                actions.addWidget(button('补充条件', lambda: self.clarify(conditions)))
            state = payload.get('query_state', {})
            if (state.get('metric') == 'expense' and not state.get('report_mode') and not state.get('date_from')
                    and state.get('amount_basis') != 'gross' and not payload.get('contributions')):
                actions.addWidget(button('调整查询条件', lambda: self.clarify(dict(
                    period=payload['scope'], category=state.get('category') or '',
                    merchant=state.get('merchant') or '', metric='expense'))))
            actions.addStretch(1)
            body.addLayout(actions)
            for choice in payload.get('choices', {}):
                body.addWidget(button(choice, lambda checked=False, value=choice, ctx=payload: self.submit(value, query_context=ctx)))
            if payload.get('choices'):
                body.addWidget(button('取消本次澄清', lambda: self.submit('取消', query_context=payload)))
            if payload.get('suggestions'):
                next_steps = QHBoxLayout()
                for suggestion in payload['suggestions']:
                    next_steps.addWidget(button(payload.get('suggestion_labels', {}).get(suggestion, suggestion), lambda checked=False, text=suggestion: self.submit(text, query_context=payload)))
                body.addLayout(next_steps)
        self.messages.insertWidget(self.messages.count() - 1, row)

    def show_data_use(self, payload):
        dialog = QDialog(self)
        dialog.setWindowTitle('本次数据使用')
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(payload['privacy_receipt']))
        layout.addWidget(label('计算时间：' + payload.get('calculated_at', '未完成计算'), 'muted'))
        controls = dialog_buttons(QDialogButtonBox.StandardButton.Close)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        dialog.exec()

    def savings_card(self, body, request, prompt):
        body.setContentsMargins(18, 14, 18, 14)
        body.setSpacing(8)
        body.addWidget(label('小财灵', 'muted'))
        body.addWidget(label('一起定个储蓄目标', 'savingsTitle'))
        body.addWidget(label(prompt, 'muted'))
        fields = QGridLayout()
        inputs = {}
        definitions = [('target_cents', '目标金额 / 元', '例如 6000'),
                       ('months', '计划期限 / 月', '例如 6'),
                       ('saved_cents', '已存金额 / 元', '默认从 0 开始')]
        if 'income_cents' in request:
            definitions.extend((('income_cents', '每月收入 / 元', '例如 3000'),
                                ('budget_cents', '每月预算 / 元', '例如 1800')))
        for index, (key, caption, placeholder) in enumerate(definitions):
            row, column = divmod(index, 2)
            entry = inputs[key] = QLineEdit(request.get(key, ''))
            entry.setAccessibleName(caption)
            entry.setPlaceholderText(placeholder)
            entry.setMaxLength(3 if key == 'months' else 24)
            fields.addWidget(label(caption), row * 2, column)
            fields.addWidget(entry, row * 2 + 1, column)
            fields.setColumnStretch(column, 1)
        body.addLayout(fields)
        protection = {}
        if request.get('needs_confirmation') or request.get('protected_categories'):
            body.addWidget(label(request.get('interpretation', '沿用已确认条件'), 'muted'))
            for note in request.get('constraint_notes', []):
                body.addWidget(label(note, 'muted'))
            body.addWidget(label('以下类别保持原支出，不纳入削减。可调整后确认：'))
            grid = QGridLayout()
            for i, category in enumerate(category_options('支出')):
                checkbox = QCheckBox(category)
                checkbox.setChecked(category in request.get('protected_categories', []))
                protection[category] = checkbox
                grid.addWidget(checkbox, i // 3, i % 3)
            body.addLayout(grid)
        estimate = label('', 'savingsEstimate')
        body.addWidget(estimate)
        error = label('', 'error')
        error.hide()
        body.addWidget(error)
        actions = QHBoxLayout()
        consent = QCheckBox('允许模型使用本次汇总')
        consent.setEnabled(self.owner.client is not None)
        consent.setToolTip('仅发送确认的规划条件和账务汇总。本次运行后自动取消。')

        def form_values():
            return dict(request, **{key: entry.text().strip() for key, entry in inputs.items()},
                        protected_categories=[c for c, box in protection.items() if box.isChecked()]
                        if protection else request.get('protected_categories', []), needs_confirmation=False)

        def values():
            target = money_to_cents(inputs['target_cents'].text().strip())
            saved = money_to_cents(inputs['saved_cents'].text().strip() or '0', allow_zero=True)
            term = inputs['months'].text().strip()
            if not re.fullmatch(r'[0-9]{1,3}', term) or not 1 <= int(term) <= 120:
                raise ValueError('期限须为 1–120 个月的整数。')
            return max(0, target - saved), int(term)

        def update(*_):
            error.hide()
            try:
                target, months = values()
            except ValueError:
                estimate.hide()
            else:
                monthly = (target + months - 1) // months
                estimate.setText(f'按当前条件，每月至少还需存 ¥{yuan(monthly)}。')
                estimate.show()

        def generate():
            request = form_values()
            try:
                values()
                if 'income_cents' in request and not all(request.get(k) for k in ('income_cents', 'budget_cents')):
                    raise ValueError('请同时填写每月收入和预算。')
                savings_inputs(dict(request, saved_cents=request['saved_cents'] or '0'))
            except ValueError as exc:
                error.setText(str(exc))
                error.show()
                return
            allowed = consent.isChecked()
            consent.setChecked(False)
            self.input.setFocus()
            self.start_savings(request, allowed)

        for entry in inputs.values():
            entry.textChanged.connect(update)
            entry.returnPressed.connect(generate)
        actions.addWidget(button('在对话中生成规划', generate, primary=True))
        actions.addStretch(1)
        body.addWidget(consent)
        body.addLayout(actions)
        update()

    def savings_result_card(self, body, payload):
        result = payload['savings_result']
        simulation = result['simulation']
        facts, args = simulation['facts'], simulation['args']
        origin = '按输入假设' if result['mode'] == 'assumption' else '按最近三个完整月的账单'
        planner = result['planner_label']
        if result['model_calls'] and result['planner'] == 'local_rules':
            planner = '模型规划未通过，已用本地规则计算'
        header = f"{origin} · {planner}"
        if result['basis'] in ('cash', 'original_purchase_month'):
            header += ' · ' + basis_label(result['basis'])
        body.addWidget(label(header, 'muted'))
        body.addWidget(label(f"{args['months']} 个月存到 ¥{yuan(args['target_cents'])}", 'savingsTitle'))
        baseline = next(row for row in facts['scenarios'] if row['label'] == 'A')
        required = facts['required_monthly_cents']
        if required == 0:
            summary = '按你填写的已存金额，目标已达成。'
        elif baseline['feasible']:
            summary = f"每月至少需存 ¥{yuan(required)}。按当前收支，预计能达成。"
        else:
            short = max(0, required - baseline['monthly_available_cents'])
            summary = f"每月至少需存 ¥{yuan(required)}，按当前收支每月还差 ¥{yuan(short)}。"
        body.addWidget(label(summary, 'savingsEstimate'))
        protected = payload['savings_state'].get('protected_categories', [])
        if protected:
            body.addWidget(label('本次保留：' + '、'.join(protected) + '。以下计算不会削减这些分类。', 'muted'))
        body.addWidget(label(f"维持现状，期末预计 ¥{yuan(baseline['projected_cents'])}，距目标还差 ¥{yuan(baseline['gap_cents'])}。", 'muted'))
        policies = {row['label']: row for row in args['scenarios']}
        for row in facts['scenarios']:
            key = row['label']
            policy = policies[key]
            title = {'A': '维持现状', 'B': '压缩部分开支', 'C': '按目标倒推'}[key]
            if key == 'B':
                title = f"减少{'和'.join(policy['categories'])}开支 {policy['cut_pct']}%" if policy['categories'] else '所有类别均保留，暂无可削减项'
            if row['status'] == 'ok':
                line = f"期末预计 ¥{yuan(row['projected_cents'])} · "
                line += f"缺口 ¥{yuan(row['gap_cents'])}" if row['gap_cents'] else '达到目标'
                if key == 'C' and row['spending_cut_cents']:
                    line += f" · 每月需少花 ¥{yuan(row['spending_cut_cents'])}"
            else:
                line = row['reason']
            body.addWidget(label(f'{key} · {title}'))
            body.addWidget(label(line, 'muted'))
            if key == 'B' and row['status'] == 'ok':
                body.addWidget(label(f"这是假设性调整：每月需少花 ¥{yuan(row['spending_cut_cents'])}，不是已实现的节省。", 'muted'))
            if key == 'C':
                body.addWidget(label('这是达标所需条件，不保证可执行。' +
                    (f"除保护分类外，每月剩余支出共 ¥{yuan(row['adjustable_cents'])}。" if row['status'] == 'ok' else '') +
                    '若调整过大，可延长期限或降低目标。', 'muted'))
        body.addWidget(label('可以继续说「改成 8 个月」或「已经存了 1000 元」，我会重算。', 'muted'))
        actions = QHBoxLayout()
        actions.addWidget(button('查看依据', lambda: self.evidence.present(payload)))

        def open_details():
            self.owner.navigate('plan')
            snapshot = result if payload['ledger_generation'] == self.ledger_generation else None
            self.owner.plan.load_chat_plan(payload['savings_state'], snapshot)

        actions.addWidget(button('打开规划详情', open_details))
        selected_plan = ComboBox()
        for candidate in facts['scenarios']:
            if candidate['status'] == 'ok' and not candidate['negative_cashflow']:
                selected_plan.addItem('选择方案 ' + candidate['label'], candidate['label'])
        save_actions = QHBoxLayout()
        save_actions.addWidget(selected_plan)
        saved = {'id': payload.get('saved_goal_id')}
        original_path = self.owner.workspace.store.path
        def save_selected():
            if saved['id']:
                from .goals import GoalsDialog
                GoalsDialog(self.owner, saved['id']).exec()
                return
            if self.owner.workspace.store.path != original_path or payload['ledger_generation'] != self.ledger_generation:
                self.status.setText('账本已变化，请重新计算后保存，避免保存旧金额。')
                return
            scenario = selected_plan.currentData()
            if not scenario:
                return
            values = scenario_goal_values(result, scenario)
            values['name'] = f"对话方案 {scenario} · ¥{yuan(values['target_cents'])}"
            if protected:
                values['name'] += ' · 保留' + '、'.join(protected)
            message = (f"目标 ¥{yuan(values['target_cents'])}，截止 {values['deadline']}，"
                       f"每月收入 ¥{yuan(values['income_cents'])}，月预算 ¥{yuan(values['budget_cents'])}。\n"
                       '仅保存储蓄目标，不改账单或分类预算。分类保护是本次模拟条件，后续执行需自行核对。')
            if not confirm(self, '确认保存储蓄目标', message):
                return
            if payload['ledger_generation'] != self.ledger_generation or self.owner.workspace.store.path != original_path:
                self.status.setText('确认期间账本变化，请重新计算。')
                return
            try:
                saved['id'] = self.owner.workspace.store.save_goal(values)
                payload['saved_goal_id'] = saved['id']
            except Exception:
                self.status.setText('目标未能保存，请检查账本状态后重试；当前方案已保留。')
                return
            self.owner.ledger_changed('储蓄目标已保存，可查看进度或登记实际存款。')
            save_button.setText('查看目标与实存进度')
        save_button = button('确认保存所选方案', save_selected, primary=True)
        save_button.setEnabled(selected_plan.count() > 0)
        save_actions.addWidget(save_button)
        save_actions.addStretch(1)
        actions.addStretch(1)
        body.addLayout(actions)
        body.addLayout(save_actions)

    def show_savings_form(self, request, prompt):
        self.reply(dict(answer=prompt, route='plan', savings_form=request, savings_state=request), '')

    def show_reduction_estimate(self, request):
        reduction = request.get('monthly_reduction')
        months = int(request.get('months') or 0)
        if request.get('months') and not 1 <= months <= SAVINGS_MONTH_LIMIT:
            answer = f'请填写 1–{SAVINGS_MONTH_LIMIT} 个月的期限。'
        elif not reduction:
            answer = '你打算每月少花多少？请补充确切金额。'
        elif not months:
            answer = f'已记录每月少花 {reduction} 元。打算按几个月估算？'
        else:
            cents = int(reduction.replace('.', '')) * months
            answer = (f'按每月少花 {reduction} 元、持续 {months} 个月估算，可多存 '
                      f'{cents // 100:,}.{cents % 100:02d} 元。')
        protected = request.get('protected_categories', [])
        if protected:
            answer += '保留的支出类别：' + '、'.join(protected) + '。'
        self.reply(dict(answer=answer, savings_state=request, parser='本地估算'), '')

    def start_savings(self, request, authorized):
        if self.busy:
            return
        request = dict(request, saved_cents=request.get('saved_cents') or '0')
        if request.get('needs_confirmation'):
            self.show_savings_form(request, '我已整理目标和限制。请核对一次，之后可以直接追问改期限；计算和保存分别由你确认。')
            return
        if not request.get('target_cents') or not request.get('months'):
            self.show_savings_form(request, '你想存多少钱，打算用几个月？可直接在这里填，也可以回一句「6000 元，6 个月」。')
            return
        assumption = 'income_cents' in request or 'budget_cents' in request
        if assumption and not all(request.get(key) for key in ('income_cents', 'budget_cents')):
            self.show_savings_form(request, '请补齐每月收入和预算，我会按你给出的数值重算。')
            return
        try:
            inputs = savings_inputs(request)
        except ValueError as error:
            self.show_savings_form(request, str(error))
            return

        path = self.owner.workspace.store.path
        mode = 'assumption' if assumption else 'historical'
        client = self.owner.client if authorized else None
        consent = self.savings_consent = AggregateConsent(client is not None)
        self.busy = True
        self.status.setText('正在比较储蓄方案…' if client is None else '正在让模型选择规划工具并核对金额…')
        self.sync_controls()
        epoch = self.begin_async()

        def finish(result, error):
            if not self.async_current(epoch):
                return
            if error:
                self.reply(None, error)
            elif result['status'] != 'ok':
                if result.get('can_use_assumptions'):
                    state = dict(request, income_cents='', budget_cents='')
                    coverage = next((item['facts'] for item in result['facts'].values()
                                     if item['tool'] == 'data_coverage'), {})
                    missing = '、'.join(coverage.get('missing_months', ()))
                    reason = f'历史账单缺少 {missing}。' if missing else '历史净支出受退款影响。'
                    self.show_savings_form(state, reason + '你可以填入每月收入和预算，改用输入假设。')
                else:
                    self.show_savings_form(request, result['answer'])
            else:
                self.reply(dict(answer=result['answer'], route='plan', savings_result=result, savings_state=request,
                                trace={'工具轨迹': result['trace'], '事实': result['facts'], '模型轨迹': result['model_trace']},
                                ledger_generation=self.ledger_generation), '')

        self.owner.jobs.start('chat', lambda event: savings_plan(path, mode, inputs, client, consent, event,
                protected_categories=request.get('protected_categories', ())), finish)

    def append(self, role, payload, *, force=False):
        follow = force or self.near_bottom()
        previous = self.scroll.verticalScrollBar().value()
        history = self.active_history()
        if not history:
            self.empty.hide()
            self.empty.deleteLater()
            self.snapshot.hide()
        history.append((role, payload))
        if role == 'user':
            self.sync_sessions()
        self.add_bubble(role, payload)
        if len(history) > MAX_MESSAGES:
            history.pop(0)
            item = self.messages.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        self.save_sessions()
        self.scroll_after_layout(follow, previous)

    def submit_input(self):
        self.submit(self.input.toPlainText())

    def clarify(self, conditions):
        if self.busy:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('补充净支出查询条件')
        layout = QFormLayout(dialog)
        period = ComboBox()
        period.setEditable(True)
        period.addItems(['本月', '上个月', *reversed(ledger_coverage(self.owner.workspace.store)['months'])])
        period.setEditText(conditions['period'])
        merchant = QLineEdit(conditions['merchant'])
        category = ComboBox()
        category.addItems(['全部分类', *category_options('支出')])
        if category.findText(conditions['category']) > 0:
            category.setCurrentText(conditions['category'])
        layout.addRow('月份或起止日期', period)
        layout.addRow('商户（可留空）', merchant)
        layout.addRow('分类', category)
        controls = dialog_buttons(action='查询净支出')
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addRow(controls)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            merchant_name = merchant.text().strip()
            category_name = category.currentText() if category.currentIndex() else ''
            filters = f'商户：{merchant_name} ' if merchant_name else ''
            if category_name:
                filters += f'分类：{category_name} ' if merchant_name else category_name
            self.submit(f'{period.currentText().strip()} {filters}净支出多少？')

    def show_expense(self, draft, *, model_used=False, offline_fallback=False):
        self.expense_pending = draft
        self.expense_generation = self.ledger_generation
        self.refresh_expense_buttons()
        fields = [draft['date'] or '日期待补充', draft['merchant'] or draft['category']]
        if draft['amount_cents']:
            fields.append(f"¥{draft['amount_cents']}")
        fields.append(draft['channel'] or '支付方式待选择')
        if draft['multiple']:
            prompt = '识别到可能有多笔支出。请取消后分笔记账；如果确实只需记一笔，请明确说「合计 X 元」。'
        elif not draft['amount_cents']:
            prompt = '这笔实际付了多少？可以直接回复金额。'
        else:
            prompt = '可以继续说「改成支付宝」等修改条件，或核对后记账。'
        payload = dict(answer=' · '.join(fields) + '\n' + prompt, expense_draft=draft, parser='本地记账草稿')
        if offline_fallback:
            payload['parser'] = '联网理解失败 · 本地草稿'
        if model_used:
            payload.update(parser='模型理解 + 本地校验',
                           privacy_receipt='本次模型只收到你刚提交的文字。账本和草稿校验在本机完成。')
        self.reply(payload, '')

    def refresh_expense_buttons(self):
        for control in self.findChildren(QPushButton, 'expenseReview'):
            current = self.expense_pending
            control.setVisible(bool(current and control.property('draftToken') == str(id(current))
                                    and (not current['multiple'] or not control.property('reviewExpense'))))

    def cancel_expense(self, draft):
        if draft is self.expense_pending:
            self.clear_expense()
            self.reply(dict(answer='已放弃这笔记账草稿。'), '')

    def clear_expense(self):
        self.expense_pending = None
        self.expense_generation = None
        self.refresh_expense_buttons()

    def review_expense(self, draft):
        if self.busy or draft is not self.expense_pending or draft['multiple']:
            return
        if self.expense_generation != self.ledger_generation:
            self.clear_expense()
            self.reply(dict(answer='账本已变化，请重新描述这笔消费。'), '')
            return
        self.clear_expense()
        self.owner.set_companion_state('writing')
        try:
            saved = self.owner.pages['ledger'].edit({}, draft=draft)
        finally:
            self.sync_controls()
        if not saved:
            self.reply(dict(answer='已取消，这笔消费没有写入账本。'), '')
            return
        answer = (f"已记支出 ¥{yuan(saved['amount_cents'])} · {saved['category']} · "
                  f"{saved['channel']}（{saved['date'][:10]}）。")
        receipt = dict(id=saved['id'], mode=self.owner.workspace.mode, undone=False)
        self.reply(dict(answer=answer, route='ledger', expense_receipt=receipt), '')

    def undo_expense(self, receipt, control):
        if self.busy or receipt['undone'] or receipt['mode'] != self.owner.workspace.mode:
            return
        if not confirm(self, '撤销这笔记账', '从当前账本移除刚才新增的这笔支出？', accept='撤销记账'):
            return
        store = self.owner.workspace.store
        removed = self.owner.perform(lambda: store.soft_delete_transaction(receipt['id'], confirmed=True))
        if removed:
            receipt['undone'] = True
            control.setEnabled(False)
            control.setText('已撤销')
            self.owner.ledger_changed('已撤销这笔记账，原始来源和审计记录已保留。')
        elif removed is False:
            control.setEnabled(False)
            self.owner.notice('这笔记录已从账本移除。')

    def submit(self, question, *, query_context=None):
        text = ' '.join(question.split())
        if not text or self.busy:
            return
        if len(text) > 500:
            self.status.setText('问题最多 500 字。')
            return
        history = self.active_history()
        if query_context is not None and (
            query_context.get('ledger_generation') != self.ledger_generation
            or not any(role == 'assistant' and payload is query_context for role, payload in history)
        ):
            self.status.setText('这条结果的账本或会话已改变，请重新查询。')
            return
        if self.expense_pending is not None and self.expense_generation != self.ledger_generation:
            self.clear_expense()
        decision = resolve_turn(text, history, self.ledger_generation,
                                expense=self.expense_pending, selected_query=query_context)
        route, text = decision.route, decision.text
        previous_query, analysis_context, previous = decision.query, decision.analysis, decision.savings
        self.owner.clear_query_feedback()
        self.input.clear()
        self.append('user', question.strip(), force=True)
        if route.kind == 'cancel_query':
            self.reply(dict(answer='已取消本次查询。'), '')
            return
        if route.kind == 'cancel_expense':
            self.cancel_expense(self.expense_pending)
            return
        if route.kind == 'revise_expense':
            self.show_expense(route.payload)
            return
        if route.kind == 'clarify':
            self.reply(route.payload, '')
            return
        if route.kind == 'cancel_analysis':
            self.reply(dict(answer='已结束这轮理财分析。', analysis_closed=True), '')
            return
        if route.kind == 'rank':
            prior = analysis_context
            if prior and prior.get('ledger_generation') != self.ledger_generation:
                if not route.payload:
                    self.reply(query_failure(
                        QueryFailure.LEDGER_STALE, '账本已更新，请指定要查看分类占比的月份。',
                    ), '')
                    return
                prior = None
            self.start_category_rank(text, prior)
            return
        client = self.owner.client if self.use_model.isChecked() else None
        if route.kind == 'analysis':
            inherited = analysis_context if depends_on_analysis_context(text, analysis_context) else None
            if inherited and inherited.get('ledger_generation') != self.ledger_generation:
                self.reply(query_failure(
                    QueryFailure.LEDGER_STALE,
                    '账本已更新，请补充要分析的分类和月份。',
                ), '')
                return
            self.start_finance_analysis(text, client, inherited)
            return
        draft = route.payload if route.kind == 'expense' else None
        if (client is not None and is_expense_candidate(text)
                and route.kind in ('expense', 'query', 'navigation', 'interpret')):
            self.busy = True
            self.status.setText('正在理解这笔消费…')
            self.sync_controls('writing')
            epoch = self.begin_async()
            def understood_expense(result, error):
                if not self.async_current(epoch):
                    return
                if result is not None:
                    self.show_expense(result, model_used=True)
                elif error and draft:
                    self.show_expense(draft, offline_fallback=True)
                elif error:
                    self.reply(dict(answer='联网理解未完成。可以重试，或说「帮我记一笔」后补充消费条件。'), '')
                else:
                    self.query(text, previous_query, client)
            self.owner.jobs.start('chat', lambda event: understand_expense(client, text) if not event.is_set() else None,
                                  understood_expense)
            return
        if draft is not None:
            self.show_expense(draft)
            return
        savings = route.payload if route.kind == 'savings' else None
        if savings is not None and 'monthly_reduction' in savings:
            self.show_reduction_estimate(savings)
            return
        if savings is not None and savings.get('needs_confirmation') and client is not None:
            self.busy = True
            self.status.setText('正在理解目标和限制，完成后请你确认…')
            self.sync_controls()
            epoch = self.begin_async()
            def understood(state, error):
                if not self.async_current(epoch):
                    return
                if error or state is None:
                    self.reply(None, '未完成理解，未生成或保存方案。请重试。')
                else:
                    self.show_savings_form(state, '请确认我理解的目标和保护范围；缺少的条件可在这里补充。')
            self.owner.jobs.start('chat', lambda event: understand_request(text, previous, savings, client, event), understood)
            return
        if savings is not None:
            self.start_savings(savings, False)
            return
        if route.kind == 'navigation':
            page, answer, target = route.payload
            self.reply(dict(answer=answer, route=page, target=target), '')
            return
        if route.kind == 'interpret' and client is not None and hasattr(client, 'interpret_finance_turn'):
            self.start_turn_interpretation(text, client, analysis_context, previous_query)
            return
        self.query(text, previous_query, client)

    def start_turn_interpretation(self, text, client, context, previous_query=None):
        if context and context.get('ledger_generation') != self.ledger_generation:
            context = None
        self.busy = True
        self.status.setText('正在理解你想处理的理财问题…')
        self.sync_controls('thinking')
        epoch = self.begin_async()

        def finish(plan, error):
            if not self.async_current(epoch):
                return
            if plan and plan['action'] == 'analysis' and plan['missing'] == 'none':
                inherited = context if plan['reference'] == 'current' else None
                self.start_finance_analysis(text, client, inherited)
                return
            if plan and plan['action'] == 'query' and plan['missing'] == 'none':
                self.query(text, previous_query, client)
                return
            if plan and plan['action'] == 'expense':
                self.reply(dict(answer='你想把这笔消费记下来吗？请说「帮我记一笔」，并补充确切金额。'), '')
                return
            missing = plan['missing'] if plan else 'intent'
            prompt = {
                'period': '你想看哪个月或哪段日期？',
                'object': '你想看哪个分类、商户，还是全部开销？',
                'amount': '这笔消费的确切金额是多少？',
            }.get(missing, '你想查已有记录、分析开销，还是新记一笔？可以直接说「查十月交通」或「帮我记一笔」。')
            if error or not plan:
                prompt = '模型未完成意图判断。' + prompt
            response = dict(answer=prompt)
            if plan and plan['action'] in ('query', 'analysis') and missing == 'period':
                response.update(pending_turn=dict(question=text, action=plan['action'], missing=missing),
                                ledger_generation=self.ledger_generation)
            self.reply(response, '')

        self.owner.jobs.start(
            'chat', lambda event: None if event.is_set() else interpret_turn(
                client, text, has_context=bool(context),
                pending_question=bool(context and context.get('follow_up_question')), context=context,
            ), finish,
        )

    def start_category_rank(self, text, previous):
        path = self.owner.workspace.store.path
        generation = self.ledger_generation
        self.busy = True
        self.status.setText('正在计算分类占比…')
        self.sync_controls()
        self.owner.begin_query_feedback()
        epoch = self.begin_async()

        def finish(result, error):
            if not self.async_current(epoch):
                return
            if result and result.get('analysis_context'):
                result['analysis_context'].update(ledger_generation=generation, reused=bool(previous))
            self.owner.complete_query_feedback(not error and bool(result) and result.get('status') == 'ok')
            self.reply(result, error)

        self.owner.jobs.start('chat', lambda event: rank_categories(path, text, event, previous), finish)

    def start_finance_analysis(self, text, client, previous):
        path = self.owner.workspace.store.path
        generation = self.ledger_generation
        self.busy = True
        self.status.setText('正在结合账本分析…' if client else '正在汇总账本…')
        self.sync_controls()
        epoch = self.begin_async()

        def finish(result, error):
            if not self.async_current(epoch):
                return
            if result is not None and 'analysis_context' in result:
                result['analysis_context'].update(ledger_generation=generation, reused=previous is not None)
            self.reply(result, error)

        self.owner.jobs.start('chat', lambda event: analyze_finances(path, text, client, event, previous), finish)

    def query(self, text, previous_query, client):
        path = self.owner.workspace.store.path
        self.busy = True
        self.status.setText('正在核对账单…')
        self.sync_controls('thinking')
        self.owner.begin_query_feedback()
        epoch = self.begin_async()
        def read_ledger(event):
            receipt = QueryModelReceipt(client) if client is not None else None
            if previous_query is None:
                result = query_ledger(path, text, receipt, event)
            else:
                result = query_ledger(path, text, receipt, event, previous=previous_query)
            if result is not None:
                if result.get('error', {}).get('code') == QueryFailure.MISSING_PERIOD.value \
                        and not result.get('choices') and previous_query is None:
                    result['pending_turn'] = dict(question=text, action='query', missing='period')
                result['privacy_receipt'] = receipt.summary() if receipt else '本次账本查询未调用模型。'
            return result

        def deliver(result, error):
            if self.async_current(epoch):
                self.owner.complete_query_feedback(not error and bool(result) and result.get('status') == 'ok')
                self.reply(result, error)

        self.owner.jobs.start('chat', read_ledger, deliver)

    def reply(self, result, error):
        if self.savings_consent is not None:
            self.savings_consent.revoke()
            self.savings_consent = None
        self.busy = False
        self.sync_controls()
        self.status.setText('Enter 发送 · Shift+Enter 换行 · 金额依据已导入记录')
        self.last_result = result
        if error:
            self.append('assistant', dict(answer=error))
        elif result:
            if any(result.get(key) for key in ('query_state', 'choices', 'awaiting_merchant', 'savings_state', 'pending_turn')):
                result['ledger_generation'] = self.ledger_generation
                result['workspace_label'] = '演示空间' if self.owner.workspace.mode == 'demo' else '我的账本'
            follow_plan = 'savings_state' in result
            self.append('assistant', result, force=follow_plan or 'expense_draft' in result or 'analysis_context' in result)
            if follow_plan:
                self.follow_range_epoch = epoch = self.scroll_epoch

                def release_follow():
                    if self.follow_range_epoch == epoch:
                        self.follow_range_epoch = None
                        cards = self.findChildren(QFrame, 'savingsReply')
                        if cards:
                            title = cards[-1].findChild(QLabel, 'savingsTitle')
                            if title is not None:
                                self.scroll.ensureWidgetVisible(title, 0, 12)

                QTimer.singleShot(250, self, release_follow)

    def stop(self, quiet=False):
        self.owner.clear_query_feedback()
        self.async_epoch += 1
        if self.savings_consent is not None:
            self.savings_consent.revoke()
            self.savings_consent = None
        self.owner.jobs.cancel('chat')
        if not quiet:
            self.clear_expense()
        self.busy = False
        self.sync_controls()
        self.status.setText('Enter 发送 · Shift+Enter 换行 · 金额依据已导入记录' if quiet else '已停止等待。')

    def new_chat(self):
        mode = self.owner.workspace.mode
        if self.active_history():
            rows = self.sessions[mode]
            rows.append([])
            self.session_titles[mode].append('')
            if len(rows) > MAX_SESSIONS:
                rows.pop(0)
                self.session_titles[mode].pop(0)
            self.active[mode] = len(rows) - 1
        self.reload()
        self.save_sessions()
        self.input.setFocus()
