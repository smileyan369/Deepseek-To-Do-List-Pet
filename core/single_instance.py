from __future__ import annotations

import ctypes
import sys


ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """Process-wide Windows mutex preventing duplicate desktop-pet instances."""

    def __init__(self, name: str = "Local\\DeepSeaTodoPet.SingleInstance"):
        self.name = name
        self.handle = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError("无法创建桌宠单实例锁")
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle and sys.platform == "win32":
            ctypes.windll.kernel32.CloseHandle(self.handle)
        self.handle = None
