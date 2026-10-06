"""Keep a frozen single-file build working when Windows cleans %TEMP%.

Windows 存储感知（Storage Sense）和联想电脑管家等清理工具会定期删除“应用未在使用
的临时文件”。单文件 PyInstaller 程序把整个运行时解压到 `%TEMP%\\_MEIxxxxx`，因此这
类清理会从**正在运行**的桌宠下面删掉文件：被进程占用的 DLL 和 `.pyd` 会保留，而
`base_library.zip` 等空闲文件会被删掉。Python 之后第一次惰性导入标准库时会报

    [Errno 2] No such file or directory: '...\\_MEIxxxxx\\base_library.zip'

在桌宠里表现为首次聊天才出现的“无法连接聊天 API”，因为聊天链路要导入 urllib/ssl。

在启动时把 `base_library.zip` 里的模块和网络栈全部导入一次，这些模块就常驻内存，
之后的清理不会再影响运行中的实例。打包说明见 docs/运行与打包说明.md。
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import sys
import time
import zipfile


BOOTSTRAP_ARCHIVE = "base_library.zip"

# Modules the chat link pulls in late; importing them up front also keeps the
# matching extension files (_ssl.pyd, _socket.pyd, ...) mapped in memory.
LATE_IMPORTS = (
    "ssl", "socket", "select", "http.client", "urllib.request", "urllib.error",
    "gzip", "zlib", "hashlib", "hmac", "email.parser", "email.message",
    "html.parser", "xml.etree.ElementTree", "ctypes", "encodings.idna",
)

REMOVED_RUNTIME_HINT = (
    "程序运行所需的临时文件已被系统清理工具删除（常见原因：Windows 存储感知或电脑管家清理 %TEMP%），"
    "请从托盘退出桌宠后重新打开。"
)


def extraction_root() -> Path | None:
    """Return the frozen extraction directory, or None when running from source."""
    root = getattr(sys, "_MEIPASS", None)
    return Path(root) if root else None


def bootstrap_archive() -> Path | None:
    """Return `_MEIPASS/base_library.zip` when it exists."""
    root = extraction_root()
    if root is None:
        return None
    archive = root / BOOTSTRAP_ARCHIVE
    return archive if archive.is_file() else None


def archive_module_names(archive: Path) -> list[str]:
    """List the importable module names stored in a PyInstaller bootstrap archive."""
    try:
        with zipfile.ZipFile(archive) as handle:
            entries = handle.namelist()
    except (OSError, zipfile.BadZipFile):
        return []
    names = set()
    for entry in entries:
        if not entry.endswith(".pyc"):
            continue
        name = entry[:-len(".pyc")].replace("\\", "/").replace("/", ".")
        if name.endswith(".__init__"):
            name = name[:-len(".__init__")]
        if name:
            names.add(name)
    return sorted(names)


def preload_modules_from_archive(archive: Path) -> list[str]:
    """Import every module of `archive`; broken entries are skipped, never fatal."""
    loaded = []
    for name in archive_module_names(archive):
        try:
            importlib.import_module(name)
        except Exception:
            continue
        loaded.append(name)
    return loaded


def preload_late_imports() -> list[str]:
    """Import the network stack so HTTPS stays usable after a temp cleanup."""
    loaded = []
    for name in LATE_IMPORTS:
        try:
            importlib.import_module(name)
        except ImportError:
            continue
        loaded.append(name)
    return loaded


def preload_frozen_runtime() -> int:
    """Warm the whole frozen runtime; a no-op when running from source."""
    if not getattr(sys, "frozen", False):
        return 0
    archive = bootstrap_archive()
    loaded = preload_modules_from_archive(archive) if archive else []
    loaded.extend(preload_late_imports())
    return len(loaded)


def was_runtime_removed(exc: BaseException) -> bool:
    """Report whether `exc` is a missing file inside the frozen extraction directory."""
    archive = bootstrap_archive()
    text = str(exc)
    if BOOTSTRAP_ARCHIVE in text:
        return True
    root = extraction_root()
    filename = getattr(exc, "filename", None)
    if root is None or not isinstance(filename, str) or not filename:
        return False
    try:
        return os.path.normcase(str(Path(filename).parent)).startswith(os.path.normcase(str(root)))
    except (OSError, ValueError):
        return False


def removed_runtime_hint() -> str:
    return REMOVED_RUNTIME_HINT


# Leftover extraction directories older than this are safe to delete: nothing can
# still be starting from them, because a launch extracts and reaches the mutex
# within seconds.
STALE_EXTRACTION_AGE_SECONDS = 3600.0


def cleanup_stale_extractions(max_age_seconds: float = STALE_EXTRACTION_AGE_SECONDS) -> int:
    """Remove `_MEI*` leftovers from forcibly closed earlier runs.

    Only meaningful for a frozen one-file build, and only called while the
    single-instance mutex is held — so any extraction directory other than our own
    belongs to a process that is no longer running. Directories younger than
    `max_age_seconds` are kept: they may belong to a launch that has extracted but
    not yet taken the mutex. Every failure is ignored; foreign files are never
    touched (entries must start with `_MEI`).
    """
    if not getattr(sys, "frozen", False):
        return 0
    root = extraction_root()
    if root is None:
        return 0
    parent = root.parent
    if not parent.is_dir():
        return 0
    removed = 0
    cutoff = time.time() - max_age_seconds
    for entry in parent.iterdir():
        if entry.name == root.name or not entry.name.startswith("_MEI"):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if entry.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed += 1
        except OSError:
            continue
    return removed
