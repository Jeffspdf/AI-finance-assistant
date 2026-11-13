"""Qt views for the ledger, its budgets, accounts, reconciliation and backups."""

import csv
from datetime import date, datetime
import json
from operator import ge, gt, le, lt
from pathlib import Path
import re
import sqlite3

from PySide6.QtCore import QLocale, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIntValidator
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit, QMenu,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from finance.analytics import budget_report, goal_progress, month_records, overview, yuan
from finance.categories import category_options
from finance.demo_generator import DemoOptions, generate_demo
from finance.evidence import calculation
from finance.help_content import load_help
from finance.insights import make_insights, suggested_budgets
from finance.importer import money_to_cents
from finance.migrations import inspect_database
from finance.llm import connection_error_message, model_status_text
from finance.m1a_demo import seed_six
from finance.model_settings import ModelSettings
from finance.qa import month_shift
from finance.updates import update_error_message
from finance.spending_presenter import basis_label, net_export_csv, spending_totals

from .chat import EVIDENCE_COLUMNS, Evidence, display_date
from .overview_chart import SpendingChart
from .services import preview_statement
from .ui import ComboBox, button, card, confirm, details, dialog_buttons, label, readonly_text, selected, selected_rows, table
from . import updates


def toolbar(layout):
    row = QHBoxLayout()
    layout.addLayout(row)
    return row


def select_month(frame, month):
    return frame.loc[frame.date.str.startswith(month)].copy() if month else frame


def periods_with_records(frame):
    return set(frame.date.str[:7]) | set(frame.attrs.get('source_months', ()))


def export_issues(parent, issues):
    path, _ = QFileDialog.getSaveFileName(parent, '导出导入异常', 'import-issues.csv', 'CSV (*.csv)')
    if not path:
        return
    columns = ('行号', '原因', '日期', '金额', '商户')
    with Path(path).open('w', encoding='utf-8-sig', newline='') as output:
        writer = csv.writer(output)
        writer.writerow(columns)
        for issue in issues:
            values = [str(issue.get(column, '') or '') for column in columns]
            writer.writerow("'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
                            for value in values)


def fields(parent, title):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(440)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)
    return dialog, layout, form


def form_buttons(dialog, layout, title='确定'):
    controls = dialog_buttons(action=title)
    controls.accepted.connect(dialog.accept)
    controls.rejected.connect(dialog.reject)
    layout.addWidget(controls)
    return controls


def display_status(value):
    return {
        'candidate': '待确认', 'confirmed': '已确认', 'rejected': '已撤销或拒绝',
        'split': '已拆分', 'pending': '待处理', 'linked': '已关联',
        'needs_review': '待复核', 'committed': '已导入', 'deleted': '已删除',
    }.get(value, value)


def save_form(dialog, layout, form, title, save):
    error = label('', 'error')
    error.hide()
    layout.addWidget(error)
    controls = form_buttons(dialog, layout, title)
    controls.accepted.disconnect(dialog.accept)

    def submit():
        try:
            save()
        except (ValueError, OSError, sqlite3.Error) as exc:
            field = getattr(exc, 'field', None)
            if form.getWidgetPosition(error)[0] >= 0:
                form.takeRow(error)
            else:
                layout.removeWidget(error)
            if field is not None:
                row, _ = form.getWidgetPosition(field)
                form.insertRow(row + 1, error)
                field.setFocus()
            else:
                layout.insertWidget(layout.indexOf(controls), error)
            error.setText(str(exc))
            error.show()
        else:
            dialog.accept()

    controls.accepted.connect(submit)


class FieldError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


class Page(QWidget):
    def __init__(self, owner, title, caption):
        super().__init__()
        self.setObjectName('workArea')
        self.setAccessibleName(title)
        self.owner = owner
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 14, 28, 18)
        self.layout.setSpacing(10)
        self.layout.addWidget(label(caption, 'muted'))

    @property
    def store(self):
        return self.owner.workspace.store

    def action(self, row, title, callback, *, primary=False):
        control = button(title, lambda: self.owner.perform(callback), primary=primary)
        row.addWidget(control)
        return control

    def add_table(self, columns, *, multiple=False):
        view, model = table(columns, multiple=multiple)
        self.layout.addWidget(view, 1)
        return view, model

    def changed(self, message):
        self.owner.ledger_changed(message)


class OverviewPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '财务概览', '净支出已扣除本口径下确认抵扣的退款。')
        grid = toolbar(self.layout)
        self.metrics = {}
        for name, text in (('income_cents', '收入'), ('gross_cents', '消费总额'),
                           ('refund_cents', '已确认抵扣'), ('net_cents', '净支出'), ('balance_cents', '结余')):
            tile, content = card()
            value = self.metrics[name] = label('—', 'amount')
            content.addWidget(label(text, 'muted'))
            content.addWidget(value)
            grid.addWidget(tile, 1)
        self.basis = label('', 'muted')
        self.layout.addWidget(self.basis)
        row = toolbar(self.layout)
        self.month = ComboBox()
        self.month.setAccessibleName('统计月份')
        row.addWidget(self.month)
        self.action(row, '查看洞察', self.insights)
        self.demo_button = self.action(row, '生成演示账单', self.demo)
        self.six_demo_button = self.action(row, '加载六笔对账示例', self.six_demo)
        row.addStretch(1)
        tabs = QTabWidget()
        self.tabs = tabs
        monthly, self.monthly = table([(name, name) for name in ('月份', '收入', '支出', '结余')])
        self.category_view, self.categories = table([(name, name) for name in ('分类', '支出', '占比')])
        self.trend_chart = SpendingChart(trend=True)
        self.category_chart = SpendingChart()
        for title, chart, view in (('月度收支 / 元', self.trend_chart, monthly),
                                   ('支出分类 / 元', self.category_chart, self.category_view)):
            page = QWidget()
            chart_layout = QVBoxLayout(page)
            chart_layout.addWidget(chart)
            chart_layout.addWidget(view, 1)
            tabs.addTab(page, title)
        actions = toolbar(chart_layout)
        self.action(actions, '查看选中分类的账单', self.open_category)
        actions.addStretch(1)
        self.category_view.doubleClicked.connect(lambda _: self.owner.perform(self.open_category))
        self.dismissed = set()
        reminder_page = QWidget()
        reminder_layout = QVBoxLayout(reminder_page)
        self.reminder_caption = label('', 'muted')
        reminder_layout.addWidget(self.reminder_caption)
        self.reminder_view, self.reminders = table((('类型', 'type'), ('项目', 'title'), ('提醒', 'message')))
        reminder_layout.addWidget(self.reminder_view, 1)
        reminder_actions = toolbar(reminder_layout)
        self.action(reminder_actions, '查看选中提醒', self.open_reminder)
        self.action(reminder_actions, '本次隐藏', self.dismiss_reminder)
        reminder_actions.addStretch(1)
        self.reminder_tab = tabs.addTab(reminder_page, '提醒')
        self.layout.addWidget(tabs, 1)
        self.month.currentIndexChanged.connect(lambda _: self.owner.perform(self.refresh))

    def refresh(self):
        frame = self.store.spending_view()
        previous = self.month.currentData()
        recorded = periods_with_records(frame)
        periods = sorted(recorded | {date.today().strftime('%Y-%m')}, reverse=True)
        self.month.blockSignals(True)
        self.month.clear()
        self.month.addItem('全部月份', '')
        for period in periods:
            self.month.addItem(period, period)
        self.month.setCurrentIndex(max(0, self.month.findData(previous)))
        self.month.blockSignals(False)
        period = self.month.currentData()
        scoped = select_month(frame, period)
        totals = spending_totals(scoped)
        for name, label_widget in self.metrics.items():
            label_widget.setText('¥' + yuan(totals[name]))
        coverage = ' · 本期暂无已导入账单' if period and period not in recorded else ''
        self.basis.setText(f"{basis_label(totals['basis'])} · {self.month.currentText()}{coverage}")
        monthly, categories = overview(scoped)
        self.monthly.replace(monthly.to_dict('records'))
        self.categories.replace(categories.to_dict('records'))
        trend, _ = overview(frame.loc[frame.date.str[:7] <= period].copy() if period else frame)
        self.trend_chart.show_values(zip(trend['月份'].tail(12), trend['支出'].tail(12)))
        top = categories.head(5)
        self.category_chart.show_values(zip(top.get('分类', ()), top.get('支出', ())))
        self.refresh_reminders(frame, period or date.today().strftime('%Y-%m'))
        for control in (self.demo_button, self.six_demo_button):
            control.setVisible(self.owner.workspace.mode == 'demo')

    def six_demo(self):
        if self.owner.workspace.mode != 'demo' or not self.store.source_transactions().empty:
            raise ValueError('六笔对账示例只能加载到空白演示空间。已有账单可以使用自定义生成入口。')
        if confirm(self, '加载六笔对账示例', '导入两笔消费、对应的两笔银行扣款、一笔退款和一笔转账？'):
            seed_six(self.store)
            self.changed('示例已加载。前往退款与对账生成候选。')

    def insights(self):
        month = self.month.currentData() or date.today().strftime('%Y-%m')
        goals = self.store.goals().to_dict('records')
        contributions = {goal['id']: self.store.contributions(goal['id']) for goal in goals}
        observations = make_insights(self.store.spending_view(), month, goals, contributions)
        if not observations:
            self.owner.notice(f'{month} 的已导入数据暂未生成洞察。')
            return
        details(self, f'{month} 财务洞察', observations)

    def open_category(self):
        category = selected(self.category_view, self.categories)['分类']
        self.owner.open_ledger(self.month.currentData(), category)

    def refresh_reminders(self, frame, month):
        rows = []
        if month in periods_with_records(frame):
            budgets = budget_report(frame, month, self.store.budgets(month))
            for budget in budgets.to_dict('records'):
                if budget['超支'] <= 0:
                    continue
                category = budget['分类']
                rows.append(dict(type='预算超支', title=category, month=month,
                                 message=f"净支出 ¥{budget['已支出']:,.2f}，超支 ¥{budget['超支']:,.2f}",
                                 key=(str(self.store.path), month, category, budget['预算'], budget['已支出'])))
        for goal in self.store.goals().to_dict('records'):
            try:
                progress = goal_progress(goal, self.store.contributions(goal['id']))
            except ValueError:
                continue
            if not progress['remaining']:
                continue
            behind = progress['behind_cents']
            deficit = progress['plan']['deficit']
            if behind or deficit:
                message = f'实际进度落后 ¥{yuan(behind)}' if behind else f'计划到期缺口 ¥{yuan(deficit)}'
                rows.append(dict(type='储蓄目标', title=goal['name'], message=message,
                                 key=(str(self.store.path), goal['id'], behind, deficit)))
        visible = [row for row in rows if row['key'] not in self.dismissed]
        self.reminders.replace(visible)
        self.reminder_caption.setText(f'{month} 预算超支 · 储蓄目标按当前进度' if visible else f'{month} · 暂无需要查看的提醒')
        self.tabs.setTabText(self.reminder_tab, f'提醒 ({len(visible)})' if visible else '提醒')

    def dismiss_reminder(self):
        row = selected(self.reminder_view, self.reminders)
        self.dismissed.add(row['key'])
        self.refresh()

    def open_reminder(self):
        row = selected(self.reminder_view, self.reminders)
        if row['type'] == '预算超支':
            self.owner.navigate('budget')
            budget = self.owner.pages['budget']
            budget.month.setCurrentText(row['month'])
            index = next((i for i, value in enumerate(budget.model.records) if value['分类'] == row['title']), None)
            if index is not None:
                budget.view.selectRow(index)
                budget.show_evidence()
        else:
            self.owner.navigate('plan')

    def demo(self):
        if self.owner.workspace.mode != 'demo':
            raise ValueError('只可在演示空间生成合成账单。')
        dialog, layout, form = fields(self, '生成演示账单')
        start = QLineEdit(month_shift(date.today(), -3))
        months, count = QLineEdit('3'), QLineEdit('30')
        for control, name, limit in ((months, 'demoMonths', 12), (count, 'demoCount', 500)):
            control.setObjectName(name)
            validator = QIntValidator(1, limit, control)
            locale = QLocale.c()
            locale.setNumberOptions(QLocale.NumberOption.RejectGroupSeparator)
            validator.setLocale(locale)
            control.setValidator(validator)
        minimum, maximum, income = QLineEdit('10'), QLineEdit('200'), QLineEdit('3000')
        income_kind = ComboBox()
        income_kind.addItems(('生活费', '工资'))
        transfers, refunds, replace = QCheckBox(), QCheckBox(), QCheckBox()
        transfers.setChecked(True)
        for title, control in (
            ('起始月份（YYYY-MM）', start), ('月份数', months), ('每月消费笔数', count),
            ('单笔最小金额 / 元', minimum), ('单笔最大金额 / 元', maximum), ('每月收入 / 元', income),
            ('收入分类', income_kind), ('生成转账', transfers), ('生成待处理退款', refunds), ('替换这些月份的演示记录', replace),
        ):
            form.addRow(title, control)
        options = None

        def validate_options():
            nonlocal options
            for field, name, limit in ((months, '月份数', 12), (count, '每月消费笔数', 500)):
                if not field.hasAcceptableInput():
                    raise FieldError(field, f'{name}须为 1–{limit} 的整数。')
            options = DemoOptions(start.text().strip(), int(months.text()), int(count.text()),
                                  money_to_cents(minimum.text()), money_to_cents(maximum.text()),
                                  money_to_cents(income.text(), allow_zero=True), income_kind.currentText(),
                                  transfers.isChecked(), refunds.isChecked(), replace.isChecked())
            options.summary()

        save_form(dialog, layout, form, '预览生成', validate_options)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        summary = options.summary()
        message = f"{'、'.join(summary['months'])} · 合成 {summary['count']} 笔"
        if options.replace_months:
            message += '\n所列月份的现有演示记录将被替换。'
        if confirm(self, '确认生成演示账单', message):
            result = generate_demo(self.store, options, space=self.owner.workspace.mode)
            self.changed(f"已生成 {result['added']} 笔演示账单，移除 {result['removed']} 笔。")


LEDGER_COLUMNS = (*EVIDENCE_COLUMNS[:5], ('计入说明', 'counting_note'), ('来源', 'source_file'))


class LedgerPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '交易账本', '交易表展示来源行。对话、概览和净额导出使用已核对的净额投影。')
        row = toolbar(self.layout)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索日期、商户、分类或摘要')
        self.search.textChanged.connect(self.filter)
        row.addWidget(self.search, 1)
        self.action(row, '导入账单', self.choose_import, primary=True)
        self.action(row, '记一笔', self.edit_new)
        actions = toolbar(self.layout)
        self.selected_actions = [self.action(actions, title, callback) for title, callback in (
            ('查看来源', self.source), ('编辑选中', self.edit_selected), ('删除选中', self.delete_selected),
        )]
        self.action(actions, '导入批次', self.import_history)
        self.action(actions, '导出净额', self.export)
        category_button = button('分类', lambda: None)
        menu = QMenu(category_button)
        for title, callback in (
            ('批量改分类', self.batch_category),
            ('改选中商户的全部同类流水', lambda: self.batch_category(same_merchant=True)),
            ('撤销上次批量分类', self.undo_batch),
            ('商户分类记忆', self.rules),
            ('历史分类待修正', self.category_conflicts),
            ('AI 分类建议', self.suggest_categories),
        ):
            menu.addAction(title, lambda _checked=False, action=callback: self.owner.perform(action))
        category_button.setMenu(menu)
        actions.addWidget(category_button)
        actions.addStretch(1)
        filters = toolbar(self.layout)
        self.month_filter, self.category_filter = ComboBox(), ComboBox()
        for name, combo in (('月份', self.month_filter), ('分类', self.category_filter)):
            filters.addWidget(label(name))
            filters.addWidget(combo)
            combo.currentIndexChanged.connect(self.filter)
        filters.addStretch(1)
        self.records = []
        self.last_batch = None
        self.view, self.model = self.add_table(LEDGER_COLUMNS, multiple=True)
        self.view.selectionModel().selectionChanged.connect(self.update_selection)
        self.model.modelReset.connect(self.update_selection)
        self.update_selection()
        self.import_result, result_layout = card()
        self.import_summary = label('')
        result_layout.addWidget(self.import_summary)
        result_actions = toolbar(result_layout)
        self.action(result_actions, '继续导入', self.choose_import)
        self.action(result_actions, '退款与对账', lambda: self.owner.navigate('review'))
        self.action(result_actions, '返回 AI 对话', lambda: self.owner.navigate('chat'))
        self.action(result_actions, '关闭摘要', self.import_result.hide)
        result_actions.addStretch(1)
        self.layout.insertWidget(self.layout.count() - 1, self.import_result)
        self.import_result.hide()

    def update_selection(self, *_):
        single = len(self.view.selectionModel().selectedRows()) == 1
        for control in self.selected_actions:
            control.setEnabled(single)

    def refresh(self):
        frame = self.store.source_transactions().sort_values('date', ascending=False)
        self.records = frame.to_dict('records')
        options = ((self.month_filter, '全部月份', sorted(periods_with_records(frame), reverse=True)),
                   (self.category_filter, '全部分类', sorted(set(frame.category) | set(category_options('支出')))))
        for control, first, values in options:
            current = control.currentText()
            control.blockSignals(True)
            control.clear()
            control.addItems([first, *values])
            if current in values:
                control.setCurrentText(current)
            control.blockSignals(False)
        self.filter()

    def filter(self, *_):
        query = self.search.text().strip().casefold()
        amount_match = re.fullmatch(r'([<>]=?)(\d+(?:\.\d{1,2})?)', query)
        if amount_match:
            comparison = {'>': gt, '>=': ge, '<': lt, '<=': le}[amount_match[1]]
            try:
                cents = money_to_cents(amount_match[2], allow_zero=True)
            except ValueError as exc:
                self.model.replace([])
                self.owner.notice(str(exc))
                return
            matches = lambda row: comparison(row['amount_cents'], cents)
        else:
            matches = lambda row: not query or any(query in str(row.get(key, '')).casefold()
                                                  for key in ('date', 'merchant', 'category', 'description', 'external_id'))
        month = self.month_filter.currentText() if self.month_filter.currentIndex() > 0 else ''
        category = self.category_filter.currentText() if self.category_filter.currentIndex() > 0 else ''
        self.model.replace(row for row in self.records
                           if (not month or row['date'].startswith(month))
                           and (not category or row['category'] == category)
                           and matches(row))

    def batch_category(self, same_merchant=False):
        rows = selected_rows(self.view, self.model)
        kinds = {row['kind'] for row in rows}
        if len(kinds) != 1 or any(row.get('status') == 'refund_pending' for row in rows):
            raise ValueError('请选择同一种收支类型的普通流水。')
        options = category_options(next(iter(kinds)))
        if not options:
            raise ValueError('所选流水类型无法调整分类。')
        if same_merchant:
            pairs = {(row['merchant'], row['kind']) for row in rows}
            rows = [row for row in self.records
                    if (row['merchant'], row['kind']) in pairs and row.get('status') != 'refund_pending']
        dialog, layout, form = fields(self, '批量改分类')
        choice = ComboBox()
        choice.addItems(options)
        form.addRow(f'{len(rows)} 笔交易的新分类', choice)
        if same_merchant:
            layout.addWidget(label('会更新整个当前空间内匹配的商户和收支类型。', 'muted'))
        form_buttons(dialog, layout, '确认分类')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_batch(rows, choice.currentText())

    def apply_batch(self, rows, category):
        snapshot = self.store.batch_category([row['id'] for row in rows], category)
        self.last_batch = (self.store.path, snapshot)
        self.changed(f'已批量调整 {len(snapshot)} 笔分类。')

    def undo_batch(self):
        if not self.last_batch or self.last_batch[0] != self.store.path:
            raise ValueError('当前空间没有可撤销的批量分类。')
        if confirm(self, '撤销批量分类', '恢复上次批量分类，保留已被另外编辑的记录？'):
            count = self.store.undo_category_batch(self.last_batch[1])
            self.last_batch = None
            self.changed(f'已恢复 {count} 笔分类。')

    def rules(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('商户分类记忆')
        dialog.resize(640, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('记忆按完整商户名称和收支类型匹配，影响之后导入的新记录。'))
        view, model = table((('商户', 'merchant'), ('收支类型', 'kind'), ('记忆分类', 'category')),
                            self.store.merchant_rules().to_dict('records'))
        layout.addWidget(view)

        def remove():
            row = selected(view, model)
            if confirm(dialog, '删除分类记忆', f"删除「{row['merchant']}」的选中记忆？"):
                self.store.delete_merchant_rule(row['merchant'], row['kind'])
                model.replace(self.store.merchant_rules().to_dict('records'))

        actions = toolbar(layout)
        self.action(actions, '删除选中记忆', remove)
        actions.addWidget(button('关闭', dialog.close))
        dialog.exec()

    def category_conflicts(self):
        records = self.store.category_conflicts()
        dialog = QDialog(self)
        dialog.setWindowTitle('历史分类待修正')
        dialog.resize(840, 530)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('核对来源后编辑单笔记录，保存时才会写入账本。'))
        view, model = table(LEDGER_COLUMNS, records.to_dict('records'))
        layout.addWidget(view, 1)
        form_buttons(dialog, layout, '编辑选中记录')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.edit(selected(view, model))

    def suggest_categories(self):
        client, path = self.owner.client, self.store.path
        if client is None:
            raise ValueError('请先在设置中启用模型。')
        rows = [row for row in self.model.records if row.get('category_source') == '待确认'][:20]
        if not rows:
            raise ValueError('当前筛选范围没有待确认分类。')
        if not confirm(self, '授权 AI 分类', f'向当前模型发送 {len(rows)} 笔商户、摘要与收支类型，继续？'):
            return
        self.owner.notice('正在获取分类建议…')

        def suggest(cancelled):
            suggestions, errors = [], set()
            for row in rows:
                if cancelled.is_set():
                    break
                try:
                    category = client.suggest_category(row['merchant'], row['description'], row['kind'])
                except Exception as exc:
                    errors.add(connection_error_message(exc))
                else:
                    suggestions.append(dict(row, suggested=category))
            return suggestions, sorted(errors)

        def show(result, error):
            if self.store.path != path or self.owner.client is not client:
                return
            if error:
                self.owner.notice(error)
                return
            self.owner.perform(lambda: self.confirm_suggestions(*result))

        self.owner.jobs.start('category', suggest, show)

    def confirm_suggestions(self, suggestions, errors):
        dialog = QDialog(self)
        dialog.setWindowTitle('人工核对 AI 分类')
        dialog.resize(740, 470)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('选中后才会保存分类。' + ('\n' + '\n'.join(errors) if errors else '')))
        view, model = table((('商户', 'merchant'), ('描述', 'description'), ('原分类', 'category'),
                             ('建议', 'suggested')), suggestions, multiple=True)
        layout.addWidget(view)
        form_buttons(dialog, layout, '接受选中')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rows = selected_rows(view, model)
            if not rows:
                raise ValueError('请选中需要接受的建议。')
            count = sum(self.store.apply_model_category(row['id'], row['suggested']) for row in rows)
            self.changed(f'已接受 {count} 笔模型分类。')

    def source(self):
        row = selected(self.view, self.model)
        details(self, '原始交易与来源', self.store.transaction_source(row['id']))

    def edit_new(self):
        self.edit({})

    def edit_selected(self):
        self.edit(selected(self.view, self.model))

    def edit(self, original):
        if original and (original.get('status') == 'refund_pending' or original['kind'] == '退款'):
            raise ValueError('请在退款与对账页核对独立退款流水。')
        dialog, layout, form = fields(self, '编辑交易' if original else '手工记账')
        self.owner.install_dialog_help(dialog)
        inputs = {}
        original_date = original.get('date', '')
        shown_date = (original_date[:10] if original_date.endswith((' 00:00:00', 'T00:00:00'))
                      else original_date.replace('T', ' ', 1))
        for key, title, default in (
            ('date', '本地日期或日期时间', date.today().isoformat()),
            ('merchant', '商户', ''), ('description', '摘要', ''),
            ('amount_cents', '金额 / 元', ''),
        ):
            initial = original.get(key, default)
            if key == 'date' and original:
                initial = shown_date
            if key == 'amount_cents' and original:
                initial = yuan(initial)
            entry = inputs[key] = QLineEdit(str(initial))
            form.addRow(title, entry)
            if key == 'date':
                entry.setPlaceholderText('YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss')
                form.addRow('', label('可只填日期。账单记录的具体时间可在来源详情查看。', 'muted'))
        kind, category = ComboBox(), ComboBox()
        kind.addItems(['支出', '收入', '转账'])
        kind.setCurrentText(original.get('kind', '支出'))

        def sync():
            category.clear()
            category.addItems(category_options(kind.currentText()))

        kind.currentTextChanged.connect(sync)
        sync()
        category.setCurrentText(original.get('category', category.currentText()))
        form.addRow('类型', kind)
        form.addRow('分类', category)
        learn = QCheckBox('记住此商户的分类')
        if original:
            form.addRow('', learn)
        def save():
            data = {key: entry.text().strip() for key, entry in inputs.items()}
            unchanged_date = bool(original) and data['date'] == shown_date
            if unchanged_date:
                data['date'] = original_date
            try:
                if not unchanged_date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)?', data['date']):
                    raise ValueError
                when = datetime.fromisoformat(data['date'])
                if not 2000 <= when.year <= 2100 or when.tzinfo:
                    raise ValueError
            except ValueError:
                raise FieldError(inputs['date'], '请输入有效本地日期，例如 2026-11-13，或加上时分秒 12:30:00。') from None
            try:
                data['amount_cents'] = money_to_cents(data['amount_cents'])
                if data['amount_cents'] <= 0:
                    raise ValueError
            except ValueError:
                raise FieldError(inputs['amount_cents'], '请输入大于零的金额，最多两位小数。') from None
            if not (data['merchant'] or data['description']):
                raise FieldError(inputs['merchant'], '请填写商户或摘要。')
            data.update(kind=kind.currentText(), category=category.currentText())
            if original:
                self.store.edit_transaction(original['id'], data, learn=learn.isChecked())
            else:
                self.store.add_manual_transaction(data['date'], data['merchant'], data['amount_cents'],
                                                  data['kind'], data['category'], data['description'])

        save_form(dialog, layout, form, '保存交易', save)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.changed('交易已保存。')

    def delete_selected(self):
        row = selected(self.view, self.model)
        if confirm(self, '删除交易', f"从当前账本移除 {row['date']} · {row['merchant']} · ¥{yuan(row['amount_cents'])}？",
                   accept='确认删除', decline='暂不删除'):
            self.store.soft_delete_transaction(row['id'], confirmed=True)
            self.changed('交易已移除，来源和审计记录已保留。')

    def choose_import(self):
        source, _ = QFileDialog.getOpenFileName(self, '选择账单', '', '账单 (*.csv *.xlsx)')
        if not source:
            return
        dialog, layout, form = fields(self, '导入选项')
        adapter = ComboBox()
        adapter.setObjectName('importFormat')
        for title, key in (('自动识别', 'auto'), ('通用表格', 'generic'), ('微信 CSV', 'wechat_csv'),
                           ('银行模板 XLSX', 'bank_template_v1')):
            adapter.addItem(title, key)
        account = ComboBox()
        account.setObjectName('importAccount')

        def refresh_accounts(preferred=None):
            account.clear()
            account.addItem('未声明账户', None)
            for item in self.store.accounts(include_archived=False):
                account.addItem(item['label'], item['id'])
            account.setCurrentIndex(max(0, account.findData(preferred)))

        def add_account():
            identifier = self.owner.pages['accounts'].edit({}, parent=dialog)
            if identifier:
                refresh_accounts(identifier)

        refresh_accounts()
        form.addRow('格式', adapter)
        form.addRow('来源账户', account)
        form.addRow('', label('指定来源账户有助于跨源对账。暂不声明仍可导入，但这些流水没有账户归属。', 'muted'))
        row = toolbar(layout)
        self.action(row, '新建来源账户', add_account)
        row.addStretch(1)
        form_buttons(dialog, layout, '生成预览')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.begin_import(source, adapter.currentData(), account.currentData())

    def begin_import(self, source, adapter, account_id):
        if 'import' in self.owner.jobs.pending:
            raise ValueError('上一份账单正在解析。')
        path = self.store.path
        self.owner.notice('正在解析账单并检查已删除记录…')
        self.owner.jobs.start('import', lambda _: preview_statement(path, source, adapter, account_id),
                              lambda result, error: self.finish_import(path, result, error))

    def finish_import(self, path, result, error):
        if path != self.store.path:
            return
        if error:
            self.owner.notice(error)
            return
        self.owner.notice()
        self.owner.perform(lambda: self.show_import(path, *result))

    def show_import(self, path, parsed, preview):
        dialog = QDialog(self)
        dialog.setWindowTitle('导入预览')
        dialog.resize(920, 620)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"识别 {len(parsed.rows)} 行 · 问题 {len(parsed.issues)} 项 · 已删除 {preview['deleted_count']} 行"))
        if preview.get('refunds_to_correct'):
            layout.addWidget(label(f"确认导入后，会把 {preview['refunds_to_correct']} 条同来源记录从普通收入更正为待处理退款。"))
        if not parsed.document.get('account_id'):
            layout.addWidget(label(f'识别的 {len(parsed.rows)} 行均未指定来源账户。', 'muted'))
        tabs = QTabWidget()
        rows, _ = table(EVIDENCE_COLUMNS, parsed.rows)
        tabs.addTab(rows, '待导入')
        if parsed.issues or parsed.warnings:
            tabs.addTab(readonly_text({'issues': parsed.issues, 'warnings': parsed.warnings}), '解析提示')
        if preview['deleted_rows']:
            deleted, _ = table((('日期', lambda row: display_date(row['date'])), ('商户', 'merchant'), ('金额 / 元', lambda r: yuan(r['amount_cents'])),
                                ('删除原因', 'reason')), preview['deleted_rows'])
            tabs.addTab(deleted, '已删除记录')
        layout.addWidget(tabs, 1)
        restore = QCheckBox('按原始账单恢复预览中列出的已删除记录')
        restore.setEnabled(bool(preview['deleted_rows']))
        layout.addWidget(restore)
        if parsed.issues:
            actions = toolbar(layout)
            self.action(actions, '导出解析异常 CSV', lambda: export_issues(dialog, parsed.issues))
            actions.addStretch(1)
        form_buttons(dialog, layout, '确认导入')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            if path == self.store.path:
                self.owner.notice('已取消导入。')
            return
        if path != self.store.path:
            return
        restore_ids = {row['id'] for row in preview['deleted_rows']} if restore.isChecked() else None
        result = self.store.import_statement(parsed, restore_deleted=restore.isChecked(), restore_ids=restore_ids)
        added, duplicates, conflicts = result
        self.changed(f'新增 {added - result.restored} · 恢复 {result.restored} · 重复 {duplicates} · '
                     f'保持删除 {result.deleted_skipped} · 冲突 {len(conflicts)}'
                     + (f' · 更正退款 {result.refunds_corrected}' if result.refunds_corrected else ''))
        summary = self.owner.message.text()
        if not parsed.document.get('account_id'):
            summary += f' · 本批新增或恢复的 {added} 笔未声明账户'
        self.import_summary.setText(f"{parsed.document['filename']} · {summary}")
        self.import_result.show()

    def import_history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('导入批次')
        dialog.resize(820, 540)
        layout = QVBoxLayout(dialog)
        columns = (('文件', 'filename'), ('账户', 'account_label'), ('状态', lambda row: display_status(row['status'])), ('新增／恢复', 'added'),
                   ('重复', 'duplicates'), ('冲突', 'conflicts'))
        view, model = table(columns, self.store.import_batches())
        layout.addWidget(view)

        def remove():
            row = selected(view, model)
            affected = self.store.preview_import_undo(row['id'])
            message = (f"批次 {row['id']}\n文件 {row['filename']}\n将移除该批次新增或恢复的 {len(affected)} 笔当前交易。"
                       "\n跳过的重复交易、导入历史和原始记录会保留。受影响的对账或退款关系会失效。")
            if confirm(dialog, '撤销选中导入', message, accept='确认撤销', decline='保留批次'):
                count = self.store.delete_import_batch(row['id'], expected_ids=affected)
                model.replace(self.store.import_batches())
                self.changed(f'已撤销选中导入，移除 {count} 笔交易。批次历史及审计已保留。')

        actions = toolbar(layout)
        self.action(actions, '查看批次', lambda: self.show_batch_details(dialog, selected(view, model)))
        self.action(actions, '导出异常与冲突 CSV',
                    lambda: export_issues(dialog, json.loads(selected(view, model)['issues_json'])))
        self.action(actions, '撤销选中导入', remove)
        actions.addStretch(1)
        actions.addWidget(button('关闭', dialog.close))
        dialog.exec()

    def show_batch_details(self, parent, batch):
        audit = self.store.import_batch_audit(batch['id'])
        issues = json.loads(batch['issues_json'])
        conflicts = batch['conflicts']
        problem_count = max(0, len(issues) - conflicts)
        status = {'deleted': '已撤销', 'committed': '已导入', 'committing': '处理中',
                  'failed': '导入失败'}.get(batch['status'], '未知状态')
        if batch['status'] == 'committed' and issues:
            status = '部分导入' if batch['added'] else '已完成，有异常'
        restored, kept = audit.get('restored'), audit.get('deleted_skipped')
        new = batch['added'] - restored if restored is not None else None
        summary = [
            ('文件', batch['filename']),
            ('导入结果', status),
            ('导入时间（UTC）', batch['finished_at'] or batch['started_at'] or '未记录'),
            ('来源账户', batch['account_label'] or '未声明'),
            ('新增', f'{new} 行' if new is not None else f"未单独记录（新增与恢复共 {batch['added']} 行）"),
            ('恢复', f'{restored} 行' if restored is not None else '未记录'),
            ('重复', f"{batch['duplicates']} 行"),
            ('保持删除', f'{kept} 行' if kept is not None else '未记录'),
            ('冲突', f'{conflicts} 行'),
            ('解析问题', f'{problem_count} 项'),
        ]
        if audit.get('refunds_corrected'):
            summary.append(('更正退款', f"{audit['refunds_corrected']} 行"))
        dialog, layout, form = fields(parent, '批次资料')
        dialog.resize(600, 440 if issues else 365)
        for title, value in summary:
            form.addRow(title, label(value))
        if issues:
            layout.addWidget(label('异常与冲突'))
            columns = (('行号', '行号'), ('原因', '原因'), ('商户', '商户'), ('日期', '日期'), ('金额', '金额'))
            view, _ = table(columns, issues)
            layout.addWidget(view, 1)
        actions = toolbar(layout)
        clipboard_text = '\n'.join(f'{title}：{value}' for title, value in summary)
        self.action(actions, '复制可读摘要', lambda: QApplication.clipboard().setText(clipboard_text))
        self.action(actions, '高级审计信息', lambda: details(dialog, '高级审计信息', {'批次': batch, '导入审计': audit}))
        actions.addStretch(1)
        controls = dialog_buttons(QDialogButtonBox.StandardButton.Close)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        dialog.exec()

    def export(self):
        period = self.month_filter.currentText() if self.month_filter.currentIndex() > 0 else ''
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出净额 · ' + (period or '全部月份'), f'净额投影-{period or "全部月份"}.csv', 'CSV (*.csv)')
        if filename:
            destination = Path(filename).resolve()
            if destination.suffix.lower() != '.csv':
                raise ValueError('请将净额导出保存为 .csv 文件。')
            destination.write_bytes(net_export_csv(select_month(self.store.spending_view(), period)))
            self.owner.notice(f'已导出{period or "全部月份"}的净额投影。')


