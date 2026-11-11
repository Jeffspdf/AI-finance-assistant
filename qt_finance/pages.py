"""Qt views for the ledger, its budgets, accounts, reconciliation and backups."""

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
    QTabWidget, QVBoxLayout, QWidget,
)

from finance.analytics import budget_report, overview, yuan
from finance.categories import category_options
from finance.help_content import load_help
from finance.importer import money_to_cents
from finance.migrations import inspect_database
from finance.llm import connection_error_message, model_status_text
from finance.qa import month_shift
from finance.spending_presenter import basis_label, net_export_csv, spending_totals

from .chat import EVIDENCE_COLUMNS
from .services import preview_statement
from .ui import button, card, confirm, details, label, readonly_text, selected, table


def toolbar(layout):
    row = QHBoxLayout()
    layout.addLayout(row)
    return row


def fields(parent, title):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(440)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)
    return dialog, layout, form


def form_buttons(dialog, layout, title='确定'):
    controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    controls.button(QDialogButtonBox.StandardButton.Ok).setText(title)
    controls.accepted.connect(dialog.accept)
    controls.rejected.connect(dialog.reject)
    layout.addWidget(controls)
    return controls


class Page(QWidget):
    def __init__(self, owner, title, caption):
        super().__init__()
        self.setObjectName('workArea')
        self.owner = owner
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 14, 28, 18)
        self.layout.setSpacing(10)
        self.layout.addWidget(label(title, 'title'))
        self.layout.addWidget(label(caption, 'muted'))

    @property
    def store(self):
        return self.owner.workspace.store

    def action(self, row, title, callback, *, primary=False):
        control = button(title, lambda: self.owner.perform(callback), primary=primary)
        row.addWidget(control)
        return control

    def add_table(self, columns):
        view, model = table(columns)
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
        tabs = QTabWidget()
        monthly, self.monthly = table([(name, name) for name in ('月份', '收入', '支出', '结余')])
        categories, self.categories = table([(name, name) for name in ('分类', '支出', '占比')])
        tabs.addTab(monthly, '月度收支 / 元')
        tabs.addTab(categories, '支出分类 / 元')
        self.layout.addWidget(tabs, 1)

    def refresh(self):
        frame = self.store.spending_view()
        totals = spending_totals(frame)
        for name, label_widget in self.metrics.items():
            label_widget.setText('¥' + yuan(totals[name]))
        self.basis.setText(basis_label(totals['basis']) + ' · 全部已导入记录')
        monthly, categories = overview(frame)
        self.monthly.replace(monthly.to_dict('records'))
        self.categories.replace(categories.to_dict('records'))


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
        self.action(actions, '查看来源', self.source)
        self.action(actions, '编辑选中', self.edit_selected)
        self.action(actions, '删除选中', self.delete_selected)
        self.action(actions, '导入批次', self.import_history)
        self.action(actions, '导出净额', self.export)
        actions.addStretch(1)
        self.records = []
        self.view, self.model = self.add_table(LEDGER_COLUMNS)

    def refresh(self):
        frame = self.store.source_transactions().sort_values('date', ascending=False)
        self.records = frame.to_dict('records')
        self.filter()

    def filter(self, *_):
        query = self.search.text().strip().casefold()
        self.model.replace(row for row in self.records if not query or any(
            query in str(row.get(key, '')).casefold() for key in ('date', 'merchant', 'category', 'description')
        ))

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
        inputs = {}
        for key, title, default in (
            ('date', '日期 / YYYY-MM-DD', date.today().isoformat()),
            ('merchant', '商户', ''), ('description', '摘要', ''),
            ('amount_cents', '金额 / 元', ''),
        ):
            initial = original.get(key, default)
            if key == 'amount_cents' and original:
                initial = yuan(initial)
            entry = inputs[key] = QLineEdit(str(initial))
            form.addRow(title, entry)
        kind, category = QComboBox(), QComboBox()
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
        form_buttons(dialog, layout, '保存交易')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = {key: entry.text().strip() for key, entry in inputs.items()}
        data['amount_cents'] = money_to_cents(data['amount_cents'])
        data.update(kind=kind.currentText(), category=category.currentText())
        if original:
            self.store.edit_transaction(original['id'], data)
        else:
            self.store.add_manual_transaction(data['date'], data['merchant'], data['amount_cents'],
                                              data['kind'], data['category'], data['description'])
        self.changed('交易已保存。')

    def delete_selected(self):
        row = selected(self.view, self.model)
        if confirm(self, '删除交易', f"从当前账本移除 {row['date']} · {row['merchant']} · ¥{yuan(row['amount_cents'])}？"):
            self.store.soft_delete_transaction(row['id'], confirmed=True)
            self.changed('交易已移除，来源和审计记录已保留。')

    def choose_import(self):
        source, _ = QFileDialog.getOpenFileName(self, '选择账单', '', '账单 (*.csv *.xlsx)')
        if not source:
            return
        dialog, layout, form = fields(self, '导入选项')
        adapter = QComboBox()
        for title, key in (('自动识别', 'auto'), ('通用表格', 'generic'), ('微信 CSV', 'wechat_csv'),
                           ('银行模板 XLSX', 'bank_template_v1')):
            adapter.addItem(title, key)
        account = QComboBox()
        account.addItem('未声明账户', None)
        for item in self.store.accounts(include_archived=False):
            account.addItem(item['label'], item['id'])
        form.addRow('格式', adapter)
        form.addRow('来源账户', account)
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
        self.owner.perform(lambda: self.show_import(path, *result))

    def show_import(self, path, parsed, preview):
        dialog = QDialog(self)
        dialog.setWindowTitle('导入预览')
        dialog.resize(920, 620)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"识别 {len(parsed.rows)} 行 · 问题 {len(parsed.issues)} 项 · 已删除 {preview['deleted_count']} 行"))
        tabs = QTabWidget()
        rows, _ = table(EVIDENCE_COLUMNS, parsed.rows)
        tabs.addTab(rows, '待导入')
        if parsed.issues or parsed.warnings:
            tabs.addTab(readonly_text({'issues': parsed.issues, 'warnings': parsed.warnings}), '解析提示')
        if preview['deleted_rows']:
            deleted, _ = table((('日期', 'date'), ('商户', 'merchant'), ('金额 / 元', lambda r: yuan(r['amount_cents'])),
                                ('删除原因', 'reason')), preview['deleted_rows'])
            tabs.addTab(deleted, '已删除记录')
        layout.addWidget(tabs, 1)
        restore = QCheckBox('恢复预览中列出的已删除记录')
        restore.setEnabled(bool(preview['deleted_rows']))
        layout.addWidget(restore)
        form_buttons(dialog, layout, '确认导入')
        if dialog.exec() != QDialog.DialogCode.Accepted or path != self.store.path:
            return
        restore_ids = {row['id'] for row in preview['deleted_rows']} if restore.isChecked() else None
        result = self.store.import_statement(parsed, restore_deleted=restore.isChecked(), restore_ids=restore_ids)
        added, duplicates, conflicts = result
        self.changed(f'新增 {added - result.restored} · 恢复 {result.restored} · 重复 {duplicates} · '
                     f'保持删除 {result.deleted_skipped} · 冲突 {len(conflicts)}')

    def import_history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('导入批次')
        dialog.resize(820, 540)
        layout = QVBoxLayout(dialog)
        columns = (('文件', 'filename'), ('账户', 'account_label'), ('状态', 'status'), ('新增／恢复', 'added'),
                   ('重复', 'duplicates'), ('冲突', 'conflicts'))
        view, model = table(columns, self.store.import_batches())
        layout.addWidget(view)

        def remove():
            row = selected(view, model)
            if confirm(dialog, '删除导入批次', f"从账本移除「{row['filename']}」这一批的交易？"):
                self.store.delete_import_batch(row['id'])
                model.replace(self.store.import_batches())
                self.changed('导入批次已移除，审计与来源记录已保留。')

        actions = toolbar(layout)
        self.action(actions, '查看批次', lambda: details(dialog, '批次资料', selected(view, model)))
        self.action(actions, '删除批次', remove)
        actions.addStretch(1)
        actions.addWidget(button('关闭', dialog.close))
        dialog.exec()

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(self, '导出净额', '净额投影.csv', 'CSV (*.csv)')
        if filename:
            destination = Path(filename).resolve()
            if destination.suffix.lower() != '.csv':
                raise ValueError('请将净额导出保存为 .csv 文件。')
            destination.write_bytes(net_export_csv(self.store.spending_view()))
            self.owner.notice('净额投影已导出。')


class BudgetPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '预算与提醒', '预算、支出和剩余均按所选月份显示。')
        row = toolbar(self.layout)
        self.month = QComboBox()
        self.month.addItems([month_shift(date.today(), i) for i in range(12, -25, -1)])
        self.month.setCurrentText(date.today().strftime('%Y-%m'))
        self.category = QComboBox()
        self.category.addItems(category_options('支出'))
        self.amount = QLineEdit()
        self.amount.setPlaceholderText('预算 / 元')
        row.addWidget(self.month)
        row.addWidget(self.category)
        row.addWidget(self.amount, 1)
        self.action(row, '保存预算', self.save, primary=True)
        self.action(row, '复制上月', self.copy_previous)
        self.coverage = label('', 'muted')
        self.layout.addWidget(self.coverage)
        self.view, self.model = self.add_table([(name, name) for name in ('分类', '预算', '已支出', '剩余', '使用率', '超支')])
        self.month.currentTextChanged.connect(lambda _: self.owner.perform(self.refresh))

    def refresh(self):
        month = self.month.currentText()
        frame = self.store.spending_view()
        present = month in frame.attrs.get('source_months', ()) or frame.date.str.startswith(month).any()
        report = budget_report(frame, month, self.store.budgets(month))
        if not present:
            for field in ('已支出', '剩余', '使用率', '超支'):
                report[field] = None
        self.coverage.setText('金额单位：元' if present else '该月暂无账单资料，仍可提前设置预算。')
        self.model.replace(report.to_dict('records'))

    def save(self):
        self.store.save_budget(self.month.currentText(), self.category.currentText(),
                               money_to_cents(self.amount.text(), allow_zero=True))
        self.amount.clear()
        self.changed('预算已保存。')

    def copy_previous(self):
        month = self.month.currentText()
        previous = month_shift(date.fromisoformat(month + '-01'), -1)
        if confirm(self, '复制预算', f'将 {previous} 的预算复制到 {month}，保留目标月已设置的分类？'):
            count = self.store.copy_budgets(previous, month, overwrite=False)
            self.changed(f'已检查上月 {count} 个分类，并补入尚未设置的预算。')


