"""Small animated companion shared by the sidebar and floating widget."""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'companion'
STATES = {
    'idle': (('idle-0', 4400), ('idle-1', 140)),
    'thinking': (('0', 360), ('1', 430), ('3', 320), ('4', 500), ('5', 890)),
    'queried': (('8', 400), ('idle-0', 1400)),
    'writing': tuple(zip(('5', '6', '7', '6', '7', '8'), (420, 290, 260, 270, 350, 1160))),
}
CAPTIONS = {'idle': '小财灵', 'thinking': '思考中', 'writing': '记账中', 'queried': '已查询'}


class Companion(QWidget):
    def __init__(self, parent=None, flags=Qt.WindowType.Widget):
        super().__init__(parent, flags)
        self.state = 'idle'
        self.caption = CAPTIONS[self.state]
        self.frame_index = 0
        self.paused = False
        self.images = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self.setAccessibleName(CAPTIONS[self.state])

    def pixmap(self):
        name, _ = STATES[self.state][self.frame_index]
        if name not in self.images:
            self.images[name] = QPixmap(str(ASSETS / (name + '.png')))
        return self.images[name]

    def schedule(self):
        if self.isVisible() and not self.paused:
            self.timer.start(STATES[self.state][self.frame_index][1])

    def advance(self):
        self.frame_index = (self.frame_index + 1) % len(STATES[self.state])
        self.update()
        self.schedule()

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.set_caption(CAPTIONS[state])
            self.restart()

    def set_caption(self, caption):
        self.caption = caption
        self.setAccessibleName(caption)
        self.update()

    def restart(self):
        self.timer.stop()
        self.frame_index = 0
        self.update()
        self.schedule()

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.timer.stop()
        else:
            self.schedule()

    def showEvent(self, event):
        super().showEvent(event)
        self.restart()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        content = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#e3efec'))
        painter.drawRoundedRect(content, 12, 12)
        # Reserve room to the right of the artwork so the status never covers the eyes.
        size = min(content.height() - 18, content.width() * .68)
        art = self.pixmap()
        portrait = QRectF(content.left() + 4, content.bottom() - size - 4, size, size)
        painter.drawPixmap(portrait, art, QRectF(art.rect()))
        if self.state != 'idle':
            bubble = QRectF(content.right() - 92, content.top() + 9, 86, 29)
            painter.setBrush(QColor('#fbfffd'))
            painter.setPen(QPen(QColor('#468071'), 1.4))
            painter.drawRoundedRect(bubble, 11, 11)
            font = QFont(self.font())
            font.setBold(True)
            font.setPixelSize(14)
            painter.setFont(font)
            painter.setPen(QColor('#17483d'))
            painter.drawText(bubble.adjusted(8, 0, -27, 0), Qt.AlignmentFlag.AlignVCenter, self.caption)
            painter.setPen(Qt.PenStyle.NoPen)
            if self.caption == CAPTIONS['queried']:
                painter.setFont(QFont('Segoe UI Symbol', 10))
                painter.setPen(QColor('#246254'))
                painter.drawText(bubble.adjusted(60, 0, -7, 0), Qt.AlignmentFlag.AlignVCenter, '✓')
            for index in range(3 if self.caption != CAPTIONS['queried'] else 0):
                active = index == self.frame_index % 3
                painter.setBrush(QColor('#246254' if active else '#8aafa2'))
                painter.drawEllipse(QRectF(bubble.right() - 27 + 7 * index,
                                           bubble.center().y() - (4 if active else 2), 4, 4))
        painter.end()
