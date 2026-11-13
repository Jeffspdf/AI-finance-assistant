"""Shared Qt widgets and asynchronous delivery to the main thread."""

import json
from math import isfinite
from numbers import Real
from pathlib import Path
from string import Template
from threading import Event

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRectF, QRunnable, QSize, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QStyledItemDelegate, QTableView, QVBoxLayout,
)


COLORS = dict(bg='#f6f7f4', surface='#ffffff', muted_surface='#eef2ef', selected='#e4ece7',
              ink='#18211d', muted='#5f6c65', border='#dde4df', rule='#ecefec', primary='#174e3f',
              hover='#103d32', accent='#2c7a61', warning='#7a4d0a', negative='#b4232a')
CHEVRON = (Path(__file__).resolve().parents[1] / 'assets' / 'qt-chevron-down.png').as_posix()

STYLES = Template("""
QWidget { color: $ink; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: $bg; }
QFrame#sidebar { background: $surface; border-right: 1px solid $border; }
QFrame#card, QFrame#composer { background: $surface; border: 1px solid $border; border-radius: 14px; }
QFrame#userBubble { background: $muted_surface; border-radius: 15px; }
QFrame#assistantBubble { background: $surface; border: 1px solid $border; border-radius: 15px; }
QFrame#headerRule { background: $border; border: none; }
QLabel#title { font-size: 24px; font-weight: 700; }
QLabel#hero { font-size: 30px; font-weight: 700; }
QLabel#muted, QLabel#parser, QLabel#brandSubtitle { color: $muted; }
QLabel#companionPortrait { background: $muted_surface; border-radius: 16px; }
QLabel#amount { color: $primary; font-size: 27px; font-weight: 700; }
QLabel#snapshotAmount { color: $primary; font-size: 19px; font-weight: 700; }
QLabel#warning { color: $warning; }
QLabel#error { color: $negative; }
QTabWidget::pane { background: $surface; border: 1px solid $border; border-radius: 9px; }
QTabBar::tab { background: $muted_surface; color: $muted; border: 1px solid $border;
               border-top-left-radius: 7px; border-top-right-radius: 7px; padding: 6px 10px; }
QTabBar::tab:selected { background: $surface; color: $primary; font-weight: 600; border-bottom-color: $surface; }
QTabBar::tab:hover { color: $primary; }
QPushButton { background: $muted_surface; border: 1px solid $border; padding: 7px 13px; border-radius: 9px; }
QPushButton:hover { background: $selected; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid $accent; }
QPushButton[primary="true"] { background: $primary; color: white; border-color: $primary; }
QPushButton[primary="true"]:hover { background: $hover; }
QPushButton:disabled { background: #edf1ed; color: #7a8b82; border-color: #e4ebe6; }
QPushButton[primary="true"]:disabled { background: #e3ebe5; color: #6c8275; border-color: #e3ebe5; }
QPushButton#send { border-radius: 19px; padding: 0; font-size: 22px; }
QPushButton[nav="true"] { background: transparent; border: 1px solid transparent; text-align: left; padding: 5px 12px; }
QPushButton[nav="true"]:checked, QPushButton[nav="true"]:hover { background: $selected; color: $primary; }
QPushButton[nav="true"]:focus { border-color: $accent; }
QPushButton[suggestion="true"] { background: $surface; text-align: left; padding: 11px 14px; }
QPushButton[suggestion="true"]:hover { background: $muted_surface; }
QLineEdit, QPlainTextEdit { background: $surface; border: 1px solid $border; border-radius: 8px; padding: 6px; }
QLineEdit[invalid="true"], QLineEdit[invalid="true"]:focus { border: 1px solid $negative; }
QPushButton[monthSelected="true"] { background: $primary; color: white; border-color: $primary; }
QLineEdit#demoMonths, QLineEdit#demoCount { min-height: 22px; }
QPushButton#sessionHistory { text-align: left; }
QListWidget#sessionList { background: $surface; border: 1px solid $border; border-radius: 8px; padding: 4px; }
QListWidget#sessionList::item { min-height: 42px; padding: 6px 10px; }
QListWidget#sessionList::item:selected { background: $selected; color: $primary; }
QComboBox { background: $surface; border: 1px solid $border; border-radius: 10px;
            min-height: 19px; padding: 6px 30px 6px 10px; }
QComboBox:hover { border-color: $accent; }
QComboBox:on { border-color: $accent; }
QComboBox:disabled { color: $muted; background: $muted_surface; border-color: $border; }
QComboBox:editable QLineEdit { background: transparent; border: none; padding: 0; }
QComboBox::drop-down { subcontrol-origin: border; subcontrol-position: top right; width: 29px; border: none; }
QComboBox::down-arrow { image: url("$chevron"); width: 13px; height: 13px; }
QComboBox QAbstractItemView { background: $surface; border: 1px solid $border; border-radius: 9px;
                            padding: 4px; outline: 0; selection-background-color: $selected; selection-color: $primary; }
QComboBox QAbstractItemView::item { min-height: 25px; padding: 4px 8px; border-radius: 5px; }
QComboBox QAbstractItemView::item:hover { background: $muted_surface; }
QComboBox QAbstractItemView::item:selected { background: $selected; color: $primary; }
QPlainTextEdit#prompt { background: transparent; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #bfcec4; min-height: 28px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QWidget#navContent { background: $surface; }
QWidget#messageContent, QWidget#workArea { background: $bg; }
QTableView { background: $surface; border: 1px solid $border; border-radius: 9px;
             selection-background-color: $selected; selection-color: $ink; }
QTableView::item { border-bottom: 1px solid $rule; padding: 3px 6px; }
QHeaderView::section { background: $muted_surface; padding: 8px; border: none; }
""").substitute(COLORS, chevron=CHEVRON)


