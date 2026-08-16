from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


class GlobalVisibilityHotkey(QAbstractNativeEventFilter):
    """Windows global Ctrl+Shift+Z registration tied to the Qt app lifetime."""

    HOTKEY_ID = 0x4450
    WM_HOTKEY = 0x0312
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_NOREPEAT = 0x4000
    VK_Z = 0x5A

    def __init__(self, app: QCoreApplication, callback: Callable[[], None], user32=None):
        super().__init__()
        self.app = app
        self.callback = callback
        self.user32 = user32 or (ctypes.windll.user32 if sys.platform == "win32" else None)
        self.registered = False

    def register(self) -> bool:
        if self.registered:
            return True
        if self.user32 is None:
            return False
        modifiers = self.MOD_CONTROL | self.MOD_SHIFT | self.MOD_NOREPEAT
        if not self.user32.RegisterHotKey(None, self.HOTKEY_ID, modifiers, self.VK_Z):
            return False
        self.app.installNativeEventFilter(self)
        self.registered = True
        return True

    def unregister(self) -> None:
        if not self.registered:
            return
        self.app.removeNativeEventFilter(self)
        self.user32.UnregisterHotKey(None, self.HOTKEY_ID)
        self.registered = False

    def nativeEventFilter(self, event_type, message):
        try:
            event_name = bytes(event_type).decode("ascii")
        except (TypeError, ValueError, UnicodeDecodeError):
            event_name = str(event_type)
        if event_name in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                self.callback()
                return True, 0
        return False, 0
