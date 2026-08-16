from __future__ import annotations

import ctypes
import sys
from datetime import datetime, timedelta
from PySide6.QtCore import QPoint, Qt, QDateTime, QTimer, Signal, QParallelAnimationGroup, QPropertyAnimation
from PySide6.QtGui import QAction, QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateTimeEdit, QHBoxLayout,
    QGraphicsOpacityEffect, QLabel, QLineEdit, QMenu, QPushButton, QStyle, QStyleOptionButton,
    QToolButton, QToolTip, QVBoxLayout, QWidget)
from core.models import Task, current_minute


UPCOMING_WINDOW = timedelta(minutes=30)
NORMAL_TEXT = "#143364"
UPCOMING_TEXT = "#b77900"
OVERDUE_TEXT = "#d03a47"


class TickCheckBox(QCheckBox):
    """Native checkbox with an explicit high-contrast tick when selected."""
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton(); self.initStyleOption(option)
        indicator = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#132a56"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(indicator.left() + 3, indicator.center().y(), indicator.left() + 7, indicator.bottom() - 3)
        painter.drawLine(indicator.left() + 7, indicator.bottom() - 3, indicator.right() - 2, indicator.top() + 3)


class ElidedTaskLabel(QLabel):
    """Shows an immediate full-name popup only when its text is elided."""
    def __init__(self, text: str, width: int, parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setFixedWidth(width)
        elided = self.fontMetrics().elidedText(text, Qt.ElideRight, width)
        self.is_elided = elided != text
        self.setText(elided.replace("…", "..."))

    def show_full_name(self):
        if self.is_elided:
            position = self.mapToGlobal(QPoint(0, self.height() + 4))
            QToolTip.showText(position, self.full_text, self)

    def enterEvent(self, event):
        self.show_full_name()
        super().enterEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)


class TaskRow(QWidget):
    complete_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    def __init__(self, task: Task, parent=None):
        super().__init__(parent); self.task = task; self.due_status = "normal"; self.completing = False
        self.setObjectName("taskRow"); self.setFixedWidth(330); self.setMinimumHeight(34); self.setMaximumHeight(34)
        layout = QHBoxLayout(self); layout.setContentsMargins(8, 1, 8, 1); layout.setSpacing(7)
        self.box = TickCheckBox(); self.box.setFixedWidth(18); self.box.toggled.connect(self._complete)
        self.name = ElidedTaskLabel(task.name, 144)
        due = task.due_datetime.strftime("%m-%d %H:%M") if task.due_datetime else "无截止时间"
        self.time = QLabel(due); self.time.setFixedWidth(104)
        self.edit_button = QToolButton(); self.edit_button.setText("✎"); self.edit_button.setToolTip("编辑任务")
        self.edit_button.setAccessibleName("编辑任务"); self.edit_button.setFixedSize(22, 22)
        self.edit_button.setCursor(Qt.PointingHandCursor); self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self.task.id))
        layout.addWidget(self.box); layout.addWidget(self.name); layout.addWidget(self.time); layout.addWidget(self.edit_button)
        self.setStyleSheet("QWidget#taskRow{background:transparent;border-radius:5px;} QWidget#taskRow:hover{background:rgba(159,217,243,48);} QLabel{color:#143364;background:transparent;font-size:12px;} QCheckBox::indicator{width:14px;height:14px;} QCheckBox::indicator:checked{background:#35c9d0;border:1px solid #132a56;} QToolButton{color:#3857a5;background:transparent;border:1px solid transparent;border-radius:4px;font-size:17px;} QToolButton:hover{background:#d9f2f7;border-color:#79c9df;} QToolButton:pressed{background:#bce8ef;}")
        self.update_due_style()

    def update_due_style(self, now: datetime | None = None):
        if self.completing:
            return
        due = self.task.due_datetime
        remaining = due - (now or datetime.now()) if due else None
        if remaining is not None and remaining.total_seconds() < 0:
            self.due_status, color = "overdue", OVERDUE_TEXT
        elif remaining is not None and remaining <= UPCOMING_WINDOW:
            self.due_status, color = "upcoming", UPCOMING_TEXT
        else:
            self.due_status, color = "normal", NORMAL_TEXT
        style = f"color:{color};"
        self.name.setStyleSheet(style); self.time.setStyleSheet(style)

    def _complete(self, checked):
        if checked:
            self.completing = True
            self.name.setStyleSheet("color:#8490a2;text-decoration: line-through;")
            self.time.setStyleSheet("color:#8490a2;")
            self.box.setEnabled(False)
            self.edit_button.setEnabled(False)
            # Remove the fixed minimum before shrinking; otherwise Qt keeps
            # the row at 34px and only the opacity animation is visible.
            self.setMinimumHeight(0)
            opacity = QGraphicsOpacityEffect(self); self.setGraphicsEffect(opacity)
            fade = QPropertyAnimation(opacity, b"opacity", self); fade.setDuration(380); fade.setStartValue(1.0); fade.setEndValue(0.0)
            shrink = QPropertyAnimation(self, b"maximumHeight", self); shrink.setDuration(380); shrink.setStartValue(34); shrink.setEndValue(0)
            self.finish = QParallelAnimationGroup(self); self.finish.addAnimation(fade); self.finish.addAnimation(shrink)
            self.finish.finished.connect(lambda: self.complete_requested.emit(self.task.id)); self.finish.start()

    def contextMenuEvent(self, event):
        menu = QMenu(self); edit = menu.addAction("编辑任务"); remove = menu.addAction("删除任务")
        chosen = menu.exec(event.globalPos())
        if chosen == edit: self.edit_requested.emit(self.task.id)
        if chosen == remove: self.delete_requested.emit(self.task.id)


