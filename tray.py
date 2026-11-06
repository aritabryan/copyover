"""System tray icon (next to the clock) and the CopyOver logo generator."""

import logging
import re
import sys

import threading

import time

from pathlib import Path

import bundle
from i18n import t


WHITE = (241, 241, 243)
AMBER = (216, 166, 87)

# The mark is always white on the dark square (so it doesn't disappear on a light taskbar); a dot in the
# corner shows the state: none = running, amber = no network.
BADGES = {

    "active": None,

    "no_network": AMBER,

}

FOLDER = bundle.data_folder()

ICO_PATH = FOLDER / "puente.ico"

def draw_logo(color=WHITE, size: int = 256, badge=None):
    """CopyOver logo: dark rounded square with two clipboards, the back one outlined and the front one solid.
    Same mark as the favicon and the panel header (web/logo.svg), on a 64 grid.
    `insignia` is the color of a status dot in the bottom-right corner, or None."""
    from PIL import Image, ImageDraw

    scale = 4
    n = size * scale
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = n / 64
    background = (26, 26, 29, 255)
    ink = (*color, 255)

    def box(x, y, width, height):
        return (x * u, y * u, (x + width) * u, (y + height) * u)

    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=14 * u, fill=background)
    # Back board: 28x36 outline at (10, 10), radius 6. Small icons get a thicker stroke so it doesn't vanish.
    stroke = 3.2 if size > 24 else 4.4
    m = stroke / 2
    d.rounded_rectangle(box(10 - m, 10 - m, 28 + stroke, 36 + stroke), radius=(6 + m) * u,
                        outline=ink, width=max(1, round(stroke * u)))
    d.rounded_rectangle(box(18, 5, 12, 7.5), radius=2.5 * u, fill=ink)
    # Front board: the background gap separates it from the back one.
    d.rounded_rectangle(box(20.5, 16.5, 37, 45), radius=8 * u, fill=background)
    d.rounded_rectangle(box(24, 21, 30, 38), radius=6 * u, fill=ink)
    d.rounded_rectangle(box(30, 15, 18, 12), radius=4 * u, fill=background)
    d.rounded_rectangle(box(32.5, 17.5, 13, 7), radius=2.5 * u, fill=ink)

    if badge:
        # Status dot with a ring in the background color so it stands apart from the mark and the taskbar.
        rp = 9 * u
        X, Y = 50 * u, 50 * u
        d.ellipse((X - rp - 3 * u, Y - rp - 3 * u, X + rp + 3 * u, Y + rp + 3 * u), fill=background)
        d.ellipse((X - rp, Y - rp, X + rp, Y + rp), fill=(*badge, 255))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def _draw(state):

    return draw_logo(size=64, badge=BADGES.get(state))

def ensure_ico(path: Path = ICO_PATH, force: bool = False) -> Path:

    """Builds a multi-resolution puente.ico (16..256 px)."""

    if force or not path.exists():

        try:

            base = draw_logo(size=256)

            base.save(

                path,

                format="ICO",

                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],

            )

        except Exception:

            pass

    return path

def enable_app_id():

    """Sets the AppUserModelID and attaches the current thread to the visible Windows desktop."""

    if sys.platform == "win32":

        try:

            import ctypes

            u = ctypes.windll.user32

            h = u.OpenDesktopW("Default", 0, False, 0x01FF)

            if h:

                u.SetThreadDesktop(h)

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CopyOver.Escritorio")

        except Exception:

            pass

# What any click or keyboard activation on the icon does (the quick view); None = the platform's native menu.
_to_left_click = None


