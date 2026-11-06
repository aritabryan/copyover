from version import APP_NAME


def _pin_async_window_icon():
    """Gives the panel window opened in the browser the puente.ico icon, so it shows right away in the
    taskbar. Only for browser windows CopyOver launched: never someone else's window."""
    if sys.platform != "win32":
        return
    import threading
    def _task():
        import ctypes
        u32 = ctypes.windll.user32
        ico_path = bundle.data_folder() / "puente.ico"
        if not ico_path.exists():
            return
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        h_sm = u32.LoadImageW(None, str(ico_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        h_bg = u32.LoadImageW(None, str(ico_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if not h_sm and not h_bg:
            return

        for _ in range(15):
            time.sleep(0.35)
            pids = _pids_panel()
            if not pids:
                continue
            windows = _find_windows(APP_NAME, pids)
            if windows:
                for hwnd in windows:
                    if h_sm:
                        u32.SendMessageW(hwnd, WM_SETICON, 0, h_sm)
                    if h_bg:
                        u32.SendMessageW(hwnd, WM_SETICON, 1, h_bg)
                break
    threading.Thread(target=_task, daemon=True).start()

"""Interfaz grafica de escritorio de CopyOver."""

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import tray
import bundle
from i18n import t

# Green only for states and the main button.
THEME = {
    "background": "#131416",
    "surface": "#1B1D21",
    "surface_hover": "#22252B",
    "surface_selected": "#17241C",
    "icon_bg": "#252830",
    "icon_bg_selected": "#213B29",
    "border": "#292C33",
    "border_hover": "#363A44",
    "border_active": "#2EA043",
    "green": "#2EA043",
    "green_hover": "#38B24F",
    "red": "#D9383A",
    "red_hover": "#E84A4C",
    "button_secondary": "#22252B",
    "button_secondary_hover": "#2C3038",
    "text": "#ECEEF2",
    "subtext": "#8E94A0",
    "off": "#636875",
}

UI_FONT = "Segoe UI" if sys.platform == "win32" else "Sans"
MONO_FONT = "Consolas" if sys.platform == "win32" else "Monospace"


def _hex_rgba(color_hex: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = color_hex.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _img_rect(width: int, height: int, radio: int, padding: str, border: str, background: str = THEME["background"], thickness: int = 1):
    """Builds a PhotoImage with rounded corners (2x supersampling)."""
    from PIL import Image, ImageDraw, ImageTk

    scale = 2
    w, h = max(4, width * scale), max(4, height * scale)
    r = min(radio * scale, w // 2, h // 2)
    img = Image.new("RGBA", (w, h), _hex_rgba(background))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        (0, 0, w - 1, h - 1),
        radius=r,
        fill=_hex_rgba(padding),
        outline=_hex_rgba(border),
        width=max(1, thickness * scale),
    )
    img = img.resize((max(2, width), max(2, height)), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(img)


def _vector_icon(kind: str, size: int = 38, active: bool = False, ext_background: str = THEME["surface"]):
    """Draws the vector icons (document, laptop, check) with 2x supersampling."""
    from PIL import Image, ImageDraw, ImageTk

    scale = 2
    n = size * scale
    img = Image.new("RGBA", (n, n), _hex_rgba(ext_background))
    d = ImageDraw.Draw(img)

    box_bg = THEME["icon_bg_selected"] if active else THEME["icon_bg"]
    stroke_color = "#46C95E" if active else "#C5C9D3"
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=int(n * 0.24), fill=_hex_rgba(box_bg))

    g = max(3, int(n * 0.055))
    c = _hex_rgba(stroke_color)

    if kind == "file":
        x0, y0, x1, y1 = int(n * 0.30), int(n * 0.22), int(n * 0.70), int(n * 0.78)
        d.rounded_rectangle((x0, y0, x1, y1), radius=int(n * 0.06), outline=c, width=g)
        d.line((int(n * 0.39), int(n * 0.43), int(n * 0.61), int(n * 0.43)), fill=c, width=g)
        d.line((int(n * 0.39), int(n * 0.56), int(n * 0.55), int(n * 0.56)), fill=c, width=g)
    elif kind == "pc":
        mx0, my0, mx1, my1 = int(n * 0.24), int(n * 0.26), int(n * 0.76), int(n * 0.64)
        d.rounded_rectangle((mx0, my0, mx1, my1), radius=int(n * 0.05), outline=c, width=g)
        d.line((int(n * 0.18), int(n * 0.73), int(n * 0.82), int(n * 0.73)), fill=c, width=g)
    elif kind == "code":
        cx0, cy0, cx1, cy1 = int(n * 0.28), int(n * 0.44), int(n * 0.72), int(n * 0.78)
        d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=int(n * 0.06), outline=c, width=g)
        d.arc((int(n * 0.36), int(n * 0.22), int(n * 0.64), int(n * 0.54)), start=180, end=0, fill=c, width=g)

    img = img.resize((size, size), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(img)


def _radio_icon(selected: bool, ext_background: str):
    """Draws the round selection indicator on the right of each computer card."""
    from PIL import Image, ImageDraw, ImageTk

    scale = 2
    size = 20
    n = size * scale
    img = Image.new("RGBA", (n, n), _hex_rgba(ext_background))
    d = ImageDraw.Draw(img)
    if selected:
        d.ellipse((2, 2, n - 3, n - 3), fill=_hex_rgba(THEME["green"]))
        d.line((int(n * 0.28), int(n * 0.52), int(n * 0.44), int(n * 0.68)), fill=(255, 255, 255, 255), width=4)
        d.line((int(n * 0.44), int(n * 0.68), int(n * 0.74), int(n * 0.34)), fill=(255, 255, 255, 255), width=4)
    else:
        d.ellipse((2, 2, n - 3, n - 3), outline=_hex_rgba(THEME["border_hover"]), width=3)
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(img)


def _ensure_visible_desktop():
    if sys.platform == "win32":
        try:
            import ctypes

            u = ctypes.windll.user32
            h = u.OpenDesktopW("Default", 0, False, 0x01FF)
            if h:
                u.SetThreadDesktop(h)
        except Exception:
            pass
    tray.enable_app_id()


def request_confirmation(offer: dict, timeout: float = 60.0) -> Optional[str]:
    """Opens the window to Accept/Reject the offer.

    Returns 'accept', 'always', 'reject' or None if it times out.
    """
    cmd = bundle.command("dialog", "confirm", json.dumps(offer), str(int(timeout)))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
    except (subprocess.TimeoutExpired, OSError):
        return None
    output = r.stdout.strip()
    if output in ("accept", "always", "reject"):
        return output
    return None


def confirm_pairing(name: str, code: str, timeout: float = 60.0, incoming: bool = False,
                             replaces: Optional[str] = None) -> bool:
    """Shows the 6-digit code and returns True if the user accepts. incoming: the other computer asked.
    replaces: the name stored here for the link being replaced, if any."""
    data = {"name": name, "code": code, "incoming": incoming}
    if replaces:
        data["replaces"] = replaces
    cmd = bundle.command("dialog", "pair", json.dumps(data), str(int(timeout)))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.stdout.strip() == "accept"


def choose_pc(pcs: list[dict], action: str = "send") -> Optional[dict]:
    """Opens the window to pick a nearby computer; accion (enviar, archivos or vincular) sets the button text."""
    cmd = bundle.command("dialog", "choose", json.dumps(pcs), action)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout.strip())
    except json.JSONDecodeError:
        return None


def manage_pcs(pcs):
    cmd = bundle.command("dialog", 'pcs', json.dumps(pcs))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, ValueError):
        return None


def _ask(root, title: str, text: str, si: str, no: str, initial_value: Optional[str] = None):
    """Small modal window whose buttons say what they do. With initial_value it asks for text and returns it;
    without it, returns True or False. Closing it means «no» (None if text was asked for)."""
    import tkinter as tk
    from tkinter import ttk

    window = tk.Toplevel(root)
    window.title(title)
    window.transient(root)
    window.resizable(False, False)
    body = ttk.Frame(window, padding=16)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text=text, wraplength=340, justify='left').pack(anchor='w')
    field = None
    if initial_value is not None:
        field = ttk.Entry(body, width=42)
        field.insert(0, initial_value)
        field.select_range(0, 'end')
        field.pack(fill='x', pady=(8, 0))
    response = {'value': None if field is not None else False}

    def accept(_=None):
        response['value'] = field.get() if field is not None else True
        window.destroy()

    row = ttk.Frame(body)
    row.pack(fill='x', pady=(14, 0))
    ttk.Button(row, text=no, command=window.destroy).pack(side='right')
    ttk.Button(row, text=si, command=accept).pack(side='right', padx=(0, 8))
    window.bind('<Return>', accept)
    window.bind('<Escape>', lambda _: window.destroy())
    window.grab_set()
    (field or window).focus_set()
    root.wait_window(window)
    return response['value']


def _pcs_window(pcs):
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    _prepare_window(root, t('Linked computers — CopyOver'), 640, 370)
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text=t('Linked computers'), font=(UI_FONT, 14, 'bold')).pack(anchor='w')
    ttk.Label(frame, text=t('Names you change are saved only on this PC.')).pack(anchor='w', pady=(4, 12))
    table = ttk.Treeview(frame, columns=('name', 'status', 'encrypted'), show='headings', selectmode='browse', height=7)
    table.heading('name', text=t('Name'))
    table.heading('status', text=t('Status'))
    table.heading('encrypted', text=t('Connection'))
    table.column('name', width=240)
    table.column('status', width=110)
    table.column('encrypted', width=220)
    scroll = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    table.pack(fill='both', expand=True)
    by_id = {pc['id']: pc for pc in pcs}
    for pc in pcs:
        table.insert('', 'end', iid=pc['id'], values=(pc['name'], t('Available') if pc['available'] else t('Offline'),
                                                      t('Encrypted') if pc.get('encrypted') else t('Not encrypted: link again')))
    if not pcs:
        ttk.Label(frame, text=t('You haven’t paired any PCs yet.')).pack(anchor='w', pady=8)
    elif not all(pc.get('encrypted') for pc in pcs):
        ttk.Label(frame, text=t('PCs marked “Not encrypted” were linked with an older version. Update CopyOver on '
                                'both and use Link > Another PC to encrypt the connection.'), wraplength=600).pack(anchor='w', pady=(8, 0))

    def run(action):
        selection = table.selection()
        if not selection:
            return
        pc = by_id[selection[0]]
        result = {'id': pc['id'], 'action': action}
        if action == 'rename':
            name = _ask(root, t('Change name'), t('Computer name:'), t('Save name'), t('Cancel'),
                                initial_value=pc['name'])
            if name is None:
                return
            name = name.strip()
            if not name or len(name) > 60 or any(ord(c) < 32 for c in name):
                messagebox.showerror(t('Change name'), t('Enter a name between 1 and 60 characters.'), parent=root)
                return
            result['name'] = name
        elif not _ask(root, t('Unpair computer'), t("Unpair {name}?\n\nIts pending transfers will be canceled. To send files, it will need to be paired again.", name=pc['name']),
                            t('Unpair'), t('Cancel')):
            return
        print(json.dumps(result), flush=True)
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=(14, 0))
    state = 'normal' if pcs else 'disabled'
    ttk.Button(buttons, text=t('Change name'), state=state, command=lambda: run('rename')).pack(side='left')
    ttk.Button(buttons, text=t('Unpair'), state=state, command=lambda: run('unpair')).pack(side='left', padx=8)
    ttk.Button(buttons, text=t('Close'), command=root.destroy).pack(side='right')
    root.bind('<Escape>', lambda _: root.destroy())
    root.mainloop()


