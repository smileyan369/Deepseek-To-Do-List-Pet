"""Entry point: keep the frozen runtime alive, and never fail invisibly."""

import os
import sys
import traceback


def _make_windowed_streams_safe() -> None:
    """Give a console-less build real stdout/stderr.

    PyInstaller's windowed bootloader leaves `sys.stdout` and `sys.stderr` as None,
    and libraries that write to them during import then raise AttributeError.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _report_startup_failure(exc: BaseException) -> None:
    """Leave a readable trace and a visible dialog when startup dies.

    A packaged windowed pet that fails before its window appears is otherwise
    undiagnosable: with no console there is nowhere for the exception to go.
    """
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path = ""
    try:
        from core.diagnostics import log_line, log_path

        path = log_path()
        log_line("启动失败:\n" + detail)
    except Exception:
        pass
    if not path:
        try:
            folder = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
                else os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(folder, "pet-error.log")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(detail + "\n")
        except OSError:
            path = ""
    if getattr(sys, "frozen", False):
        try:
            import ctypes

            last = detail.strip().splitlines()[-1] if detail.strip() else str(exc)
            ctypes.windll.user32.MessageBoxW(
                None,
                "桌宠启动失败，窗口无法显示。\n\n详细错误已写入：\n"
                + (path or "(日志写入失败)")
                + "\n\n"
                + last[:300],
                "待办桌宠",
                0x10,
            )
        except Exception:
            pass


_make_windowed_streams_safe()

from core.runtime_guard import preload_frozen_runtime

# Import the frozen runtime before the GUI: Windows temp cleaners can delete the
# extracted files while the pet runs (see core/runtime_guard.py).
preload_frozen_runtime()

from app.main_window import run

if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        _report_startup_failure(exc)
        if getattr(sys, "frozen", False):
            sys.exit(1)
        raise
