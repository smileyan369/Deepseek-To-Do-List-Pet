"""The pet's balance balloon: the official remaining balance, nothing else.

It tucks against the pet's upper right (upper left when that side is taken) so it
reads as something the pet itself is saying, and the amount is set in type that
grows with the balance.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.balance import MIN_FONT_PX, Balance, DeepSeekBalanceClient
from core.chat import ChatConfig
from core.positioning import Rect, balance_bubble_position

MIN_WIDTH = 150
# Generous, because a bigger balance is also a longer number: the cap must not
# claw back the type scale the feature is built on. Only extreme amounts shrink.
MAX_WIDTH = 420


class BalanceBubble(QWidget):
    """Oval manga-style balloon with a tail pointing down at the pet."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.on_right = True; self.value_text = ""
        layout = QVBoxLayout(self)
        # Wide side margins keep the text inside the oval, whose curve cuts the corners.
        layout.setContentsMargins(22, 12, 22, 28); layout.setSpacing(0)
        self.amount = QLabel("—", self); self.amount.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.amount)

    def show_loading(self):
        """Shown on the first hover, before the network answers."""
        self._set_amount("查询中…", 15, "#5d7690")

    def set_balance(self, balance: Balance | None, error: str = ""):
        if balance is not None:
            self._set_amount(balance.format_total(), balance.font_px(), "#132a56")
        else:
            self._set_amount("余额不可用", 15, "#b42318")
            self.amount.setToolTip(error)

    def _set_amount(self, text: str, pixel_size: int, colour: str):
        """Shrink the type only if an extreme amount would outgrow the balloon."""
        self.value_text = text
        while True:
            font = self.amount.font(); font.setPixelSize(pixel_size); font.setWeight(QFont.DemiBold)
            self.amount.setFont(font); self.amount.setStyleSheet(f"color:{colour};")
            self.amount.setText(text)
            width = self.amount.sizeHint().width() + 44
            if width <= MAX_WIDTH or pixel_size <= MIN_FONT_PX:
                break
            pixel_size -= 1
        self.setFixedWidth(max(MIN_WIDTH, width))
        self.layout().activate(); self.adjustSize()

    def place(self, pet: Rect, screen: Rect, blocked: list[Rect] | None = None):
        """Position against the pet, flipping when the preferred side is busy."""
        x, y, on_right = balance_bubble_position(pet, (self.width(), self.height()), screen, blocked or [])
        self.on_right = on_right
        self.move(x, y)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#6cbdd7"), 1.4)); painter.setBrush(QColor(247, 251, 255, 245))
        painter.drawEllipse(QRectF(2, 2, self.width() - 4, self.height() - 18))
        if self.on_right:
            painter.drawEllipse(QRectF(22, self.height() - 21, 13, 10))
            painter.drawEllipse(QRectF(13, self.height() - 13, 8, 6))
        else:
            painter.drawEllipse(QRectF(self.width() - 35, self.height() - 21, 13, 10))
            painter.drawEllipse(QRectF(self.width() - 21, self.height() - 13, 8, 6))


class BalanceRunner(QObject):
    """One balance lookup on a worker thread; the UI never blocks."""

    loaded = Signal(object, str)          # (Balance | None, error message)

    def __init__(self, config: ChatConfig, client=None):
        super().__init__(); self.config = config; self.client = client or DeepSeekBalanceClient()

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="desktop-pet-balance").start()

    def _run(self):
        try:
            self.loaded.emit(self.client.fetch(self.config), "")
        except Exception as exc:
            self.loaded.emit(None, str(exc))