class TaskList(QWidget):
    activity = Signal()
    edit_requested = Signal(str); delete_requested = Signal(str); complete_requested = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setFixedWidth(342)
        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(6, 6, 6, 6); self.layout.setSpacing(0)
        self.body = QWidget(); self.body.setStyleSheet("background:rgba(247,251,255,228);border:1px solid #7bcce7;border-radius:7px;")
        self.rows = QVBoxLayout(self.body); self.rows.setContentsMargins(0, 5, 0, 5); self.rows.setSpacing(0); self.layout.addWidget(self.body)
        self.due_timer = QTimer(self); self.due_timer.setInterval(30_000); self.due_timer.timeout.connect(self._refresh_due_styles)

    def set_tasks(self, tasks: list[Task]):
        while self.rows.count():
            item = self.rows.takeAt(0); widget = item.widget()
            if widget: widget.deleteLater()
        for task in tasks:
            row = TaskRow(task); row.complete_requested.connect(self.complete_requested); row.edit_requested.connect(self.edit_requested); row.delete_requested.connect(self.delete_requested); self.rows.addWidget(row)
        self.rows.addStretch() if not tasks else None
        self.setFixedHeight(max(20, len(tasks) * 34 + 22))

    def _refresh_due_styles(self):
        now = datetime.now()
        for index in range(self.rows.count()):
            row = self.rows.itemAt(index).widget()
            if isinstance(row, TaskRow):
                row.update_due_style(now)

    def showEvent(self, event):
        self._refresh_due_styles(); self.due_timer.start(); super().showEvent(event)

    def hideEvent(self, event):
        self.due_timer.stop(); super().hideEvent(event)

    def enterEvent(self, event): self.activity.emit(); super().enterEvent(event)
    def leaveEvent(self, event): self.activity.emit(); super().leaveEvent(event)


class TaskEditor(QWidget):
    saved = Signal(str, object, bool) # name, datetime, no_due
    validation_failed = Signal()
    activity = Signal()
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setFixedSize(570, 70); self.editing_id = None
        outer = QHBoxLayout(self); outer.setContentsMargins(8, 8, 8, 8)
        self.content = QWidget(); self.content.setObjectName("editorContent")
        self.content.setStyleSheet("""
            QWidget#editorContent { background:#f7fbff; border:1px solid #7bcce7; border-radius:7px; }
            QLineEdit, QDateTimeEdit, QComboBox { background:white; border:1px solid #b8d8e8; border-radius:4px; padding:3px; color:#143364; }
            QCheckBox { background:transparent; border:none; color:#143364; }
            QPushButton { background:#35c9d0; border:1px solid #1f8fa5; border-radius:4px; padding:4px; color:#132a56; }
            QPushButton:hover { background:#62d9dc; }
        """)
        layout = QHBoxLayout(self.content); layout.setContentsMargins(10, 9, 10, 9); layout.setSpacing(7)
        self.name = QLineEdit(); self.name.setPlaceholderText("输入待办事项"); self.name.setFixedWidth(160)
        self.name.setCursorMoveStyle(Qt.LogicalMoveStyle)
        self.name.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.name.setInputMethodHints(Qt.ImhNone)
        self.name.returnPressed.connect(self._save)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.date = QDateTimeEdit(); self.date.setDisplayFormat("yyyy-MM-dd"); self.date.setCalendarPopup(True); self.date.setFixedWidth(112)
        self.hour = QComboBox(); self.hour.addItems([str(i).zfill(2) for i in range(24)]); self.hour.setFixedWidth(48)
        self.minute = QComboBox(); self.minute.addItems([str(i).zfill(2) for i in range(60)]); self.minute.setFixedWidth(48)
        self.no_due = TickCheckBox("无截止时间"); self.no_due.toggled.connect(self._toggle_due)
        self.ok = QPushButton("确定"); self.ok.clicked.connect(self._save); self.ok.setFixedWidth(50)
        for w in (self.name, self.date, self.hour, self.minute, self.no_due, self.ok): layout.addWidget(w)
        outer.addWidget(self.content)

    def open_task(self, task: Task | None = None):
        self.editing_id = task.id if task else None; self.name.setText(task.name if task else "")
        due = task.due_datetime if task else current_minute()
        self.no_due.setChecked(task is not None and task.due_at is None)
        self.date.setDateTime(QDateTime(due)); self.hour.setCurrentText(f"{due.hour:02d}"); self.minute.setCurrentText(f"{due.minute:02d}")
        self.show(); self.raise_()
        QTimer.singleShot(0, self._focus_name)

    def _focus_name(self):
        self.activateWindow()
        if sys.platform == "win32":
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            # Re-associate the default IME context after activation from a no-focus pet window.
            ctypes.windll.imm32.ImmAssociateContextEx(int(self.winId()), 0, 0x0010)
        self.name.setFocus(Qt.MouseFocusReason)
        QGuiApplication.inputMethod().update(Qt.ImQueryAll)

    def _toggle_due(self, checked):
        for w in (self.date, self.hour, self.minute): w.setDisabled(checked)

    def _save(self):
        name = self.name.text().strip()
        if not name:
            self.name.setPlaceholderText("任务名称不能为空")
            self.name.setFocus()
            self.validation_failed.emit()
            return
        dt = self.date.dateTime().toPython().replace(hour=int(self.hour.currentText()), minute=int(self.minute.currentText()), second=0, microsecond=0)
        self.saved.emit(name, dt, self.no_due.isChecked())

    def enterEvent(self, event): self.activity.emit(); super().enterEvent(event)
    def leaveEvent(self, event): self.activity.emit(); super().leaveEvent(event)
