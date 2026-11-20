"""Piggy-bank mascot: one alpha asset, deterministic coin drop, no sprite seams."""
import math
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class PiggyBank(QWidget):
    def __init__(self, parent=None, flags=Qt.WindowType.Widget):
        super().__init__(parent, flags)
        self.art = QPixmap(str(Path(__file__).resolve().parents[1] / 'assets' / 'piggy_bank.png'))
        self.setAccessibleName('小财灵：金币落入小猪存钱罐')
        self.setMinimumHeight(130)
        self.phase = 0
        self.paused = False
        self.state = 'idle'
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.advance)

    def pixmap(self):
        # Compatibility for the layout smoke; the live animation is painted.
        return self.art.scaledToHeight(self.height(), Qt.TransformationMode.SmoothTransformation)

    def advance(self):
        self.phase = (self.phase + .025) % 1
        self.update()

    def play(self, state='idle'):
        self.state, self.phase = state, 0
        if not self.paused and self.isVisible():
            self.timer.start()
        self.update()

    def toggle_pause(self):
        self.paused = not self.paused
        self.timer.stop() if self.paused else self.play()

    def showEvent(self, event):
        super().showEvent(event)
        self.play()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = min(self.width(), self.height() / .78)
        left = (self.width() - size) / 2
        top = (self.height() - size * .78) / 2
        painter.drawPixmap(QRectF(left, top, size, size * .78), self.art,
                           QRectF(0, self.art.height() * .22, self.art.width(), self.art.height() * .78))
        # Image slot centre is (0.50, 0.47). Clip the coin at its mouth so it
        # disappears into the bank, rather than crossing the pig's face.
        mouth = top + size * .25
        progress = min(self.phase / .75, 1)
        if self.phase < .75:
            painter.save()
            painter.setClipRect(QRectF(left, top, size, mouth - top))
            radius = size * .045
            x = left + size * .50
            y = top - radius + (mouth - top + radius * 2) * progress ** 1.7
            width = radius * (0.6 + .4 * abs(math.cos(progress * math.pi * 2)))
            painter.setPen(QPen(QColor('#bd7e18'), max(1, size * .007)))
            painter.setBrush(QColor('#f5c64d'))
            painter.drawEllipse(QRectF(x - width, y - radius, width * 2, radius * 2))
            painter.setPen(QPen(QColor('#fff0a5'), max(1, size * .006)))
            painter.drawLine(int(x), int(y - radius * .5), int(x), int(y + radius * .5))
            painter.restore()
        painter.end()