def is_window_of(title: str, searched: str = APP_NAME, pid: int = 0, pids=()) -> bool:
    """Whether a window is the one we're looking for: by its exact title or because it belongs to the process
    CopyOver opened (the browser in app mode, whose title can vary). Nothing by prefix or «contains»:
    Explorer on a «CopyOver» folder, a GitHub tab or Notepad with the log aren't the panel. The quick view
    doesn't count either: when you tap its «Open panel» it's still visible, and when it was taken for the
    panel it focused itself and the panel never opened."""
    import preview

    title = (title or "").strip()
    if title == preview.WINDOW_TITLE:
        return False
    if title == searched:
        return True
    return bool(title) and bool(pid) and pid in pids


def _pids_panel() -> set:
    """Processes that show the panel: its own window (pywebview) and the browser CopyOver launched."""
    import panel_window

    pids = set()
    for proc in (_PROC_PANEL, getattr(panel_window._LAUNCHER, "process", None)):
        pid = getattr(proc, "pid", None)
        try:
            alive = proc is not None and proc.poll() is None
        except Exception:
            alive = False
        if alive and isinstance(pid, int):
            pids.add(pid)
    return pids


def _find_windows(searched: str, pids=()) -> list:
    """Visible HWNDs that pass is_window_of (Windows)."""
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    windows = []

    def _evaluate(hwnd):
        if not u32.IsWindowVisible(hwnd):
            return
        length = u32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return
        title_buf = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(hwnd, title_buf, length + 1)
        pid = wintypes.DWORD(0)
        if pids:
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if is_window_of(title_buf.value, searched, pid.value, pids):
            windows.append(hwnd)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, lparam):
        _evaluate(hwnd)
        return True

    cb_func = WNDENUMPROC(_cb)
    u32.EnumWindows(cb_func, 0)

    if not windows:
        hdesk = u32.OpenDesktopW("Default", 0, False, 0x10000000)
        if hdesk:
            try:
                u32.EnumDesktopWindows(hdesk, cb_func, 0)
            finally:
                u32.CloseDesktop(hdesk)
    return windows


