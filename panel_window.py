"""The CopyOver panel in its own window (pywebview), without depending on Brave or Edge.

It runs in a separate process, like the quick view, because pywebview and pystray each want the main thread.
CopyOver starts it with the port and sends it commands through stdin: «mostrar» brings the window to the front
and «salir» closes it. If the user closes the window, the process ends and the next «Open panel» launches it
again.
"""

import ctypes
import importlib.util
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any
from pathlib import Path

import bundle
import local_secret
from version import APP_NAME

WIDTH, HEIGHT = 1180, 760
MINIMUM = (900, 600)


def _work_area() -> tuple[int, int] | None:
    """Usable width and height (without the taskbar) of the screen under the pointer, in logical pixels."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        u32, shcore = ctypes.windll.user32, ctypes.windll.shcore
        u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        u32.MonitorFromPoint.restype = ctypes.c_void_p
        u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        point = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(point))
        monitor = u32.MonitorFromPoint(point, 1)  # MONITOR_DEFAULTTOPRIMARY
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if not u32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        dpi, dpi_y = ctypes.c_uint(96), ctypes.c_uint(96)
        shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi), ctypes.byref(dpi_y))
        # Without DPI awareness Windows gives the area in logical pixels at 96 DPI; with it, physical pixels
        # and the real DPI.
        scale = (dpi.value or 96) / 96
        area = info.rcWork
        return round((area.right - area.left) / scale), round((area.bottom - area.top) / scale)
    except Exception:
        return None


def window_size(area: tuple[int, int] | None = None) -> tuple[int, int]:
    """WIDTH×HEIGHT, shrunk to fit the screen: a 1080p laptop at 150 % leaves about 1280×690 usable."""
    area = area or _work_area()
    if not area:
        return WIDTH, HEIGHT
    margin = 24
    return max(MINIMUM[0], min(WIDTH, area[0] - margin)), max(MINIMUM[1], min(HEIGHT, area[1] - margin))


def available_pywebview() -> bool:
    return importlib.util.find_spec("webview") is not None


def _visible_window() -> bool:
    """True if there's already a visible window with the panel's exact title."""
    if sys.platform != "win32":
        return True
    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, APP_NAME)
    return bool(hwnd) and bool(u32.IsWindowVisible(hwnd))


class PanelLauncher:
    """CopyOver's side: launches the window process once and asks it to show itself."""

    def __init__(self, port, executable=None, popen=subprocess.Popen, wait=2.0):
        self.port = port
        self.executable = executable or bundle.gui_executable()
        self.popen = popen
        self.wait = wait
        self.process: Any = None
        self.lock = threading.Lock()

    def _alive(self):
        return self.process is not None and self.process.poll() is None

    def _command(self):
        return bundle.command("panel", self.port, os.getpid(), executable=self.executable)

    def show(self) -> bool:
        """Opens or focuses the window. False if there's no pywebview or the process died on startup."""
        with self.lock:
            if self._alive():
                return self._sort("mostrar")
            if not available_pywebview():
                return False
            options: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            try:
                # Explicit stdout and stderr: under pythonw they don't exist and Popen would fail inheriting
                # them.
                self.process = self.popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, **options)
            except OSError:
                logging.exception("Couldn't launch the panel window")
                self.process = None
                return False
            self._yield_foreground()
            return self._wait_startup()

    def _wait_startup(self) -> bool:
        limit = time.monotonic() + self.wait
        while time.monotonic() < limit:
            if not self._alive():
                logging.warning("The panel window exited while starting (code %s)", self.process.returncode)
                self.process = None
                return False
            if _visible_window():
                return True
            time.sleep(0.1)
        return self._alive()

    def _yield_foreground(self):
        # The tray click gave us the foreground; we hand it to the window's process, also when launching it
        # (otherwise Windows leaves it behind the app that was in front).
        if sys.platform == "win32" and isinstance(getattr(self.process, "pid", None), int):
            ctypes.windll.user32.AllowSetForegroundWindow(self.process.pid)

    def _sort(self, order) -> bool:
        try:
            self._yield_foreground()
            self.process.stdin.write(f"{order}\n".encode())
            self.process.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            self.process = None
            return False

    def close(self):
        with self.lock:
            if self._alive():
                self._sort("salir")
                try:
                    self.process.wait(2)
                except (OSError, subprocess.TimeoutExpired):
                    self.process.kill()
            self.process = None


_LAUNCHER = None


def launcher(port) -> PanelLauncher:
    global _LAUNCHER
    if _LAUNCHER is None or _LAUNCHER.port != port:
        _LAUNCHER = PanelLauncher(port)
    return _LAUNCHER


def close():
    if _LAUNCHER is not None:
        _LAUNCHER.close()


def _bring_to_front():
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, APP_NAME)
    if hwnd:
        hwnd = wintypes.HWND(hwnd)
        u32.ShowWindow(hwnd, 9 if u32.IsIconic(hwnd) else 5)  # SW_RESTORE / SW_SHOW
        # On top of the others even if Windows denies focus (a game or a full-screen app in front). HWND(-1)
        # and not -1: ctypes passes ints as 32 bits and on 64-bit Windows the call failed silently.
        u32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0003)  # HWND_TOPMOST, SWP_NOSIZE | SWP_NOMOVE
        u32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, 0x0003)  # HWND_NOTOPMOST
        u32.SetForegroundWindow(hwnd)
        if u32.GetForegroundWindow() != hwnd.value:
            # Windows only lets whoever just got a click or a key take the foreground; if the order came
            # without one, input is briefly attached to the foreground window so it's allowed.
            front = u32.GetForegroundWindow()
            front_thread = u32.GetWindowThreadProcessId(wintypes.HWND(front), None) if front else 0
            own_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            if front_thread and front_thread != own_thread and u32.AttachThreadInput(own_thread, front_thread, True):
                try:
                    u32.BringWindowToTop(hwnd)
                    u32.SetForegroundWindow(hwnd)
                finally:
                    u32.AttachThreadInput(own_thread, front_thread, False)