ACCOUNT_TYPES = {'wallet': '钱包', 'debit': '储蓄卡', 'credit': '信用卡'}


class AccountsPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '资金账户', '仅记录平台与卡号末四位。支付绑定用于跨源对账。')
        row = toolbar(self.layout)
        self.action(row, '添加账户', lambda: self.edit({}), primary=True)
        self.action(row, '编辑账户', lambda: self.edit(selected(self.view, self.model)))
        self.action(row, '归档／恢复', self.archive)
        row.addStretch(1)
        self.view, self.model = self.add_table((
            ('账户名', 'label'), ('平台', 'channel'), ('类型', lambda r: ACCOUNT_TYPES.get(r['type'], r['type'])),
            ('末四位', 'last4'), ('状态', lambda r: '已归档' if r['archived_at'] else '使用中'),
        ))
        row = toolbar(self.layout)
        row.addWidget(label('支付绑定'), 1)
        self.action(row, '新建绑定', self.bind_account)
        self.action(row, '结束绑定', self.end_binding)
        self.links_view, self.links = self.add_table((
            ('钱包', 'wallet_label'), ('资金账户', 'bank_label'), ('生效日期', 'active_from'), ('结束日期', 'active_to'),
        ))

    def refresh(self):
        accounts = self.store.accounts()
        self.model.replace(accounts)
        names = {row['id']: row['label'] for row in accounts}
        self.links.replace(dict(row, wallet_label=names.get(row['wallet_account_id']),
                                bank_label=names.get(row['bank_account_id'])) for row in self.store.funding_links())

    def edit(self, original):
        dialog, layout, form = fields(self, '编辑账户' if original else '添加账户')
        name = QLineEdit(original.get('label', ''))
        channel = QLineEdit(original.get('channel', ''))
        last4 = QLineEdit(original.get('last4', ''))
        last4.setMaxLength(4)
        kind = QComboBox()
        for key, title in ACCOUNT_TYPES.items():
            kind.addItem(title, key)
        if original:
            kind.setCurrentIndex(kind.findData(original['type']))
            kind.setEnabled(False)
        for title, widget in (('账户名', name), ('平台', channel), ('类型', kind), ('末四位', last4)):
            form.addRow(title, widget)
        form_buttons(dialog, layout, '保存账户')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if original:
                self.store.update_account(original['id'], label=name.text(), channel=channel.text(),
                                          last4=last4.text(), confirmed=True)
            else:
                self.store.add_account(channel.text(), kind.currentData(), name.text(), last4.text())
            self.changed('账户已保存。')

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
        wallet, bank = QComboBox(), QComboBox()
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
        tabs = QTabWidget()
        self.layout.addWidget(tabs, 1)
        _, group_layout = self.tab(tabs, '跨源对账')
        self.groups_view, self.groups = table((
            ('状态', 'status'), ('置信度', 'confidence'), ('类型', 'kind'),
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

        _, refund_layout = self.tab(tabs, '退款流水')
        self.refunds_view, self.refunds = table((
            ('日期', 'date'), ('商户', 'merchant'), ('金额 / 元', lambda r: yuan(r['amount_cents'])),
            ('关联状态', 'association_status'), ('来源数', lambda r: len(r['member_ids'])),
        ))
        refund_layout.addWidget(self.refunds_view, 1)
        row = toolbar(refund_layout)
        self.action(row, '查看退款来源', lambda: details(self, '退款来源', selected(self.refunds_view, self.refunds)))
        self.action(row, '关联原消费', self.associate_refund, primary=True)
        row.addStretch(1)

        _, links_layout = self.tab(tabs, '退款关联')
        self.links_view, self.links = table((
            ('状态', 'status'), ('抵扣 / 元', lambda r: yuan(r['amount_cents'])),
            ('退款交易', 'refund_txn_id'), ('原消费', 'original_txn_id'), ('核对说明', 'reason'),
        ))
        links_layout.addWidget(self.links_view, 1)
        row = toolbar(links_layout)
        self.action(row, '查看依据', self.link_details)
        self.action(row, '撤销确认', self.revoke)
        self.action(row, '复核失效关系', self.repair)
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
                    acknowledge = confirm(self, '核对无钱包来源', '此组没有已声明的钱包。已核对并确定是同一笔消费？')
                    if not acknowledge:
                        return
            self.store.decide_group(row['id'], decision, acknowledge_no_wallet=acknowledge)
        self.changed('对账决定已保存。')

    def associate_refund(self):
        refund = selected(self.refunds_view, self.refunds)
        dialog = QDialog(self)
        dialog.setWindowTitle('选择原消费')
        dialog.resize(840, 530)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"退款 ¥{yuan(refund['amount_cents'])} · {refund['date']}"))
        frame = self.store.source_transactions()
        purchases = frame[(frame.kind == '支出') & (frame.status == 'normal')].sort_values('date', ascending=False)
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