def focus_existing_window(title: str = APP_NAME) -> bool:
    """Restores and brings to the front the window with that exact title if it's already open. For the panel
    (the default title) the browser window CopyOver launched counts too."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        windows = _find_windows(title, _pids_panel() if title == APP_NAME else ())
        if not windows:
            return False

        hwnd_target = windows[0]
        if u32.IsIconic(hwnd_target):
            u32.ShowWindow(hwnd_target, 9)
        else:
            u32.ShowWindow(hwnd_target, 5)

        fore_hwnd = u32.GetForegroundWindow()
        fore_thread = u32.GetWindowThreadProcessId(fore_hwnd, None)
        cur_thread = k32.GetCurrentThreadId()

        if fore_thread != cur_thread and fore_thread != 0:
            u32.AttachThreadInput(cur_thread, fore_thread, True)
            u32.BringWindowToTop(hwnd_target)
            u32.SetForegroundWindow(hwnd_target)
            u32.AttachThreadInput(cur_thread, fore_thread, False)
        else:
            u32.BringWindowToTop(hwnd_target)
            u32.SetForegroundWindow(hwnd_target)

        return True
    except Exception:
        return False


_last_launch_ts = 0.0


def _launch_file() -> Path:
    """Marks when the window was last opened, in this user's own temporary folder (a fixed name in the shared
    /tmp could be a link someone else left there)."""
    import private_files
    return private_files.temporary_folder() / "copyover_launch.lock"


def _recently_launched(threshold: float = 2.5) -> bool:
    global _last_launch_ts
    lock_file = _launch_file()
    now = time.time()
    if now - _last_launch_ts < threshold:
        return True
    try:
        if lock_file.exists():
            if now - lock_file.stat().st_mtime < threshold:
                return True
    except Exception:
        pass
    return False


def _mark_launch():
    global _last_launch_ts
    _last_launch_ts = time.time()
    try:
        lock_file = _launch_file()
        lock_file.write_text(str(_last_launch_ts), encoding="utf-8")
    except Exception:
        pass


_PROC_PANEL = None


def close_web_window():
    """Closes the panel window CopyOver opened (if it's still open), so it isn't left orphaned on exit."""
    global _PROC_PANEL
    import panel_window

    panel_window.close()
    proc = _PROC_PANEL
    _PROC_PANEL = None
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        proc.wait(3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return True


def launch_web_window(port: int = 8765) -> bool:
    """Opens the panel in its own window (pywebview); if that fails, in Brave or Edge in app mode, or in the
    browser."""
    import panel_window

    if panel_window.launcher(port)._alive():
        return panel_window.launcher(port).show()

    if focus_existing_window():
        return True

    if _recently_launched(2.5):
        for _ in range(6):
            time.sleep(0.3)
            if focus_existing_window():
                return True
        return True

    _mark_launch()

    if panel_window.launcher(port).show():
        return True
    logging.info("No window of its own for the panel; opening it in the browser")
    import local_secret
    return _open_in_browser(local_secret.link_for_browser(port))


def _open_in_browser(url: str) -> bool:
    """Fallback: Brave or Edge in app mode (window without tabs) and, if neither is there, the default
    browser."""
    global _PROC_PANEL
    import os
    import tempfile
    import webbrowser

    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        prog_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        app_profile = Path(local_app or tempfile.gettempdir()) / "PuentePortapapeles" / "perfil_app"
        try:
            app_profile.mkdir(parents=True, exist_ok=True)
            for f_fav in (app_profile / "Default").glob("Favicons*"):
                try:
                    f_fav.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        _pin_async_window_icon()

        candidates = [
            Path(local_app) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(prog_files) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(prog_x86) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(prog_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(prog_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]

        si = subprocess.STARTUPINFO()
        si.lpDesktop = "Winsta0\\Default"

        for exe in candidates:
            if exe.exists():
                try:
                    cmd = [str(exe), f"--user-data-dir={app_profile}", f"--app={url}", "--start-maximized"]
                    proc = subprocess.Popen(cmd, startupinfo=si)
                    _PROC_PANEL = proc
                    time.sleep(0.3)
                    if proc.poll() is None or proc.returncode == 0:
                        return True
                except OSError:
                    pass

        try:
            os.startfile(url)
            return True
        except Exception:
            pass

    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def files_selector_title() -> str:
    """Title of the file picker: the panel looks for this exact text to bring it to the front."""
    return t("Select files — CopyOver")


def folder_selector_title() -> str:
    return t("Select CopyOver download folder")


# With 2 minutes the picker closed while people were still looking for the file.
SELECTOR_WAIT = 3600


def select_native_files() -> list[dict]:
    cmd = bundle.command("dialog", "files")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SELECTOR_WAIT)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout.strip())
    except Exception:
        pass
    return []


def select_native_folder() -> Optional[str]:
    cmd = bundle.command("dialog", "folder")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SELECTOR_WAIT)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


class ProgressMonitor:
    """Progress window in a subprocess fed through stdin."""

    def __init__(self, name_destination: str):
        self.proc: Optional[subprocess.Popen] = None
        cmd = bundle.command("dialog", "progreso", name_destination)
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
            )
        except OSError:
            self.proc = None

    def update(self, sent: int, total: int, file: str):
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            return
        try:
            line = json.dumps({"sent": sent, "total": total, "file": file})
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except OSError:
            pass

    def close(self):
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


def _format_bytes(n: int) -> str:
    value = float(n)
    for drive in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or drive == "TB":
            return f"{int(value)} B" if drive == "B" else f"{value:.1f} {drive}"
        value /= 1024
    return f"{n} B"


def _apply_window_icon(root):
    tray.enable_app_id()
    ico = tray.ensure_ico()
    if ico.exists():
        try:
            root.iconbitmap(default=str(ico))
            root.iconbitmap(str(ico))
        except Exception:
            pass
    try:
        from PIL import ImageTk

        root._large_icon = ImageTk.PhotoImage(tray.draw_logo(size=64))
        root._small_icon = ImageTk.PhotoImage(tray.draw_logo(size=32))
        root.iconphoto(True, root._large_icon, root._small_icon)
    except Exception:
        pass


def _prepare_window(root, title: str, width: int, height: int, offset_x: int = 0, offset_y: int = 0):
    root.withdraw()
    root.title(title)
    root.configure(bg=THEME["background"])
    root.resizable(False, False)
    _apply_window_icon(root)
    x = (root.winfo_screenwidth() - width) // 2 + offset_x
    y = (root.winfo_screenheight() - height) // 3 + offset_y
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.update_idletasks()
    root.deiconify()
    root.attributes("-topmost", True)
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            dark = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            corners = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners))
            ctypes.windll.user32.ShowWindow(hwnd, 5)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    root.lift()
    root.focus_force()


def _rounded_container(parent, pad_x: int = 14, pad_y: int = 12, radio: int = 12, active: bool = False):
    """Container with a rounded background and border drawn with Pillow."""
    import tkinter as tk

    padding = THEME["surface_selected"] if active else THEME["surface"]
    border = THEME["border_active"] if active else THEME["border"]

    canvas = tk.Canvas(parent, bg=THEME["background"], highlightthickness=0, bd=0)
    inner = tk.Frame(canvas, bg=padding)
    window_id = canvas.create_window((pad_x, pad_y), window=inner, anchor="nw")
    state = {"img": None, "width": 0, "height": 0, "fill": padding, "border": border}

    def redraw(_=None):
        inner.update_idletasks()
        w = max(canvas.winfo_width(), inner.winfo_reqwidth() + pad_x * 2)
        h = inner.winfo_reqheight() + pad_y * 2
        if w < 20 or h < 20:
            return
        canvas.configure(height=h)
        canvas.itemconfigure(window_id, width=w - pad_x * 2)
        key = (w, h, state["fill"], state["border"])
        if key != (state["width"], state["height"], getattr(state, "_r", None), getattr(state, "_b", None)):
            state["width"], state["height"] = w, h
            state["_r"], state["_b"] = state["fill"], state["border"]
            try:
                state["img"] = _img_rect(w, h, radio=radio, padding=state["fill"], border=state["border"])
                canvas.delete("fondo_tarjeta")
                canvas.create_image(0, 0, image=state["img"], anchor="nw", tags="fondo_tarjeta")
                canvas.tag_lower("fondo_tarjeta")
            except Exception:
                pass

    def change_style(new_padding: str, new_border: str):
        state["fill"] = new_padding
        state["border"] = new_border
        inner.configure(bg=new_padding)
        state["width"] = 0
        redraw()

    canvas.change_style = change_style
    canvas.bind("<Configure>", redraw)
    inner.bind("<Configure>", redraw)
    return canvas, inner


def _button(
    parent,
    text: str,
    command,
    primary: bool = False,
    danger: bool = False,
    px_width: int = 128,
    px_height: int = 34,
    parent_background: Optional[str] = None,
):
    """Rounded button with hover and active states."""
    import tkinter as tk

    bg_ext = parent_background or parent.cget("bg")
    if primary:
        bg, hover, border = THEME["green"], THEME["green_hover"], THEME["green"]
    elif danger:
        bg, hover, border = THEME["red"], THEME["red_hover"], THEME["red"]
    else:
        bg, hover, border = THEME["button_secondary"], THEME["button_secondary_hover"], THEME["border"]

    try:
        img_normal = _img_rect(px_width, px_height, radio=8, padding=bg, border=border, background=bg_ext)
        img_hover = _img_rect(px_width, px_height, radio=8, padding=hover, border=hover if primary else THEME["border_hover"], background=bg_ext)
        lbl = tk.Label(
            parent,
            text=text,
            image=img_normal,
            compound="center",
            bg=bg_ext,
            fg="#FFFFFF" if primary or danger else THEME["text"],
            font=(UI_FONT, 9, "bold" if primary else "normal"),
            cursor="hand2",
            bd=0,
        )
        lbl._img_normal = img_normal
        lbl._img_hover = img_hover
        lbl.bind("<Enter>", lambda _: lbl.configure(image=img_hover))
        lbl.bind("<Leave>", lambda _: lbl.configure(image=img_normal))
        lbl.bind("<ButtonRelease-1>", lambda _: command())
        return lbl
    except Exception:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=THEME["text"],
            font=(UI_FONT, 9, "bold" if primary else "normal"),
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )


def _header(parent, right_detail: str = ""):
    """Top bar with the CopyOver logo on the left and the status on the right."""
    import tkinter as tk

    row = tk.Frame(parent, bg=THEME["background"])
    row.pack(fill="x", pady=(0, 14))

    try:
        from PIL import ImageTk

        logo_tk = ImageTk.PhotoImage(tray.draw_logo(size=24))
        lbl_logo = tk.Label(row, image=logo_tk, bg=THEME["background"], bd=0)
        lbl_logo._logo_tk = logo_tk
        lbl_logo.pack(side="left", padx=(0, 8))
    except Exception:
        pass

    tk.Label(
        row,
        text="CopyOver",
        bg=THEME["background"],
        fg=THEME["text"],
        font=(UI_FONT, 11, "bold"),
    ).pack(side="left")

    if right_detail:
         state_frame = tk.Frame(row, bg=THEME["background"])
         state_frame.pack(side="right")
         tk.Label(
             state_frame,
             text="●",
             bg=THEME["background"],
             fg=THEME["green"],
             font=(UI_FONT, 8),
         ).pack(side="left", padx=(0, 4))
         tk.Label(
             state_frame,
             text=right_detail,
             bg=THEME["background"],
             fg=THEME["subtext"],
             font=(UI_FONT, 9),
         ).pack(side="left")


def _confirm_window(offer: dict, seconds: int):
    import tkinter as tk

    de = offer.get("sender", {}).get("name", t("Another computer"))
    files = offer.get("files", [])
    total = sum(int(a.get("size", 0)) for a in files)
    txt_size = _format_bytes(total)

    if len(files) == 1:
        file_name = files[0].get("name", t("file"))
        file_detail = t("1 file  ·  {size}", size=txt_size)
    else:
        file_name = ", ".join(a.get("name", "") for a in files[:2])
        if len(files) > 2:
            file_name += t(" (+{n} more)", n=len(files) - 2)
        file_detail = t("{n} files  ·  {size}", n=len(files), size=txt_size)

    result = {"value": ""}
    root = tk.Tk()

    frame = tk.Frame(root, bg=THEME["background"], padx=22, pady=18)
    frame.pack(fill="both", expand=True)

    _header(frame, t("From {name}", name=de))

    canvas_t, card = _rounded_container(frame, pad_x=14, pad_y=12)
    canvas_t.pack(fill="x", pady=(0, 12))

    try:
        file_ic = _vector_icon("file", size=38, active=True, ext_background=THEME["surface"])
        lbl_ic = tk.Label(card, image=file_ic, bg=THEME["surface"], bd=0)
        lbl_ic._ic = file_ic
        lbl_ic.pack(side="left", padx=(0, 12))
    except Exception:
        pass

    col_info = tk.Frame(card, bg=THEME["surface"])
    col_info.pack(side="left", fill="x", expand=True)

    tk.Label(
        col_info,
        text=file_name,
        bg=THEME["surface"],
        fg=THEME["text"],
        font=(UI_FONT, 10, "bold"),
        anchor="w",
    ).pack(fill="x")

    tk.Label(
        col_info,
        text=t("{name} wants to send you {detail}", name=de, detail=file_detail),
        bg=THEME["surface"],
        fg=THEME["subtext"],
        font=(UI_FONT, 9),
        anchor="w",
    ).pack(fill="x", pady=(2, 0))

    always_var = tk.BooleanVar(value=False)
    chk = tk.Checkbutton(
        frame,
        text=t("Automatically accept from this computer next time"),
        variable=always_var,
        bg=THEME["background"],
        fg=THEME["subtext"],
        selectcolor=THEME["surface"],
        activebackground=THEME["background"],
        activeforeground=THEME["text"],
        font=(UI_FONT, 9),
        anchor="w",
    )
    chk.pack(fill="x", pady=(0, 14))

    btn_row = tk.Frame(frame, bg=THEME["background"])
    btn_row.pack(fill="x", side="bottom")

    time_lbl = tk.Label(
        btn_row,
        text=f"{seconds} s",
        bg=THEME["background"],
        fg=THEME["off"],
        font=(MONO_FONT, 9),
    )
    time_lbl.pack(side="left")

    def accept():
        result["value"] = "always" if always_var.get() else "accept"
        root.destroy()

    def reject():
        result["value"] = "reject"
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", reject)
    root.bind("<Return>", lambda _: accept())
    root.bind("<Escape>", lambda _: reject())

    _button(btn_row, t("Decline"), reject, px_width=96).pack(side="right", padx=(8, 0))
    _button(btn_row, t("Save"), accept, primary=True, px_width=154).pack(side="right")

    _prepare_window(root, t("Receive file — CopyOver"), 440, 225)

    remaining = [seconds]

    def countdown():
        remaining[0] -= 1
        if remaining[0] <= 0:
            root.destroy()
            return
        time_lbl.config(text=f"{remaining[0]} s")
        root.after(1000, countdown)

    root.after(1000, countdown)
    root.mainloop()

    if result["value"]:
        print(result["value"], flush=True)


def _pair_window(data: dict, seconds: int):
    import tkinter as tk

    from pc_pairing import visible_name

    name = visible_name(data.get("name"), t("Another computer"))
    code = str(data.get("code", "000000"))
    replaces = visible_name(data.get("replaces"), "") if data.get("replaces") else ""
    result = {"value": ""}

    root = tk.Tk()
    frame = tk.Frame(root, bg=THEME["background"], padx=22, pady=18)
    frame.pack(fill="both", expand=True)

    _header(frame, t("Pair with {name}", name=name))

    tk.Label(
        frame,
        text=t("Check that {name} shows the same code:", name=name),
        bg=THEME["background"],
        fg=THEME["subtext"],
        font=(UI_FONT, 9),
        anchor="w",
    ).pack(fill="x", pady=(0, 8))

    if replaces:
        tk.Label(
            frame,
            text=t("This replaces the link with {name}.", name=replaces),
            bg=THEME["background"],
            fg=THEME["text"],
            font=(UI_FONT, 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=360,
        ).pack(fill="x", pady=(0, 8))

    canvas_c, code_card = _rounded_container(frame, pad_x=14, pad_y=12)
    canvas_c.pack(fill="x", pady=(0, 16))

    tk.Label(
        code_card,
        text=f"{code[:3]} {code[3:]}",
        bg=THEME["surface"],
        fg=THEME["text"],
        font=(MONO_FONT, 22, "bold"),
    ).pack(fill="x")

    def accept():
        result["value"] = "accept"
        root.destroy()

    def reject():
        result["value"] = "reject"
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", reject)
    row = tk.Frame(frame, bg=THEME["background"])
    row.pack(fill="x", side="bottom")
    _button(row, t("Decline") if data.get("incoming") else t("Cancel"), reject, px_width=96).pack(side="right", padx=(8, 0))
    _button(row, t("The codes match"), accept, primary=True, px_width=176).pack(side="right")

    _prepare_window(root, t("Pair computer — CopyOver"), 410, 265 if replaces else 235)
    root.after(seconds * 1000, root.destroy)
    root.mainloop()

    if result["value"]:
        print(result["value"], flush=True)


def _create_pc_card(parent, eq: dict, selected: bool, on_choose):
    """Draws a computer card with its vector icon and selection indicator."""
    import tkinter as tk

    bg_card = THEME["surface_selected"] if selected else THEME["surface"]
    canvas, inner = _rounded_container(parent, pad_x=12, pad_y=10, radio=10, active=selected)
    canvas.pack(fill="x", pady=(0, 6))

    try:
        ic_pc = _vector_icon("pc", size=36, active=selected, ext_background=bg_card)
        lbl_ic = tk.Label(inner, image=ic_pc, bg=bg_card, bd=0, cursor="hand2")
        lbl_ic._ic = ic_pc
        lbl_ic.pack(side="left", padx=(0, 12))
    except Exception:
        lbl_ic = None

    col = tk.Frame(inner, bg=bg_card, cursor="hand2")
    col.pack(side="left", fill="x", expand=True)

    name_lbl = tk.Label(
        col,
        text=eq.get("name", t("Computer")),
        bg=bg_card,
        fg=THEME["text"],
        font=(UI_FONT, 10, "bold"),
        anchor="w",
        cursor="hand2",
    )
    name_lbl.pack(fill="x")

    so_raw = eq.get("so", "")
    readable_so = "Windows" if so_raw.startswith("win") else ("Linux" if so_raw.startswith("linux") else so_raw.capitalize())
    sub = f"{readable_so}  ·  {eq.get('ip', '')}" if readable_so else eq.get("ip", "")
    lbl_sub = tk.Label(
        col,
        text=sub,
        bg=bg_card,
        fg=THEME["subtext"],
        font=(UI_FONT, 8),
        anchor="w",
        cursor="hand2",
    )
    lbl_sub.pack(fill="x")

    try:
        ic_rad = _radio_icon(selected, bg_card)
        lbl_rad = tk.Label(inner, image=ic_rad, bg=bg_card, bd=0, cursor="hand2")
        lbl_rad._ic = ic_rad
        lbl_rad.pack(side="right", padx=(8, 2))
    except Exception:
        lbl_rad = None

    for w in (canvas, inner, col, name_lbl, lbl_sub, lbl_ic, lbl_rad):
        if w is not None:
            w.bind("<Button-1>", lambda _: on_choose())


SELECTOR_VISIBLE = 3
CARD_HEIGHT = 64


def _choose_window(pcs: list[dict], action: str = "send"):
    import tkinter as tk

    title, button = {
        "link": (t("Link with"), t("Link")),
        "files": (t("Send to"), t("Choose files…")),
    }.get(action, (t("Send to"), t("Send")))

    selection = {"index": 0, "pc": None}
    root = tk.Tk()

    frame = tk.Frame(root, bg=THEME["background"], padx=22, pady=18)
    frame.pack(fill="both", expand=True)

    _header(frame, t("Local network"))

    tk.Label(
        frame,
        text=title,
        bg=THEME["background"],
        fg=THEME["subtext"],
        font=(UI_FONT, 9, "bold"),
        anchor="w",
    ).pack(fill="x", pady=(0, 8))

    LIST_HEIGHT = SELECTOR_VISIBLE * CARD_HEIGHT + CARD_HEIGHT // 2
    if len(pcs) > SELECTOR_VISIBLE:
        # With more PCs than fit, the list scrolls with the wheel or the arrow keys.
        list_frame = tk.Frame(frame, bg=THEME["background"])
        list_frame.pack(fill="both", expand=True, pady=(0, 12))
        canvas = tk.Canvas(list_frame, bg=THEME["background"], highlightthickness=0, bd=0,
                           height=LIST_HEIGHT)
        bar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        pcs_container = tk.Frame(canvas, bg=THEME["background"])
        list_window = canvas.create_window((0, 0), window=pcs_container, anchor="nw")
        pcs_container.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(list_window, width=e.width))
        root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        root.bind_all("<Button-4>", lambda _: canvas.yview_scroll(-1, "units"))
        root.bind_all("<Button-5>", lambda _: canvas.yview_scroll(1, "units"))
    else:
        canvas = None
        pcs_container = tk.Frame(frame, bg=THEME["background"])
        pcs_container.pack(fill="both", expand=True, pady=(0, 12))

    def render_cards():
        for child in pcs_container.winfo_children():
            child.destroy()
        for idx, eq in enumerate(pcs):
            _create_pc_card(
                pcs_container,
                eq,
                selected=(idx == selection["index"]),
                on_choose=lambda i=idx: choose_idx(i),
            )

    def choose_idx(i: int):
        selection["index"] = i
        render_cards()
        if canvas is not None:
            top, bottom = canvas.yview()
            fraction = i / len(pcs)
            if not top <= fraction < bottom - 1 / len(pcs) + 1e-9:
                canvas.yview_moveto(max(0.0, fraction - (bottom - top) / 2))

    def move(step: int):
        if pcs:
            choose_idx(min(len(pcs) - 1, max(0, selection["index"] + step)))

    def confirm(_=None):
        if pcs and 0 <= selection["index"] < len(pcs):
            selection["pc"] = pcs[selection["index"]]
            root.destroy()

    render_cards()
    root.bind("<Return>", confirm)
    root.bind("<Escape>", lambda _: root.destroy())
    root.bind("<Down>", lambda _: move(1))
    root.bind("<Up>", lambda _: move(-1))

    row = tk.Frame(frame, bg=THEME["background"])
    row.pack(fill="x", side="bottom")
    _button(row, t("Cancel"), root.destroy, px_width=96).pack(side="right", padx=(8, 0))
    _button(row, button, confirm, primary=True, px_width=136).pack(side="right")

    height_v = 185 + (LIST_HEIGHT if canvas is not None else max(1, len(pcs)) * CARD_HEIGHT)
    _prepare_window(root, t("Choose PC — CopyOver"), 420, height_v)
    root.mainloop()

    if selection["pc"]:
        print(json.dumps(selection["pc"]), flush=True)
    else:
        sys.exit(1)


def _progress_window(name_destination: str):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
        style.configure(
            "Puente.Horizontal.TProgressbar",
            troughcolor=THEME["surface"],
            background=THEME["green"],
            bordercolor=THEME["border"],
            lightcolor=THEME["green"],
            darkcolor=THEME["green"],
        )
    except Exception:
        pass

    frame = tk.Frame(root, bg=THEME["background"], padx=22, pady=16)
    frame.pack(fill="both", expand=True)

    _header(frame, t("To {name}", name=name_destination))

    file_lbl = tk.Label(
        frame,
        text=t("Waiting for the other computer to accept..."),
        bg=THEME["background"],
        fg=THEME["text"],
        font=(UI_FONT, 9, "bold"),
        anchor="w",
    )
    file_lbl.pack(fill="x", pady=(0, 8))

    bar = ttk.Progressbar(
        frame,
        style="Puente.Horizontal.TProgressbar",
        orient="horizontal",
        length=380,
        mode="determinate",
        maximum=100,
    )
    bar.pack(fill="x", pady=(0, 6))

    detail_lbl = tk.Label(
        frame,
        text="0%",
        bg=THEME["background"],
        fg=THEME["subtext"],
        font=(MONO_FONT, 8),
        anchor="e",
    )
    detail_lbl.pack(fill="x")

    _prepare_window(root, t("Sending to {name}", name=name_destination), 420, 155, offset_y=205)

    state = {"sent": 0, "total": 0, "file": "", "closed": False}

    def read_stdin():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                state["sent"] = int(d.get("sent", 0))
                state["total"] = int(d.get("total", 0))
                state["file"] = str(d.get("file", ""))
            except Exception:
                pass
        state["closed"] = True

    threading.Thread(target=read_stdin, daemon=True).start()

    def refresh():
        if state["closed"]:
            root.destroy()
            return
        tot = state["total"]
        env = state["sent"]
        if tot > 0:
            pct = int((env * 100) / tot)
            bar["value"] = pct
            file_lbl.config(text=state["file"])
            detail_lbl.config(text=t("{pct}%  ·  {sent} of {total}", pct=pct, sent=_format_bytes(env), total=_format_bytes(tot)))
        root.after(80, refresh)

    root.after(80, refresh)
    root.mainloop()


def _files_dialog():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    paths = filedialog.askopenfilenames(title=files_selector_title())
    root.destroy()
    output = []
    for r in paths or []:
        p = Path(r)
        if p.is_file():
            output.append({"path": str(p.resolve()), "name": p.name, "size": p.stat().st_size})
    print(json.dumps(output), flush=True)


def _folder_dialog():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title=folder_selector_title())
    root.destroy()
    if folder and Path(folder).is_dir():
        print(str(Path(folder).resolve()), flush=True)


def main():
    _ensure_visible_desktop()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "confirm" and len(sys.argv) >= 3:
        data = json.loads(sys.argv[2])
        secs = int(sys.argv[3]) if len(sys.argv) >= 4 else 60
        _confirm_window(data, secs)
    elif mode == "pair" and len(sys.argv) >= 3:
        data = json.loads(sys.argv[2])
        secs = int(sys.argv[3]) if len(sys.argv) >= 4 else 60
        _pair_window(data, secs)
    elif mode == "choose" and len(sys.argv) >= 3:
        _choose_window(json.loads(sys.argv[2]), sys.argv[3] if len(sys.argv) >= 4 else "send")
    elif mode == 'pcs' and len(sys.argv) >= 3:
        _pcs_window(json.loads(sys.argv[2]))
    elif mode == "progreso" and len(sys.argv) >= 3:
        _progress_window(sys.argv[2])
    elif mode == "files":
        _files_dialog()
    elif mode == "folder":
        _folder_dialog()


if __name__ == "__main__":
    main()
