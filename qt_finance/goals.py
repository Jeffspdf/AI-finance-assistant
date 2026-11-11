"""Explicit goal and contribution management over the shared Store."""

from datetime import date

from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout

from finance.analytics import goal_progress, plan_savings, yuan
from finance.importer import money_to_cents
from .pages import fields, form_buttons, toolbar
from .ui import button, confirm, label, selected, table


class GoalsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.store = owner, owner.workspace.store
        self.setWindowTitle('储蓄目标与实际存款')
        self.resize(790, 565)
        layout = QVBoxLayout(self)
        layout.addWidget(label('储蓄目标与实际存款', 'title'))
        self.view, self.model = table((('目标', 'name'), ('截止日期', 'deadline'),
                                      ('目标 / 元', lambda r: yuan(r['target_cents'])),
                                      ('已存 / 元', lambda r: yuan(r['actual_saved'])),
                                      ('待存 / 元', lambda r: yuan(r['remaining']))))
        layout.addWidget(self.view, 2)
        row = toolbar(layout)
        for title, callback in (
            ('新建目标', lambda: self.edit({})), ('编辑目标', lambda: self.edit(self.goal())),
            ('删除目标', self.delete), ('记录存款', self.deposit),
        ):
            row.addWidget(button(title, lambda checked=False, action=callback: self.run(action)))
        row.addStretch(1)
        self.deposits_view, self.deposits = table((('存入日期', 'date'), ('金额 / 元', lambda r: yuan(r['amount_cents']))))
        layout.addWidget(self.deposits_view, 1)
        row = toolbar(layout)
        row.addWidget(button('撤销选中存款', lambda: self.run(self.remove_deposit)))
        row.addStretch(1)
        row.addWidget(button('关闭', self.close))
        self.status = label('', 'muted')
        layout.addWidget(self.status)
        self.view.selectionModel().selectionChanged.connect(self.refresh_deposits)
        self.refresh()

    def goal(self):
        return selected(self.view, self.model)

    def run(self, action):
        try:
            action()
        except ValueError as exc:
            self.status.setText(str(exc))
        except Exception:
            self.status.setText('目标操作未完成，请重试。')

    def refresh(self):
        rows = []
        for row in self.store.goals().to_dict('records'):
            try:
                progress = goal_progress(row, self.store.contributions(row['id']))
            except ValueError:
                progress = {'actual_saved': row['saved_cents'], 'remaining': row['target_cents'] - row['saved_cents']}
            rows.append(dict(row, **progress))
        self.model.replace(rows)
        if rows:
            self.view.selectRow(0)
        self.refresh_deposits()

    def refresh_deposits(self, *_):
        if self.view.selectionModel().hasSelection():
            self.deposits.replace(self.store.contributions(self.goal()['id']).to_dict('records'))
        else:
            self.deposits.replace([])

    def edit(self, original, *, prefilled=None):
        initial_values = prefilled or original
        dialog, layout, form = fields(self, '编辑目标' if original else '新建储蓄目标')
        inputs = {}
        for key, title, initial in (
            ('name', '目标名', ''), ('target_cents', '目标金额 / 元', ''),
            ('saved_cents', '已有储蓄 / 元', '0'), ('income_cents', '月收入 / 元', ''),
            ('budget_cents', '月预算 / 元', ''), ('start_date', '开始日', date.today().isoformat()),
            ('deadline', '截止日 / YYYY-MM-DD', ''),
        ):
            value = initial_values.get(key, initial)
            field = inputs[key] = QLineEdit(yuan(value).replace(',', '') if key.endswith('_cents') and key in initial_values else str(value))
            form.addRow(title, field)
        form_buttons(dialog, layout, '保存目标')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = {key: (money_to_cents(field.text(), allow_zero=key != 'target_cents')
                        if key.endswith('_cents') else field.text().strip()) for key, field in inputs.items()}
        if not values['name']:
            raise ValueError('请填写目标名。')
        plan_savings(*(values[key] for key in ('target_cents', 'saved_cents', 'start_date', 'deadline', 'income_cents', 'budget_cents')))
        if original:
            self.store.update_goal(original['id'], values)
        else:
            self.store.save_goal(values)
        self.refresh()
        self.status.setText('目标已保存。')
        return True

    def delete(self):
        row = self.goal()
        if confirm(self, '删除目标', f"删除「{row['name']}」及其存入记录？"):
            self.store.delete_goal(row['id'])
            self.refresh()

    def deposit(self):
        goal = self.goal()
        dialog, layout, form = fields(self, '记录实际存款')
        when, amount = QLineEdit(date.today().isoformat()), QLineEdit()
        form.addRow('日期 / YYYY-MM-DD', when)
        form.addRow('金额 / 元', amount)
        form_buttons(dialog, layout, '记录存款')
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.add_contribution(goal['id'], when.text().strip(), money_to_cents(amount.text()))
            self.refresh()

    def remove_deposit(self):
        row = selected(self.deposits_view, self.deposits)
        if confirm(self, '撤销存款记录', '移除这条实际存款记录？'):
            self.store.remove_contribution(self.goal()['id'], row['id'])
            self.refresh()