def _enable_pystray_double_click():

    """Makes clicks and double clicks respond right away on Windows."""

    if sys.platform != "win32":

        return

    try:

        import ctypes

        import pystray._win32 as w32

        win32 = w32.win32

        if getattr(w32.Icon, '_bridge_menu_click', False):
            return

        def _reg_with_double_click(self):

            return win32.RegisterClassEx(

                win32.WNDCLASSEX(

                    cbSize=ctypes.sizeof(win32.WNDCLASSEX),

                    style=0x0008,  # CS_DBLCLKS

                    lpfnWndProc=w32._dispatcher,

                    cbClsExtra=0,

                    cbWndExtra=0,

                    hInstance=win32.GetModuleHandle(None),

                    hIcon=None,

                    hCursor=None,

                    hbrBackground=win32.COLOR_WINDOW + 1,

                    lpszMenuName=None,

                    lpszClassName="%s%dSystemTrayIcon" % (self.name, id(self)),

                    hIconSm=None,

                )

            )

        w32.Icon._register_class = _reg_with_double_click

        orig_on_notify = w32.Icon._on_notify

        def _on_notify_double_click(self, wparam, lparam):

            # Left click, double click, right click and the keyboard (NIN_SELECT, NIN_KEYSELECT) all open the
            # same quick view; without one, the native menu.
            menu_events = (0x0202, 0x0203, win32.WM_RBUTTONUP, 1024, 1025)
            if lparam in menu_events:
                if _to_left_click:
                    threading.Thread(target=_to_left_click, daemon=True).start()
                    return None
                return orig_on_notify(self, wparam, win32.WM_RBUTTONUP)

            return orig_on_notify(self, wparam, lparam)

        w32.Icon._on_notify = _on_notify_double_click
        w32.Icon._bridge_menu_click = True

    except Exception:

        pass

def without_ip(name):
    """«iPhone (192.168.1.35)» → «iPhone»: the IP means nothing to whoever reads the menu."""
    return re.sub(r"\s*\(\d{1,3}(\.\d{1,3}){3}\)$", "", name) if name else name


