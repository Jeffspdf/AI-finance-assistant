"""Qt form for the shared, read-only savings Agent."""

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from finance.agent_runtime import AggregateConsent
from finance.analytics import yuan
from finance.spending_presenter import BASIS_LABELS
from .goals import GoalsDialog
from .services import savings_inputs, savings_plan
from .ui import button, card, details, label


class PlanPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.setObjectName('workArea')
        self.owner = owner
        self.result = None
        self.consent = None
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(label('储蓄目标 · A/B/C', 'title'))
        layout.addWidget(label('比较按现状、压缩部分开支和按目标反推三种情景。', 'muted'))
        frame, panel = card()
        fields = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem('最近三个完整月', 'historical')
        self.mode.addItem('手动输入假设', 'assumption')
        fields.addRow('计算依据', self.mode)
        self.inputs = {}
        self.assumptions = []
        for key, caption, initial in (
            ('target_cents', '目标金额 / 元', '6000'),
            ('months', '期限 / 月', '6'),
            ('saved_cents', '已有储蓄 / 元', '0'),
            ('income_cents', '每月收入 / 元', ''),
            ('budget_cents', '每月预算 / 元', ''),
        ):
            field = self.inputs[key] = QLineEdit(initial)
            fields.addRow(caption, field)
            field.textChanged.connect(self.invalidate)
            if key in ('income_cents', 'budget_cents'):
                self.assumptions.append((fields.labelForField(field), field))
        panel.addLayout(fields)
        self.confirmed = QCheckBox('我已核对本次输入')
        self.authorized = QCheckBox('允许向已配置的模型发送本次汇总数据')
        panel.addWidget(self.confirmed)
        panel.addWidget(self.authorized)
        actions = QHBoxLayout()
        self.generate = button('生成规划', self.start, primary=True)
        self.stop_button = button('取消', self.invalidate)
        self.evidence = button('查看工具轨迹', self.trace)
        actions.addWidget(self.generate)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.evidence)
        actions.addStretch(1)
        actions.addWidget(button('目标与存款', lambda: GoalsDialog(self.owner).exec()))
        panel.addLayout(actions)
        layout.addWidget(frame)
        self.status = label('规划为本次模拟，金额由本地服务计算。', 'muted')
        layout.addWidget(self.status)
        grid = QHBoxLayout()
        self.cards = {}
        for name, title in (('A', '按现状'), ('B', '压缩餐饮和交通'), ('C', '按目标反推')):
            tile, contents = card()
            contents.addWidget(label(f'{name} · {title}'))
            amount = label('—', 'amount')
            breakdown = label('生成后可查看每月结余与期末缺口。', 'muted')
            contents.addWidget(amount)
            contents.addWidget(breakdown)
            self.cards[name] = (amount, breakdown)
            grid.addWidget(tile, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.authorized.toggled.connect(self.authorization_changed)
        self.change_mode()

    def change_mode(self, *_):
        visible = self.mode.currentData() == 'assumption'
        for pair in self.assumptions:
            for widget in pair:
                widget.setVisible(visible)
        self.invalidate()

    def authorization_changed(self, allowed):
        if not allowed and self.consent is not None:
            self.invalidate()

    def invalidate(self, *_):
        if self.consent is not None:
            self.consent.revoke()
            self.consent = None
        self.owner.jobs.cancel('plan')
        self.result = None
        self.confirmed.setChecked(False)
        self.authorized.setChecked(False)
        self.generate.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.evidence.setEnabled(False)
        self.status.setText('请核对本次输入后生成规划。')
        for amount, breakdown in self.cards.values():
            amount.setText('—')
            breakdown.setText('等待规划')

    def reset_workspace(self):
        self.invalidate()
        self.authorized.setChecked(False)
        self.authorized.setEnabled(self.owner.client is not None)

    def start(self):
        if not self.confirmed.isChecked():
            self.status.setText('请先核对输入并勾选确认。')
            return
        mode = self.mode.currentData()
        values = {key: field.text() for key, field in self.inputs.items()
                  if mode == 'assumption' or key not in ('income_cents', 'budget_cents')}
        try:
            inputs = savings_inputs(values)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        path = self.owner.workspace.store.path
        client = self.owner.client
        consent = self.consent = AggregateConsent(client is not None and self.authorized.isChecked())
        self.result = None
        self.generate.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.evidence.setEnabled(False)
        self.status.setText('正在生成本次规划…')
        self.owner.jobs.start('plan', lambda event: savings_plan(path, mode, inputs, client, consent, event), self.finish)

    def finish(self, result, error):
        self.consent = None
        self.authorized.setChecked(False)
        self.generate.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.result = result
        self.evidence.setEnabled(bool(result))
        if error:
            self.status.setText(error)
            return
        if result['status'] != 'ok':
            self.status.setText(result['answer'] + ('\n可明确切换为手动输入假设。' if result['can_use_assumptions'] else ''))
            return
        mode = '手动输入假设' if result['mode'] == 'assumption' else '历史账单'
        basis = BASIS_LABELS.get(result['basis'], '按输入金额')
        self.status.setText(f"{result['planner_label']} · {mode} · {basis} · 仅供模拟")
        for scenario in result['simulation']['facts']['scenarios']:
            amount, breakdown = self.cards[scenario['label']]
            if scenario['status'] != 'ok':
                amount.setText('待补资料')
                breakdown.setText(scenario['reason'])
            else:
                amount.setText(f"¥{yuan(scenario['projected_cents'])}")
                text = (f"期末预计 · 每月结余 ¥{yuan(scenario['monthly_available_cents'])}\n"
                        f"目标缺口 ¥{yuan(scenario['gap_cents'])}")
                if scenario['negative_cashflow']:
                    text += '\n存在负现金流，方案不可直接执行。'
                breakdown.setText(text)

    def trace(self):
        if self.result:
            details(self, '储蓄规划 · 计算依据与工具轨迹', {
                key: self.result[key] for key in ('answer', 'inputs', 'trace', 'model_trace', 'facts')
                if key in self.result
            })