class ComboBox(QComboBox):
    def __init__(self):
        super().__init__()
        view = self.view()
        self.setItemDelegate(QStyledItemDelegate(view))
        popup = view.window()
        if isinstance(popup, QFrame):
            popup.setFrameShape(QFrame.Shape.NoFrame)


class Switch(QCheckBox):
    """Keep Qt's keyboard and checked-state behavior with a compact switch face."""

    def __init__(self, text):
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 64, 34)

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() or self.hasFocus():
            painter.setPen(QPen(QColor(COLORS['accent']), 1) if self.hasFocus() else Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS['muted_surface']))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 9, 9)
        track = QRectF(self.width() - 43, (self.height() - 20) / 2, 36, 20)
        active = self.isChecked()
        painter.setPen(Qt.PenStyle.NoPen if active else QPen(QColor('#819386'), 1))
        painter.setBrush(QColor(COLORS['accent'] if active else '#cbd5ce'))
        painter.drawRoundedRect(track, 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS['surface']))
        painter.drawEllipse(QRectF(track.right() - 17 if active else track.left() + 3,
                                   track.top() + 3, 14, 14))
        painter.setPen(QColor(COLORS['ink' if self.isEnabled() else 'muted']))
        text_area = self.rect().adjusted(8, 0, -51, 0)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, text_area.width())
        painter.drawText(text_area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


def label(text, role=None):
    widget = QLabel(str(text))
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if role:
        widget.setObjectName(role)
    return widget


def button(text, callback, *, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setProperty('primary', primary)
    widget.clicked.connect(callback)
    return widget


def card():
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 12)
    return frame, layout


def dialog_buttons(standard=QDialogButtonBox.StandardButton.Ok, *, action='确定'):
    cancel = QDialogButtonBox.StandardButton.Cancel
    controls = QDialogButtonBox(standard | cancel)
    controls.button(standard).setText(action)
    controls.button(cancel).setText('取消')
    return controls


def confirm(parent, title, message, *, accept='确认', decline='取消'):
    dialog = QMessageBox(QMessageBox.Icon.Question, title, message,
                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.button(QMessageBox.StandardButton.Yes).setText(accept)
    dialog.button(QMessageBox.StandardButton.No).setText(decline)
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
    dialog.setEscapeButton(QMessageBox.StandardButton.No)
    return dialog.exec() == QMessageBox.StandardButton.Yes


class Rows(QAbstractTableModel):
    def __init__(self, columns, records=(), parent=None):
        super().__init__(parent)
        self.columns, self.records = columns, list(records)

    def replace(self, records):
        self.beginResetModel()
        self.records = list(records)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.TextAlignmentRole, Qt.ItemDataRole.ToolTipRole):
            return None
        heading, get = self.columns[index.column()]
        row = self.records[index.row()]
        value = get(row) if callable(get) else row.get(get)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if isinstance(value, Real) or '金额' in heading or heading.endswith('/ 元'):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return None
        if isinstance(value, Real) and not isfinite(value):
            return '—'
        if isinstance(value, Real) and heading in ('占比', '使用率'):
            return f'{value:.2f}%'
        if isinstance(value, Real) and heading in ('收入', '支出', '结余', '预算', '已支出', '剩余', '超支'):
            return f'{value:,.2f}'
        return '—' if value is None else str(value)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][0]
        return super().headerData(section, orientation, role)


def table(columns, records=(), *, multiple=False):
    view = QTableView()
    model = Rows(columns, records, view)
    view.setModel(model)
    view.setAlternatingRowColors(False)
    view.setShowGrid(False)
    view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection if multiple else QTableView.SelectionMode.SingleSelection)
    view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
    view.verticalHeader().hide()
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    view.horizontalHeader().setDefaultSectionSize(124)
    return view, model


def selected_rows(view, model):
    return [model.records[index.row()] for index in view.selectionModel().selectedRows()]


def selected(view, model):
    rows = selected_rows(view, model)
    if len(rows) != 1:
        raise ValueError('此操作需要选中一行。')
    return rows[0]


def readonly_text(value):
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str))
    return box


def details(parent, title, content):
    window = QDialog(parent)
    window.setWindowTitle(title)
    window.resize(800, 550)
    layout = QVBoxLayout(window)
    layout.addWidget(readonly_text(content))
    controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    controls.rejected.connect(window.reject)
    layout.addWidget(controls)
    window.exec()


class Worker(QRunnable):
    def __init__(self, token, function, cancelled, signal):
        super().__init__()
        self.token, self.function, self.cancelled, self.signal = token, function, cancelled, signal

    def run(self):
        if self.cancelled.is_set():
            return
        try:
            result = self.function(self.cancelled)
        except ValueError as exc:
            result, error = None, str(exc)
        except Exception:
            result, error = None, '操作未完成，请检查文件或账本后再试。'
        else:
            error = ''
        self.signal.emit(self.token, result, error)


class Jobs(QObject):
    finished = Signal(int, object, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.pending = {}
        self.serial = 0
        self.finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def start(self, channel, function, callback):
        self.cancel(channel)
        self.serial += 1
        event = Event()
        self.pending[channel] = (self.serial, event, callback)
        self.pool.start(Worker(self.serial, function, event, self.finished))
        return event

    def cancel(self, channel):
        job = self.pending.pop(channel, None)
        if job:
            job[1].set()

    @Slot(int, object, str)
    def _deliver(self, token, result, error):
        for channel, (active, _, callback) in list(self.pending.items()):
            if active == token:
                del self.pending[channel]
                callback(result, error)
                return