class BackupPage(Page):
    def __init__(self, owner):
        super().__init__(owner, '备份与恢复', '恢复只替换当前 Qt 空间，并由共享服务保留恢复前备份。')
        self.directory = label('', 'muted')
        self.layout.addWidget(self.directory)
        row = toolbar(self.layout)
        self.action(row, '创建备份', self.create, primary=True)
        self.action(row, '从备份恢复', self.choose_restore)
        row.addStretch(1)
        self.history, self.model = self.add_table((
            ('文件', lambda r: r['path'].name), ('大小 / 字节', 'size'), ('位置', lambda r: str(r['path'])),
        ))

    def refresh(self):
        folder = Path(self.store.path).parent / 'backups'
        self.directory.setText(str(folder))
        paths = sorted(folder.glob(f'{Path(self.store.path).stem}-v*.sqlite3'),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
        self.model.replace(dict(path=p, size=p.stat().st_size) for p in paths)

    def create(self):
        path = self.store.backup()
        self.refresh()
        self.owner.notice(f'已创建备份 {path.name}')

    def choose_restore(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择账本备份', '', 'SQLite (*.sqlite3 *.db);;所有文件 (*)')
        if path:
            self.restore(path)

    def restore(self, path):
        info = inspect_database(path)
        counts = info['counts']
        message = (f"文件 {Path(path).name}\n数据库模式 {info['version']} · 交易 {counts.get('transactions', 0)} 条\n"
                   f"替换 {self.owner.space.currentText()} 的数据？恢复前会创建备份。")
        if confirm(self, '确认恢复', message):
            self.store.restore(path, confirmed=True)
            self.owner.chat.history[self.owner.workspace.mode].clear()
            self.owner.chat.reload()
            self.changed('账本已恢复，恢复前备份已保留。')


class HelpPage(Page):
    def __init__(self, owner, asset_root):
        super().__init__(owner, '使用帮助', '本地帮助按主题查看。')
        topics = load_help(asset_root / 'help.md')
        chapters = QComboBox()
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
        self.action(row, '测试已保存连接', self.test_connection)
        self.action(row, '清除密钥', self.clear)
        row.addStretch(1)
        self.model_status = label('', 'muted')
        panel.addWidget(self.model_status)
        self.layout.addWidget(frame)

        spending, controls = card()
        controls.addWidget(label('退款抵扣口径'))
        row = toolbar(controls)
        self.basis = QComboBox()
        self.basis.addItem('按退款发生月', 'cash')
        self.basis.addItem('回溯原消费月', 'original_purchase_month')
        row.addWidget(self.basis)
        self.action(row, '应用到当前账本', self.apply_basis)
        row.addStretch(1)
        self.layout.addWidget(spending)
        self.layout.addStretch(1)
        self.load_fields()

    def load_fields(self):
        config = self.owner.model_configuration
        self.base.setText(config.base_url)
        self.model.setText(config.model)
        self.secret.setText(config.api_key)
        self.enabled.setChecked(config.enabled)
        self.remember.setChecked(config.remember_key)
        self.model_status.setText(config.notice or model_status_text(self.owner.client))

    def refresh(self):
        self.basis.setCurrentIndex(self.basis.findData(self.store.spending_basis()))

    def save(self):
        config = self.owner.model_settings.save(self.base.text(), self.model.text(), self.secret.text(),
                                               self.enabled.isChecked(), self.remember.isChecked())
        self.owner.configure_model(config)
        self.load_fields()
        self.owner.notice('Qt 版模型配置已保存。')

    def clear(self):
        if confirm(self, '清除 API Key', '清除 Qt 版保存的密钥并停用模型？'):
            self.owner.configure_model(self.owner.model_settings.clear_key())
            self.load_fields()

    def test_connection(self):
        current = self.owner.client
        if current is None:
            raise ValueError('请先保存并启用模型。')
        self.model_status.setText('正在测试已保存的连接…')

        def probe(_):
            try:
                current.test_connection()
            except Exception as exc:
                return connection_error_message(exc)
            return '连接测试通过。'

        def show(result, error):
            if self.owner.client is current:
                self.model_status.setText(error or result)

        self.owner.jobs.start('model-test', probe, show)

    def apply_basis(self):
        basis = self.basis.currentData()
        if basis != self.store.spending_basis() and confirm(
            self, '切换退款抵扣口径', '应用所选口径后，概览、预算、问答和规划会按新口径计算。继续？'
        ):
            self.store.set_spending_basis(basis, confirmed=True)
            self.changed('退款抵扣口径已更新。')
