"""Shared Qt widgets and asynchronous delivery to the main thread."""

import json
from threading import Event

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QTableView, QVBoxLayout,
)


STYLES = """
QWidget { color: #21362f; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #f6f9f7; }
QFrame#sidebar { background: #eef5f0; border-right: 1px solid #dfe9e2; }
QFrame#card, QFrame#composer { background: #ffffff; border: 1px solid #dce7e0; border-radius: 15px; }
QFrame#userBubble { background: #dceee3; border-radius: 16px; }
QFrame#assistantBubble { background: white; border: 1px solid #dce7e0; border-radius: 16px; }
QLabel#title { font-size: 24px; font-weight: 700; }
QLabel#hero { font-size: 30px; font-weight: 700; }
QLabel#muted { color: #536b60; }
QLabel#amount { color: #106858; font-size: 24px; font-weight: 700; }
QPushButton { background: #edf5ef; border: 1px solid #dce8df; padding: 7px 13px; border-radius: 9px; }
QPushButton:hover { background: #e1efe5; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #428677; }
QPushButton[primary="true"] { background: #126c5c; color: white; border-color: #126c5c; }
QPushButton[primary="true"]:hover { background: #0b594c; }
QPushButton:disabled { background: #edf1ed; color: #7a8b82; border-color: #e4ebe6; }
QPushButton[primary="true"]:disabled { background: #e3ebe5; color: #6c8275; border-color: #e3ebe5; }
QPushButton#send { border-radius: 19px; padding: 0; font-size: 22px; }
QPushButton[nav="true"] { background: transparent; border: 1px solid transparent; text-align: left; padding: 5px 12px; }
QPushButton[nav="true"]:checked, QPushButton[nav="true"]:hover { background: #dcece1; color: #125a49; }
QPushButton[nav="true"]:focus { border-color: #428677; }
QPushButton[suggestion="true"] { background: white; text-align: left; padding: 14px; }
QPushButton[suggestion="true"]:hover { background: #eaf4ed; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background: white; border: 1px solid #dce7e0; border-radius: 8px; padding: 6px; }
QPlainTextEdit#prompt { background: transparent; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #bfcec4; min-height: 28px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QWidget#navContent { background: #eef5f0; }
QWidget#messageContent { background: #f6f9f7; }
QWidget#workArea { background: #f6f9f7; }
QTableView { background: white; alternate-background-color: #f4f8f5; border: 1px solid #dce7e0; border-radius: 9px;
             selection-background-color: #dceee3; selection-color: #21362f; gridline-color: #edf2ee; }
QHeaderView::section { background: #edf5ef; padding: 8px; border: none; }
"""


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


def confirm(parent, title, message):
    dialog = QMessageBox(QMessageBox.Icon.Question, title, message,
                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
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
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        _, get = self.columns[index.column()]
        row = self.records[index.row()]
        value = get(row) if callable(get) else row.get(get)
        return '—' if value is None else str(value)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][0]
        return super().headerData(section, orientation, role)


def table(columns, records=(), *, multiple=False):
    view = QTableView()
    model = Rows(columns, records, view)
    view.setModel(model)
    view.setAlternatingRowColors(True)
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