class BudgetMonthPicker(QPushButton):
    currentTextChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAccessibleName('选择预算月份')
        self.setCurrentText(date.today().strftime('%Y-%m'))
        self.clicked.connect(self.choose)

    def currentText(self):
        return self.month

    def setCurrentText(self, month):
        if not re.fullmatch(r'20[0-9]{2}-(?:0[1-9]|1[0-2])', month):
            raise ValueError('预算月份须在 2000–2099 年之间。')
        if month != getattr(self, 'month', None):
            self.month = month
            self.setText(f'{month}  ▾')
            self.currentTextChanged.emit(month)

    def choose(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('选择预算月份')
        layout = QVBoxLayout(dialog)
        header = toolbar(layout)
        year_label = label('')
        year_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        year = int(self.month[:4])
        previous = button('上一年', lambda: change_year(-1))
        following = button('下一年', lambda: change_year(1))
        header.addWidget(previous)
        header.addWidget(year_label, 1)
        header.addWidget(following)
        grid = QGridLayout()
        layout.addLayout(grid)

        def pick(number):
            self.setCurrentText(f'{year:04d}-{number:02d}')
            dialog.accept()

        months = [button(f'{number} 月', lambda checked=False, number=number: pick(number))
                  for number in range(1, 13)]
        for index, choice in enumerate(months):
            grid.addWidget(choice, index // 4, index % 4)

        def update_year():
            year_label.setText(f'{year} 年')
            previous.setEnabled(year > 2000)
            following.setEnabled(year < 2099)
            for number, choice in enumerate(months, 1):
                choice.setProperty('monthSelected', f'{year:04d}-{number:02d}' == self.month)
                choice.style().unpolish(choice)
                choice.style().polish(choice)

        def change_year(step):
            nonlocal year
            year += step
            update_year()

        layout.addWidget(button('取消', dialog.reject), alignment=Qt.AlignmentFlag.AlignRight)
        update_year()
        months[int(self.month[5:]) - 1].setFocus()
        dialog.exec()


class BudgetPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '预算与提醒', '预算、支出和剩余均按所选月份显示。')
        row = toolbar(self.layout)
        self.month = BudgetMonthPicker()
        self.category = ComboBox()
        self.category.addItems(category_options('支出'))
        self.amount = QLineEdit()
        self.amount.setPlaceholderText('预算 / 元')
        self.amount.setAccessibleName('预算金额（元）')
        self.amount_error = label('', 'error')
        self.amount_error.hide()
        amount_field = QWidget()
        amount_layout = QVBoxLayout(amount_field)
        amount_layout.setContentsMargins(0, 0, 0, 0)
        amount_layout.setSpacing(4)
        amount_layout.addWidget(self.amount)
        amount_layout.addWidget(self.amount_error)
        row.addWidget(self.month)
        row.addWidget(self.category)
        row.addWidget(amount_field, 1)
        self.action(row, '保存预算', self.save, primary=True)
        self.action(row, '复制上月', self.copy_previous)
        self.action(row, '建议预算', self.suggest)
        for index in range(row.count()):
            row.setAlignment(row.itemAt(index).widget(), Qt.AlignmentFlag.AlignTop)
        self.amount.textChanged.connect(lambda _: self.set_amount_error(''))
        self.coverage = label('', 'muted')
        self.layout.addWidget(self.coverage)
        self.view, self.model = self.add_table([(name, name) for name in ('分类', '预算', '已支出', '剩余', '使用率', '超支')])
        actions = toolbar(self.layout)
        self.action(actions, '查看选中预算的依据', self.show_evidence)
        self.action(actions, '查看对应账单', self.open_ledger)
        actions.addStretch(1)
        self.evidence_dialog = Evidence(self)
        self.month.currentTextChanged.connect(lambda _: self.owner.perform(self.refresh))

    def refresh(self):
        month = self.month.currentText()
        frame = self.store.spending_view()
        present = month in periods_with_records(frame)
        report = budget_report(frame, month, self.store.budgets(month))
        if not present:
            for field in ('已支出', '剩余', '使用率', '超支'):
                report[field] = None
        self.coverage.setText('金额单位：元' if present else '该月暂无账单资料，仍可提前设置预算。')
        self.model.replace(report.to_dict('records'))

    def show_evidence(self):
        month = self.month.currentText()
        category = selected(self.view, self.model)['分类']
        limit = self.store.budgets(month)[category]
        period = month_records(self.store.spending_view(), month)
        rows = period.loc[(period.kind == '支出') & (period.category == category)]
        trace = calculation('budget', 'budget.spent_minus_limit', rows, inputs={'month': month, 'category': category},
                            extra=[dict(type='Budget', id=f'{month}/{category}', snapshot={'amount_cents': limit})])
        if month in periods_with_records(period):
            net = int(rows.amount_cents.sum())
            explanation = f'预算 ¥{yuan(limit)} − 净支出 ¥{yuan(net)} = 剩余 ¥{yuan(limit - net)}。'
        else:
            explanation = f'已设置预算 ¥{yuan(limit)}。本月尚无已导入记录，暂无支出依据。'
        self.evidence_dialog.setWindowTitle(f'{month} · {category}预算依据')
        self.evidence_dialog.present({'answer': explanation, 'details': explanation, 'trace': trace,
                                      'evidence': rows.to_dict('records')})

    def open_ledger(self):
        category = selected(self.view, self.model)['分类']
        self.owner.open_ledger(self.month.currentText(), category)

    def set_amount_error(self, message):
        self.amount_error.setText(message)
        self.amount_error.setVisible(bool(message))
        self.amount.setProperty('invalid', bool(message))
        self.amount.style().unpolish(self.amount)
        self.amount.style().polish(self.amount)

    def save(self):
        try:
            cents = money_to_cents(self.amount.text(), allow_zero=True)
            if cents < 0:
                raise ValueError('预算不能为负数。')
        except ValueError as exc:
            self.set_amount_error(str(exc))
            self.amount.setFocus()
            return
        self.store.save_budget(self.month.currentText(), self.category.currentText(), cents)
        self.amount.clear()
        self.changed('预算已保存。')

    def copy_previous(self):
        month = self.month.currentText()
        if month == '2000-01':
            self.owner.notice('2000 年 1 月之前没有可复制的预算月份。')
            return
        previous = month_shift(date.fromisoformat(month + '-01'), -1)
        if confirm(self, '复制预算', f'将 {previous} 的预算复制到 {month}，保留目标月已设置的分类？'):
            count = self.store.copy_budgets(previous, month, overwrite=False)
            self.changed(f'已检查上月 {count} 个分类，并补入尚未设置的预算。')

    def suggest(self):
        month = self.month.currentText()
        amounts = suggested_budgets(self.store.spending_view())
        if not amounts:
            self.owner.notice('历史账单暂未生成分类预算建议。')
            return
        lines = '\n'.join(f'{category} · ¥{yuan(amount)}' for category, amount in amounts.items())
        if confirm(self, '保存预算建议', f'按近三个有记录完整月估算。保存将覆盖 {month} 同名分类的预算。\n\n{lines}'):
            self.store.save_budgets(month, amounts)
            self.changed(f'已保存 {month} 的 {len(amounts)} 个分类预算。')


ACCOUNT_TYPES = {'wallet': '钱包', 'debit': '储蓄卡', 'credit': '信用卡'}


class AccountsPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '资金账户', '仅记录平台与卡号末四位。支付绑定用于跨源对账。')
        row = toolbar(self.layout)
        self.action(row, '添加账户', lambda: self.edit({}), primary=True)
        self.account_actions = (self.action(row, '编辑账户', lambda: self.edit(selected(self.view, self.model))),
                                self.action(row, '归档／恢复', self.archive))
        row.addStretch(1)
        self.view, self.model = self.add_table((
            ('账户名', 'label'), ('平台', 'channel'), ('类型', lambda r: ACCOUNT_TYPES.get(r['type'], r['type'])),
            ('末四位', 'last4'), ('状态', lambda r: '已归档' if r['archived_at'] else '使用中'),
        ))
        row = toolbar(self.layout)
        row.addWidget(label('支付绑定'), 1)
        self.action(row, '新建绑定', self.bind_account)
        self.end_link_button = self.action(row, '结束绑定', self.end_binding)
        self.links_view, self.links = self.add_table((
            ('钱包', 'wallet_label'), ('资金账户', 'bank_label'), ('生效日期', 'active_from'), ('结束日期', 'active_to'),
        ))
        for view, model in ((self.view, self.model), (self.links_view, self.links)):
            view.selectionModel().selectionChanged.connect(self.update_selection)
            model.modelReset.connect(self.update_selection)
        self.update_selection()

    def update_selection(self, *_):
        for control in self.account_actions:
            control.setEnabled(self.view.selectionModel().hasSelection())
        self.end_link_button.setEnabled(self.links_view.selectionModel().hasSelection())

    def refresh(self):
        accounts = self.store.accounts()
        self.model.replace(accounts)
        names = {row['id']: row['label'] for row in accounts}
        self.links.replace(dict(row, wallet_label=names.get(row['wallet_account_id']),
                                bank_label=names.get(row['bank_account_id'])) for row in self.store.funding_links())

    def edit(self, original, *, parent=None):
        dialog, layout, form = fields(parent or self, '编辑账户' if original else '添加账户')
        name = QLineEdit(original.get('label', ''))
        channel = QLineEdit(original.get('channel', ''))
        last4 = QLineEdit(original.get('last4', ''))
        last4.setMaxLength(4)
        kind = ComboBox()
        for key, title in ACCOUNT_TYPES.items():
            kind.addItem(title, key)
        if original:
            kind.setCurrentIndex(kind.findData(original['type']))
            kind.setEnabled(False)
        for title, widget in (('账户名', name), ('平台', channel), ('类型', kind), ('末四位', last4)):
            form.addRow(title, widget)
        identifier = None

        def save():
            nonlocal identifier
            for field, limit, title in ((name, 60, '账户名'), (channel, 30, '平台')):
                if not 1 <= len(field.text().strip()) <= limit:
                    raise FieldError(field, f'请填写{title}（1–{limit} 字）。')
            if last4.text().strip() and not re.fullmatch(r'[0-9]{4}', last4.text().strip()):
                raise FieldError(last4, '请填写四位数字，或留空。')
            if original:
                self.store.update_account(original['id'], label=name.text(), channel=channel.text(),
                                          last4=last4.text(), confirmed=True)
                identifier = original['id']
            else:
                identifier = self.store.add_account(channel.text(), kind.currentData(), name.text(), last4.text())

        save_form(dialog, layout, form, '保存账户', save)
        if dialog.exec() == QDialog.DialogCode.Accepted and identifier is not None:
            self.changed('账户已保存。')
            return identifier

    def archive(self):
        row = selected(self.view, self.model)
        restoring = bool(row['archived_at'])
        verb = '恢复' if restoring else '归档'
        if confirm(self, verb+'账户', f"{verb}「{row['label']}」？"):
            action = self.store.restore_account if restoring else self.store.archive_account
            action(row['id'], confirmed=True)
            self.changed(f'账户已{verb}。')

    def bind_account(self):
        dialog, layout, form = fields(self, '声明支付绑定')
        wallet, bank = ComboBox(), ComboBox()
        for account in self.store.accounts(include_archived=False):
            combo = wallet if account['type'] == 'wallet' else bank
            combo.addItem(account['label'], account['id'])
        if not wallet.count() or not bank.count():
            raise ValueError('请先添加活动的钱包与资金账户。')
        start = QLineEdit(date.today().isoformat())
        end = QLineEdit()
        for title, widget in (('钱包', wallet), ('资金账户', bank), ('开始 / YYYY-MM-DD', start),
                              ('结束 / 可留空', end)):
            form.addRow(title, widget)
        form_buttons(dialog, layout, '确认绑定')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.add_funding_link(wallet.currentData(), bank.currentData(), start.text().strip(),
                                        end.text().strip() or None)
            self.changed('支付绑定已保存。')

    def end_binding(self):
        row = selected(self.links_view, self.links)
        dialog, layout, form = fields(self, '结束支付绑定')
        end = QLineEdit(date.today().isoformat())
        form.addRow('结束日期', end)
        form_buttons(dialog, layout, '结束绑定')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.end_funding_link(row['id'], end.text().strip())
            self.changed('支付绑定已结束。')


class ReviewPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '退款与对账', '核对来源后确认关联。结果会影响对话、概览和预算的净额。')
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs, 1)
        _, group_layout = self.tab(self.tabs, '跨源对账')
        self.groups_view, self.groups = table((
            ('状态', lambda row: display_status(row['status'])), ('置信度', 'confidence'), ('类型', 'kind'),
            ('成员数', lambda r: len(r['members'])), ('说明', 'reason'),
        ))
        group_layout.addWidget(self.groups_view, 1)
        row = toolbar(group_layout)
        self.action(row, '生成候选', self.generate, primary=True)
        self.action(row, '查看来源', self.group_details)
        self.action(row, '确认', lambda: self.decide('confirmed'))
        self.action(row, '拒绝', lambda: self.decide('rejected'))
        self.action(row, '拆分', lambda: self.decide('split'))
        self.action(row, '重新核对', lambda: self.decide('reopen'))

        _, refund_layout = self.tab(self.tabs, '退款流水')
        self.refunds_view, self.refunds = table((
            ('日期', lambda row: display_date(row['date'])), ('商户', 'merchant'), ('金额 / 元', lambda r: yuan(r['amount_cents'])),
            ('关联状态', lambda row: display_status(row['association_status'])), ('来源数', lambda r: len(r['member_ids'])),
        ))
        refund_layout.addWidget(self.refunds_view, 1)
        row = toolbar(refund_layout)
        self.action(row, '查看退款来源', lambda: details(self, '退款来源', selected(self.refunds_view, self.refunds)))
        self.action(row, '关联原消费', self.associate_refund, primary=True)
        self.action(row, '退款去重建组', self.create_refund_group)
        self.action(row, '确认原消费角色', self.purchase_role)
        row.addStretch(1)

        _, links_layout = self.tab(self.tabs, '退款关联')
        self.links_view, self.links = table((
            ('状态', lambda row: display_status(row['status'])), ('抵扣 / 元', lambda r: yuan(r['amount_cents'])),
            ('退款交易', 'refund_txn_id'), ('原消费', 'original_txn_id'), ('核对说明', 'reason'),
        ))
        links_layout.addWidget(self.links_view, 1)
        row = toolbar(links_layout)
        self.action(row, '查看依据', self.link_details)
        self.action(row, '撤销确认', self.revoke)
        self.action(row, '复核失效关系', self.repair)
        row.addStretch(1)

        _, work_layout = self.tab(self.tabs, '待复核与阻塞')
        self.work_view, self.work = table((
            ('类型', lambda r: '退款组' if r['kind'] == 'group' else '退款关联'),
            ('编号', 'id'), ('原因', lambda r: '；'.join(r['reasons'])),
        ))
        work_layout.addWidget(self.work_view, 1)
        row = toolbar(work_layout)
        self.action(row, '查看来源', self.work_details)
        self.action(row, '保存定点修复', self.repair_selected, primary=True)
        self.action(row, '撤销关联', self.revoke_work)
        row.addStretch(1)

    @staticmethod
    def tab(tabs, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        tabs.addTab(page, title)
        return page, layout

    def refresh(self):
        self.groups.replace(self.store.reconciliation_groups())
        self.refunds.replace(self.store.refund_sources())
        self.links.replace(self.store.refund_links())
        self.work.replace(self.store.refund_work_items())

    def generate(self):
        self.store.generate_candidates()
        self.changed('已重新生成对账候选，请核对来源。')

    def group_details(self):
        row = selected(self.groups_view, self.groups)
        details(self, '候选组来源', self.store.group_diagnostics(row['id']))

    def decide(self, decision):
        row = selected(self.groups_view, self.groups)
        word = {'confirmed': '确认排重', 'rejected': '拒绝', 'split': '拆分', 'reopen': '重新核对'}[decision]
        sources = '\n'.join(f"{item['date']} · {item['merchant']} · ¥{yuan(item['amount_cents'])}"
                            for item in row['members'])
        if not confirm(self, word, f'{word}以下来源组成的关联？\n\n{sources}'):
            return
        if decision == 'reopen':
            self.store.reopen_group(row['id'])
        else:
            acknowledge = False
            if decision == 'confirmed':
                wallets = {a['id'] for a in self.store.accounts() if a['type'] == 'wallet'}
                if not any(member['account_id'] in wallets for member in row['members']):
                    acknowledge = confirm(self, '核对无钱包来源', '此组没有已声明的钱包。确认这些来源对应同一笔账务事件？')
                    if not acknowledge:
                        return
            self.store.decide_group(row['id'], decision, acknowledge_no_wallet=acknowledge)
        self.changed('对账决定已保存。')

    def create_refund_group(self):
        frame = self.store.source_transactions()
        sources = frame.loc[frame.status == 'refund_pending'].sort_values('date', ascending=False)
        dialog, layout, form = fields(self, '建立退款重复组')
        dialog.resize(800, 480)
        reason = QLineEdit()
        form.addRow('核对说明', reason)
        layout.addWidget(label('选择同一笔退款的至少两个来源。建组后请在跨源对账中核对并确认。'))
        view, model = table(LEDGER_COLUMNS, sources.to_dict('records'), multiple=True)
        layout.addWidget(view, 1)
        form_buttons(dialog, layout, '建立候选')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rows = selected_rows(view, model)
        if len(rows) < 2:
            raise ValueError('请至少选择两个退款来源。')
        identifier = self.store.create_refund_group([row['id'] for row in rows], reason=reason.text())
        self.changed('退款去重候选已建立，请核对来源后确认。')
        self.tabs.setCurrentIndex(0)
        index = next(i for i, row in enumerate(self.groups.records) if row['id'] == identifier)
        self.groups_view.selectRow(index)

    def associate_refund(self):
        refund = selected(self.refunds_view, self.refunds)
        dialog = QDialog(self)
        dialog.setWindowTitle('选择原消费')
        dialog.resize(840, 530)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"退款 ¥{yuan(refund['amount_cents'])} · {refund['date']}"))
        frame = self.store.source_transactions()
        purchases = frame[(frame.kind == '支出') & frame.status.isin(('normal', 'legacy'))].sort_values('date', ascending=False)
        view, model = table(LEDGER_COLUMNS, purchases.to_dict('records'))
        layout.addWidget(view)
        form_buttons(dialog, layout, '核对关联')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        purchase = selected(view, model)
        refund_id = refund['representative_id']
        proposal = self.store.validate_refund_link(refund_id, purchase['id'])
        message = f"将退款 ¥{yuan(proposal['amount_cents'])} 关联到 {purchase['date']} · {purchase['merchant']}？"
        if confirm(self, '确认退款关联', message):
            self.store.confirm_refund_link(refund_id, purchase['id'])
            self.changed('退款关联已确认。')

    def purchase_role(self):
        frame = self.store.source_transactions()
        sources = frame.loc[(frame.status == 'review') & (frame.kind == '支出')]
        if sources.empty:
            self.owner.notice('当前没有需要明确原消费角色的流水。')
            return
        dialog, layout, form = fields(self, '确认原消费角色')
        dialog.resize(800, 480)
        reason = QLineEdit()
        form.addRow('核对说明', reason)
        view, model = table(LEDGER_COLUMNS, sources.to_dict('records'))
        layout.addWidget(view, 1)
        form_buttons(dialog, layout, '确认是原消费')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.confirm_purchase_role(selected(view, model)['id'], reason=reason.text())
            self.changed('原消费角色已确认，请返回退款流水建立关联。')

    def link_details(self):
        row = selected(self.links_view, self.links)
        details(self, '退款关联依据', self.store.refund_link_evidence(row['id']))

    def revoke(self):
        row = selected(self.links_view, self.links)
        if confirm(self, '撤销退款确认', '撤销选中关联并重新计算净额？'):
            self.store.revoke_refund_link(row['id'])
            self.changed('退款关联已撤销。')

    def repair(self):
        if confirm(self, '复核失效关系', '按现有规则标记失效关联为待复核？'):
            self.store.maintain_links(source='qt_review')
            self.changed('已更新待复核状态。')

    def work_details(self):
        row = selected(self.work_view, self.work)
        source = self.store.group_diagnostics(row['id']) if row['kind'] == 'group' else self.store.refund_link_evidence(row['id'])
        details(self, '待复核来源', source)

    def repair_selected(self):
        row = selected(self.work_view, self.work)
        if row['kind'] == 'group' and not row['repair_pending']:
            raise ValueError('该关联已进入待复核，请检查来源后重新确认。')
        if confirm(self, '保存定点修复', '保存此项及其相关失效关系的待复核状态？'):
            repair = self.store.repair_invalid_groups if row['kind'] == 'group' else self.store.repair_refund_links
            repair(identifier=row['id'], source='qt_review')
            self.changed('已保存待复核状态。可在对应对账或退款标签重新核对。')

    def revoke_work(self):
        row = selected(self.work_view, self.work)
        if row['kind'] != 'link' or 'revoke_refund_link' not in row['actions']:
            raise ValueError('请先保存定点修复，再到跨源对账中重新核对该组。')
        if confirm(self, '撤销退款确认', '撤销选中关联并重新计算净额？'):
            self.store.revoke_refund_link(row['id'])
            self.changed('退款关联已撤销。')


class BackupPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '备份与恢复', '恢复只替换当前 Qt 空间，并由共享服务保留恢复前备份。')
        self.directory = label('', 'muted')
        self.layout.addWidget(self.directory)
        row = toolbar(self.layout)
        self.action(row, '创建备份', self.create, primary=True)
        self.action(row, '恢复选中备份', self.restore_selected)
        self.action(row, '从文件恢复', self.choose_restore)
        row.addStretch(1)
        row = toolbar(self.layout)
        self.action(row, '查看选中备份', self.inspect)
        self.action(row, '删除选中备份', self.delete)
        self.action(row, '语义修复历史', lambda: details(self, '语义修复历史', self.store.semantic_repair_history()))
        row.addStretch(1)
        self.history, self.model = self.add_table((
            ('创建时间', lambda row: datetime.fromtimestamp(row['created']).strftime('%Y-%m-%d %H:%M:%S')),
            ('用途', 'purpose'), ('标识', 'reference'), ('大小 / 字节', 'bytes'), ('文件', 'name'),
        ))
        for index, width in enumerate((154, 160, 86, 105)):
            self.history.setColumnWidth(index, width)

    def refresh(self):
        folder = self.store.managed_backup_folder()
        self.directory.setText(str(folder))
        self.model.replace(self.store.managed_backups())

    def inspect(self):
        row = selected(self.history, self.model)
        info = self.store.preview_managed_backup(row['path'])
        counts = info['counts']
        created = datetime.fromtimestamp(info['created']).strftime('%Y-%m-%d %H:%M:%S')
        details(self, '备份预览', f"文件：{info['name']}\n创建时间：{created}\n用途：{info['purpose']}\n"
                f"大小：{info['bytes']:,} 字节\n数据库模式：{info['version']}\n\n"
                f"交易 {counts.get('transactions', 0)} 条 · 预算 {counts.get('budgets', 0)} 项 · "
                f"储蓄目标 {counts.get('goals', 0)} 项 · 退款关联 {counts.get('refund_links', 0)} 条")

    def delete(self):
        row = selected(self.history, self.model)
        if confirm(self, '删除备份', f"删除 {row['name']}？删除后无法使用这份备份恢复。"):
            self.store.delete_managed_backup(row['path'], confirmed=True, expected_identity=row['identity'])
            self.refresh()
            self.owner.notice('已删除选中备份。')

    def create(self):
        path = self.store.create_managed_backup('manual')
        self.refresh()
        self.history.selectRow(next(i for i, row in enumerate(self.model.records) if row['path'] == str(path)))
        self.owner.notice(f'已创建备份 {path.name}')

    def choose_restore(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择账本备份', '', 'SQLite (*.sqlite3 *.db);;所有文件 (*)')
        if path:
            self.restore(path)

    def restore_selected(self):
        row = selected(self.history, self.model)
        self.restore(row['path'], expected_identity=row['identity'])

    def restore(self, path, expected_identity=None):
        info = self.store.preview_managed_backup(path) if expected_identity is not None else inspect_database(path)
        if expected_identity is not None and info['identity'] != expected_identity:
            raise ValueError('备份已变化，请刷新后重新选择。')
        counts = info['counts']
        modified = datetime.fromtimestamp(info.get('created', Path(path).stat().st_mtime)).strftime('%Y-%m-%d %H:%M:%S')
        message = (f"文件 {Path(path).name}\n文件时间 {modified}\n数据库模式 {info['version']} · 交易 {counts.get('transactions', 0)} 条\n"
                   f"目标空间：{self.owner.space.currentText()}\n将替换该空间的交易、预算、目标和其他账本资料。恢复前会创建备份。")
        if confirm(self, '确认恢复', message, accept='确认恢复', decline='暂不恢复'):
            if expected_identity is not None and self.store.preview_managed_backup(path)['identity'] != expected_identity:
                raise ValueError('备份已变化，请刷新后重新选择。')
            self.store.restore(path, confirmed=True)
            self.owner.pages['ledger'].last_batch = None
            self.owner.pages['ledger'].import_result.hide()
            self.owner.chat.reset_sessions()
            self.changed('账本已恢复，恢复前备份已保留。')


class HelpPage(Page):
    def __init__(self, owner, asset_root):
        super().__init__(owner, '使用帮助', '本地帮助按主题查看。')
        topics = {name: text.replace('设置与连接 → 管理账户与绑定', '侧栏 → 资金账户')
                  for name, text in load_help(asset_root / 'help.md').items()}
        chapters = ComboBox()
        chapters.addItems(topics)
        self.layout.addWidget(chapters)
        text = readonly_text('')
        self.layout.addWidget(text, 1)
        chapters.currentTextChanged.connect(lambda name: text.setPlainText(topics[name]))
        text.setPlainText(topics[chapters.currentText()])


class SettingsPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '设置与连接', 'Qt 版使用自己的账本目录与模型配置。启用后仍需在对应页面授权发送。')
        frame, panel = card()
        form = QFormLayout()
        self.base = QLineEdit()
        self.base.setPlaceholderText('https://api.example.com/v1')
        self.model = QLineEdit()
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.enabled = QCheckBox('启用模型')
        self.remember = QCheckBox('用 Windows 加密保存密钥')
        for title, widget in (('接口地址', self.base), ('模型', self.model), ('API Key', self.secret),
                              ('', self.enabled), ('', self.remember)):
            form.addRow(title, widget)
        panel.addLayout(form)
        row = toolbar(panel)
        self.action(row, '保存配置', self.save, primary=True)
        self.action(row, '测试连接', self.test_connection)
        self.action(row, '清除密钥', self.clear)
        row.addStretch(1)
        self.model_status = label('', 'muted')
        panel.addWidget(self.model_status)
        self.layout.addWidget(frame)

        spending, controls = card()
        controls.addWidget(label('退款抵扣口径'))
        row = toolbar(controls)
        self.basis = ComboBox()
        self.basis.addItem('按退款发生月', 'cash')
        self.basis.addItem('回溯原消费月', 'original_purchase_month')
        row.addWidget(self.basis)
        self.action(row, '应用到当前账本', self.apply_basis)
        row.addStretch(1)
        self.layout.addWidget(spending)
        release_card, release_controls = card()
        self.install_directory = updates.installation_directory()
        self.install_mode = updates.installation(self.install_directory) if self.install_directory else ''
        self.pending_install = None
        release_controls.addWidget(label(f"Qt {updates.VERSION} · {'安装版' if self.install_mode else '便携版'}更新"))
        row = toolbar(release_controls)
        self.action(row, '检查 Qt 更新', self.check_updates)
        self.download_button = self.action(row, '下载 Qt 安装包' if self.install_mode else '下载 Qt 压缩包', self.download_update)
        self.download_button.setEnabled(False)
        self.cancel_update = self.action(row, '停止等待', self.stop_update)
        self.cancel_update.setEnabled(False)
        self.action(row, 'GitHub 发布页', lambda: QDesktopServices.openUrl(QUrl(updates.RELEASES)))
        row.addStretch(1)
        self.update_status = label('', 'muted')
        release_controls.addWidget(self.update_status)
        self.install_update = self.action(toolbar(release_controls), '退出并启动升级', self.apply_update)
        self.install_update.hide()
        self.layout.addWidget(release_card)
        self.release = None
        self.layout.addStretch(1)
        self.tested = None
        self.endpoint = ''
        self.load_fields()
        self.base.textChanged.connect(self.endpoint_changed)
        for field in (self.base, self.model, self.secret):
            field.textEdited.connect(self.form_edited)

    def load_fields(self):
        config = self.owner.model_configuration
        self.endpoint = config.base_url
        self.base.setText(config.base_url)
        self.model.setText(config.model)
        self.secret.setText(config.api_key)
        self.enabled.setChecked(config.enabled)
        self.remember.setChecked(config.remember_key)
        self.model_status.setText(config.notice or model_status_text(self.owner.client))

    def endpoint_changed(self, text):
        address = text.strip().rstrip('/')
        if address != self.endpoint:
            self.endpoint = address
            self.secret.clear()
            self.tested = None
            self.owner.jobs.cancel('model-test')
            self.model_status.setText('接口地址已变化，请重新填写该接口的密钥。')

    def form_edited(self, *_):
        self.tested = None
        self.owner.jobs.cancel('model-test')

    def form_values(self):
        return self.base.text().strip().rstrip('/'), self.model.text().strip(), self.secret.text().strip()

    def refresh(self):
        self.basis.setCurrentIndex(self.basis.findData(self.store.spending_basis()))

    def save(self):
        values = self.form_values()
        config = self.owner.model_settings.save(*values, self.enabled.isChecked(), self.remember.isChecked())
        verified = self.tested[1] if self.tested and self.tested[0] == values else None
        self.owner.configure_model(config, verified)
        self.load_fields()
        self.owner.notice('Qt 版模型配置已保存。')

    def clear(self):
        if confirm(self, '清除 API Key', '清除 Qt 版保存的密钥并停用模型？'):
            self.tested = None
            self.owner.configure_model(self.owner.model_settings.clear_key())
            self.load_fields()

    def test_connection(self):
        values = self.form_values()
        current = ModelSettings._validate(*values)
        self.model_status.setText('正在测试当前表单，只发送一条测试消息…')

        def probe(_):
            try:
                current.test_connection()
            except Exception as exc:
                return False, connection_error_message(exc)
            return True, '连接测试通过，保存后生效。'

        def show(result, error):
            if self.form_values() == values:
                if error:
                    self.model_status.setText(error)
                else:
                    ok, message = result
                    self.tested = (values, current) if ok else None
                    self.model_status.setText(message)

        self.owner.jobs.start('model-test', probe, show)

    def apply_basis(self):
        basis = self.basis.currentData()
        if basis != self.store.spending_basis() and confirm(
            self, '切换退款抵扣口径', '应用所选口径后，概览、预算、问答和规划会按新口径计算。继续？'
        ):
            self.store.set_spending_basis(basis, confirmed=True)
            self.changed('退款抵扣口径已更新。')

    def stop_update(self):
        self.owner.jobs.cancel('qt-update')
        self.cancel_update.setEnabled(False)
        self.download_button.setEnabled(self.release is not None)
        self.update_status.setText('已停止等待。')

    def check_updates(self):
        self.release = None
        self.pending_install = None
        self.install_update.hide()
        self.download_button.setEnabled(False)
        self.cancel_update.setEnabled(True)
        self.update_status.setText('正在检查 Qt 更新…')

        def show(result, error):
            self.cancel_update.setEnabled(False)
            self.release = result
            self.download_button.setEnabled(result is not None)
            if error:
                self.update_status.setText(error)
            elif result is not None:
                action = '下载后可退出应用并启动升级。' if self.install_mode else '下载后可解压到单独文件夹。'
                self.update_status.setText(f'发现 {result.tag}。{action}')
            else:
                self.update_status.setText('暂无此通道可下载的较新 Qt 版本。')

        def probe(_):
            try:
                return updates.check(installed=bool(self.install_mode)), ''
            except Exception as exc:
                return None, update_error_message(exc, '检查更新')

        self.owner.jobs.start('qt-update', probe, lambda result, error: show(*(result or (None, error))))

    def download_update(self):
        release = self.release
        if release is None:
            return
        file_type = 'Windows 安装包 (*.exe)' if self.install_mode else 'ZIP (*.zip)'
        target, _ = QFileDialog.getSaveFileName(self, '保存 Qt 更新', release.name, file_type)
        if not target:
            return
        if self.install_mode and Path(target).name != release.name:
            raise ValueError('请保留 Qt 安装包的原始文件名。')
        self.pending_install = None
        self.install_update.hide()
        self.download_button.setEnabled(False)
        self.cancel_update.setEnabled(True)
        self.update_status.setText('正在下载并校验 Qt 更新…')

        def fetch(stop):
            try:
                return updates.download(release, target, stop), ''
            except Exception as exc:
                return None, update_error_message(exc)

        def show(result, error):
            self.cancel_update.setEnabled(False)
            self.download_button.setEnabled(True)
            path, failure = result or (None, error)
            self.update_status.setText(f'已通过 SHA256 校验并保存到 {path}。' if path else failure)
            if path and self.install_mode:
                self.pending_install = (release, path)
                self.install_update.show()

        self.owner.jobs.start('qt-update', fetch, show)

    def apply_update(self):
        if not self.pending_install or not self.install_mode:
            return
        if confirm(self, '退出并升级 Qt', '确认关闭 Qt 并启动已校验的安装包？升级会使用当前程序目录。'):
            updates.launch_installer(*self.pending_install, self.install_directory, self.install_mode)
            self.owner.close()
