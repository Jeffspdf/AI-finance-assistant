"""Read-only conversational view and reusable evidence dialog."""

from datetime import date
import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from finance.analytics import yuan
from finance.qa import month_shift
from finance.spending_presenter import basis_label, spending_totals
from finance.categories import category_options
from .services import guidance, ledger_coverage, query_ledger
from .session_history import MAX_MESSAGES, MAX_SESSIONS, MAX_TITLE, SessionHistory
from .ui import ComboBox, button, card, confirm, dialog_buttons, label, readonly_text, table


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
        self.setPlaceholderText('问问账本，或聊聊你的储蓄目标…')
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
        self.busy = False
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

        composer = QFrame()
        composer.setObjectName('composer')
        form = QVBoxLayout(composer)
        form.setContentsMargins(12, 8, 12, 8)
        self.input = Composer()
        self.input.submitted.connect(self.submit_input)
        self.input.textChanged.connect(self.sync_controls)
        form.addWidget(self.input)
        actions = QHBoxLayout()
        self.use_model = QCheckBox('允许模型理解本条问题')
        self.use_model.setToolTip('授权后，复杂问题的原文可能发送给所配置的模型，包括你在问题中输入的商户或金额。账单与计算留在本机。')
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

    def sync_controls(self):
        self.use_model.setEnabled(self.owner.client is not None and not self.busy)
        self.send.setVisible(not self.busy)
        self.send.setEnabled(bool(self.input.toPlainText().strip()))
        self.stop_button.setVisible(self.busy)
        self.owner.companion_status.setText('正在核对账本…' if self.busy else '小财灵')

    def reload(self):
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
            if amount:
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
            if payload.get('scope'):
                body.addWidget(label('查询范围 · ' + payload['scope'], 'muted'))
            if payload.get('archived_text'):
                body.addWidget(label('历史文本 · 重新提问可查看本次计算依据', 'muted'))
            actions = QHBoxLayout()
            if payload.get('evidence') or payload.get('trace'):
                actions.addWidget(button('查看依据', lambda: self.evidence.present(payload)))
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
            actions.addStretch(1)
            body.addLayout(actions)
        self.messages.insertWidget(self.messages.count() - 1, row)

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

    def submit(self, question):
        text = ' '.join(question.split())
        if not text or self.busy:
            return
        if len(text) > 500:
            self.status.setText('问题最多 500 字。')
            return
        self.input.clear()
        self.append('user', question.strip(), force=True)
        client = self.owner.client if self.use_model.isChecked() else None
        self.use_model.setChecked(False)
        action = guidance(text)
        if action:
            page, answer, target = action
            self.reply(dict(answer=answer, route=page, target=target), '')
            return
        path = self.owner.workspace.store.path
        self.busy = True
        self.status.setText('正在核对账单…')
        self.sync_controls()
        self.owner.jobs.start('chat', lambda event: query_ledger(path, text, client, event), self.reply)

    def reply(self, result, error):
        self.busy = False
        self.sync_controls()
        self.status.setText('Enter 发送 · Shift+Enter 换行 · 金额依据已导入记录')
        self.last_result = result
        if error:
            self.append('assistant', dict(answer=error))
        elif result:
            self.append('assistant', result)

    def stop(self, quiet=False):
        self.owner.jobs.cancel('chat')
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