def _run(port, pid_copyover=None):
    # No console under pythonw: any print from pywebview would crash the process.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(bundle.data_folder() / "panel_window.log", "a", encoding="utf-8")
    if sys.platform == "win32":
        # Its own taskbar group (the tray's ID): otherwise, run from source, Windows groups the window with
        # pythonw.exe and shows the Python logo.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CopyOver.Escritorio")
        except Exception:
            pass
    icon = None
    if sys.platform.startswith("linux"):
        # Without its own app_id the window is «panel_window.py» and the dock shows a generic icon.
        from gi.repository import GLib

        import linux_system

        GLib.set_prgname(linux_system.ID_APP)
        if linux_system.APP_ICON.exists():
            icon = str(linux_system.APP_ICON)

    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    width, height = window_size()
    api_linux = ApiLinux() if sys.platform.startswith("linux") else None
    window = webview.create_window(
        APP_NAME, local_secret.input_url(port), width=width, height=height, min_size=MINIMUM,
        background_color="#0a0a0b", text_select=True, js_api=api_linux,
    )
    assert window is not None

    def listen():
        if sys.stdin is None:
            return  # opened without a channel to CopyOver (pythonw by hand): the window closes with its button
        for line in sys.stdin:
            order = line.strip()
            if order == "mostrar":
                window.restore()
                window.show()
                _bring_to_front()
            elif order == "salir":
                break
        window.destroy()  # also when CopyOver exits and closes stdin

    def start():
        threading.Thread(target=listen, daemon=True).start()
        if api_linux:
            api_linux.connect(window)
        _set_icon()
        _bring_to_front()

    webview.start(start, private_mode=False, storage_path=str(_data_folder()), icon=icon)


class ApiLinux:
    """What the panel asks the window for on Linux, where WebKitGTK falls short: the system file picker
    (attached to the window, not a separate Tk process in the dock) and the paths of a drop, which arrive
    without files in dataTransfer."""

    def __init__(self):
        self._view: Any = None
        self._drag: list[str] = []

    def connect(self, window):
        from gi.repository import GLib
        from webview.platforms.gtk import BrowserView

        def hook():
            self._view = BrowserView.instances.get(window.uid)
            if self._view:
                self._view.webview.connect("drag-data-received", self._on_receive_drag)
            return False

        GLib.idle_add(hook)

    def _on_receive_drag(self, _widget, _context, _x, _y, data, _info, _time):
        # WebKitGTK asks for the data while dragging over, before the drop: the last one received is kept.
        from gi.repository import Gio

        paths = [Gio.File.new_for_uri(uri).get_path() for uri in data.get_uris() or []]
        self._drag = [r for r in paths if r]
        return False

    def last_drag(self) -> list[str]:
        paths, self._drag = self._drag, []
        return paths

    def choose_paths(self, folder=False) -> list[str]:
        from gi.repository import GLib, Gtk

        import dialogs

        result: list[str] = []
        ready = threading.Event()

        def open_stream():
            try:
                action = Gtk.FileChooserAction.SELECT_FOLDER if folder else Gtk.FileChooserAction.OPEN
                title = dialogs.folder_selector_title() if folder else dialogs.files_selector_title()
                selector = Gtk.FileChooserNative.new(title, self._view.window if self._view else None,
                                                     action, None, None)
                selector.set_select_multiple(not folder)
                if selector.run() == Gtk.ResponseType.ACCEPT:
                    result.extend(selector.get_filenames())
                selector.destroy()
            finally:
                ready.set()
            return False

        GLib.idle_add(open_stream)
        ready.wait()
        return result


def _data_folder() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    folder = Path(base) / APP_NAME / "panel"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _set_icon():
    """WinForms gives the window python.exe's icon; it's swapped for CopyOver's."""
    if sys.platform != "win32":
        return
    ico = bundle.data_folder() / "puente.ico"
    if not ico.exists():
        return
    u32 = ctypes.windll.user32
    for _ in range(20):
        hwnd = _own_window(u32)
        if hwnd:
            for kind, side in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
                h = u32.LoadImageW(None, str(ico), 1, side, side, 0x0010)  # IMAGE_ICON, LR_LOADFROMFILE
                if h:
                    u32.SendMessageW(hwnd, 0x0080, kind, h)  # WM_SETICON
            return
        time.sleep(0.1)


def _own_window(u32):
    """This process's window titled APP_NAME. Only by title it could be another one (a browser tab
    called «CopyOver», for example) and the icon went there."""
    found = []
    own = os.getpid()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def check(hwnd, _):
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        if pid.value == own:
            title = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 256)
            if title.value == APP_NAME:
                found.append(hwnd)
                return False
        return True

    u32.EnumWindows(check, 0)
    return found[0] if found else None


def main():
    _run(int(sys.argv[1]) if len(sys.argv) > 1 else 8765, int(sys.argv[2]) if len(sys.argv) > 2 else None)


if __name__ == "__main__":
    main()
