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


# A second launch used to exit silently, which from the outside is
# indistinguishable from a broken app ("double-clicked and nothing happened").
# The running pet creates this manual-reset event and polls it; the second launch
# signals it and exits, so double-clicking the icon pops the pet back up.
SHOW_EVENT_NAME = "Local\\DeepSeaTodoPet.ShowRequest"
EVENT_MODIFY_STATE = 0x0002


def create_show_request_event():
    """Create the (manual-reset) event the running pet polls. None off Windows."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateEventW.restype = ctypes.c_void_p
    handle = kernel32.CreateEventW(None, True, False, SHOW_EVENT_NAME)
    return handle or None


def take_show_request(handle) -> bool:
    """Consume a pending show request without blocking."""
    if not handle or sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint
    kernel32.ResetEvent.argtypes = (ctypes.c_void_p,)
    kernel32.ResetEvent.restype = ctypes.c_bool
    if kernel32.WaitForSingleObject(handle, 0) != 0:  # WAIT_OBJECT_0 == 0
        return False
    kernel32.ResetEvent(handle)
    return True


def request_show_existing() -> bool:
    """Signal the running pet to show itself; False when no instance is listening."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenEventW.argtypes = (ctypes.c_uint, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.SetEvent.argtypes = (ctypes.c_void_p,)
    kernel32.SetEvent.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)
