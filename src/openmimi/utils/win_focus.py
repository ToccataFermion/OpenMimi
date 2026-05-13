"""Windows foreground-lock workarounds for ``SetForegroundWindow``.

Windows refuses to let a background process steal the foreground unless the
caller satisfies one of several preconditions (the user just clicked, the
foreground app is gone, the system was idle past ``SPI_GETFOREGROUNDLOCKTIMEOUT``,
etc). ``agent_browser:focus`` and ``computer.focus_window`` were both hitting
``error 258 (WAIT_TIMEOUT)`` on plain ``SetForegroundWindow`` calls — 80% of
focus attempts on this machine.

This helper tries three escalating workarounds in order:

1. Send a fake ``VK_MENU`` (Alt) keystroke to ourselves. Alt has the documented
   side effect of releasing the foreground-lock queue without producing any
   visible action on the desktop. After that ``SetForegroundWindow`` usually
   succeeds.
2. Minimise then restore the target window. Windows treats a ``ShowWindow``
   restore on the *same* window as a legitimate state transition and re-grants
   foreground.
3. ``AttachThreadInput`` to the current foreground thread, set foreground,
   then detach. While attached, the kernel treats our thread as if it owned
   the foreground itself, so ``SetForegroundWindow`` works regardless of lock.

Returns ``(success, method)``: ``method`` is one of ``"direct"``, ``"toggle"``,
``"attached"``, or ``"failed"`` so callers can log which path actually worked.

The helper is conservatively wrapped — every win32 call is guarded so a missing
module on non-Windows (or a partial ``pywin32`` install) degrades to "best-effort
``SetForegroundWindow`` only" rather than crashing the tool.
"""
from __future__ import annotations

from typing import Any


def _send_alt_keystroke() -> None:
    """Release the foreground-lock queue with a no-op Alt press/release.

    Documented Windows trick. If ``win32api``/``win32con`` are unavailable we
    just skip — the next steps still work, they're just less likely to.
    """
    try:
        import win32api
        import win32con
    except ImportError:
        return
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(
            win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0
        )
    except Exception:
        # keybd_event is best-effort; don't let a peripheral failure block
        # the actual SetForegroundWindow attempt below.
        pass


def _try_set_foreground(win32gui: Any, hwnd: int) -> bool:
    """Plain ``SetForegroundWindow`` wrapped in a single try.

    ``win32gui.SetForegroundWindow`` raises ``pywintypes.error`` on failure;
    we catch broadly because the exception type lives outside our test shim.
    """
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _try_attach_thread_input(win32gui: Any, hwnd: int) -> bool:
    """Last-resort: attach our thread to the current foreground thread.

    While attached, our process behaves (to the kernel) as the foreground
    owner — ``SetForegroundWindow`` is then allowed for any window. Always
    detach in ``finally`` so the next event loop tick doesn't inherit the
    attachment.
    """
    try:
        import win32api
        import win32process
    except ImportError:
        return False
    try:
        fg_hwnd = win32gui.GetForegroundWindow()
        if not fg_hwnd:
            return False
        fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0]
        my_thread = win32api.GetCurrentThreadId()
        if fg_thread == my_thread:
            # Already the foreground thread — no attach needed.
            return _try_set_foreground(win32gui, hwnd)
        attached = False
        try:
            win32process.AttachThreadInput(my_thread, fg_thread, True)
            attached = True
            return _try_set_foreground(win32gui, hwnd)
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(my_thread, fg_thread, False)
                except Exception:
                    pass
    except Exception:
        return False


def force_set_foreground(hwnd: int) -> tuple[bool, str]:
    """Bring ``hwnd`` to the foreground, working around Windows foreground-lock.

    Returns ``(success, method)`` where ``method`` reports which strategy
    actually succeeded — useful for logs and audit-stats follow-up.
    """
    try:
        import win32gui
        import win32con
    except ImportError:
        return False, "win32gui_missing"

    # Step 0: always restore first. SetForegroundWindow on a minimised window
    # is a no-op even when the lock is open.
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass

    # Step 1: unlock foreground queue, then try the direct call.
    _send_alt_keystroke()
    if _try_set_foreground(win32gui, hwnd):
        return True, "direct"

    # Step 2: ShowWindow toggle. Minimise → restore re-asserts foreground
    # rights for the same window.
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    if _try_set_foreground(win32gui, hwnd):
        return True, "toggle"

    # Step 3: AttachThreadInput as the heavy hammer.
    if _try_attach_thread_input(win32gui, hwnd):
        return True, "attached"

    return False, "failed"


__all__ = ["force_set_foreground"]
