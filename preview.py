"""Quick view next to the clock: any click on the icon (or the keyboard) opens it, with the history, the linked
devices, a few settings and the usual actions.

It runs in its own process because pywebview and pystray each want the main thread. CopyOver launches it
hidden the first time and afterwards only writes «mostrar» to its stdin, so it opens instantly; the window
hides by itself when it loses focus or on Escape.
"""
import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import bundle
import local_secret
from version import APP_NAME

WIDTH, HEIGHT, MARGIN = 320, 480, 10


def position(area, width=WIDTH, height=HEIGHT, margin=MARGIN):
    """Bottom-right corner of the work area (right above the taskbar)."""
    left, top, right, bottom = area
    return max(left, right - width - margin), max(top, bottom - height - margin)


def position_over(point, area, width=WIDTH, height=HEIGHT, margin=MARGIN):
    """Centered on the point that was clicked (the icon), with the bottom edge right above it, without leaving
    the work area. If it doesn't fit above (taskbar on top), it goes below."""
    px, py = point
    left, top, right, bottom = area
    x = min(max(px - width // 2, left + margin), right - width - margin)
    y = py - height - margin
    if y < top + margin:
        y = min(py + margin, bottom - height - margin)
    return x, max(y, top + margin)


def cursor():
    if sys.platform != "win32":
        return (0, 0)

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y)


def point_to_show(order, own_cursor=None):
    """Where to open. On Windows the cursor is read from this process: it's DPI-aware and CopyOver isn't, so
    at a scale other than 100 % the point CopyOver sends comes in different pixels and the window opened
    off to the side. The received point is kept for when the cursor can't be read."""
    if sys.platform == "win32":
        try:
            return (own_cursor or cursor)()
        except Exception:
            pass
    if len(order) == 3:
        return (int(order[1]), int(order[2]))
    return None


def visibility_notice(visible: bool) -> str:
    """JS that tells the page whether the window is visible (so it doesn't refresh while hidden)."""
    return f"window.copyoverVisible && window.copyoverVisible({'true' if visible else 'false'})"


def work_area(point=None):
    """Work area (without the taskbar) of the monitor where the point (or the cursor) is, in physical pixels."""
    if sys.platform != "win32":
        return (0, 0, 1280, 720)

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    u32 = ctypes.windll.user32
    if point:
        point = POINT(*point)
    else:
        point = POINT()
        u32.GetCursorPos(ctypes.byref(point))
    monitor = u32.MonitorFromPoint(point, 2)  # MONITOR_DEFAULTTONEAREST: the monitor where the click happened
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if monitor and u32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return (r.left, r.top, r.right, r.bottom)
    rect = RECT()
    u32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA, just in case
    return (rect.left, rect.top, rect.right, rect.bottom)


class Launcher:
    """CopyOver's side: starts the window process once and asks it to show itself."""

    def __init__(self, port, executable=None, popen=subprocess.Popen):
        self.port = port
        self.executable = executable or sys.executable
        self.popen = popen
        self.process = None
        self.lock = threading.Lock()

    def _alive(self):
        return self.process is not None and self.process.poll() is None

    def _command(self):
        return bundle.command("preview", self.port, os.getpid(), executable=self.executable)

    def show(self):
        try:
            self._show(cursor())
        except Exception:
            logging.exception("The quick view failed to open")

    def warm_up(self):
        """Starts the hidden process when CopyOver starts, so the first click is instant too."""
        try:
            with self.lock:
                self._launch()
        except Exception:
            logging.exception("Couldn't warm up the quick view")

    def _launch(self):
        if self._alive():
            return
        logging.debug("Launching the quick view with %s", self.executable)
        options: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        # Explicit stdout and stderr: under pythonw they don't exist and Popen would fail inheriting them.
        self.process = self.popen(self._command(), stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)

    def _show(self, point):
        with self.lock:
            self._launch()
            try:
                if sys.platform == "win32" and isinstance(self.process.pid, int):
                    # The icon click gave us the foreground; we hand it to the window's process.
                    ctypes.windll.user32.AllowSetForegroundWindow(self.process.pid)
                self.process.stdin.write(f"mostrar {point[0]} {point[1]}\n".encode())
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.process = None

    def close(self):
        with self.lock:
            if self._alive():
                try:
                    self.process.stdin.write(b"salir\n")
                    self.process.stdin.flush()
                    self.process.wait(2)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    self.process.kill()
            self.process = None


def _run(port, bridge_pid=None):
    # No console under pythonw: stdout/stderr are None and any print from pywebview would crash the process.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(Path(__file__).with_name("preview.log"), "a", encoding="utf-8")

    import webview

    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # so move() works in the same pixels as the work area
        except Exception:
            pass

    visible = {"now": False}

    def hide():
        visible["now"] = False
        window.hide()
        try:
            window.evaluate_js(visibility_notice(False))
        except Exception:
            pass

    class Api:
        def hide(self):
            hide()

        def yield_focus(self):
            # Before asking CopyOver to open the panel: this window has the foreground and hands it over,
            # otherwise Windows won't let CopyOver bring the panel window to the front.
            if sys.platform == "win32" and bridge_pid:
                ctypes.windll.user32.AllowSetForegroundWindow(int(bridge_pid))

        def focus_panel(self):
            # If the panel window already exists, bring it to the front from here (this process does have
            # permission).
            return _focus_panel_window()

    window = webview.create_window(
        WINDOW_TITLE, local_secret.input_url(port, "/app/preview"),
        width=WIDTH, height=HEIGHT, frameless=True, on_top=True, hidden=True, easy_drag=False,
        background_color="#0a0a0b", js_api=Api(),
    )

    def listen():
        if sys.stdin is None:
            return  # without a channel to CopyOver no commands arrive; the window stays as it is
        for line in sys.stdin:
            order = line.split()
            if order and order[0] == "mostrar":
                point = point_to_show(order)
                if point is not None:
                    x, y = position_over(point, work_area(point))
                else:
                    x, y = position(work_area())
                window.move(x, y)
                window.show()
                _without_button_in_bar()  # WinForms puts it back when showing; it's removed every time
                _bring_to_front()
                window.evaluate_js("window.dispatchEvent(new Event('airbridge-mostrar'))")
                window.evaluate_js(visibility_notice(True))
                visible["now"] = True
            elif order and order[0] == "salir":
                break
        window.destroy()  # also when CopyOver exits and closes stdin

    def watch_focus():
        # Hides when it loses focus (if it ever had it) or on a click outside it, even if the page gets no
        # «blur».
        had_focus = False
        button_before = False
        while True:
            time.sleep(0.1)
            if not visible["now"]:
                had_focus = False
                continue
            if _has_focus():
                had_focus = True
            elif had_focus:
                hide()
                continue
            button = _pressed_left_button()
            if button and not button_before and not _cursor_inside():
                hide()
            button_before = button

    def start():
        _without_button_in_bar()
        threading.Thread(target=listen, daemon=True).start()
        if sys.platform == "win32":
            threading.Thread(target=watch_focus, daemon=True).start()

    webview.start(start)


WINDOW_TITLE = f"{APP_NAME} · vista rápida"


def _hwnd():
    return ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE) if sys.platform == "win32" else 0


