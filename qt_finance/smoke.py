"""Exercise the native Qt screens with synthetic ledgers."""

from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QPushButton, QTableView

from finance.m1a_demo import seed_six
from finance.qa import month_shift
from finance.storage import Store
from .services import guidance
from .window import FinanceWindow


def wait(app, predicate, label):
    until = monotonic() + 10
    while monotonic() < until:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError(f'Timed out: {label}')


def click(parent, text):
    QApplication.processEvents()
    widget = next(w for w in parent.findChildren(QPushButton) if w.text() == text and w.isVisible())
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def chat_and_plan(app, window, output):
    chat, plan = window.chat, window.plan
    ledger = Path(window.workspace.store.path)
    before = ledger.read_bytes()
    assert window.grab().save(str(output / 'qt-welcome.png'))
    chat.input.setPlainText('上个月')
    chat.input.moveCursor(QTextCursor.MoveOperation.End)
    QTest.keyClick(chat.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert chat.input.toPlainText() == '上个月\n'
    chat.input.insertPlainText('餐饮花了多少？')
    QTest.keyClick(chat.input, Qt.Key.Key_Return)
    wait(app, lambda: not chat.busy and chat.last_result is not None, 'ledger answer')
    assert '123.45 元，共 1 笔' in chat.last_result['answer']
    assert chat.last_result['evidence'][0]['merchant'] == 'Qt 合成食堂'
    click(chat, '查看依据')
    assert chat.evidence.isVisible() and chat.evidence.tabs.count() == 3
    chat.evidence.close()
    assert window.grab().save(str(output / 'qt-answer.png'))
    chat.input.setPlainText('我想存 6800 元')
    QTest.mouseClick(chat.send, Qt.MouseButton.LeftButton)
    assert chat.last_result['route'] == 'plan'
    click(chat, '打开储蓄规划')
    assert window.current == 'plan' and plan.inputs['target_cents'].text() == '6800'
    plan.confirmed.setChecked(True)
    QTest.mouseClick(plan.generate, Qt.MouseButton.LeftButton)
    wait(app, lambda: plan.result is not None, 'A/B/C')
    assert plan.result['status'] == 'ok' and plan.result['model_calls'] == 0
    assert len(plan.result['trace']) == 3
    assert all(amount.text().startswith('¥') for amount, _ in plan.cards.values())
    assert before == ledger.read_bytes()
    QTest.qWait(50)
    sizes = [(box.width(), box.height(), box.heightForWidth(box.width())) for _, box in plan.cards.values()]
    assert all(height >= needed for _, height, needed in sizes), sizes
    assert window.grab().save(str(output / 'qt-planner.png'))
    assert guidance('怎么管理银行卡')[0] == 'accounts'
    assert guidance('如何恢复备份')[0] == 'backup'
    assert guidance('本月餐饮预算还剩多少') is None
    assert guidance('我有多少退款待处理') is None


def space_isolation(app, window):
    window.navigate('chat')
    started, release = Event(), Event()

    def paused(*_):
        started.set()
        release.wait(5)
        return {'answer': '模拟迟到的演示数据'}

    with patch('qt_finance.chat.query_ledger', paused):
        window.chat.submit('上个月结余是多少？')
        try:
            wait(app, started.is_set, 'query thread')
            window.space.setCurrentIndex(window.space.findData('personal'))
            assert window.workspace.mode == 'personal'
        finally:
            release.set()
        wait(app, lambda: window.jobs.pool.activeThreadCount() == 0, 'late result')
        app.processEvents()
        assert not window.chat.history['personal']
        assert not any(role == 'assistant' and value['answer'] == '模拟迟到的演示数据'
                       for role, value in window.chat.history['demo'])


def import_budget_backup(app, window, directory):
    ledger = window.pages['ledger']
    source = directory / 'synthetic.csv'
    when = date.today().replace(day=1)
    source.write_text(f'交易ID,日期,商户,描述,金额,类型,平台,币种\n'
                      f'QT-01,{when},Qt 导入商户,午餐,36.50,支出,样例,CNY\n', encoding='utf-8-sig')
    window.navigate('ledger')
    path = Path(window.workspace.store.path)
    before = path.read_bytes()
    preview = []
    deadline = monotonic() + 5

    def accept_preview():
        dialog = app.activeModalWidget()
        if not dialog or dialog.windowTitle() != '导入预览':
            if monotonic() < deadline:
                QTimer.singleShot(20, accept_preview)
            return
        preview.append(path.read_bytes() == before and not dialog.findChild(QCheckBox).isChecked())
        confirm = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        QTest.mouseClick(confirm, Qt.MouseButton.LeftButton)

    ledger.begin_import(str(source), 'generic', None)
    QTimer.singleShot(20, accept_preview)
    wait(app, lambda: bool(preview) and ledger.model.rowCount() == 1, 'preview and import')
    assert preview == [True]
    imported = ledger.model.records[0]
    assert imported['merchant'] == 'Qt 导入商户' and imported['amount_cents'] == 3650
    assert window.workspace.store.import_batches()[0]['added'] == 1
    window.navigate('budget')
    budget = window.pages['budget']
    budget.category.setCurrentText('餐饮')
    budget.amount.setText('200.00')
    click(budget, '保存预算')
    assert window.workspace.store.budgets(when.strftime('%Y-%m'))['餐饮'] == 20000
    window.navigate('backup')
    backup = window.pages['backup']
    click(backup, '创建备份')
    backup_path = backup.model.records[0]['path']
    window.navigate('ledger')
    ledger.view.selectRow(0)
    with patch('qt_finance.pages.confirm', return_value=True):
        click(ledger, '删除选中')
    assert window.workspace.store.transaction_source(imported['id'])['status'] == 'deleted'
    window.navigate('backup')
    with patch('qt_finance.pages.confirm', return_value=True):
        backup.restore(backup_path)
    assert window.workspace.store.transaction_source(imported['id'])['status'] != 'deleted'


def reconciliation(window, directory):
    window.navigate('backup')
    fixture = Store(directory / 'cross-source.sqlite3')
    seed_six(fixture)
    with patch('qt_finance.pages.confirm', return_value=True):
        window.pages['backup'].restore(fixture.path)
    window.navigate('accounts')
    accounts = window.pages['accounts']
    assert accounts.model.rowCount() == 2 and accounts.links.records[0]['wallet_label'] == '示例微信'
    window.navigate('overview')
    summary = window.pages['overview']
    assert summary.metrics['net_cents'].text() == '¥120.00'
    window.navigate('review')
    review = window.pages['review']
    click(review, '生成候选')
    identifiers = [row['id'] for row in review.groups.records if row['confidence'] == 'A']
    assert len(identifiers) == 2
    with patch('qt_finance.pages.confirm', return_value=True):
        for identifier in identifiers:
            index = next(i for i, row in enumerate(review.groups.records) if row['id'] == identifier)
            review.groups_view.selectRow(index)
            click(review, '确认')
    window.navigate('overview')
    assert summary.metrics['net_cents'].text() == '¥60.00'
    window.navigate('review')
    assert review.refunds.rowCount() == 1 and review.refunds.records[0]['amount_cents'] == 1000
    review.refunds_view.selectRow(0)

    def choose_purchase():
        dialog = QApplication.activeModalWidget()
        if dialog is None or dialog.windowTitle() != '选择原消费':
            return
        view = dialog.findChild(QTableView)
        index = next(i for i, row in enumerate(view.model().records) if row['merchant'] == '校园书店')
        view.selectRow(index)
        ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        QTest.mouseClick(ok, Qt.MouseButton.LeftButton)

    QTimer.singleShot(30, choose_purchase)
    with patch('qt_finance.pages.confirm', return_value=True):
        review.associate_refund()
    window.navigate('overview')
    assert summary.metrics['net_cents'].text() == '¥50.00'


def exercise(app, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='qt-ledgers-') as temporary:
        root = Path(temporary)
        demo = Store(root / 'demo.sqlite3')
        for offset in (-3, -2, -1):
            when = month_shift(date.today(), offset) + '-08'
            demo.add_manual_transaction(when, 'Qt 合成食堂', 12345, '支出', '餐饮')
            demo.add_manual_transaction(when, 'Qt 合成地铁', 4500, '支出', '交通')
            demo.add_manual_transaction(when, 'Qt 合成工资', 520000, '收入', '工资')
        window = FinanceWindow(root)
        window.resize(1020, 660)
        window.show()
        app.processEvents()
        try:
            assert window.current == 'chat'
            chat_and_plan(app, window, output)
            space_isolation(app, window)
            import_budget_backup(app, window, root)
            reconciliation(window, root)
            for name, navigation in window.nav.items():
                navigation.click()
                app.processEvents()
                assert window.current == name and not window.message.text(), (name, window.message.text())
            window.navigate('chat')
            window.resize(760, 480)
            app.processEvents()
            assert window.chat.input.isVisible() and window.chat.send.isVisible()
            assert window.chat.send.width() == window.chat.send.height() == 38
            assert window.chat.send.mapTo(window, window.chat.send.rect().bottomRight()).y() < window.height()
        finally:
            window.close()
            window.jobs.pool.waitForDone()
    (output / 'results.json').write_text(json.dumps({
        'platform': app.platformName(), 'scale': app.primaryScreen().devicePixelRatio(),
        'pages': list(FinanceWindow.page_labels),
        'checks': ['chat and sources', 'read-only A/B/C', 'late result and space isolation', 'import preview and commit',
                   'budget save', 'soft delete and backup restore', 'cross-source reconciliation and refund 120 to 60 to 50',
                   'minimum-size send button'],
    }, ensure_ascii=False, indent=2), encoding='utf-8')
