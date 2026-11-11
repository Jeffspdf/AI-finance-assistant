"""Qt floating assistant using the existing cropped animation atlas."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QMenu

from finance.animation import TIMELINES, load_frames


class DesktopPet(QLabel):
    def __init__(self, owner, atlas):
        super().__init__(owner, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAccessibleName('小财灵桌面助手')
        self.setToolTip('拖动移动，单击打招呼，右键打开菜单')
        width, height = 246, 164
        self.setFixedSize(width, height)
        self.frames = {
            state: [QPixmap.fromImage(QImage(frame.tobytes(), width, height, QImage.Format.Format_RGBA8888).copy())
                    for frame in frames]
            for state, frames in load_frames(atlas, (width, height)).items()
        }
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self.paused = False
        self.press = None
        self.dragged = False
        self.state = 'idle'
        self.step = 0
        screen = owner.screen().availableGeometry()
        self.move(screen.right() - width - 16, screen.bottom() - height - 16)

    def play(self, state='idle'):
        self.state, self.step = state, 0
        self.advance()

    def advance(self):
        timeline = TIMELINES[self.state]
        if self.step == len(timeline):
            self.state, self.step = 'idle', 0
            timeline = TIMELINES[self.state]
        index, delay = timeline[self.step]
        self.setPixmap(self.frames[self.state][index])
        self.step += 1
        if not self.paused and self.isVisible():
            self.timer.start(delay)

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.timer.stop()
        else:
            self.play()

    def showEvent(self, event):
        super().showEvent(event)
        self.play()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

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
                self.play('wave')
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