def show(

    state,

    name,

    open_folder,

    open_log,

    open_p2p_folder=None,

    send_phone=None,

    link_android=None,
    link_iphone=None,

    send_pc=None,

    connect_pc=None,

    manage_pcs=None,

    open_panel=None,

    open_preview=None,

    restart=None,

    automatic_startup=None,

    update=None,

):

    """Shows the icon and blocks until "Quit" is chosen.

    Returns False if the system has no tray, so the bridge keeps running without an icon. «actualizar» installs
    the new version by itself (only when CopyOver came from the installer); without it the menu opens the
    releases page.
    """

    enable_app_id()

    global _to_left_click
    _to_left_click = open_preview

    _enable_pystray_double_click()

    try:

        import pystray

    except Exception:

        return False

    ensure_ico(force=True)

    images = {k: _draw(k) for k in BADGES}

    def state_line(_=None):

        pc = state.get("pc_name")

        if state["announced"]:

            if pc:
                return t("Active on {pc}", pc=pc)
            return t("Active")

        return t("No network: the phone can’t find this PC")

    def phone_line(_=None):
        # The phone and the last thing that happened, on one line.
        phone = without_ip(state.get("phone_device"))
        if state["last"]:
            when, what = state["last"]
            last = f"{when:%H:%M} · {what}"
        else:
            last = t("nothing received yet")
        return f"{phone} · {last}" if phone else t("Phone: {last}", last=last)

    def has_pcs(_=None):
        return bool(state.get("pcs"))

    def pcs_line(_=None):

        pcs = state.get("pcs", [])

        if len(pcs) == 1:

            return t("Another PC nearby: {name}", name=pcs[0]['name'])

        return t("Other PCs nearby: {n}", n=len(pcs))

    def has_transfer(_=None):
        return bool(state.get("transfer"))

    def transfer_line(_=None):

        tr = state.get("transfer") or {}

        total = tr.get("total", 0)

        rec = tr.get("received_bytes", 0)

        pct = int((rec * 100) / total) if total > 0 else 100

        return t("Receiving: {file} ({pct}%)", file=tr.get('file', ''), pct=pct)

    def title():

        status_text = t("Active") if state["announced"] else t("No network")

        if state.get("transfer"):

            tr = state["transfer"]

            tot = tr.get("total", 0)

            pct = int((tr.get("received_bytes", 0) * 100) / tot) if tot > 0 else 100

            return f"CopyOver ({pct}%)\n{tr.get('file', '')}"[:63]

        phone = without_ip(state.get("phone_device"))

        if phone:

            return f"CopyOver: {status_text}\n{phone}"[:63]

        return f"CopyOver\n{status_text}"[:63]

    def in_thread(action):
        return lambda: threading.Thread(target=action, daemon=True).start()

    # Status: fixed lines on top; nearby PCs and the transfer line only when they apply.
    items_menu = [

        pystray.MenuItem(state_line, None, enabled=False),

        pystray.MenuItem(phone_line, None, enabled=False),

        pystray.MenuItem(pcs_line, None, enabled=False, visible=has_pcs),

        pystray.MenuItem(transfer_line, None, enabled=False, visible=has_transfer),

        pystray.Menu.SEPARATOR,

    ]

    # Explicit actions; clicking the icon only shows this menu.
    if open_panel:
        items_menu.append(pystray.MenuItem(t("Open panel"), in_thread(open_panel)))
    if manage_pcs:
        items_menu.append(pystray.MenuItem(t("Linked computers…"), in_thread(manage_pcs)))
    if send_phone:
        items_menu.append(pystray.MenuItem(t("Send to phone…"), in_thread(send_phone)))
    if send_pc:
        items_menu.append(pystray.MenuItem(t("Send to another PC…"), in_thread(send_pc)))

    link = []
    if link_iphone:
        link.append(pystray.MenuItem(t("iPhone or iPad…"), in_thread(link_iphone)))
    if link_android:
        link.append(pystray.MenuItem("Android…", in_thread(link_android)))
    if connect_pc:
        link.append(pystray.MenuItem(t("Another PC…"), in_thread(connect_pc)))
    if link:
        items_menu.append(pystray.MenuItem(t("Link"), pystray.Menu(*link)))

    folders = [pystray.MenuItem(t("Received from phone"), lambda: open_folder())]
    if open_p2p_folder:
        folders.append(pystray.MenuItem(t("Received from other PCs"), lambda: open_p2p_folder()))
    items_menu.append(pystray.MenuItem(t("Open folder"), pystray.Menu(*folders)))

    def version_line(_=None):
        if update:
            return t("Update to CopyOver {version}", version=state.get('new_version', ''))
        if sys.platform != "win32":
            return t("CopyOver {version} available…", version=state.get('new_version', ''))
        return t("Download CopyOver {version}…", version=state.get('new_version', ''))

    def active_startup():
        try:
            return bool(automatic_startup and automatic_startup[0]())
        except Exception:
            return False

    def toggle_startup():
        if automatic_startup:
            enable = not active_startup()
            threading.Thread(target=automatic_startup[1], args=(enable,), daemon=True).start()

    def open_releases():
        import webbrowser
        from updates import update_page
        webbrowser.open(update_page())

    def update_or_open_releases():
        # If the download or the check fails, the releases page is still there.
        try:
            if update and update():
                return
        except Exception:
            logging.exception("Couldn't install the new version")
        open_releases()

    items_menu.extend([

        pystray.MenuItem(version_line, in_thread(update_or_open_releases), visible=lambda _=None: bool(state.get("new_version"))),

        pystray.MenuItem(t("Start with Windows"), lambda: toggle_startup(), checked=lambda _=None: active_startup(),
                         visible=lambda _=None: bool(automatic_startup)),

        pystray.MenuItem(t("View log"), lambda: open_log()),

        pystray.Menu.SEPARATOR,

        pystray.MenuItem(t("Restart"), lambda icon: (restart(), icon.stop()), visible=lambda _=None: bool(restart)),

        pystray.MenuItem(t("Quit"), lambda icon: icon.stop()),

    ])

    icon = pystray.Icon(

        "copyover",

        images["active" if state["announced"] else "no_network"],

        title(),

        pystray.Menu(*items_menu),

    )

    def refresh():

        enable_app_id()

        while icon.visible:

            icon.icon = images["active" if state["announced"] else "no_network"]

            icon.title = title()

            icon.update_menu()

            time.sleep(2)

    def on_start(icon):

        enable_app_id()

        icon.visible = True

        threading.Thread(target=refresh, daemon=True).start()

    try:

        icon.run(setup=on_start)

    except Exception:

        return False

    return True

