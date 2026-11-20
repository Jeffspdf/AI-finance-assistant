"""Floating form of the Qt companion."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu
from .companion import Companion


class DesktopPet(Companion):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setToolTip('拖动移动，点击重播当前动作，右键暂停或隐藏')
        self.setFixedSize(220, 180)
        self.press = None
        self.dragged = False
        screen = owner.screen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press = event.globalPosition().toPoint(), self.pos()
            self.dragged = False

    def mouseMoveEvent(self, event):
        if self.press and event.buttons() & Qt.MouseButton.LeftButton:
            start, origin = self.press
            offset = event.globalPosition().toPoint() - start
            if self.dragged or offset.manhattanLength() > 5:
                self.dragged = True
                self.move(origin + offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.press and not self.dragged and not self.paused:
                self.restart()
            self.press = None

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction('打开 AI 对话', self.open_chat)
        menu.addAction('恢复动画' if self.paused else '暂停动画', self.toggle_pause)
        menu.addAction('隐藏小财灵', self.hide)
        menu.exec(event.globalPos())

    def open_chat(self):
        self.owner.showNormal()
        self.owner.raise_()
        self.owner.activateWindow()
        self.owner.navigate('chat')
