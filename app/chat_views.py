from __future__ import annotations

import ctypes
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QKeyEvent, QPainter, QPainterPath, QPen, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from core.chat import ChatApiError, ChatConfig, OpenAICompatibleClient, WebSearchClient, add_search_failure_context, add_web_search_context, is_current_time_query, needs_web_search
from core.positioning import Rect, fit_overlay_position


PANEL_STYLE = """
QWidget#panel { background:#f7fbff; border:1px solid #7bcce7; border-radius:7px; }
QLabel { color:#143364; background:transparent; }
QLineEdit { background:white; border:1px solid #b8d8e8; border-radius:4px; padding:5px; color:#143364; }
QPushButton { background:#e7f7fb; border:1px solid #79c9df; border-radius:5px; padding:6px 12px; color:#132a56; }
QPushButton:hover { background:#cceef5; }
QPushButton#primary { background:#35c9d0; border-color:#1f8fa5; font-weight:600; }
QPushButton#primary:hover { background:#62d9dc; }
QPushButton#primary:pressed { background:#25b4bd; }
QPushButton#primary:disabled { background:#d8e6eb; border-color:#bdcdd5; color:#738390; }
QPushButton#closeChat { background:rgba(231,247,251,190); border:1px solid #79c9df; border-radius:5px; padding:0; color:#3857a5; font-size:18px; font-weight:300; }
QPushButton#closeChat:hover { background:#d9f2f7; color:#b42318; }
"""


class ActionChooser(QWidget):
    chat_selected = Signal()
    task_selected = Signal()
    activity = Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(236, 62)
        outer = QHBoxLayout(self); outer.setContentsMargins(7, 7, 7, 7)
        panel = QWidget(); panel.setObjectName("panel"); panel.setStyleSheet(PANEL_STYLE)
        layout = QHBoxLayout(panel); layout.setContentsMargins(9, 7, 9, 7); layout.setSpacing(8)
        chat = QPushButton("聊天"); chat.setObjectName("primary"); chat.clicked.connect(self.chat_selected)
        task = QPushButton("布置任务"); task.clicked.connect(self.task_selected)
        layout.addWidget(chat); layout.addWidget(task); outer.addWidget(panel)

    def enterEvent(self, event):
        self.activity.emit(); super().enterEvent(event)

    def leaveEvent(self, event):
        self.activity.emit(); super().leaveEvent(event)


class ApiConfigDialog(QDialog):
    def __init__(self, config: ChatConfig, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True); self.setFixedSize(440, 312)
        outer = QVBoxLayout(self); outer.setContentsMargins(8, 8, 8, 8)
        panel = QWidget(); panel.setObjectName("panel"); panel.setStyleSheet(PANEL_STYLE)
        layout = QVBoxLayout(panel); layout.setContentsMargins(16, 13, 16, 13); layout.setSpacing(9)
        title = QLabel("配置聊天 API"); title.setStyleSheet("font-size:15px;font-weight:600;color:#132a56;")
        form = QFormLayout(); form.setHorizontalSpacing(10); form.setVerticalSpacing(8)
        self.base_url = QLineEdit(config.base_url); self.base_url.setPlaceholderText("https://api.openai.com/v1")
        self.model = QLineEdit(config.model); self.model.setPlaceholderText("模型名称")
        self.api_key = QLineEdit(config.api_key); self.api_key.setEchoMode(QLineEdit.Password); self.api_key.setPlaceholderText("API Key")
        form.addRow("接口地址", self.base_url); form.addRow("模型", self.model); form.addRow("API Key", self.api_key)
        self.search_api_key = QLineEdit(config.search_api_key); self.search_api_key.setEchoMode(QLineEdit.Password)
        self.search_api_key.setPlaceholderText("Tavily Key（可选，免费额度）")
        form.addRow("Tavily 搜索 Key（可选）", self.search_api_key)
        self.web_search = QCheckBox("允许按需联网搜索网页资料"); self.web_search.setChecked(config.web_search)
        hint = QLabel("没有 Tavily Key 时自动使用免费公共搜索源")
        hint.setStyleSheet("color:#5d7690;font-size:11px;")
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject)
        save = QPushButton("保存"); save.setObjectName("primary"); save.clicked.connect(self._accept_if_valid)
        buttons.addWidget(cancel); buttons.addWidget(save)
        self.error = QLabel(""); self.error.setStyleSheet("color:#b42318;")
        layout.addWidget(title); layout.addLayout(form); layout.addWidget(self.web_search); layout.addWidget(hint); layout.addWidget(self.error); layout.addLayout(buttons)
        outer.addWidget(panel)

    def _accept_if_valid(self):
        base = self.base_url.text().strip()
        if not (base.startswith("https://") or base.startswith("http://")):
            self.error.setText("接口地址必须以 http:// 或 https:// 开头")
            return
        if not self.model.text().strip() or not self.api_key.text().strip():
            self.error.setText("模型和 API Key 不能为空")
            return
        self.accept()

    def value(self) -> ChatConfig:
        return ChatConfig(self.base_url.text().strip(), self.model.text().strip(), self.api_key.text().strip(), self.web_search.isChecked(), self.search_api_key.text().strip())


