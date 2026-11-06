"""Clipboard, notifications, context menu and autostart on Linux (Fedora with GNOME or KDE).

Uses wl-clipboard on Wayland and xclip on X11, notify-send for notifications and a systemd user service to
start with the session.
"""

import ast
import io
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from i18n import t

SERVICE = Path.home() / ".config/systemd/user/copyover.service"
# The name used up to 1.0.6: installing or uninstalling removes it, so it never runs twice.
OLD_SERVICE = Path.home() / ".config/systemd/user/puente-portapapeles.service"
NAUTILUS_SCRIPTS = Path.home() / ".local/share/nautilus/scripts"
KIO_SERVICEMENUS = Path.home() / ".local/share/kio/servicemenus"
# The panel window's app_id: GNOME and KDE look for <ID_APP>.desktop to show its icon in the dock.
ID_APP = "copyover"
APPS = Path.home() / ".local/share/applications"
APP_ICON = Path.home() / ".local/share/icons/hicolor/256x256/apps" / f"{ID_APP}.png"
TEXT_TYPES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING")
FILES_TYPES = ("x-special/gnome-copied-files", "text/uri-list")


def _user_folder(xdg_name, names):
    # On Fedora in Spanish the folders are called "Descargas" and "Documentos"; xdg-user-dir returns the home
    # folder when it isn't set.
    try:
        r = subprocess.run(["xdg-user-dir", xdg_name], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip() and Path(r.stdout.strip()) != Path.home():
            return Path(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    for name in names:
        if (Path.home() / name).is_dir():
            return Path.home() / name
    return Path.home() / names[-1]


DOWNLOADS = _user_folder("DOWNLOAD", ("Descargas", "Downloads"))
# Received files go in Documents unless another folder was chosen in Settings.
DOCUMENTS = _user_folder("DOCUMENTS", ("Documentos", "Documents"))


def _wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")) and shutil.which("wl-copy") is not None


def _copy(data, kind):
    if _wayland():
        cmd = ["wl-copy", "--type", kind]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard", "-t", kind, "-i"]
    else:
        raise RuntimeError(t("wl-clipboard (Wayland) or xclip (X11) is missing: sudo dnf install wl-clipboard"))
    # wl-copy and xclip stay in the background serving the clipboard; no output pipes so they don't block the
    # wait.
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        proc.communicate(data, timeout=10)
    except subprocess.TimeoutExpired:
        # On GNOME, wl-copy sometimes takes a while to let go of the terminal while it gets focus. It isn't
        # killed: killing it empties the clipboard.
        logging.warning("wl-copy is still waiting for the desktop; leaving it in the background")
        return
    if proc.returncode:
        raise RuntimeError(t("{program} failed with code {code}", program=cmd[0], code=proc.returncode))


def _available_types():
    if _wayland():
        cmd = ["wl-paste", "--list-types"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]
    else:
        return []
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _paste(kind):
    if _wayland():
        cmd = ["wl-paste", "--no-newline", "--type", kind]
    else:
        cmd = ["xclip", "-selection", "clipboard", "-t", kind, "-o"]
    r = subprocess.run(cmd, capture_output=True, timeout=10)
    return r.stdout if r.returncode == 0 else None


def set_text(text):
    _copy(text.encode("utf-8"), "text/plain;charset=utf-8")


def read_text():
    types = _available_types()
    for kind in TEXT_TYPES:
        if kind in types:
            data = _paste(kind)
            return data.decode("utf-8", errors="replace") if data else None
    return None


def read_files():
    """Paths of the files copied in Nautilus/Dolphin (Ctrl+C)."""
    types = _available_types()
    for kind in FILES_TYPES:
        if kind in types:
            data = _paste(kind) or b""
            return [Path(unquote(urlparse(line).path))
                    for line in data.decode("utf-8", errors="replace").splitlines()
                    if line.startswith("file://")]
    return []


def set_image(path):
    from PIL import Image

    # Linux apps expect image/png on the clipboard.
    buf = io.BytesIO()
    with Image.open(path) as img:
        img.save(buf, "PNG")
    _copy(buf.getvalue(), "image/png")
    return True


def read_image(path):
    """Saves the copied image to `ruta` as PNG, if there is one."""
    if "image/png" not in _available_types():
        return False
    data = _paste("image/png")
    if not data:
        return False
    path.write_bytes(data)
    return True


def set_files(paths):
    uris = [Path(r).resolve().as_uri() for r in paths]
    if "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        # Nautilus pastes files from this GNOME-specific format.
        _copy(("copy\n" + "\n".join(uris)).encode(), "x-special/gnome-copied-files")
    else:
        _copy(("\r\n".join(uris) + "\r\n").encode(), "text/uri-list")
    return True


def notify(title, text, link="", note="", file="", open_file=True):
    """notify-send can't show buttons without waiting; the link or file is opened from the panel's history."""
    if shutil.which("notify-send"):
        body = "\n".join(line for line in (text, note) if line)
        subprocess.Popen(["notify-send", "--app-name=CopyOver", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def serve_notice(code, action):
    """Linux notifications have no buttons."""
    return False


def open_stream(path):
    """Opens a file or folder with the desktop's default app."""
    if not Path(path).suffix:
        Path(path).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def highlight_in_explorer(path):
    """Opens the Linux file manager in the containing folder."""
    p = Path(path).resolve()
    dest = p.parent if p.is_file() else p
    open_stream(dest)


def to_trash(paths) -> bool:
    """Sends the files to the desktop trash (gio). Without gio nothing is deleted."""
    paths = [str(r) for r in paths]
    if not paths:
        return True
    if not shutil.which("gio"):
        return False
    try:
        return subprocess.run(["gio", "trash", "--", *paths], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --- Context menu (Nautilus and Dolphin) and autostart (systemd) ------------


NAUTILUS_NAMES = ("Send with CopyOver", "Enviar con CopyOver")


def install_context_menu(
    script,
    nautilus_folder: Path = NAUTILUS_SCRIPTS,
    kio_folder: Path = KIO_SERVICEMENUS,
):
    script = Path(script).resolve()
    exe = sys.executable

    nautilus_folder.mkdir(parents=True, exist_ok=True)
    # Nautilus shows the file's name, so it follows CopyOver's language; the other one is left over from a change.
    for name in NAUTILUS_NAMES:
        (nautilus_folder / name).unlink(missing_ok=True)
    nautilus_file = nautilus_folder / t("Send with CopyOver")
    nautilus_file.write_text(
        "#!/bin/sh\n"
        f'exec "{exe}" "{script}" --send "$@"\n',
        encoding="utf-8",
    )
    try:
        nautilus_file.chmod(0o755)
    except OSError:
        pass

    kio_folder.mkdir(parents=True, exist_ok=True)
    kio_file = kio_folder / "send-with-copyover.desktop"
    kio_file.write_text(
        "[Desktop Entry]\n"
        "Type=Service\n"
        "X-KDE-ServiceTypes=KonqPopupMenu/Plugin\n"
        "MimeType=application/octet-stream;\n"
        "Actions=SendWithCopyOver;\n"
        "\n"
        "[Desktop Action SendWithCopyOver]\n"
        f"Name={t('Send with CopyOver')}\n"
        "Icon=document-send\n"
        f'Exec="{exe}" "{script}" --send %F\n',
        encoding="utf-8",
    )
    try:
        kio_file.chmod(0o755)
    except OSError:
        pass


def remove_context_menu(
    nautilus_folder: Path = NAUTILUS_SCRIPTS,
    kio_folder: Path = KIO_SERVICEMENUS,
):
    for f in (*(nautilus_folder / name for name in NAUTILUS_NAMES), kio_folder / "send-with-copyover.desktop", kio_folder / "enviar-con-copyover.desktop",
              nautilus_folder / "Enviar con Puente", kio_folder / "enviar-con-puente.desktop"):
        if f.exists():
            f.unlink()


def install_app_icon(script, apps_folder: Path = APPS, icon: Path = APP_ICON):
    """Entry in the applications menu; it also gives the panel window its dock icon. If CopyOver is already
    running, launching it again only shows the panel."""
    from tray import draw_logo

    icon.parent.mkdir(parents=True, exist_ok=True)
    draw_logo(size=256).save(icon, format="PNG")
    apps_folder.mkdir(parents=True, exist_ok=True)
    (apps_folder / f"{ID_APP}.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=CopyOver\n"
        f"Comment={t('Clipboard between your phone and your PC')}\n"
        f"Icon={icon}\n"
        f'Exec="{sys.executable}" "{Path(script).resolve()}"\n'
        "Terminal=false\n"
        "Categories=Utility;\n"
        f"StartupWMClass={ID_APP}\n",
        encoding="utf-8",
    )
    _update_menu(apps_folder)


def remove_app_icon(apps_folder: Path = APPS, icon: Path = APP_ICON):
    for f in (apps_folder / f"{ID_APP}.desktop", icon):
        if f.exists():
            f.unlink()
    _update_menu(apps_folder)


def _update_menu(apps_folder: Path = APPS):
    # Most menus watch the folder on their own; this is for the ones that read the cache.
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(["update-desktop-database", str(apps_folder)], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _desktop_folder():
    """The desktop folder («Escritorio» in Spanish), or None when there is none."""
    try:
        r = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip() and Path(r.stdout.strip()) != Path.home():
            return Path(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    for name in ("Escritorio", "Desktop"):
        if (Path.home() / name).is_dir():
            return Path.home() / name
    return None


def install_desktop_access(origin: Path, folder: Path | None = None):
    """A CopyOver icon on the desktop (Ubuntu, KDE, Cinnamon, XFCE). Fedora's GNOME shows no desktop icons:
    there it only stays in the folder."""
    folder = folder or _desktop_folder()
    if folder is None or not folder.is_dir() or not origin.exists():
        return None
    destination = folder / f"{ID_APP}.desktop"
    shutil.copyfile(origin, destination)
    destination.chmod(0o755)
    # Ubuntu's desktop icons only open launchers marked as trusted; otherwise they ask first.
    if shutil.which("gio"):
        try:
            subprocess.run(["gio", "set", str(destination), "metadata::trusted", "true"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return destination


def remove_desktop_access(folder: Path | None = None):
    folder = folder or _desktop_folder()
    if folder is not None and (folder / f"{ID_APP}.desktop").exists():
        (folder / f"{ID_APP}.desktop").unlink()


FAVORITES = ("org.gnome.shell", "favorite-apps")


def _read_favorites():
    """The apps pinned to the GNOME dock (Fedora and Ubuntu), or None outside GNOME."""
    if not shutil.which("gsettings"):
        return None
    try:
        r = subprocess.run(["gsettings", "get", *FAVORITES], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = r.stdout.strip()
    if r.returncode != 0 or not text:
        return None
    if text.startswith("@as "):  # an empty list comes as «@as []»
        text = text[4:]
    try:
        items = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(items, (list, tuple)) or not all(isinstance(x, str) for x in items):
        return None
    return list(items)


def _write_favorites(items):
    value = "[" + ", ".join(repr(x) for x in items) + "]"
    try:
        subprocess.run(["gsettings", "set", *FAVORITES, value], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def pin_in_dock(read=_read_favorites, write=_write_favorites):
    """Pins CopyOver at the end of the GNOME dock. Returns False when there is no GNOME dock."""
    items = read()
    if items is None:
        return False
    if f"{ID_APP}.desktop" not in items:
        write(items + [f"{ID_APP}.desktop"])
    return True


def remove_from_dock(read=_read_favorites, write=_write_favorites):
    items = read()
    if items and f"{ID_APP}.desktop" in items:
        write([x for x in items if x != f"{ID_APP}.desktop"])


def _remove_old_service():
    if OLD_SERVICE.exists():
        subprocess.run(["systemctl", "--user", "disable", "--now", OLD_SERVICE.name])
        OLD_SERVICE.unlink()


def install(script):
    script = Path(script).resolve()
    _remove_old_service()
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(
        "[Unit]\n"
        "Description=CopyOver (clipboard between your phone and your PC)\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n"
        "\n"
        "[Service]\n"
        f'ExecStart={sys.executable} "{script}"\n'
        f"WorkingDirectory={script.parent}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE.name], check=True)
    install_context_menu(script)
    install_app_icon(script)
    install_desktop_access(APPS / f"{ID_APP}.desktop")
    pin_in_dock()
    print(t("Done: CopyOver will start automatically when you sign in and has the “Send with CopyOver” right-click option.") + f"\n  {SERVICE}")
    print(t("CopyOver is now in the applications menu, on the desktop and pinned to the dock."))
    print(t("Status:") + " systemctl --user status copyover")


def uninstall():
    remove_context_menu()
    remove_from_dock()
    remove_desktop_access()
    remove_app_icon()
    _remove_old_service()
    if not SERVICE.exists():
        print(t("It wasn’t installed."))
        return
    subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE.name])
    SERVICE.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    print(t("Startup entry and context menu removed."))


def start_global_shortcut(on_enable):
    """Global shortcut not implemented on Linux."""
    return None
