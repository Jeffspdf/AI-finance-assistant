"""Independent Qt application shell and workspace lifecycle."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QMainWindow, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from finance.llm import ModelClient
from finance.model_settings import ModelSettings
from .chat import ChatPage
from .pages import AccountsPage, BackupPage, BudgetPage, HelpPage, LedgerPage, OverviewPage, ReviewPage, SettingsPage
from .plan import PlanPage
from .services import Workspace
from .ui import Jobs, STYLES, button, label


ASSETS = Path(__file__).resolve().parents[1] / 'assets'


class FinanceWindow(QMainWindow):
    page_labels = {
        'chat': 'AI 对话', 'overview': '财务概览', 'ledger': '交易账本',
        'budget': '预算与提醒', 'plan': '储蓄规划', 'accounts': '资金账户',
        'review': '退款与对账', 'backup': '备份与恢复', 'settings': '设置与连接', 'help': '使用帮助',
    }

    def __init__(self, directory):
        super().__init__()
        self.setWindowTitle('AI理财小助手 · Qt 预览')
        self.setMinimumSize(760, 480)
        screen = self.screen().availableGeometry()
        self.resize(min(1180, screen.width() - 24), min(780, screen.height() - 48))
        self.setStyleSheet(STYLES)
        self.workspace = Workspace(directory)
        self.jobs = Jobs(self)
        self.model_settings = ModelSettings(directory)
        self.model_configuration = self.model_settings.load()
        self.client = self.make_client(self.model_configuration)

        shell = QWidget()
        self.setCentralWidget(shell)
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar())

        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(26, 13, 24, 10)
        self.title = label('', 'title')
        header.addWidget(self.title, 1)
        self.space = QComboBox()
        self.space.addItem('演示空间', 'demo')
        self.space.addItem('我的账本', 'personal')
        header.addWidget(self.space)
        layout.addLayout(header)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet('color:#dce7e0')
        layout.addWidget(separator)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.message = label('', 'muted')
        self.message.setContentsMargins(26, 4, 20, 8)
        self.message.hide()
        layout.addWidget(self.message)
        root.addWidget(main, 1)

        self.chat = ChatPage(self)
        self.plan = PlanPage(self)
        self.pages = {
            'chat': self.chat, 'overview': OverviewPage(self), 'ledger': LedgerPage(self),
            'budget': BudgetPage(self), 'plan': self.plan, 'accounts': AccountsPage(self),
            'review': ReviewPage(self), 'backup': BackupPage(self), 'settings': SettingsPage(self),
            'help': HelpPage(self, ASSETS),
        }
        self.containers = {}
        for key, page in self.pages.items():
            if key in ('plan', 'settings'):
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setContentsMargins(16, 8, 16, 10)
                scroll.setWidget(page)
                self.containers[key] = scroll
            else:
                self.containers[key] = page
            self.stack.addWidget(self.containers[key])
        self.space.currentIndexChanged.connect(self.change_space)
        self.help_shortcut = QShortcut(QKeySequence('F1'), self)
        self.help_shortcut.activated.connect(lambda: self.navigate('help'))
        self.current = 'chat'
        self.plan.reset_workspace()
        self.navigate('chat')

    def sidebar(self):
        side = QFrame()
        side.setObjectName('sidebar')
        side.setFixedWidth(216)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(13, 18, 13, 13)
        layout.setSpacing(5)
        layout.addWidget(label('AI理财小助手', 'title'))
        layout.addWidget(label('PySide6 预览版', 'muted'))
        layout.addSpacing(10)
        layout.addWidget(button('＋  新对话', self.new_chat, primary=True))
        layout.addSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName('navContent')
        nav = QVBoxLayout(content)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav = {}
        for key, title in self.page_labels.items():
            if key in ('overview', 'settings'):
                nav.addSpacing(7)
                nav.addWidget(label('账本工具' if key == 'overview' else '应用', 'muted'))
            item = button(title, lambda checked=False, target=key: self.navigate(target))
            item.setProperty('nav', True)
            item.setCheckable(True)
            self.nav_group.addButton(item)
            self.nav[key] = item
            nav.addWidget(item)
        nav.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        mascot = label('')
        portrait = QPixmap(str(ASSETS / 'campus_heroine.png'))
        if not portrait.isNull():
            mascot.setPixmap(portrait.scaled(90, 94, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
            mascot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(mascot)
        return side

    @staticmethod
    def make_client(config):
        return ModelClient(config.base_url, config.model, config.api_key, enabled=True) if config.enabled else None

    def configure_model(self, config):
        self.jobs.cancel('model-test')
        self.model_configuration = config
        self.client = self.make_client(config)
        self.chat.stop(quiet=True)
        self.chat.use_model.setChecked(False)
        self.chat.sync_controls()
        self.plan.reset_workspace()

    def notice(self, message=''):
        self.message.setText(str(message))
        self.message.setVisible(bool(message))

    def perform(self, action):
        try:
            return action()
        except (OSError, ValueError) as exc:
            self.notice(str(exc))
        except Exception:
            self.notice('操作未完成，请刷新账本后重试。')

    def navigate(self, page, target=''):
        if page != self.current:
            self.chat.evidence.close()
            if self.current == 'ledger':
                self.jobs.cancel('import')
        self.current = page
        self.nav[page].setChecked(True)
        self.title.setText(self.page_labels[page])
        self.stack.setCurrentWidget(self.containers[page])
        self.notice()
        if target and page == 'plan':
            self.plan.inputs['target_cents'].setText(target)
        self.refresh()

    def refresh(self):
        page = self.pages[self.current]
        if hasattr(page, 'refresh'):
            self.perform(page.refresh)

    def ledger_changed(self, message):
        self.chat.stop(quiet=True)
        self.plan.invalidate()
        self.refresh()
        self.notice(message)

    def change_space(self, *_):
        mode = self.space.currentData()
        if mode == self.workspace.mode:
            return
        try:
            self.workspace.open(mode)
        except Exception:
            self.space.setCurrentIndex(self.space.findData(self.workspace.mode))
            self.notice('无法打开所选账本。请检查数据目录或备份。')
            return
        self.jobs.cancel('import')
        self.chat.reload()
        self.plan.reset_workspace()
        self.pages['ledger'].search.clear()
        self.pages['budget'].amount.clear()
        self.notice()
        self.refresh()

    def new_chat(self):
        self.navigate('chat')
        self.chat.new_chat()

    def closeEvent(self, event):
        self.plan.reset_workspace()
        for channel in list(self.jobs.pending):
            self.jobs.cancel(channel)
        super().closeEvent(event)