def _without_button_in_bar():
    """Tool window: no button in the taskbar or in Alt+Tab, like any flyout."""
    hwnd = _hwnd()
    if not hwnd:
        return
    u32 = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x00000080, 0x00040000
    style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new_item = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if new_item != style:
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_item)
        # SWP_FRAMECHANGED: so the taskbar notices the change even if the window is already visible.
        u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)


def _focus_panel_window() -> bool:
    """Finds the panel window (exact title «CopyOver») and restores it to the front."""
    if sys.platform != "win32":
        return False
    import ctypes.wintypes as wt
    u32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        length = u32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(hwnd, title, length + 1)
        # The panel (its own window or the browser in app mode) is titled exactly «CopyOver»; a normal tab
        # with the QR or the web app is titled «CopyOver - …» and isn't the panel.
        if title.value.strip() == APP_NAME:
            found.append(hwnd)
        return True

    u32.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = found[0]
    u32.ShowWindow(hwnd, 9 if u32.IsIconic(hwnd) else 5)  # SW_RESTORE / SW_SHOW
    # If Windows denies the focus change, say so and the view asks to open it the normal way.
    return bool(u32.SetForegroundWindow(hwnd))


def _bring_to_front():
    hwnd = _hwnd()
    if hwnd:
        ctypes.windll.user32.SetForegroundWindow(hwnd)


def _has_focus():
    if sys.platform != "win32":
        return True
    hwnd = _hwnd()
    if not hwnd:
        return True
    u32 = ctypes.windll.user32
    front = u32.GetForegroundWindow()
    return front == hwnd or u32.GetAncestor(front, 2) == hwnd  # GA_ROOT: the WebView's children count as ours


def _pressed_left_button():
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def _cursor_inside():
    hwnd = _hwnd()
    if not hwnd:
        return True

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    point, rect = POINT(), RECT()
    u32 = ctypes.windll.user32
    u32.GetCursorPos(ctypes.byref(point))
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left <= point.x <= rect.right and rect.top <= point.y <= rect.bottom


def main():
    _run(int(sys.argv[1]) if len(sys.argv) > 1 else 8765, int(sys.argv[2]) if len(sys.argv) > 2 else None)


if __name__ == "__main__":
    main()
