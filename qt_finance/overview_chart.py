"""Native Qt charts for signed spending values."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class SpendingChart(QWidget):
    def __init__(self, *, trend=False):
        super().__init__()
        self.trend = trend
        self.points = []
        self.setMinimumHeight(125)

    def show_values(self, values):
        self.points = list(values)
        self.setAccessibleName('月度净支出趋势' if self.trend else '分类净支出结构')
        self.setAccessibleDescription('；'.join(f'{label} {value:,.2f} 元' for label, value in self.points))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#ffffff'))
        painter.setPen(QColor('#435c50'))
        if not self.points:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '所选范围暂无计入净支出的记录')
            return
        if self.trend:
            self.draw_trend(painter)
        else:
            self.draw_categories(painter)

    def draw_trend(self, painter):
        labels, values = zip(*self.points)
        low, high = min(0, min(values)), max(0, max(values))
        if low == high:
            high = 1
        left, top, right, bottom = 74, 18, self.width() - 24, self.height() - 30
        span = bottom - top
        zero = top + high / (high - low) * span
        painter.setPen(QColor('#aec1b5'))
        painter.drawLine(QPointF(left, zero), QPointF(right, zero))
        painter.setPen(QColor('#435c50'))
        painter.drawText(QPointF(10, zero + 4), '¥0')
        if high != 0:
            painter.drawText(QPointF(10, top + 4), f'¥{high:,.0f}')
        if low != 0:
            painter.drawText(QPointF(10, bottom), f'¥{low:,.0f}')
        points = [QPointF(left + (right - left) * (i / (len(values) - 1) if len(values) > 1 else .5),
                          top + (high - value) / (high - low) * span) for i, value in enumerate(values)]
        painter.setPen(QPen(QColor('#13745d'), 2))
        if len(points) > 1:
            painter.drawPolyline(QPolygonF(points))
        for i, point in enumerate(points):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#13745d' if values[i] >= 0 else '#b45c47'))
            painter.drawEllipse(point, 4, 4)
            if i in (0, len(points) - 1) or (self.width() >= 580 and i % 3 == 0 and i < len(points) - 2):
                painter.setPen(QColor('#435c50'))
                painter.drawText(QRectF(point.x() - 43, bottom + 5, 86, 20),
                                 Qt.AlignmentFlag.AlignHCenter, labels[i])

    def draw_categories(self, painter):
        maximum = max(1, max(abs(value) for _, value in self.points))
        left, right = 68, self.width() - 103
        center = (left + right) / 2
        step = (self.height() - 14) / len(self.points)
        painter.setPen(QColor('#aec1b5'))
        painter.drawLine(QPointF(center, 7), QPointF(center, self.height() - 7))
        for i, (category, value) in enumerate(self.points):
            y = 7 + step * (i + .5)
            end = center + (right - left) / 2 * (value / maximum)
            painter.fillRect(QRectF(min(center, end), y - 5, abs(end - center), 10),
                             QColor('#13745d' if value >= 0 else '#b45c47'))
            painter.setPen(QColor('#435c50'))
            painter.drawText(QPointF(10, y + 4), category)
            painter.drawText(QPointF(right + 8, y + 4), f'¥{value:,.2f}')
