"""Read-only conversational view and reusable evidence dialog."""

import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from finance.analytics import yuan
from finance.categories import category_options
from .services import guidance, ledger_coverage, query_ledger
from .ui import button, label, readonly_text, table


DINING = '上个月餐饮花了多少？'
PROMPTS = (
    ('消费明细', DINING),
    ('收支结余', '上个月结余是多少？'),
    ('消费排行', '上个月哪个分类支出最高？'),
    ('储蓄规划', '帮我做一个储蓄计划'),
)
EVIDENCE_COLUMNS = (
    ('日期', 'date'), ('商户', 'merchant'), ('金额 / 元', lambda row: yuan(row['amount_cents'])),
    ('类型', 'kind'), ('分类', 'category'), ('来源', 'source_file'), ('行号', 'source_row'),
)


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
        self.active = {'demo': 0, 'personal': 0}
        self.evidence = Evidence(self)
        self.last_result = None
        self.scroll_epoch = 0
        self.busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 12, 26, 16)
        layout.setSpacing(8)
        self.coverage = label('', 'muted')
        layout.addWidget(self.coverage)

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
        self.use_model.setToolTip('模型只接收问题文本，账单与计算在本地处理。')
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
        coverage = ledger_coverage(self.owner.workspace.store)
        months = coverage['months']
        shown = '、'.join(months[-4:]) or '尚无账单'
        if len(months) > 4:
            shown = f'{len(months)} 个月 · 最近 {shown}'
        latest = f" · 最近导入（UTC） {coverage['latest']}" if coverage['latest'] else ''
        self.coverage.setText(f"账本覆盖 · {shown} · {coverage['count']} 条来源记录{latest}")
        self.coverage.setToolTip('、'.join(months) + '\n有记录的月份仍可能存在未导入交易。')

    def sync_sessions(self):
        mode = self.owner.workspace.mode
        selector = self.owner.chat_sessions
        selector.blockSignals(True)
        selector.clear()
        for rows in self.sessions[mode]:
            title = next((value for role, value in rows if role == 'user'), '新对话')
            selector.addItem(title[:21] + ('…' if len(title) > 21 else ''))
        selector.setCurrentIndex(self.active[mode])
        selector.blockSignals(False)

    def select_session(self, index):
        mode = self.owner.workspace.mode
        if 0 <= index < len(self.sessions[mode]) and index != self.active[mode]:
            self.active[mode] = index
            self.reload()

    def reset_sessions(self):
        mode = self.owner.workspace.mode
        self.sessions[mode] = [[]]
        self.active[mode] = 0
        self.reload()

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

    def reload(self):
        self.stop(quiet=True)
        self.last_result = None
        self.evidence.close()
        self.input.clear()
        self.use_model.setChecked(False)
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        rows = self.active_history()
        self.last_result = next((value for role, value in reversed(rows) if role == 'assistant'), None)
        if rows:
            for role, payload in rows:
                self.add_bubble(role, payload)
        else:
            self.welcome()
        self.sync_controls()
        self.sync_sessions()
        self.scroll_after_layout(True)

    def welcome(self):
        self.empty = QWidget()
        layout = QVBoxLayout(self.empty)
        layout.setContentsMargins(16, 36, 16, 12)
        headline = label('今天想了解哪笔账？', 'hero')
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(headline)
        caption = label('查收支、核对来源，或给一个储蓄目标做 A/B/C 规划。', 'muted')
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)
        layout.addSpacing(18)
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (title, prompt) in enumerate(PROMPTS):
            item = button(f'{title}\n{prompt}', lambda checked=False, q=prompt: self.submit(q))
            item.setProperty('suggestion', True)
            item.setMinimumHeight(70)
            grid.addWidget(item, i // 2, i % 2)
        layout.addLayout(grid)
        self.messages.insertWidget(0, self.empty)

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
            body.addWidget(label('小财灵', 'muted'))
            answer = payload.get('display_answer', payload['answer'])
            headline, _, explanation = answer.partition('\n')
            amount = re.fullmatch(r'(.+?)\s+(-?[\d,]+\.\d{2} 元)([。，].*)', headline)
            if amount:
                body.addWidget(label(amount[1]))
                body.addWidget(label(amount[2], 'amount'))
                if amount[3].strip('。， '):
                    body.addWidget(label(amount[3].strip('。， '), 'muted'))
            else:
                body.addWidget(label(headline))
            if explanation:
                body.addWidget(label(explanation, 'muted'))
            if payload.get('scope'):
                body.addWidget(label('查询范围 · ' + payload['scope'], 'muted'))
            if payload.get('parser'):
                body.addWidget(label(payload['parser'], 'muted'))
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
        history.append((role, payload))
        if role == 'user':
            self.sync_sessions()
        self.add_bubble(role, payload)
        if len(history) > 40:
            history.pop(0)
            item = self.messages.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        self.scroll_after_layout(follow, previous)

    def submit_input(self):
        self.submit(self.input.toPlainText())

    def clarify(self, conditions):
        if self.busy:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('补充净支出查询条件')
        layout = QFormLayout(dialog)
        period = QComboBox()
        period.setEditable(True)
        period.addItems(['本月', '上个月', *reversed(ledger_coverage(self.owner.workspace.store)['months'])])
        period.setEditText(conditions['period'])
        merchant = QLineEdit(conditions['merchant'])
        category = QComboBox()
        category.addItems(['全部分类', *category_options('支出')])
        if category.findText(conditions['category']) > 0:
            category.setCurrentText(conditions['category'])
        layout.addRow('月份或起止日期', period)
        layout.addRow('商户（可留空）', merchant)
        layout.addRow('分类', category)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText('查询净支出')
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
            if len(rows) > 15:
                rows.pop(0)
            self.active[mode] = len(rows) - 1
        self.reload()
        self.input.setFocus()
