"""Explicit goal and contribution management over the shared Store."""

from datetime import date

from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout

from finance.analytics import goal_progress, plan_savings, yuan
from finance.importer import money_to_cents
from .pages import fields, form_buttons, save_form, toolbar
from .ui import button, confirm, label, selected, table


class GoalsDialog(QDialog):
    def __init__(self, owner, goal_id=None):
        super().__init__(owner)
        self.owner, self.store = owner, owner.workspace.store
        self.setWindowTitle('储蓄目标与实际存款')
        self.resize(790, 565)
        layout = QVBoxLayout(self)
        layout.addWidget(label('储蓄目标与实际存款', 'title'))
        self.view, self.model = table((('目标', 'name'), ('截止日期', 'deadline'),
                                      ('目标 / 元', lambda r: yuan(r['target_cents'])),
                                      ('已存 / 元', lambda r: yuan(r['actual_saved'])),
                                      ('月预算 / 元', lambda r: yuan(r['budget_cents'])),
                                      ('到期缺口 / 元', lambda r: yuan(r['current_deficit']) if 'current_deficit' in r else '—'),
                                      ('待存 / 元', lambda r: yuan(r['remaining']))))
        layout.addWidget(self.view, 2)
        row = toolbar(layout)
        for title, callback in (
            ('新建目标', lambda: self.edit({})), ('编辑目标', lambda: self.edit(self.goal())),
            ('删除目标', self.delete), ('记录存款', self.deposit),
        ):
            row.addWidget(button(title, lambda checked=False, action=callback: self.run(action)))
        row.addStretch(1)
        self.summary = label('', 'muted')
        layout.addWidget(self.summary)
        self.deposits_view, self.deposits = table((('存入日期', 'date'), ('金额 / 元', lambda r: yuan(r['amount_cents']))))
        layout.addWidget(self.deposits_view, 1)
        row = toolbar(layout)
        row.addWidget(button('撤销选中存款', lambda: self.run(self.remove_deposit)))
        row.addStretch(1)
        row.addWidget(button('关闭', self.close))
        self.status = label('', 'muted')
        layout.addWidget(self.status)
        self.view.selectionModel().selectionChanged.connect(self.refresh_deposits)
        self.refresh(goal_id)

    def goal(self):
        return selected(self.view, self.model)

    def run(self, action):
        try:
            action()
        except ValueError as exc:
            self.status.setText(str(exc))
        except Exception:
            self.status.setText('目标操作未完成，请重试。')

    def refresh(self, goal_id=None):
        rows = []
        for row in self.store.goals().to_dict('records'):
            try:
                progress = goal_progress(row, self.store.contributions(row['id']))
            except ValueError:
                progress = {'actual_saved': row['saved_cents'], 'remaining': row['target_cents'] - row['saved_cents']}
            rows.append(dict(row, **progress))
        self.model.replace(rows)
        if rows:
            self.view.selectRow(next((i for i, row in enumerate(rows) if row['id'] == goal_id), 0))
        self.refresh_deposits()

    def refresh_deposits(self, *_):
        if self.view.selectionModel().hasSelection():
            row = self.goal()
            self.deposits.replace(self.store.contributions(row['id']).to_dict('records'))
            if 'plan' not in row:
                self.summary.setText('请检查此目标的日期和金额。')
                return
            plan = row['plan']
            dates = row['future_dates']
            self.summary.setToolTip('剩余计划日期：' + ('、'.join(dates) if dates else '无'))
            self.summary.setText(
                f"起止 {row['start_date']} 至 {row['deadline']} · 月收入 ¥{yuan(row['income_cents'])} · "
                f"月预算 ¥{yuan(row['budget_cents'])}。\n"
                f"原计划 {plan['months']} 次存入，预计 ¥{yuan(plan['projected'])}，缺口 ¥{yuan(plan['deficit'])}。\n"
                f"当前预测（{date.today().isoformat()}）：实际已存 ¥{yuan(row['actual_saved'])} + "
                f"{len(dates)} 次 × 每月可存 ¥{yuan(plan['monthly_available'])} = "
                f"¥{yuan(row['current_projected'])}，到期缺口 ¥{yuan(row['current_deficit'])}。\n"
                f"剩余计划日期：{('、'.join(dates[:4]) + (' 等' if len(dates) > 4 else '')) if dates else '无'}。")
        else:
            self.deposits.replace([])
            self.summary.setToolTip('')
            self.summary.setText('暂无储蓄目标。')

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
        plan_keys = ('target_cents', 'saved_cents', 'start_date', 'deadline', 'income_cents', 'budget_cents')
        if prefilled:
            plan = plan_savings(*(initial_values[key] for key in plan_keys))
            layout.addWidget(label(f"月预算 ¥{yuan(initial_values['budget_cents'])} · "
                                   f"预计到期 ¥{yuan(plan['projected'])} · 缺口 ¥{yuan(plan['deficit'])}。"
                                   "此摘要基于填入的方案。", 'muted'))
        saved_id = None

        def save():
            nonlocal saved_id
            values = {key: (money_to_cents(field.text(), allow_zero=key != 'target_cents')
                            if key.endswith('_cents') else field.text().strip()) for key, field in inputs.items()}
            if not values['name']:
                raise ValueError('请填写目标名。')
            plan_savings(*(values[key] for key in plan_keys))
            if original:
                saved_id = original['id']
                self.store.update_goal(saved_id, values)
                return
            duplicate = any(all(row[key] == value for key, value in values.items())
                            for row in self.store.goals().to_dict('records'))
            if duplicate and not confirm(dialog, '目标已存在', '已有相同名称与参数的目标。仍要另存一份吗？'):
                raise ValueError('已保留现有目标。可修改名称或参数后再保存。')
            saved_id = self.store.save_goal(values)

        save_form(dialog, layout, form, '保存目标', save)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh(saved_id)
            self.status.setText('目标已保存。')
            return saved_id

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
            self.refresh(goal['id'])

    def remove_deposit(self):
        row = selected(self.deposits_view, self.deposits)
        if confirm(self, '撤销存款记录', '移除这条实际存款记录？'):
            goal_id = self.goal()['id']
            self.store.remove_contribution(goal_id, row['id'])
            self.refresh(goal_id)