class ChatInputPanel(QWidget):
    send_requested = Signal(str)
    close_requested = Signal()
    activity = Signal()
    deactivated = Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setFixedSize(420, 78)
        outer = QHBoxLayout(self); outer.setContentsMargins(7, 7, 7, 7)
        panel = QWidget(); panel.setObjectName("panel"); panel.setStyleSheet(PANEL_STYLE)
        layout = QHBoxLayout(panel); layout.setContentsMargins(9, 6, 9, 6); layout.setSpacing(7)
        self.input = ChatTextInput(); self.input.setPlaceholderText("和她聊点什么...  Enter 发送 · Shift+Enter 换行 · Esc 退出")
        self.input.setAttribute(Qt.WA_InputMethodEnabled, True); self.input.setInputMethodHints(Qt.ImhNone)
        self.input.setFixedHeight(54)
        self.input.setStyleSheet("QTextEdit{background:white;border:1px solid #b8d8e8;border-radius:4px;padding:5px;color:#143364;} QScrollBar:vertical{width:6px;background:transparent;} QScrollBar::handle:vertical{background:#9dd7e6;border-radius:3px;min-height:18px;} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self.send = QPushButton("×"); self.send.setObjectName("closeChat"); self.send.setToolTip("退出聊天")
        self.send.setAccessibleName("退出聊天"); self.send.setFixedSize(54, 54); self.send.clicked.connect(self.close_requested)
        self.input.submit_requested.connect(self._send); self.input.escape_requested.connect(self.close_requested)
        layout.addWidget(self.input, 1); layout.addWidget(self.send); outer.addWidget(panel)

    def open_panel(self):
        self.show(); self.raise_(); QTimer.singleShot(0, self._focus_input)

    def _focus_input(self):
        self.activateWindow()
        if sys.platform == "win32":
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            ctypes.windll.imm32.ImmAssociateContextEx(int(self.winId()), 0, 0x0010)
        self.input.setFocus(Qt.MouseFocusReason)
        QGuiApplication.inputMethod().update(Qt.ImQueryAll)

    def _send(self):
        value = self.input.toPlainText().strip()
        if value and self.input.isEnabled():
            self.input.clear(); self.send_requested.emit(value)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_requested.emit(); event.accept(); return
        super().keyPressEvent(event)

    def set_busy(self, busy: bool):
        self.input.setDisabled(busy)
        self.send.setDisabled(False)
        if not busy and self.isVisible():
            QTimer.singleShot(0, self._focus_input)

    def enterEvent(self, event):
        self.activity.emit(); super().enterEvent(event)

    def leaveEvent(self, event):
        self.activity.emit(); super().leaveEvent(event)


class ChatTextInput(QTextEdit):
    submit_requested = Signal()
    escape_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            self.escape_requested.emit(); event.accept(); return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.submit_requested.emit(); event.accept(); return
        super().keyPressEvent(event)


class SpeechBubble(QWidget):
    """Bottom-anchored bubble that reveals streamed text one character at a time."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.anchor = QPoint(); self.pending = ""; self.full_text = ""
        self.text = QTextEdit(self); self.text.setReadOnly(True); self.text.setFrameStyle(0)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text.setStyleSheet("QTextEdit{background:transparent;color:#132a56;border:none;font-size:13px;} QScrollBar:vertical{width:7px;background:transparent;margin:5px 1px 5px 0;} QScrollBar::handle:vertical{background:#7bcce7;border-radius:3px;min-height:22px;} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}")
        self.reveal_timer = QTimer(self); self.reveal_timer.setInterval(38); self.reveal_timer.timeout.connect(self._reveal_one)
        self.setFixedSize(148, 70)

    def begin(self, anchor: QPoint):
        self.anchor = QPoint(anchor); self.pending = ""; self.full_text = ""; self.text.clear()
        self._resize_and_anchor(); self.show(); self.raise_()

    def enqueue(self, chunk: str):
        self.pending += chunk
        if not self.reveal_timer.isActive():
            self.reveal_timer.start()

    def show_error(self, message: str):
        self.pending = ""; self.full_text = message; self.text.setPlainText(message)
        self._resize_and_anchor(); self.show()

    def finalize(self):
        """Keep the completed reply in the scrollable text area."""
        self.text.ensureCursorVisible()

    def _reveal_one(self):
        if not self.pending:
            self.reveal_timer.stop()
            return
        self.full_text += self.pending[0]; self.pending = self.pending[1:]
        self.text.setPlainText(self.full_text)
        cursor = self.text.textCursor(); cursor.movePosition(QTextCursor.End); self.text.setTextCursor(cursor)
        self._resize_and_anchor(); self.text.ensureCursorVisible()

    def set_anchor(self, anchor: QPoint):
        self.anchor = QPoint(anchor)
        if self.isVisible(): self._resize_and_anchor()

    def _resize_and_anchor(self):
        metrics = QFontMetrics(self.text.font())
        single_width = metrics.horizontalAdvance(self.full_text.replace("\n", " ")) + 42
        width = max(148, min(336, single_width))
        self.text.document().setTextWidth(width - 30)
        text_bounds = metrics.boundingRect(0, 0, width - 30, 10_000, Qt.TextWordWrap, self.full_text or " ")
        # Keep a compact single-line bubble, but make the second wrapped line
        # visibly expand the bubble while preserving the bottom anchor.
        height = max(70, min(190, text_bounds.height() + 48))
        self.setFixedSize(width, height)
        self.text.setGeometry(13, 9, width - 26, height - 25)
        screen = QGuiApplication.screenAt(self.anchor) or QGuiApplication.primaryScreen()
        geometry = screen.availableGeometry()
        available = Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())
        self.move(*fit_overlay_position((self.anchor.x(), self.anchor.y() - height), (width, height), available))

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#6cbdd7"), 1.4)); painter.setBrush(QColor(247, 251, 255, 245))
        body = QRectF(2, 2, self.width() - 4, self.height() - 18)
        path = QPainterPath(); path.addRoundedRect(body, 16, 16); painter.drawPath(path)
        painter.drawEllipse(QRectF(18, self.height() - 21, 15, 12))
        painter.drawEllipse(QRectF(9, self.height() - 13, 9, 7))


class ChatRunner(QObject):
    chunk = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, config: ChatConfig, messages: list[dict], client=None, search_client=None):
        super().__init__(); self.config = config; self.messages = messages
        self.client = client or OpenAICompatibleClient(); self.search_client = search_client or WebSearchClient(config.search_api_key); self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="desktop-pet-chat").start()

    def _run(self):
        parts: list[str] = []
        try:
            messages = self.messages
            query = next((item.get("content", "") for item in reversed(messages) if item.get("role") == "user"), "")
            if is_current_time_query(query):
                now = datetime.now().astimezone().strftime("%Y年%m月%d日 %H:%M（本机本地时间）")
                messages = messages[:-1] + [{"role": "system", "content": f"当前本机时间是：{now}。请直接用这个时间回答，不要联网搜索。"}, messages[-1]]
            elif self.config.web_search and needs_web_search(query):
                try:
                    messages = add_web_search_context(messages, query, self.search_client)
                except ChatApiError as exc:
                    messages = add_search_failure_context(messages, str(exc))
            for chunk in self.client.stream(self.config, messages):
                if self.cancelled:
                    return
                parts.append(chunk); self.chunk.emit(chunk)
            if self.cancelled:
                return
            answer = "".join(parts).strip()
            if not answer:
                raise RuntimeError("API 没有返回可显示的内容")
            self.finished.emit(answer)
        except Exception as exc:
            if not self.cancelled:
                self.failed.emit(str(exc))
