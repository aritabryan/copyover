"""CopyOver: a shared clipboard between iPhone/iPad, Android and the PC (Windows or Linux).

Usage:
    python copyover.py                       start the server (with a window)
    pythonw copyover.py                      start the server in the background (Windows)
    python copyover.py --info                show the URLs and the key for the shortcuts
    python copyover.py --link                explain how to link Android over the local network
    python copyover.py --send <files...>     send files to another computer with CopyOver
    python copyover.py --send --to <pc> ...  send straight to a computer, by name or IP
    python copyover.py --pair [computer]     pair with another computer using a 6-digit code
    python copyover.py --install             start on login and add the right-click menu
    python copyover.py --uninstall           remove the autostart and the right-click menu
"""

import atexit
import errno
import hmac
import ipaddress
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.parse
from urllib.parse import quote, urlsplit, parse_qs

import private_files
import dialogs
import tray_actions
import bundle
import links
import configuration
from configuration import NEW_NETWORK_NAME, load_config, save_config, modify_config, modify_devices  # noqa: F401
import devices
import p2p
import linked_pcs
import network
import wifi_direct
import panel_routes
import receiving
import rich_text
import local_secret
import i18n
from i18n import t
from wire_keys import OLD_HEADERS, from_wire, query_from_old, route_from_old, route_to_old, to_wire
from version import APP_NAME, VERSION

_CONFIG_LOCK = configuration.LOCK
# The panel routes read this module's state (which may be __main__).
panel_routes.bridge = sys.modules[__name__]
_PC_SENDS = {}

dialogs._ensure_visible_desktop()

if sys.platform == "win32":
    import windows_system as so
else:
    import linux_system as so

FOLDER = bundle.data_folder()
LOG = FOLDER / "copyover.log"
OLD_LOG = "puente.log"  # name before 1.0.7; renamed (with its rotated copies) when logging starts

# Everything received goes here. What your own phones send lands in it directly; another PC and guests
# get a folder of their own inside it.
RECEIVED_FOLDER = "From CopyOver"
SUBFOLDERS = {"p2p": "From another PC", "guests": "From guests"}
# Folders used by earlier versions. They stay where they are so their files and history entries keep working,
# and the cleanup still covers them. The Spanish ones became the English ones in 1.0.7.
SPANISH_FOLDERS = {"Desde iPhone": "From iPhone", "Desde otra PC": "From another PC", "Desde Puente": "From another PC",
                   "Desde Invitados": "From guests"}
OLD_FOLDERS = (*dict.fromkeys(SPANISH_FOLDERS.values()), *SPANISH_FOLDERS)


def get_downloads_folder(kind: str = "received") -> Path:
    """The folder for received files; «root» is the one that holds From CopyOver: Documents, or the folder
    chosen in Settings."""
    custom = load_config().get("download_folder", "").strip()
    base = Path(custom) if custom and Path(custom).is_dir() else so.DOCUMENTS
    folder = base if kind == "root" else base / RECEIVED_FOLDER / SUBFOLDERS.get(kind, "")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def temporary() -> Path:
    """Where the clipboard image and the zips for the phone are left: a folder of this user only (on Linux
    a fixed name in the shared /tmp could be taken by someone else first)."""
    return private_files.temporary_subfolder("puente_portapapeles")


MAX_BYTES = 500 * 1024 * 1024
MAX_BYTES_WEB = 10 * 1024 * 1024 * 1024  # 10 GB by streaming in the web app
MAX_IMAGE_IN_RAM = 200 * 1024 * 1024  # largest photo that gets converted (HEIC to JPG); anything bigger is saved as is

STATE = {
    "ip": None,
    "announced": False,
    "repeated_name": False,  # another PC on the network also answers to <nombre>.local
    "last": None,
    "pcs": [],
    "transfer": None,
    "apple_devices": {},
    "phone_ip": None,
    "phone_device": None,
    "phone_name": "My iPhone",
    "ipad_name": "My iPad",
    "history": [],
    "guest_tokens": {},  # links handed to other people with «Invite» (the QR in the Guests tab)
    "phone_tokens": {},  # links for the owner's own phone (send to iPhone/iPad, Wi-Fi Direct)
    "qr_tokens": {},
    "show_window": False,
    "window_alive": 0,
    "prepared_files": [],
    "prepared_on_clipboard": None,  # the panel's files, once they're also on the PC clipboard
    "phone_offer": None,
    "wifi_direct": {"active": False, "ssid": "", "key": "", "ip": "192.168.137.1"},
}

_PROC_WIFI_DIRECT = None
_LOCK_WIFI_DIRECT = threading.Lock()
_HISTORY_LOCK = threading.Lock()
_LOCK_CONFIG = _CONFIG_LOCK
_LOCK_SELECTOR = threading.Lock()
MAX_HISTORY = 40  # not counting pinned ones, which aren't trimmed


HISTORY = FOLDER / "history.json"
OLD_HISTORY = "historial.json"  # name before 1.0.7, next to the new one
# Entry keys written before 1.0.7. They're renamed when the history is loaded.
HISTORY_OLD_KEYS = {
    "hora": "time", "fecha": "date", "momento": "timestamp", "tipo": "type", "direccion": "direction",
    "origen": "origin", "detalle": "detail", "nombre": "name", "tamano": "size", "tamano_texto": "size_text",
    "ruta": "path", "enlace": "link", "fijado": "pinned",
}
HISTORY_OLD_KINDS = {"envio": "sent", "recepcion": "received"}
KEEP_OPTIONS = (0, 1, 7, 30)  # days; 0 = forever


# The iPhone shortcut sends each photo in its own request: files arriving back to back give a single
# notification.
NOTICE_WAIT = 3.0
_PENDING_NOTICES: list = []
_NOTICES_LOCK = threading.Lock()
_notice_timer = None


def notify_received(paths, origin: str = "") -> None:
    """Notification for files received from `origen` (the device or PC name; empty if unknown)."""
    global _notice_timer
    with _NOTICES_LOCK:
        _PENDING_NOTICES.append(([Path(r) for r in paths], origin))
        if _notice_timer:
            _notice_timer.cancel()
        _notice_timer = threading.Timer(NOTICE_WAIT, release_notices)
        _notice_timer.daemon = True
        _notice_timer.start()


def received_texts(paths: list[Path], origin: str) -> tuple[str, str]:
    """Notification title and text: «Photo from Ana's iPhone» / «3 files from Office» and the names."""
    if len(paths) == 1:
        is_photo = paths[0].suffix.lower() in IMAGE_EXTENSIONS
        if is_photo:
            title = t("Photo from {source}", source=origin) if origin else t("Photo received")
        else:
            title = t("File from {source}", source=origin) if origin else t("File received")
        return title, paths[0].name
    summary = ", ".join(r.name for r in paths[:3])
    if len(paths) > 3:
        summary = t("{names} and {count} more", names=summary, count=len(paths) - 3)
    if origin:
        return t("{count} files from {source}", count=len(paths), source=origin), summary
    return t("{count} files received", count=len(paths)), summary


def release_notices() -> None:
    with _NOTICES_LOCK:
        batch = list(_PENDING_NOTICES)
        _PENDING_NOTICES.clear()
    if not batch:
        return
    paths = [path for from_batch, _ in batch for path in from_batch]
    origins = {origin for _, origin in batch}
    title, text = received_texts(paths, origins.pop() if len(origins) == 1 else "")
    # A single file opens from the notification; with several, the first one is shown in its folder.
    so.notify(title, text, note=t("It’s on the clipboard: paste it with Ctrl+V"),
              file=paths[0], open_file=len(paths) == 1)


def crop_history(entries: list) -> list:
    """All pinned entries stay; of the rest, the MAX_HISTORY most recent. Keeps the order."""
    loose = 0
    remain = []
    for entry in entries:
        if entry.get("pinned"):
            remain.append(entry)
        elif loose < MAX_HISTORY:
            remain.append(entry)
            loose += 1
    return remain


def sorted_history(entries: list) -> list:
    """Pinned first; within each group, newest to oldest (the list order)."""
    return [e for e in entries if e.get("pinned")] + [e for e in entries if not e.get("pinned")]


def pin_history(id_item: str, pinned: bool) -> bool:
    """Pins or unpins an entry. Returns False if it doesn't exist."""
    with _HISTORY_LOCK:
        entry = next((h for h in STATE["history"] if h.get("id") == id_item), None)
        if entry is None:
            return False
        if pinned:
            entry["pinned"] = True
        else:
            entry.pop("pinned", None)
            STATE["history"] = crop_history(STATE["history"])
        _save_history()
    return True


def delete_history(id_item: str) -> bool:
    """Removes one entry without touching the file or the system clipboard."""
    with _HISTORY_LOCK:
        entries = STATE["history"]
        remaining = [entry for entry in entries if entry.get("id") != id_item]
        if len(remaining) == len(entries):
            return False
        STATE["history"] = remaining
        _save_history()
    return True


def empty_history() -> int:
    """Removes everything that isn't pinned from the list (files are left alone). Returns how many remain."""
    with _HISTORY_LOCK:
        STATE["history"] = [e for e in STATE["history"] if e.get("pinned")]
        _save_history()
        return len(STATE["history"])


def notify_text(text: str, link: str = "", origin: str = "") -> None:
    """Notification for received text; if it's an http(s) link, it offers «Open» and tapping it opens it."""
    if not load_config().get("clipboard_notifications", False):
        return
    # The title says it's already copied; the body is only what arrived, so nothing reads as part of the text.
    if link:
        title = t("Link copied from {source}", source=origin) if origin else t("Link copied")
        so.notify(title, link[:80], link=link)
    else:
        title = t("Copied from {source}", source=origin) if origin else t("Text copied")
        so.notify(title, text[:80])


def register_history(kind: str, origin: str, detail: str, size: int = 0, local_path: str = "", link: str = ""):
    entry = {
        "id": uuid.uuid4().hex[:10],
        "time": datetime.now().strftime("%H:%M"),
        "date": datetime.now().strftime("%d/%m"),
        "timestamp": time.time(),
        "type": kind,
        "direction": kind,
        "origin": origin,
        "detail": detail,
        "name": detail,
        "size": size,
        "size_text": p2p.format_size(size) if size else "",
        "path": str(local_path) if local_path else "",
    }
    link = links.web_link(link)
    if link:
        entry["link"] = link  # full: the detail is cut at 40 characters
    with _HISTORY_LOCK:
        STATE["history"].insert(0, entry)
        STATE["history"] = crop_history(STATE["history"])
        _save_history()


def _save_history():
    """Called with _HISTORY_LOCK held. The list survives restarts. Only writes after load_history():
    that way the tests, which don't start the server, don't touch the real file."""
    if not STATE.get("history_persistent"):
        return
    try:
        configuration.write_json(HISTORY, STATE["history"])
    except OSError:
        logging.exception("Couldn't save the history")


def _paths_in_old_folders(entries: list) -> bool:
    """Entries whose file was in a download folder with its old name and now is in the renamed one."""
    change = False
    for entry in entries:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not path or Path(path).exists():
            continue
        parts = Path(path).parts
        for i, part in enumerate(parts):
            if part in SPANISH_FOLDERS:
                moved = Path(*parts[:i], SPANISH_FOLDERS[part], *parts[i + 1:])
                if moved.exists():
                    entry["path"] = str(moved)
                    change = True
                break
    return change


def load_history():
    configuration.move_if_missing(HISTORY.with_name(OLD_HISTORY), HISTORY)
    migrated = False
    try:
        entries = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
        if not isinstance(entries, list):
            raise ValueError("not a list")
    except OSError:
        logging.exception("Couldn't read the history")
        entries = []
    except ValueError as error:
        configuration.back_up_corrupt(HISTORY, error)
        entries = []
    for entry in entries:
        if isinstance(entry, dict):
            migrated |= configuration.rename_keys(entry, HISTORY_OLD_KEYS)
            for field in ("type", "direction"):
                if entry.get(field) in HISTORY_OLD_KINDS:
                    entry[field] = HISTORY_OLD_KINDS[entry[field]]
                    migrated = True
    migrated |= _paths_in_old_folders(entries)
    with _HISTORY_LOCK:
        STATE["history"] = crop_history([e for e in entries if isinstance(e, dict) and e.get("id")])
        STATE["history_persistent"] = True
        if migrated:
            _save_history()


def days_to_keep(cfg=None) -> int:
    days = (cfg or load_config()).get("keep_days", 0)
    return days if days in KEEP_OPTIONS else 0


def clean_old_received(now: float | None = None) -> int:
    """Moves to the Recycle Bin what was received more than «keep_days» days ago, only inside CopyOver's
    own folders (From CopyOver and the ones from earlier versions), and removes those history entries. Returns
    how many it removed. If the Recycle Bin isn't available nothing is deleted: a file is never lost for
    good."""
    days = days_to_keep()
    if not days:
        return 0
    limit = (now or time.time()) - days * 86400
    with _HISTORY_LOCK:
        pinned = set()
        for entry in STATE["history"]:
            if entry.get("pinned"):
                pinned |= _input_paths(entry)
    old = []
    folders = [get_downloads_folder()]
    # Earlier versions saved in Downloads, or in the folder chosen in Settings.
    for root in dict.fromkeys((get_downloads_folder("root"), so.DOWNLOADS)):
        folders += [root / name for name in OLD_FOLDERS if (root / name).is_dir()]
    for folder in dict.fromkeys(folders):
        for file in folder.rglob("*"):
            try:
                if file.is_file() and file.stat().st_mtime < limit and _path_key(file) not in pinned:
                    old.append(file)
            except OSError:
                pass
    if old and not so.to_trash(old):
        logging.warning("Cleanup: couldn't move the old files to the Recycle Bin; they stay where they are")
    deleted = sum(1 for file in old if not file.exists())
    with _HISTORY_LOCK:
        before = len(STATE["history"])
        STATE["history"] = [e for e in STATE["history"]
                               if e.get("pinned") or e.get("timestamp", limit) >= limit
                               or (e.get("path") and Path(e["path"]).exists())]
        if len(STATE["history"]) != before:
            _save_history()
    if deleted:
        logging.info("Cleanup: %d files received more than %d days ago, moved to the Recycle Bin", deleted, days)
    return deleted


def _path_key(path) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(str(path))


def _input_paths(entry: dict) -> set:
    """The files of an entry: its «path» and, if several arrived together («a.jpg, b.jpg»), the others from
    the same folder (the history stores the first one's path and everyone's names)."""
    if not entry.get("path"):
        return set()
    path = Path(entry["path"])
    paths = {_path_key(path)}
    for name in str(entry.get("detail") or "").split(", "):
        if name and Path(name).name == name:
            paths.add(_path_key(path.parent / name))
    return paths


def _full_disk(error: BaseException) -> bool:
    """The error is «no space left» (ENOSPC on Linux; ERROR_DISK_FULL or ERROR_HANDLE_DISK_FULL on Windows)."""
    return isinstance(error, OSError) and (error.errno == errno.ENOSPC
                                           or getattr(error, "winerror", None) in (39, 112))


def _clean_periodically():
    while True:
        try:
            clean_old_received()
        except Exception:
            logging.exception("Cleaning up received files failed")
        for kind in ("iphone", "guests"):
            try:
                receiving.delete_old_temporary(get_downloads_folder(kind))
            except Exception:
                logging.exception("Cleaning up temporary files failed")
        time.sleep(3600)


def qr_as_svg(text: str) -> str:
    try:
        import qrcode

        qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(text)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix)
        strokes = "".join(
            f"M{x},{y}h1v1h-1z" for y, row in enumerate(matrix) for x, cell in enumerate(row) if cell
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{strokes}" fill="#0b1120"/></svg>'
        )
    except Exception:
        return ""


GUEST_VALIDITY = 1800  # a guest link lasts 30 minutes, renewed only while its page stays open
MOBILE_VALIDITY = 7200  # the owner's own phone link
APK_VALIDITY = 1800  # the QR to download the Android app from the PC
RENEW_MOBILE = 3600


def _clean_tokens(name: str) -> dict:
    tokens = STATE.setdefault(name, {})
    now = time.time()
    for token, expires in list(tokens.items()):
        if expires <= now:
            tokens.pop(token, None)
    return tokens


def clean_guest_tokens() -> dict:
    """Removes expired guest links (they used to pile up while CopyOver stayed open), and the guests that
    came with them leave the device list."""
    tokens = _clean_tokens("guest_tokens")
    mapping = STATE.get("apple_devices", {})
    for map_key in [k for k, d in mapping.items() if isinstance(d, dict) and d.get("guest") and d["guest"] not in tokens]:
        mapping.pop(map_key, None)
    numbers = STATE.setdefault("guest_numbers", {})
    for token in [k for k in numbers if k not in tokens]:
        numbers.pop(token)
    return tokens


def guest_number(token: str) -> int:
    """Guests are called «Guest 1», «Guest 2»…: a web page can't know the device's name, and guessing
    the model by screen size often got it wrong. Each link keeps its number; a new one takes the lowest free."""
    clean_guest_tokens()  # numbers of links that are gone become free
    numbers = STATE.setdefault("guest_numbers", {})
    if token not in numbers:
        used = set(numbers.values())
        numbers[token] = next(n for n in range(1, len(used) + 2) if n not in used)
    return numbers[token]


def active_guests() -> list[dict]:
    """Guests with a live link, for the panel's countdown: name and when the link runs out."""
    tokens = clean_guest_tokens()
    return [{"name": d.get("name", ""), "type": d.get("type", ""), "expires": tokens[d["guest"]]}
            for d in STATE.get("apple_devices", {}).values() if isinstance(d, dict) and d.get("guest")]


def clean_mobile_tokens() -> dict:
    return _clean_tokens("phone_tokens")


def web_token_type(token: str) -> str | None:
    """«guest» or «mobile» if the web page token is still valid, None otherwise."""
    if not token:
        return None
    now = time.time()
    for kind, name in (("guest", "guest_tokens"), ("mobile", "phone_tokens")):
        if now < STATE.get(name, {}).get(token, 0):
            return kind
    return None


def renew_web_token(token: str) -> None:
    """The web page is still open, so its link keeps working. A guest link gets 30 more minutes from now,
    never more; the owner's phone link is never shortened."""
    now = time.time()
    kind = web_token_type(token)
    if kind == "guest":
        STATE["guest_tokens"][token] = now + GUEST_VALIDITY
    elif kind == "mobile":
        tokens = STATE["phone_tokens"]
        tokens[token] = max(tokens.get(token, 0), now + RENEW_MOBILE)


def guest_destination(ip: str) -> str:
    """How the panel addresses one guest's card: what's sent to the owner's own phones never matches it."""
    return f"guest:{ip}"


def offer_for_guest(token: str) -> dict | None:
    """What a guest may receive: only what the panel sent to that guest's own card, after its link was made.
    Never the PC's clipboard, nor what goes to the owner's iPhone, iPad or Android."""
    offer = STATE.get("phone_offer")
    since = STATE.get("guests_since", {}).get(token)
    if not offer or since is None or offer.get("ts", 0) < since:
        return None
    ips = {d.get("ip") for d in STATE.get("apple_devices", {}).values() if isinstance(d, dict) and d.get("guest") == token}
    return offer if offer.get("destination") in {guest_destination(ip) for ip in ips if ip} else None


def cut_guests() -> int:
    """Revokes every guest link right away. The owner's phone links keep working. Returns how many were cut."""
    tokens = clean_guest_tokens()
    count = len(tokens)
    tokens.clear()
    clean_guest_tokens()  # their cards leave the panel too
    if count:
        logging.info("Cut off access for %d guest link(s)", count)
    return count


def _get_or_create_mobile_token(preferred_ip: str | None = None, port: int = 8765) -> dict:
    # Only the owner's own links are reused: a guest link is never handed to the owner's phone, nor extended
    # by what the owner does.
    now = time.time()
    tokens = clean_mobile_tokens()
    reusable_token = None
    for tk_val, exp in list(tokens.items()):
        if exp - now > 600:
            reusable_token = tk_val
            break
    if not reusable_token:
        reusable_token = secrets.token_urlsafe(10)
    tokens[reusable_token] = now + MOBILE_VALIDITY
    ip_srv = preferred_ip or (
        "192.168.137.1" if STATE.get("wifi_direct", {}).get("active") else (STATE.get("ip") or ip_local())
    )
    url = f"http://{ip_srv}:{port}/guest?t={reusable_token}"
    return {"token": reusable_token, "url": url, "qr_svg": qr_as_svg(url), "ip": ip_srv}


def linked_to_choose(token: str) -> list | None:
    """The owner's linked devices, for «Which one are you?» on the web page. Only an owner link gets the list:
    a guest never sees which devices the PC has."""
    if web_token_type(token) != "mobile":
        return None
    registry = load_config().get("devices", {})
    return [{"id": device_id, "name": data.get("name") or devices.default_name(data.get("type", "mobile")),
             "type": data.get("type", "mobile")}
            for device_id, data in registry.items() if isinstance(data, dict) and not devices.unused(data)]


def associate_web_to_linked(token: str, device_id: str, ip_cli: str, action: str = "In the web app") -> bool:
    """The owner said this web page is one of their linked devices (Safari has no key and, on Wi-Fi Direct, a
    different IP): its card takes this IP, so the requests without a key from here land on it and not on a new
    generic «iPhone» card."""
    if web_token_type(token) != "mobile" or not ip_cli or ip_cli in ("127.0.0.1", "::1") or is_this_pc(ip_cli):
        return False
    current_cfg = load_config()
    data = current_cfg.get("devices", {}).get(device_id)
    if not isinstance(data, dict) or devices.unused(data):
        return False
    state_map = STATE.setdefault("apple_devices", {})
    for old_key, info_v in list(state_map.items()):
        if info_v.get("ip") != ip_cli or old_key == device_id:
            continue
        if not info_v.get("id") and not info_v.get("guest"):
            state_map.pop(old_key, None)  # the generic card this page created before choosing
        elif info_v.get("id"):
            info_v["ip"] = ""  # another linked device's stale IP: it would win the match by IP
    str_time = datetime.now().strftime("%H:%M")
    entry = state_map.get(device_id) or {}
    entry.update({
        "id": device_id,
        "ip": ip_cli,
        "pc_network": network_signature(),
        "name": data.get("name") or devices.default_name(data.get("type", "mobile")),
        "type": data.get("type", "mobile"),
        "timestamp": time.time(),
        "last_seen": str_time,
        "last_event": f"{action} at {str_time}",
        "linked": True,
        "online": True,
        "repeats": _repeated_info(current_cfg.get("devices", {}), data),
    })
    state_map[device_id] = entry
    if ip_cli in current_cfg.get("apple_devices", {}):
        modify_config(lambda cfg: cfg.get("apple_devices", {}).pop(ip_cli, None))
    return True


def start_wifi_direct() -> dict:
    global _PROC_WIFI_DIRECT
    if sys.platform != "win32":
        return {"ok": False, "message": t("Wi-Fi Direct is only available on Windows 10/11")}
    with _LOCK_WIFI_DIRECT:
        cfg = load_config()
        port = int(cfg.get("port", 8765))
        ssid = cfg.get("wifi_direct_ssid", "").strip()
        wfd_key = cfg.get("wifi_direct_password", "").strip()
        # If it was saved with the app's old name, it's redone.
        if not ssid.startswith(f"{APP_NAME}-"):
            suffix = re.sub(r"[^a-zA-Z0-9]", "", socket.gethostname())[:6] or "PC"
            ssid = f"{APP_NAME}-{suffix}"
            cfg["wifi_direct_ssid"] = ssid
        if len(wfd_key) < 16:
            wfd_key = secrets.token_urlsafe(12)
            cfg["wifi_direct_password"] = wfd_key
        save_config(cfg)

        if _PROC_WIFI_DIRECT is not None and _PROC_WIFI_DIRECT.poll() is None and STATE["wifi_direct"].get("active"):
            ip_wfd = STATE["wifi_direct"].get("ip", "192.168.137.1")
            app_data = _get_or_create_mobile_token(ip_wfd, port)
            qr_wifi = qr_as_svg(f"WIFI:T:WPA;S:{ssid};P:{wfd_key};;")
            return {
                "ok": True,
                "active": True,
                "ssid": ssid,
                "key": wfd_key,
                "ip": ip_wfd,
                "qr_wifi_svg": qr_wifi,
                "app_url": app_data["url"],
                "qr_app_svg": app_data["qr_svg"],
            }

        stop_wifi_direct()
        try:
            proc = subprocess.Popen(
                wifi_direct.command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=wifi_direct.environment(ssid, wfd_key),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            line = ""
            while proc.stdout:
                line = proc.stdout.readline()
                if not line or wifi_direct.is_result(line):
                    break
                logging.info("Wi-Fi Direct: %s", line.strip())
            line = line.strip()
            mode, message = wifi_direct.parse(line)
            if mode:
                _PROC_WIFI_DIRECT = proc
                ip_wfd = "192.168.137.1"
                STATE["wifi_direct"] = {"active": True, "ssid": ssid, "key": wfd_key, "ip": ip_wfd}
                logging.info("CopyOver network %s active (%s)", ssid, "mobile hotspot" if mode == "hotspot" else "Wi-Fi Direct")
                threading.Thread(target=_follow_wifi_direct, args=(proc,), daemon=True).start()
                app_data = _get_or_create_mobile_token(ip_wfd, port)
                qr_wifi = qr_as_svg(f"WIFI:T:WPA;S:{ssid};P:{wfd_key};;")
                return {
                    "ok": True,
                    "active": True,
                    "ssid": ssid,
                    "key": wfd_key,
                    "ip": ip_wfd,
                    "qr_wifi_svg": qr_wifi,
                    "app_url": app_data["url"],
                    "qr_app_svg": app_data["qr_svg"],
                }
            logging.warning("Couldn't start Wi-Fi Direct: %s", line or "no response")
            _close_wifi_direct_process(proc)
            return {"ok": False, "active": False, "message": message}
        except Exception as e:
            return {"ok": False, "active": False, "message": str(e)}


def _follow_wifi_direct(proc):
    """Log what the script says while the network is up (phones joining and leaving), and mark it off if the
    network ends on its own (Wi-Fi turned off, hotspot timed out)."""
    try:
        for line in iter(proc.stdout.readline, ""):
            logging.info("Wi-Fi Direct: %s", line.strip())
    except Exception:
        pass
    with _LOCK_WIFI_DIRECT:
        if _PROC_WIFI_DIRECT is proc:
            _close_wifi_direct_process(proc)
            _clean_wifi_direct_state()


def _close_wifi_direct_process(proc, wait: float = 8.0):
    # Asked to stop through stdin first, so the hotspot (if that's what's running) is turned off and the user's
    # settings put back; only killed if it doesn't end in time.
    try:
        if proc.stdin:
            proc.stdin.write("STOP\n")
            proc.stdin.flush()
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=wait)
        return
    except Exception:
        pass
    for action in (proc.terminate, proc.kill):
        try:
            action()
            proc.wait(timeout=1.5)
            return
        except Exception:
            pass


def _clean_wifi_direct_state():
    global _PROC_WIFI_DIRECT
    _PROC_WIFI_DIRECT = None
    STATE["wifi_direct"] = {"active": False, "ssid": "", "key": "", "ip": "192.168.137.1"}


def stop_wifi_direct() -> dict:
    proc = _PROC_WIFI_DIRECT
    _clean_wifi_direct_state()
    if proc is not None:
        _close_wifi_direct_process(proc)
    remove_cards_without_wifi_direct_key()
    return {"ok": True, "active": False}


WIFI_DIRECT_SUBNET = ipaddress.ip_network("192.168.137.0/24")


def _in_wifi_direct(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in WIFI_DIRECT_SUBNET
    except ValueError:
        return False


def remove_cards_without_wifi_direct_key() -> int:
    """With Wi-Fi Direct off, cards of guests and keyless web pages on its network can't come back: they're
    removed. Linked devices (own key) stay and go offline through the usual check."""
    mapping = STATE.get("apple_devices", {})
    removed = [k for k, d in list(mapping.items())
                if isinstance(d, dict) and not d.get("id") and _in_wifi_direct(str(d.get("ip") or ""))]
    for key in removed:
        mapping.pop(key, None)
    return len(removed)


WIFI_DIRECT_SSID_PREFIX = f"{APP_NAME}-"
_WIFI_DIRECT_SUFFIX = re.compile(r"[A-Za-z0-9-]{1,20}")


def _saved_wifi_direct_ssid(cfg: dict) -> str:
    ssid = (cfg.get("wifi_direct_ssid") or "").strip()
    if ssid.startswith(WIFI_DIRECT_SSID_PREFIX):
        return ssid
    return WIFI_DIRECT_SSID_PREFIX + (re.sub(r"[^a-zA-Z0-9]", "", socket.gethostname())[:6] or "PC")


def wifi_direct_suffix_error(suffix: str) -> str | None:
    """Why the network name suffix isn't valid, or None if it is."""
    if not _WIFI_DIRECT_SUFFIX.fullmatch(suffix or ""):
        return t("The network name can only have letters, numbers and hyphens (1 to 20).")
    if len((WIFI_DIRECT_SSID_PREFIX + suffix).encode("utf-8")) > 32:
        return t("The network name is too long.")
    return None


def wifi_direct_network_data() -> dict:
    cfg = load_config()
    ssid = _saved_wifi_direct_ssid(cfg)
    return {"prefix": WIFI_DIRECT_SSID_PREFIX, "suffix": ssid[len(WIFI_DIRECT_SSID_PREFIX):], "ssid": ssid}


def change_wifi_direct_network(suffix: str | None = None, new_key: bool = False) -> dict:
    """Renames the Wi-Fi Direct network and/or gives it a new password. If it's on, it's restarted with them:
    devices that saved the old network have to scan the QR again."""
    if suffix is not None:
        suffix = suffix.strip()
        error = wifi_direct_suffix_error(suffix)
        if error:
            return {"ok": False, "invalid": True, "message": error}
    with _LOCK_WIFI_DIRECT:
        cfg = load_config()
        ssid_before = _saved_wifi_direct_ssid(cfg)
        ssid = WIFI_DIRECT_SSID_PREFIX + suffix if suffix is not None else ssid_before
        change = ssid != ssid_before or ssid != cfg.get("wifi_direct_ssid")
        cfg["wifi_direct_ssid"] = ssid
        if new_key:
            old_key = cfg.get("wifi_direct_password") or ""
            generated_key = secrets.token_urlsafe(12)
            while generated_key == old_key:
                generated_key = secrets.token_urlsafe(12)
            cfg["wifi_direct_password"] = generated_key
            change = True
        if change:
            save_config(cfg)
    response = {**wifi_direct_network_data(), "ok": True, "active": False}
    if change and STATE.get("wifi_direct", {}).get("active"):
        stop_wifi_direct()
        res = start_wifi_direct()
        return {**response, **res}
    if STATE.get("wifi_direct", {}).get("active"):
        response["active"] = True
    return response

# --- Config ------------------------------------------------------------------


def add_trusted_pc(peer_id: str):
    with _CONFIG_LOCK:
        cfg = load_config()
        if peer_id in cfg.get('revoked_pcs', []):
            return
        trusted = list(cfg.get('trusted_pcs', []))
        if peer_id not in trusted:
            trusted.append(peer_id)
            cfg['trusted_pcs'] = trusted
            save_config(cfg)


def rotate_common_key() -> str:
    """Makes a new shared PC key and starts using it right away, without restarting. Devices that use the
    shared key (shortcuts set up by hand, phones linked with an older version, PCs that share it) stop
    working until they're linked again; devices with their own key and paired PCs aren't affected."""
    new_value = secrets.token_urlsafe(9)
    with _CONFIG_LOCK:
        cfg = load_config()
        cfg["key"] = new_value
        save_config(cfg)
    Handler.key = new_value
    if Handler.links is not None:
        Handler.links.cfg["key"] = new_value
    logging.info("The PC's shared key was changed")
    return new_value


def save_pc_key(peer_id: str, token: str, ca_pem: str | None = None):
    """Stores the link with another PC. `ca_pem`: its CA, if they paired with encryption (protocol 2)."""
    with _CONFIG_LOCK:
        cfg = load_config()
        cfg.setdefault('pcs', {})[peer_id] = token
        cfg['revoked_pcs'] = [p for p in cfg.get('revoked_pcs', []) if p != peer_id]
        nearby = next((pc for pc in STATE['pcs'] if pc['id'] == peer_id), None)
        if nearby:
            cfg.setdefault('pcs_info', {}).setdefault(peer_id, {})['name'] = nearby['name']
        linked_pcs.save_ca(cfg, peer_id, ca_pem)
        save_config(cfg)
        manager = getattr(Handler, 'pairing_manager', None)
        if manager is not None:
            manager.remember_key(peer_id, token, encrypted=bool(ca_pem))
    if not ca_pem:
        logging.warning("PC %s linked without encryption (it has an older CopyOver): update it and link them again",
                        peer_id[:8])


def modify_pc(peer_id, action, name=''):
    with _CONFIG_LOCK:
        cfg = load_config()
        if peer_id not in cfg.get('pcs', {}):
            raise ValueError(t('This computer is no longer paired.'))
        if action == 'rename':
            name = name.strip()
            if not name or len(name) > 60 or any(ord(c) < 32 for c in name):
                raise ValueError(t('Enter a name between 1 and 60 characters.'))
            cfg.setdefault('pcs_info', {}).setdefault(peer_id, {})['alias'] = name
        elif action == 'unpair':
            cfg['pcs'].pop(peer_id)
            cfg.get('pcs_info', {}).pop(peer_id, None)
            cfg['trusted_pcs'] = [p for p in cfg.get('trusted_pcs', []) if p != peer_id]
            cfg['revoked_pcs'] = list(set(cfg.get('revoked_pcs', [])) | {peer_id})
        else:
            raise ValueError(t('Unknown action.'))
        save_config(cfg)
        if action == 'unpair':
            for event in _PC_SENDS.get(peer_id, ()):
                event.set()
            Handler.pairing_manager.forget_pc(peer_id)
            Handler.p2p_manager.forget_pc(peer_id)


def _diagnose() -> list:
    import diagnostics
    return diagnostics.check(load_config(), ip=STATE.get("ip") or ip_local(),
                               announced=bool(STATE.get("announced")),
                               repeated_name=bool(STATE.get("repeated_name")))


def open_panel_in(tab: str, port: int) -> None:
    """Opens the panel with the link modal on that tab (atajo, android or qr)."""
    STATE["open_modal"] = tab
    dialogs.launch_web_window(port)


def open_panel_on_start(cfg) -> bool:
    """The panel opens by itself until the welcome card is closed or something is linked."""
    return not cfg.get("welcome_seen") and not cfg.get("devices")


def issue_android_key(name: str) -> str:
    """When the link is accepted on the desktop, the Android device gets its own key."""
    def high(registry):
        id_d, key = devices.register({"devices": registry}, name, "android", origin="android", pinned_name=True)
        # Android doesn't send X-Device: its device name is the one it gave when linking.
        registry[id_d]["device_name"] = registry[id_d]["name"]
        repeated = devices.find_repeated({"devices": registry}, id_d)
        if repeated:
            registry[id_d]["repeats"] = repeated
        return key, id_d, _linked_card(id_d, registry[id_d], registry)
    key, device_id, card = modify_devices(high)
    STATE.setdefault("apple_devices", {})[device_id] = card
    return key


def ip_local():
    """The IP that's announced and goes in the QR (no virtual adapters; see network.ip_local)."""
    return network.ip_local()


# --- Content types -----------------------------------------------------------


HEVC_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1")


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"GIF8"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in HEVC_BRANDS:
        return "heic"
    return None


def likely_extension(data):
    if image_type(data) in ("heic", "jpg"):
        return "." + image_type(data)
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"PK\x03\x04"):
        return ".zip"
    if data[4:8] == b"ftyp":
        return ".mov" if data[8:10] == b"qt" else ".mp4"
    return ".bin"


def as_text(data):
    """The decoded text, or None if it looks like a binary file."""
    if b"\x00" in data or likely_extension(data) != ".bin":
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
        printable = sum(c.isprintable() or c in "\r\n\t" for c in text)
        return text if printable / len(text) > 0.95 else None


def save_as_jpg(data, path):
    # iPhone photos come in HEIC or with the rotation in EXIF; most desktop apps understand neither.
    import io

    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    with Image.open(io.BytesIO(data)) as img:
        upright = ImageOps.exif_transpose(img).convert("RGB")
        # iPhone photos are in Display P3: without their profile the JPG looks washed out.
        options = dict(quality=92, icc_profile=img.info.get("icc_profile"))
        try:
            # The date and location stay; the rotation was already applied and exif_transpose took it out.
            upright.save(path, "JPEG", exif=upright.getexif(), **options)
        except ValueError:
            # A JPG holds up to 64 KB of EXIF: better the photo without it than no conversion at all.
            logging.warning("The EXIF of a received photo doesn't fit in a JPG: it was left out")
            upright.save(path, "JPEG", **options)


def photo_as_jpg(data, base):
    """Saves the photo as «base».jpg and returns its path, or None if it can't be decoded."""
    path = free_path(base + ".jpg")
    try:
        save_as_jpg(data, path)
    except Exception as error:
        path.unlink(missing_ok=True)
        if _full_disk(error):
            raise
        logging.warning("Couldn't convert a received photo to JPG: %s", error)
        return None
    return path


def heic_a_jpg(path: Path) -> Path:
    """Converts a saved HEIC photo to JPG and deletes the original; if it can't, leaves it as is."""
    try:
        destination = free_path(path.stem + ".jpg", path.parent)
        save_as_jpg(path.read_bytes(), destination)
    except Exception:
        logging.exception("Couldn't convert %s to JPG", path.name)
        return path
    path.unlink(missing_ok=True)
    return destination


def free_path(name, folder=None):
    if folder is None:
        folder = get_downloads_folder("iphone")
    name = p2p.safe_name(str(name))
    path = folder / name
    n = 1
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def request_parts(kind, body):
    """Returns [(name, content_type, bytes)] for both Form (multipart) and File (raw)."""
    if kind.startswith("multipart/form-data"):
        msg = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + kind.encode("latin-1") + b"\r\n\r\n" + body)
        parts = []
        for p in msg.iter_parts():
            data = p.get_payload(decode=True) or b""
            logging.debug("  part: name=%r type=%s bytes=%d", p.get_filename(), p.get_content_type(), len(data))
            parts.append((p.get_filename(), p.get_content_type(), data))
        return parts
    base_type = kind.split(";")[0].strip().lower()
    if base_type == "application/x-www-form-urlencoded":
        # Shortcuts sends the form this way when the field is text (for example, the clipboard).
        return [(None, "text/plain", value.encode("utf-8"))
                for _, value in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()
                for value in value]
    return [(None, base_type, body)]


def linked_types() -> list[str]:
    """Types of the linked phones (iphone, ipad, android), to know how to prepare something for them."""
    return [d.get("type", "") for d in load_config().get("devices", {}).values()]


# --- HTTP server -------------------------------------------------------------


def _create_p2p_manager(cfg=None) -> p2p.TransfersManager:
    trusted = set((cfg or {}).get("trusted_pcs", []))

    def to_progress(info):
        STATE["transfer"] = info

    return p2p.TransfersManager(
        destination=lambda: get_downloads_folder("p2p"),
        ask_user=dialogs.request_confirmation,
        trusted_pcs=trusted,
        on_trust=add_trusted_pc,
        to_progress=to_progress,
    )


def _create_pairing_manager(cfg=None) -> p2p.PairingManager:
    pcs = dict((cfg or {}).get("pcs", {}))
    return p2p.PairingManager(
        paired_pcs=pcs,
        confirm_code=dialogs.confirm_pairing,
        on_save=save_pc_key,
        encrypted=linked_pcs.encrypted_ids(cfg or {}),
        allow_unencrypted=allow_unencrypted(cfg or {}),
        link_name=link_name,
    )


def is_this_pc(ip: str) -> bool:
    """Whether the request comes from this same PC (localhost or any of its IPs): not a device for the panel."""
    return ip in ("127.0.0.1", "::1") or ip == STATE.get("ip") or ip in network.all_ips(network.interfaces())


# Devices already noted in the log for using the shared key (once per run).
_COMMON_KEY_NOTIFIED: set = set()


def link_name(peer_id: str) -> str:
    """The name this PC knows an already linked PC by (its alias or the last name seen)."""
    info = load_config().get("pcs_info", {}).get(peer_id, {})
    return info.get("alias") or info.get("name") or ""


def allow_unencrypted(cfg) -> bool:
    """Whether pairing with PCs running an older CopyOver (protocol 1, plain text) is accepted. Yes for now,
    while they all update; with "allow_unencrypted_pairing": false in config.json only encrypted pairing is
    allowed."""
    return cfg.get("allow_unencrypted_pairing", True) is not False


def own_certificates():
    """This PC's CA and server certificate; None if they couldn't be prepared (no TLS)."""
    return getattr(Handler, "certificates", None)


def own_ca() -> str:
    """This PC's CA in PEM, the one handed to another PC when pairing ("" if there's no TLS)."""
    certs = own_certificates()
    return certs.ca_pem() if certs is not None else ""


from android_transfers import AndroidTransfers


class KeysThrottle:
    """10 wrong keys within a minute from the same IP, and that IP waits for the minute to pass."""

    def __init__(self, attempts: int = 10, window: float = 60.0):
        self.attempts = attempts
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, ip: str, now: float) -> list[float]:
        failures = [f for f in self._failures.get(ip, []) if now - f < self.window]
        if failures:
            self._failures[ip] = failures
        else:
            self._failures.pop(ip, None)
        return failures

    def wait(self, ip: str) -> int:
        """Seconds left before it can try again; 0 if it can try now."""
        now = time.monotonic()
        with self._lock:
            failures = self._recent(ip, now)
            if len(failures) < self.attempts:
                return 0
            return max(1, int(failures[0] + self.window - now) + 1)

    def failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures[ip] = self._recent(ip, now) + [now]

    def hit(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


KEYS_THROTTLE = KeysThrottle()


class Handler(AndroidTransfers, panel_routes.PanelPaths, BaseHTTPRequestHandler):
    android_manager = p2p.TransfersManager(
        destination=lambda: get_downloads_folder("android"),
        ask_user=lambda _: "accept", max_bytes=None,
        to_progress=lambda info: STATE.__setitem__("transfer", info))
    android_offers = None

    OLD_TYPE_NAMES = {"text": "texto", "image": "imagen", "file": "archivo", "none": "nada"}

    def parse_request(self):
        if not super().parse_request():
            return False
        path, sign, query = self.path.partition("?")
        self.old_type_names = path == "/traer/tipo"
        self.path = route_from_old(path) + sign + query_from_old(query)
        for old, new in OLD_HEADERS.items():
            if self.headers.get(old) is not None and self.headers.get(new) is None:
                self.headers[new] = self.headers[old]
        return True

    @classmethod
    def clean_expired_transfers(cls):
        cls.p2p_manager.clean_expired()
        cls.android_manager.clean_expired()

    # A rejected POST is answered before its body is read. Closing a socket with unread data makes Windows send
    # a reset, and the client could get it instead of the response («connection aborted» instead of a 403).
    # So, as web servers do, the write side is closed first and what's left of the body is discarded.
    DRAIN_MAXIMUM = 1 << 20
    DRAIN_WAIT = 1.0

    def finish(self):
        try:
            super().finish()
        finally:
            self._drain_unread_body()

    def _drain_unread_body(self):
        if getattr(self, "command", None) not in ("POST", "PUT"):
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (AttributeError, ValueError):
            length = 0
        if length <= 0:
            return
        connection = self.connection
        try:
            connection.shutdown(socket.SHUT_WR)
            connection.settimeout(self.DRAIN_WAIT)
            limit = time.monotonic() + self.DRAIN_WAIT
            remaining = self.DRAIN_MAXIMUM
            while remaining > 0 and time.monotonic() < limit:
                data = connection.recv(min(65536, remaining))
                if not data:
                    break
                remaining -= len(data)
        except OSError:
            pass

    def _note_android_on_network(self):
        """The Android app polls here with its key, so a tablet linked a moment ago shows up without sending
        anything. Only presence: the card's last action stays, and this runs every few seconds."""
        ip_cli = self.client_address[0]
        if ip_cli in ("127.0.0.1", "::1"):
            return
        current_cfg = load_config()
        device_id = devices.find_by_key(current_cfg, self.headers.get("X-Key") or "")
        if not device_id:
            return
        data = current_cfg["devices"][device_id]
        state_map = STATE.setdefault("apple_devices", {})
        entry = state_map.setdefault(device_id, _linked_card(device_id, data, current_cfg["devices"]))
        entry.pop("pending", None)
        entry.update({"ip": ip_cli, "pc_network": network_signature(), "timestamp": time.time(), "online": True})
        if data.get("last_ip") != ip_cli or not data.get("used"):
            def note(registry):
                if device_id in registry:
                    registry[device_id].update({"last_ip": ip_cli, "used": True})
            modify_devices(note)

    def serve_android_offer(self, confirm=False):
        # Key first: before looking at paths or reading the body.
        if not self.authorized():
            return
        self._note_android_on_network()
        import android_offers
        if Handler.android_offers is None:
            with _HISTORY_LOCK:
                if Handler.android_offers is None:
                    Handler.android_offers = android_offers.AndroidOffers()
        store = Handler.android_offers
        route = urlsplit(self.path).path
        if route == '/android/offers' and not confirm:
            return self.query_android_offer(store)
        parts = route.strip('/').split('/')
        if len(parts) != 4 or parts[:2] != ['android', 'offers']:
            return self.respond(404, t('Offer not found'))
        offer_id, action = parts[2:]
        metadata = store.metadata(offer_id)
        if metadata is None:
            return self.respond(404, t('Offer not found'))
        if confirm and action == 'confirm':
            if not store.confirm(offer_id, self.headers.get('X-Key', '')):
                return self.respond(409, t('Incomplete download'))
            return self.respond(200, json.dumps({'ok': True}), 'application/json', {'Cache-Control': 'no-store'})
        if confirm or action != 'file':
            return self.respond(404, t('Offer not found'))
        return self.download_android_offer(store, offer_id, metadata)

    def query_android_offer(self, store):
        from android_offers import OfferPreparing
        try:
            offer = store.query(STATE, socket.gethostname())
        except OfferPreparing:
            return self.respond(503, json.dumps(to_wire({'preparing': True})), 'application/json',
                                  {'Retry-After': '1', 'Cache-Control': 'no-store'})
        except ValueError:
            return self.respond(413, t('The offer exceeds the allowed limit'))
        except OSError as error:
            return self.respond(507, t('Not enough disk space')) if error.errno == 28 else self.respond(409, t('The offer is no longer available'))
        if not self.authorized():
            return
        return self.respond(200, json.dumps(to_wire({'offer': offer})), 'application/json', {'Cache-Control': 'no-store'})

    def download_android_offer(self, store, offer_id, metadata):
        stream = store.open_stream(offer_id)
        if stream is None:
            return self.respond(404, t('Offer not found'))
        with stream:
            size = metadata['size']
            range_header = self.headers.get('Range')
            from android_offers import download_range, copy_download
            try:
                offset = download_range(range_header, size)
            except ValueError:
                return self.respond(416, b'', extra={'Content-Range': f'bytes */{size}'})
            stream.seek(offset)
            self.send_response(206 if range_header else 200)
            self.send_header('Accept-Ranges', 'bytes')
            if range_header:
                self.send_header('Content-Range', f'bytes {offset}-{size - 1}/{size}')
            self.send_header('Content-Type', metadata['mime'])
            self.send_header('Content-Length', str(size - offset))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-File-Name', urllib.parse.quote(metadata['name']))
            self.send_header('X-Nombre-Archivo', urllib.parse.quote(metadata['name']))  # for an APK not yet updated
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(metadata['name'], safe=''))
            self.end_headers()
            try:
                if not copy_download(stream, self.wfile, size - offset, authorize=lambda: self._valid_key()):
                    return
            except (OSError, ConnectionError):
                return
        store.downloaded(offer_id, self.headers.get('X-Key', ''))
    server_version = "PuentePortapapeles/2.0"
    key = ""
    notices = False
    p2p_manager: p2p.TransfersManager = _create_p2p_manager()
    pairing_manager: p2p.PairingManager = _create_pairing_manager()
    identifier = ""
    links = None
    updates = None
    actions = {}  # the tray menu ones, which the quick view also uses
    changes = None
    certificates = None  # certificates.Certificados of this PC; None = no TLS

    def log_message(self, format, *args):
        logging.info("%s %s", self.client_address[0], format % args)

    def log_request(self, code="-", size="-"):
        # The panel and the quick view poll every few seconds: their successful responses only filled the log.
        if is_routine_request(getattr(self, "path", ""), code):
            return
        super().log_request(code, size)

    def respond(self, code, body: bytes | str = b"", kind="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        # No CopyOver page can be shown inside another one (keeps another site from clicking for you).
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _valid_key(self, accept_pcs: bool = False, accept_devices: bool = True) -> bool:
        # Keys of paired computers only work for sending files between PCs.
        received = self.headers.get("X-Key", "")
        if bool(self.key) and hmac.compare_digest(received.encode(), self.key.encode()):
            return True
        # Each linked phone has its own key; removing it from the panel revokes it.
        if accept_devices and devices.find_by_key(load_config(), received):
            return True
        return accept_pcs and self.pairing_manager.pc_valid_key(received, by_tls=self.by_tls())

    def is_shortcut(self) -> bool:
        """The request comes from iOS Shortcuts. From the app it says «Shortcuts/…», but from Spotlight,
        Control Center or the share sheet another process runs it and only says «… CFNetwork/… Darwin/…»."""
        agent = self.headers.get("User-Agent") or ""
        return "Shortcuts/" in agent or "CFNetwork/" in agent

    def by_tls(self) -> bool:
        """Whether this request came encrypted (another PC paired with protocol 2)."""
        return isinstance(self.connection, ssl.SSLSocket)

    def _throttled(self) -> bool:
        seconds = KEYS_THROTTLE.wait(self.client_address[0])
        if not seconds:
            return False
        self.respond(429, t("CopyOver: too many wrong keys. Wait {seconds} seconds and try again.",
                              seconds=seconds), "text/plain; charset=utf-8", {"Retry-After": str(seconds)})
        return True

    def authorized(self):
        if self._throttled():
            return False
        if self._valid_key():
            KEYS_THROTTLE.hit(self.client_address[0])
            return True
        KEYS_THROTTLE.failure(self.client_address[0])
        # Hint for debugging the shortcut without exposing the key: 0 = none arrived, 32 = a device key.
        received = self.headers.get("X-Key", "")
        logging.info("Key rejected from %s: %d characters%s", self.client_address[0], len(received),
                     "" if received == received.strip() else " (with spaces or line breaks)")
        # The shortcut pastes whatever it gets: the error has to make sense when read.
        reason = t("no key was received") if not received else t("this device’s key is no longer valid")
        self.respond(401, t("CopyOver: {reason}. On the PC, open Link phone, scan the QR code and tap Save to Shortcuts.", reason=reason), "text/plain; charset=utf-8")
        return False

    def p2p_authorized(self, accept_pcs: bool = False):
        # PC-to-PC routes (profile, computers, transfers) don't open with a phone's key.
        if self._throttled():
            return False
        if self._valid_key(accept_pcs, accept_devices=False):
            KEYS_THROTTLE.hit(self.client_address[0])
            return True
        KEYS_THROTTLE.failure(self.client_address[0])
        self.respond(403, t("Wrong key"))
        return False

    def _is_localhost(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _local_origin(self) -> bool:
        """From this PC and with its own Host/Origin (prevents DNS rebinding and CSRF)."""
        if not self._is_localhost():
            return False
        port = self.server.server_address[1]
        valid = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if self.headers.get("Host", "") not in valid:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlsplit(origin).netloc in valid

    def _cookie(self, name: str) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _fetches_local_secret(self, cookie: bool = False) -> bool:
        """Whether the request carries this run's local secret (see local_secret.py): the helpers send it as a
        header, the panel window as a cookie. Another account on this PC can't read it."""
        expected = getattr(self.server, "local_secret", None) or ""
        received = self.headers.get(local_secret.HEADER) or (self._cookie(local_secret.COOKIE) if cookie else "")
        return bool(expected) and bool(received) and hmac.compare_digest(received.encode(), expected.encode())

    def _panel_local(self) -> bool:
        """The /app panel only answers this PC, with its own Host/Origin and with the local secret.

        BridgeServer always has a secret (servir() creates it once it has the port); test servers built
        without one only check the origin."""
        if not self._local_origin():
            return False
        return getattr(self.server, "local_secret", None) is None or self._fetches_local_secret(cookie=True)

    def _identify_linked(self, device_id: str, current_cfg: dict, action: str, ip_cli: str, agent: str,
                               requested_name: str, model: str) -> str:
        """A linked device is recognized by its key: the IP and the name it sends are just labels."""
        data = current_cfg["devices"][device_id]
        changes = {}
        if data.get("type", "mobile") == "mobile":
            kind = devices.type_from_agent(agent, requested_name)
            if kind != "mobile":
                changes["type"] = kind
        # The first name the shortcut sends names the device; after that the panel decides.
        if not data.get("name_locked") and (requested_name or model):
            changes["name"] = devices.clean_name(requested_name or model)
            changes["name_locked"] = True
        # The name the device claims (even if the panel shows it under another one): it's used to recognize it
        # when one of its shortcuts still uses the PC's shared key.
        if requested_name and data.get("device_name") != devices.clean_name(requested_name):
            changes["device_name"] = devices.clean_name(requested_name)
            repeated = devices.find_repeated(
                {"devices": {**current_cfg["devices"], device_id: {**data, **changes}}}, device_id)
            if repeated:
                changes["repeats"] = repeated
        local = ip_cli in ("127.0.0.1", "::1")
        if not local and data.get("last_ip") != ip_cli:
            changes["last_ip"] = ip_cli
        if not data.get("used"):
            changes["used"] = True  # has sent at least one request with its key
        if not local and _must_save_use(data.get("last_use"), action):
            # Its last action stays in the config so it shows as soon as CopyOver starts.
            changes["last_use"] = {"action": action, "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
        # The entry guessed by IP (from before linking) is also redundant in the config.
        guessed = not local and ip_cli in current_cfg.get("apple_devices", {})
        if changes or guessed:
            data.update(changes)

            def apply(cfg):
                # On the freshly read config: if it was removed from the panel meanwhile, it isn't brought
                # back.
                if device_id in cfg.get("devices", {}):
                    cfg["devices"][device_id].update(changes)
                if guessed:
                    cfg.get("apple_devices", {}).pop(ip_cli, None)

            modify_config(apply)
        name = data.get("name") or devices.default_name(data.get("type", "mobile"))
        if local:
            return name
        str_time = datetime.now().strftime("%H:%M")
        state_map = STATE.setdefault("apple_devices", {})
        # The entry guessed by IP (from before linking) is redundant: we know who it is now.
        for old_key, info_v in list(state_map.items()):
            if not info_v.get("id") and info_v.get("ip") == ip_cli:
                state_map.pop(old_key, None)
        state_map[device_id] = {
            "id": device_id,
            "ip": ip_cli,
            "pc_network": network_signature(),
            "name": name,
            "type": data.get("type", "mobile"),
            "timestamp": time.time(),
            "last_seen": str_time,
            "last_event": f"{action} at {str_time}",
            "linked": True,
            # It just made a request: it's online now, without waiting for the next check (up to 30 s).
            "online": True,
            "repeats": _repeated_info(current_cfg.get("devices", {}), data),
        }
        STATE["phone_ip"] = ip_cli
        STATE["phone_device"] = f"{name} ({ip_cli})"
        return f"{name} ({ip_cli})"

    def _sender_name(self) -> str:
        """Who's sending, for the notification: the linked device that owns the key or the PC at that IP;
        empty if unknown."""
        headers = getattr(self, "headers", None) or {}
        current_cfg = load_config()
        device_id = devices.find_by_key(current_cfg, headers.get("X-Key") or "")
        if device_id:
            return current_cfg["devices"][device_id].get("name", "")
        ip_cli = getattr(self, "client_address", ("", 0))[0]
        return next((e.get("name", "") for e in STATE.get("pcs", []) if ip_cli and e.get("ip") == ip_cli), "")

    def _identify_client(self, action: str = "Connected", query_params: dict | None = None) -> str:
        ip_cli = getattr(self, "client_address", ("127.0.0.1", 0))[0]
        current_cfg = load_config()
        headers = getattr(self, "headers", None) or {}
        ua_raw = headers.get("User-Agent") or ""
        ua = ua_raw.lower()

        x_device = (
            headers.get("X-Device")
            or headers.get("X-Device-Name")
            or ""
        ).strip()
        x_model = (headers.get("X-Device-Model") or "").strip()

        q_device = ""
        q_type = ""
        q_model = ""
        if query_params:
            q_device = query_params.get("device", [""])[0].strip()
            q_type = query_params.get("type", [""])[0].strip().lower()
            q_type = "mobile" if q_type == configuration.OLD_DEVICE_TYPE else q_type
            q_model = query_params.get("model", [""])[0].strip()

        # Names sent by the client: no control characters and short.
        x_device, x_model, q_device, q_model = (
            "".join(c for c in v if c.isprintable())[:60] for v in (x_device, x_model, q_device, q_model)
        )

        device_id = devices.find_by_key(current_cfg, headers.get("X-Key") or "")
        if device_id:
            return self._identify_linked(device_id, current_cfg, action, ip_cli, ua_raw, q_device or x_device, q_model or x_model)

        # Without a key, if the IP belongs to a linked phone seen recently (it opened /ping or the web app),
        # it's that phone: no second «not linked» card that then has to be removed separately.
        if ip_cli not in ("127.0.0.1", "::1"):
            state_map = STATE.setdefault("apple_devices", {})
            str_time = datetime.now().strftime("%H:%M")
            linked = next((v for v in list(state_map.values())
                              if v.get("id") and v.get("ip") == ip_cli and time.time() - v.get("timestamp", 0) < 2 * 3600), None)
            if linked:
                linked["last_seen"] = str_time
                linked["last_event"] = f"{action} at {str_time}"
                linked["timestamp"] = time.time()
                linked["pc_network"] = network_signature()
                STATE["phone_ip"] = ip_cli
                STATE["phone_device"] = f"{linked['name']} ({ip_cli})"
                return f"{linked['name']} ({ip_cli})"

        # With the PC's shared key and the name of a linked device, it's that device with a shortcut set up
        # before linking by QR: it shows on its card and not on a second one.
        said_name = devices.clean_name(q_device or x_device) if (q_device or x_device) else ""
        common_key = current_cfg.get("key") or ""
        if said_name and common_key and hmac.compare_digest(headers.get("X-Key") or "", common_key):
            known = [i for i, d in current_cfg.get("devices", {}).items()
                        if (d.get("device_name") or "").lower() == said_name.lower()]
            if len(known) == 1:
                if known[0] not in _COMMON_KEY_NOTIFIED:
                    _COMMON_KEY_NOTIFIED.add(known[0])
                    logging.info("%s uses the shared key in some shortcut; linking it again by QR code gives it its own",
                                 said_name)
                return self._identify_linked(known[0], current_cfg, action, ip_cli, ua_raw, "", "")

        # Without its own key, all that's left is guessing from User-Agent, name and IP (unlinked device).
        is_android ="android" in ua or q_type == "android" or "android" in x_device.lower()
        is_ipad = "ipad" in ua or q_type == "ipad" or "ipad" in x_device.lower() or (
            "macintosh" in ua and "mobile" in ua
        )
        is_iphone = "iphone" in ua or q_type == "iphone" or "iphone" in x_device.lower()

        saved_device = current_cfg.get("apple_devices", {})

        if is_android:
            kind = "android"
            auto_model = ""
            m = re.search(r"android\s+[\d\.]+;\s*([^;\)]+?)(?:\s+build|[;\)])", ua_raw, re.IGNORECASE)
            if m:
                m_txt = m.group(1).strip()
                auto_model = f"Samsung {m_txt}" if m_txt.upper().startswith("SM-") else m_txt
            suggested_name = q_device or x_device or q_model or x_model or auto_model or t("Android phone")
        elif is_ipad:
            kind = "ipad"
            suggested_name = q_device or x_device or q_model or x_model or devices.configured_name(current_cfg, "ipad")
        elif is_iphone:
            kind = "iphone"
            suggested_name = q_device or x_device or q_model or x_model or devices.configured_name(current_cfg, "iphone")
        else:
            kind = q_type or "mobile"
            suggested_name = q_device or x_device or "Mobile device"

        if ip_cli in saved_device:
            prev_type = saved_device[ip_cli].get("type")
            if (is_android and prev_type != "android") or (is_iphone and prev_type != "iphone") or (is_ipad and prev_type != "ipad"):
                name = q_device or x_device or q_model or x_model or suggested_name
            elif q_device or x_device or q_model or x_model:
                name = q_device or x_device or q_model or x_model
            elif "15 Pro Max" in str(saved_device[ip_cli].get("name", "")):
                name = suggested_name
            else:
                name = saved_device[ip_cli].get("name") or suggested_name
            if not is_android and not is_ipad and not is_iphone:
                kind = q_type or prev_type or kind
        else:
            name = suggested_name

        # A guest is identified by its link, lasts as long as the link and is never saved in the config.
        requested_path = urlsplit(getattr(self, "path", "") or "")
        guest_token = (query_params or urllib.parse.parse_qs(requested_path.query)).get("t", [""])[0]             if requested_path.path.startswith("/guest") else ""
        guest = guest_token if guest_token and web_token_type(guest_token) == "guest" else ""
        if guest:
            name = f"Guest {guest_number(guest)}"  # shown in the panel's language

        if not is_this_pc(ip_cli):
            str_time = datetime.now().strftime("%H:%M")
            state_map = STATE.setdefault("apple_devices", {})
            STATE["phone_ip"] = ip_cli
            STATE["phone_device"] = f"{name} ({ip_cli})"
            for old_ip, info_v in list(state_map.items()):
                if info_v.get("id") or guest or info_v.get("guest"):
                    continue  # linked devices and guests don't overwrite each other
                if old_ip != ip_cli and (
                    (info_v.get("name") or "").strip().lower() == name.strip().lower()
                    or (info_v.get("type") == kind and kind in ("iphone", "ipad"))
                ):
                    state_map.pop(old_ip, None)
            state_map[ip_cli] = {
                "ip": ip_cli,
                "pc_network": network_signature(),
                "name": name,
                "type": kind,
                "timestamp": time.time(),
                "last_seen": str_time,
                "last_event": f"{action} at {str_time}",
                "linked": False,
            }
            if guest:
                state_map[ip_cli]["guest"] = guest
            if isinstance(saved_device, dict) and not guest:
                cfg_change = False
                for old_ip, info_v in list(saved_device.items()):
                    if old_ip != ip_cli and (
                        (info_v.get("name") or "").strip().lower() == name.strip().lower()
                        or (info_v.get("type") == kind and kind in ("iphone", "ipad"))
                    ):
                        saved_device.pop(old_ip, None)
                        cfg_change = True
                if ip_cli not in saved_device or saved_device[ip_cli].get("name") != name or saved_device[ip_cli].get("type") != kind:
                    saved_device[ip_cli] = {"name": name, "type": kind}
                    cfg_change = True
                if cfg_change:
                    current_cfg["apple_devices"] = saved_device
                    save_config(current_cfg)
        return f"{name} ({ip_cli})" if not is_this_pc(ip_cli) else name

    def do_GET(self):
        if urlsplit(self.path).path == "/android/transfers" or urlsplit(self.path).path.startswith("/android/transfers/"):
            return self.serve_android_transfer("GET")
        if urlsplit(self.path).path == '/android/offers' or urlsplit(self.path).path.startswith('/android/offers/'):
            return self.serve_android_offer()
        if urlsplit(self.path).path == '/sincronizar':
            return self.sync()
        if self.path == '/android/update' or self.path.startswith('/android/apk/'):
            return self.send_update()
        if urlsplit(self.path).path == '/app-android':
            return self.send_apk_with_link()
        if self.path == "/link/status":
            if self.links is None:
                return self.respond(503, t("Restart CopyOver on the PC"))
            status = self.links.poll(self.headers.get("X-Link-Session", ""))
            return self.respond(200, json.dumps(status), "application/json", {"Cache-Control": "no-store"})
        if urlsplit(self.path).path == "/identity":
            return self.send_identity()
        path = self.path.split("?")[0]
        if path == "/favicon.ico":
            ico_path = FOLDER / "puente.ico"
            if not ico_path.exists():
                try:
                    import tray
                    tray.ensure_ico(force=True)
                except Exception:
                    pass
            if ico_path.exists():
                return self.respond(200, ico_path.read_bytes(), "image/x-icon", {"Cache-Control": "no-cache, max-age=0"})
            return self.respond(204)

        if path == "/favicon.svg":
            import web_ui
            return self.respond(200, web_ui.SVG_RADAR_LOGO.encode("utf-8"), "image/svg+xml", {"Cache-Control": "public, max-age=86400"})

        if path in ("/app/logo.png", "/app/logo-192.png", "/app/logo-512.png"):
            size = 512 if "512" in path else (192 if "192" in path else 256)
            try:
                import io, tray
                img = tray.draw_logo(size=size)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return self.respond(200, buf.getvalue(), "image/png", {"Cache-Control": "public, max-age=86400"})
            except Exception:
                return self.respond(204)

        if path == "/manifest.json":
            manifest = {
                "name": "CopyOver",
                "short_name": "CopyOver",
                "start_url": "/app",
                "display": "standalone",
                "background_color": "#080b11",
                "theme_color": "#080b11",
                "icons": [
                    {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
                    {"src": "/app/logo-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/app/logo-512.png", "sizes": "512x512", "type": "image/png"}
                ]
            }
            return self.respond(200, json.dumps(manifest), "application/manifest+json", {"Cache-Control": "public, max-age=86400"})

        if path == "/ping":
            if self.client_address[0] != "127.0.0.1":
                # Link PC sends the name in ?device=: iOS cuts header values at the first non-ASCII character
                # («Ana’s iPhone» arrived as «Ana»).
                q_ping = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                STATE["phone_device"] = self._identify_client(query_params=q_ping)
            return self.respond(200, "Puente activo")  # installed shortcuts read this; don't change it

        if path == "/app" or path.startswith("/app/"):
            return self._get_app(path)

        if path == "/guest":
            parsed = urllib.parse.urlparse(self.path)
            token = urllib.parse.parse_qs(parsed.query).get("t", [""])[0]
            if not web_token_type(token):
                import web_ui
                return self.respond(403, i18n.translate_html(web_ui.EXPIRED_QR_PAGE), "text/html; charset=utf-8")
            import web_ui
            return self.respond(200, i18n.translate_html(web_ui.GUEST_PAGE), "text/html; charset=utf-8")

        if path == "/guest/status":
            parsed = urllib.parse.urlparse(self.path)
            q_params = urllib.parse.parse_qs(parsed.query)
            token = q_params.get("t", [""])[0]
            if not web_token_type(token):
                return self.respond(403, json.dumps({"ok": False, "expired": True}), "application/json; charset=utf-8")
            guest = web_token_type(token) == "guest"
            renew_web_token(token)
            me = q_params.get("me", [""])[0].strip()
            # The owner chose which of their linked devices this page is: before identifying it, so it lands on
            # that card. A guest's «me» is ignored.
            am_valid = bool(me) and not guest and associate_web_to_linked(token, me, self.client_address[0])
            self._identify_client("In the web app", query_params=q_params)
            ip_cli = self.client_address[0]
            # Linked devices are keyed by id and guessed ones by IP: look up the IP in both.
            for device_info in STATE.get("apple_devices", {}).values():
                if device_info.get("ip") == ip_cli:
                    device_info["in_app"] = True
                    device_info["last_seen"] = "live"
            offer = offer_for_guest(token) if guest else STATE.get("phone_offer")
            txt_clip = ""
            if not guest:  # a guest never sees the PC's clipboard
                try:
                    txt_clip = (so.read_text() or "")[:300]
                except Exception:
                    pass
            body = json.dumps({
                "ok": True,
                "pc_name": STATE.get("pc_name") or socket.gethostname(),
                "offer": offer,
                "clipboard_text": txt_clip,
                "transfer": STATE.get("transfer"),
                "is_valid_self": am_valid,
            })
            return self.respond(200, body, "application/json; charset=utf-8")

        if path == "/guest/who":
            token = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("t", [""])[0]
            items = linked_to_choose(token)
            if items is None:
                return self.respond(403, json.dumps({"ok": False}), "application/json; charset=utf-8")
            return self.respond(200, json.dumps({"ok": True, "devices": items}), "application/json; charset=utf-8",
                                  {"Cache-Control": "no-store"})

        if self.path.startswith("/guest/download"):
            parsed = urllib.parse.urlparse(self.path)
            token = urllib.parse.parse_qs(parsed.query).get("t", [""])[0]
            if not web_token_type(token):
                return self.respond(403, t("This guest link has expired. Ask for a new one on the PC."))
            self._quien = self._identify_client("Downloaded in the app").rsplit(" (", 1)[0]
            if web_token_type(token) == "guest":
                return self._deliver_to_guest(token)
            files = files_for_phone()
            if files:
                if STATE.get("phone_offer"):
                    STATE["phone_offer"]["delivered"] = True
                STATE["last"] = (datetime.now(), t("File downloaded on {name}", name=self._quien))
                return self.send_files(files, bytes_limit=MAX_BYTES_WEB)
            text = so.read_text()
            if text:
                if STATE.get("phone_offer"):
                    STATE["phone_offer"]["delivered"] = True
                return self.respond(200, text, "text/plain; charset=utf-8")
            img_path = temporary() / "salida.png"
            if so.read_image(img_path):
                if STATE.get("phone_offer"):
                    STATE["phone_offer"]["delivered"] = True
                return self.respond(200, img_path.read_bytes(), "image/png")
            return self.respond(200, t("Nothing ready on the PC"), "text/plain; charset=utf-8")

        if self.path.startswith("/configure"):
            return self._respond_configure()

        if self.path == "/p2p/peers" and self._is_localhost() and self._fetches_local_secret():
            # «CopyOver.exe --send» asks the running one for nearby PCs with the local secret, not the shared key.
            return self.respond(200, json.dumps(STATE.get("pcs", [])), "application/json; charset=utf-8")
        if self.path.startswith("/p2p/"):
            if not self.p2p_authorized(accept_pcs=self.path == "/p2p/peers"):
                return
            if self.path == "/p2p/peers":
                body = json.dumps(STATE.get("pcs", []))
                return self.respond(200, body, "application/json; charset=utf-8")
            if self.path == "/p2p/profile":
                return self._respond_json_profile()
            if self.path == "/p2p/status":
                return self._respond_json_state()
            return self.respond(404, t("Not found"))
        if path == "/receive/type":
            # Receive PC asks this before /receive: «file» also opens the share sheet, the rest only goes to
            # the clipboard. it answers in English, and the old route keeps the Spanish words.
            if not self.authorized():
                return
            files = files_for_phone()
            if files:
                # Over 500 MB doesn't fit in the shortcut: it gets a text explaining why, and the web app
                # downloads it. A single image goes to the phone's clipboard, not to Files.
                if _weigh_more_than(files, MAX_BYTES):
                    kind = "text"
                elif len(files) == 1 and _goes_to_clipboard(files[0]):
                    kind = "image"  # for the shortcut «image» means «copy it to the clipboard»
                else:
                    kind = "file"
            elif so.read_text():
                kind = "text"
            elif so.read_image(temporary() / "salida.png"):
                kind = "image"
            else:
                kind = "none"
            if getattr(self, "type_in_english", False):
                kind = self.TYPES_IN_ENGLISH[kind]
            return self.respond(200, kind, "text/plain; charset=utf-8", {"Cache-Control": "no-store"})
        if path != "/receive":
            return self.respond(404, t("Not found"))
        if not self.authorized():
            return
        try:
            # Identified without saying «Got something» yet: that's noted only once something reached the phone.
            quien = self._quien = self._identify_client("Connected").rsplit(" (", 1)[0]
            files = files_for_phone()
            if files and _weigh_more_than(files, MAX_BYTES):
                name = files[0].name if len(files) == 1 else t("{count} files", count=len(files))
                if not self.is_shortcut():
                    return self.respond(413, t("{name} is over the 500 MB limit.", name=name))
                return self.respond(200, t("{name} is over 500 MB and can’t go through the shortcut. On the PC, open Link phone → Guests and download it from there (up to 10 GB).", name=name))
            if files:
                if not self.send_files(files, bytes_limit=MAX_BYTES):
                    return
                self._fetched(t("File sent to {name}", name=quien))
                if STATE.get("phone_offer") and sum(r.stat().st_size for r in files if r.is_file()) <= MAX_BYTES:
                    STATE["phone_offer"]["delivered"] = True
                return
            text = so.read_text()
            if text:
                self.respond(200, text)
                if STATE.get("phone_offer"):
                    STATE["phone_offer"]["delivered"] = True
                return self._fetched(t("Text sent to {name}", name=quien))
            path = temporary() / "salida.png"
            if so.read_image(path):
                self.respond(200, path.read_bytes(), "image/png")
                if STATE.get("phone_offer"):
                    STATE["phone_offer"]["delivered"] = True
                return self._fetched(t("Image sent to {name}", name=quien))
            # For the shortcut (which pastes whatever it gets) an empty 204 would leave the clipboard blank
            # with no explanation; other clients still get 204.
            if self.is_shortcut():
                return self.respond(200, t("CopyOver: the PC has nothing copied or ready to send."), "text/plain; charset=utf-8")
            self.respond(204)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # The phone cut the download (the shortcut was cancelled or ran again): not our error, and there's
            # nobody left to answer.
            logging.info("The phone cut off the /receive download halfway")
        except Exception as e:
            logging.exception("Error reading the clipboard")
            self.respond(500, t("CopyOver had a problem on the PC. Try again; if it keeps happening, restart CopyOver."))

    def _fetched(self, last: str):
        """What's recorded when /receive delivered something: the panel's «latest» and the device's event."""
        STATE["last"] = (datetime.now(), last)
        ip_cli = self.client_address[0]
        event = f"Got something at {datetime.now():%H:%M}"
        for data in STATE.get("apple_devices", {}).values():
            if data.get("ip") == ip_cli:
                data["last_event"] = event

    def send_files(self, paths, bytes_limit: int = MAX_BYTES) -> bool:
        """Sends the files (or a .zip with them). True if they went out complete."""
        if len(paths) == 1 and paths[0].is_file():
            path = paths[0]
        else:
            path = temporary() / ("files.zip" if len(paths) > 1 else f"{paths[0].name}.zip")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                for r in paths:
                    if r.is_dir():
                        for f in r.rglob("*"):
                            if f.is_file():
                                z.write(f, f.relative_to(r.parent))
                    else:
                        z.write(r, r.name)
        size = path.stat().st_size
        if size > bytes_limit:
            self.respond(413, t("The file is over 500 MB"))
            return False
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        sent = 0
        t0 = time.monotonic()
        try:
            with open(path, "rb") as f:
                while True:
                    block = f.read(262144)
                    if not block:
                        break
                    self.wfile.write(block)
                    sent += len(block)
                    if size > 5 * 1024 * 1024:
                        trans = max(0.001, time.monotonic() - t0)
                        speed = (sent / (1024 * 1024)) / trans
                        STATE["transfer"] = {
                            "direction": "sent",
                            "peer": getattr(self, "_quien", ""),
                            "file": path.name,
                            "received_bytes": sent,
                            "total": size,
                            "percent": round((sent * 100.0) / size, 1),
                            "speed_mb_s": round(speed, 2),
                        }
        finally:
            STATE["transfer"] = None
        return True

    def sync(self):
        if not self.authorized():
            return
        if self.changes is None:
            return self.respond(501, t('Automatic receiving requires Windows on the PC.'))
        previous = parse_qs(urlsplit(self.path).query).get('since', [''])[0]
        if previous and not re.fullmatch(r'[a-f0-9]{32}:[0-9]{1,10}', previous):
            return self.respond(400, t('Invalid cursor'))
        # The key is checked again while waiting: removing the device or changing the shared key cuts
        # a connection that is already open.
        token, changed = self.changes.wait(previous, still_allowed=self._valid_key)
        if changed is None:
            logging.info("Stopped waiting for %s: its key is no longer valid", self.client_address[0])
            self.close_connection = True
            return self.respond(401, t("CopyOver: {reason}. On the PC, open Link phone, scan the QR code and tap Save to Shortcuts.",
                                         reason=t("this device’s key is no longer valid")), "text/plain; charset=utf-8")
        headers = {'X-CopyOver-Version': token, 'X-Puente-Version': token, 'Cache-Control': 'no-store'}
        try:
            if not changed:
                return self.respond(204, extra=headers)
            token, data, mime = self.changes.frame(self.headers.get('X-Key') or '', so)
            if not self.authorized():
                return
            headers.update({'X-CopyOver-Version': token, 'X-Puente-Version': token})
            return self.respond(204 if data is None else 200, data or b'', mime, headers)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception('Error syncing the clipboard')
            self.respond(503, t('Couldn’t read the clipboard. Retrying.'))

    def send_update(self):
        if not self.authorized():
            return
        if self.updates is None:
            return self.respond(503, t('Restart CopyOver on the PC'))
        if self.path == '/android/update':
            return self.respond(200, json.dumps(self.updates.status()), 'application/json',
                                  {'Cache-Control': 'no-store'})
        path = self.updates.apk(self.path.removeprefix('/android/apk/'))
        if path is None:
            return self.respond(404, t('Update not available'))
        self._send_apk(path)

    def send_apk_with_link(self):
        """The APK for a phone that isn't linked yet and has no key: the QR in the panel's Android tab
        carries a 30-minute link. It works with a private repository and without a GitHub account."""
        token = parse_qs(urlsplit(self.path).query).get('t', [''])[0]
        if not token or token not in _clean_tokens('tokens_apk'):
            return self.respond(403, t('The link expired. Open «Link phone» on the PC again and scan the new code.'))
        path = self.updates.current_apk() if self.updates else None
        if path is None:
            return self.respond(404, t('There’s no APK available on the PC. Check its internet connection.'))
        self._send_apk(path)

    def _send_apk(self, path):
        try:
            with path.open('rb') as source:
                self.send_response(200)
                self.send_header('Content-Type', 'application/vnd.android.package-archive')
                self.send_header('Content-Length', str(path.stat().st_size))
                self.send_header('Content-Disposition', 'attachment; filename="Puente.apk"')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                while block := source.read(64 * 1024):
                    self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _deliver_to_guest(self, token: str):
        """A guest downloads only what the panel offered it: the prepared files or the offered text."""
        offer = offer_for_guest(token)
        if offer and offer.get("type") == "file":
            files = [Path(r) for r in STATE.get("prepared_files", []) if Path(r).exists()]
            if files:
                offer["delivered"] = True
                STATE["last"] = (datetime.now(), t("File downloaded on {name}", name=self._quien))
                return self.send_files(files, bytes_limit=MAX_BYTES_WEB)
        if offer and offer.get("type") == "text" and offer.get("text"):
            offer["delivered"] = True
            return self.respond(200, offer["text"], "text/plain; charset=utf-8")
        return self.respond(200, t("Nothing ready on the PC"), "text/plain; charset=utf-8")

    def _respond_configure(self):
        """Page the phone opens after scanning the panel's QR: it hands over its own key, once."""
        token = parse_qs(urlsplit(self.path).query).get("t", [""])[0]
        expires = STATE.setdefault("qr_tokens", {}).pop(token, 0) if token else 0
        import web_ui
        if time.time() > expires:
            return self.respond(403, i18n.translate_html(web_ui.EXPIRED_QR_PAGE).encode("utf-8"), "text/html; charset=utf-8")
        ip_cli = self.client_address[0]
        kind = devices.type_from_agent(self.headers.get("User-Agent", ""))
        name = devices.default_name(kind)

        def high(registry):
            # Scanning again from the same device without having used the previous key replaces it.
            for old_id in [k for k, v in registry.items() if v.get("origin") == "qr" and not v.get("used") and v.get("last_ip") == ip_cli]:
                registry.pop(old_id)
                STATE.get("apple_devices", {}).pop(old_id, None)
            for old_id in devices.remove_unused(registry):
                STATE.get("apple_devices", {}).pop(old_id, None)
            id_d, key_d = devices.register({"devices": registry}, name, kind, origin="qr")
            registry[id_d]["last_ip"] = ip_cli
            return id_d, key_d

        device_id, key = modify_devices(high)
        current_cfg = load_config()
        str_time = datetime.now().strftime("%H:%M")
        STATE.setdefault("apple_devices", {})[device_id] = {
            "id": device_id, "ip": ip_cli, "name": name, "type": kind, "timestamp": time.time(),
            "last_seen": str_time, "last_event": f"Linked by QR code at {str_time}", "linked": True,
            # Scanning isn't linking: the panel shows it once its key reaches the PC (Link PC's /ping).
            "pending": True,
        }
        server_ip = STATE.get("ip") or ip_local()
        base = f"http://{server_ip}:{self.server.server_address[1]}"
        # The page is read on the phone: in its language, not the PC's.
        language = i18n.from_browser(self.headers.get("Accept-Language", ""))
        html = web_ui.configure_page(current_cfg.get("name", "puente-pc"), base, key, kind, language)
        return self.respond(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def _fix_own_type(self):
        """The QR page reports from the browser that it's an iPad: Safari on iPad presents itself as a Mac,
        and by its User-Agent it was registered as a «mobile device» with nothing to correct it later."""
        length = int(self.headers.get("Content-Length") or 0)
        kind = self.rfile.read(min(length, 32)).decode("utf-8", "replace").strip() if length > 0 else ""
        if kind not in devices.TYPES:
            return self.respond(400, t("Invalid request"))
        key = self.headers.get("X-Key") or ""

        def apply(registry):
            id_d = devices.find_by_key({"devices": registry}, key)
            if id_d and registry[id_d].get("type", "mobile") == "mobile":
                registry[id_d]["type"] = kind
                if not registry[id_d].get("name_locked"):
                    registry[id_d]["name"] = devices.default_name(kind)
            return id_d, dict(registry[id_d]) if id_d else None

        device_id, data = modify_devices(apply)
        if not device_id:
            return self.respond(401, t("This device’s key is no longer valid"))
        in_state = STATE.setdefault("apple_devices", {}).get(device_id)
        if in_state:
            in_state.update(type=data["type"], name=data["name"])
        return self.respond(200, "ok")

    def send_identity(self):
        from linking import key_fingerprint, identity_test

        query = parse_qs(urlsplit(self.path).query)
        challenge = query.get("challenge", [""])[0]
        fingerprint = query.get("fingerprint", [""])[0]
        if not re.fullmatch(r"[a-f0-9]{64}", challenge) or (fingerprint and not re.fullmatch(r"[a-f0-9]{32}", fingerprint)):
            return self.respond(400, t("Invalid challenge"))
        if not self.identifier:
            return self.respond(503, t("Restart CopyOver on the PC"))
        key = self.key
        if fingerprint:
            # An Android phone has its own key and the PC only keeps its hash: the proof is signed with that
            # hash, which the phone works out from its key. Without a fingerprint (older apps), the shared key.
            hashes = [devices._hash(self.key)] if self.key else []
            hashes += [d.get("key_hash", "") for d in load_config().get("devices", {}).values()]
            key = None
            for hash_key in hashes:
                if hash_key and hmac.compare_digest(key_fingerprint(hash_key), fingerprint):
                    key = hash_key
            if key is None:
                return self.respond(403, t("this device’s key is no longer valid"))
        address = self.connection.getsockname()[0]
        test = identity_test(self.identifier, key, challenge, address)
        return self.respond(200, json.dumps(test), "application/json", {"Cache-Control": "no-store"})

    def request_linking(self, reveal=False):
        if self.links is None:
            return self.respond(503, t("Restart CopyOver on the PC"))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                return self.respond(400, t("Invalid request"))
            request = json.loads(self.rfile.read(length))
            if reveal:
                ticket = self.links.reveal(request["session"], request["public_key"])
            else:
                ticket = self.links.request(request.get("name", "Android"), request["commitment"],
                                                    self.client_address[0], self.connection.getsockname()[0])
            self.respond(202, json.dumps(ticket), "application/json", {"Cache-Control": "no-store"})
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            logging.info("Android linking rejected at %s: %r", self.path, error)
            self.respond(400, t("Couldn’t start linking. Wait a few seconds and try again."))

    def do_POST(self):
        if urlsplit(self.path).path == "/android/transfers" or urlsplit(self.path).path.startswith("/android/transfers/"):
            return self.serve_android_transfer("POST")
        if self.path == LOCAL_SEND_PATH:
            return self._receive_local_send()
        if self.path == LOCAL_NOTICE_PATH:
            return self._serve_local_notice()
        if urlsplit(self.path).path.startswith('/android/offers/'):
            return self.serve_android_offer(confirm=True)
        if self.path == "/link":
            return self.request_linking(reveal=False)
        if self.path == "/link/code":
            return self.request_linking(reveal=True)
        if self.path == "/device/type":
            return self._fix_own_type()
        if self.path.startswith("/app/"):
            return self._post_app()
        if self.path.startswith("/guest/"):
            parsed = urllib.parse.urlparse(self.path)
            token = urllib.parse.parse_qs(parsed.query).get("t", [""])[0]
            if not web_token_type(token):
                return self.respond(403, t("This guest link has expired. Ask for a new one on the PC."))
            
            if parsed.path == "/guest/text":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    data = json.loads(body.decode("utf-8"))
                    text = str(data.get("text", "")).strip()
                    if text:
                        so.set_text(text)
                        link = links.web_link(text)
                        register_history("received", self._identify_client("Sent from the app"), f"Nota: {text[:40]}" + ("…" if len(text) > 40 else ""), size=len(text.encode("utf-8")), link=link)
                        STATE["last"] = (datetime.now(), t("Note received from guest"))
                        if load_config().get("notifications", True):
                            notify_text(text, link, t("a guest"))
                        return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
                except Exception:
                    pass
                return self.respond(400, t("Couldn’t process the text"))

            if parsed.path == "/guest/confirm-receipt":
                if STATE.get("phone_offer"):
                    STATE["phone_offer"]["delivered"] = True
                return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")

            if parsed.path == "/guest/upload":
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0:
                    return self.respond(400, t("The file arrived empty"))
                if length > MAX_BYTES_WEB:
                    return self.respond(413, t("The file is over the 10 GB limit"))
                who_uploads = self._identify_client("Sent from the app").rsplit(" (", 1)[0]
                hdr_name = self.headers.get("X-File-Name", "").strip()
                if hdr_name:
                    try:
                        dec_name = urllib.parse.unquote(hdr_name)
                    except Exception:
                        dec_name = hdr_name
                    safe_name = Path(dec_name).name or "ios_file"
                    dest_folder = get_downloads_folder("iphone")
                    try:
                        free = shutil.disk_usage(dest_folder).free
                        if free < length + 20 * 1024 * 1024:
                            return self.respond(507, t("Not enough disk space on the PC"))
                    except OSError:
                        pass
                    destination_f = free_path(safe_name, dest_folder)
                    received = 0
                    t0 = time.monotonic()
                    try:
                        with open(destination_f, "wb") as f_out:
                            remaining = length
                            while remaining > 0:
                                # «Cut off guest access» also stops a send that is still arriving.
                                if not web_token_type(token):
                                    break
                                chunk = self.rfile.read(min(262144, remaining))
                                if not chunk:
                                    break
                                f_out.write(chunk)
                                received += len(chunk)
                                remaining -= len(chunk)
                                trans = max(0.001, time.monotonic() - t0)
                                speed = (received / (1024 * 1024)) / trans
                                STATE["transfer"] = {
                                    "direction": "received",
                                    "peer": who_uploads,
                                    "file": destination_f.name,
                                    "received_bytes": received,
                                    "total": length,
                                    "percent": round((received * 100.0) / length, 1),
                                    "speed_mb_s": round(speed, 2),
                                }
                    finally:
                        STATE["transfer"] = None
                    if remaining > 0 and not web_token_type(token):
                        destination_f.unlink(missing_ok=True)
                        self.close_connection = True
                        return self.respond(403, t("This guest link has expired. Ask for a new one on the PC."))
                    if destination_f.suffix.lower() in (".heic", ".heif"):
                        destination_f = heic_a_jpg(destination_f)
                    so.set_files([destination_f])
                    register_history("received", self._identify_client("Sent from the app"), destination_f.name, size=received, local_path=str(destination_f))
                    STATE["last"] = (datetime.now(), t("Received: {name}", name=destination_f.name))
                    if load_config().get("notifications", True):
                        notify_received([destination_f], self._sender_name())
                    return self.respond(200, json.dumps({"ok": True, "name": destination_f.name, "size": received}), "application/json; charset=utf-8")

                guest_folder = get_downloads_folder("guests")
                try:
                    if shutil.disk_usage(guest_folder).free < length + 20 * 1024 * 1024:
                        self.close_connection = True
                        return self.respond(507, t("Not enough disk space on the PC"))
                except OSError:
                    pass
                parts = None
                try:
                    self.connection.settimeout(self.BODY_WAIT)
                    def read_while_valid(n, read=self.rfile.read):
                        # «Cut off guest access» also stops a send that is still arriving.
                        return read(n) if web_token_type(token) else b""
                    parts = receiving.read_parts(read_while_valid, length, self.headers.get("Content-Type", ""), guest_folder)
                    saved = []
                    for orig_name, ctype, bin_data in parts:
                        if not bin_data:
                            continue
                        name = Path(orig_name).name if orig_name else "guest_file"
                        destination_f = free_path(name, guest_folder)
                        if isinstance(bin_data, receiving.PartOnDisk):
                            bin_data.move_a(destination_f)
                        else:
                            destination_f.write_bytes(bin_data)
                        saved.append(destination_f)
                        register_history("received", self._identify_client("Sent from the app"), destination_f.name, size=len(bin_data), local_path=str(destination_f))
                    if saved:
                        STATE["last"] = (datetime.now(), t("Guest file(s) ({count})", count=len(saved)))
                        if load_config().get("notifications", True):
                            notify_received(saved, t("a guest"))
                        return self.respond(200, "OK")
                except receiving.CutSend as error:
                    logging.warning("Guest upload cut off: %s", error)
                    self.close_connection = True
                    return self.respond(400, t("The transfer arrived incomplete (the connection dropped). Try again."))
                except receiving.MalformedSend as error:
                    logging.warning("Malformed guest upload: %s", error)
                    self.close_connection = True
                    return self.respond(400, t("Invalid request"))
                except Exception as error:
                    if _full_disk(error):
                        logging.warning("Disk full during a guest upload")
                        self.close_connection = True
                        return self.respond(507, t("Not enough disk space on the PC"))
                    logging.exception("Error in a guest upload")
                    return self.respond(500, t("CopyOver had a problem on the PC. Try again; if it keeps happening, restart CopyOver."))
                finally:
                    receiving.discard(parts)
                return self.respond(400, t("No valid file arrived"))

            return self.respond(404, t("Guest route not found"))

        if self.path.startswith("/p2p/"):
            return self._post_p2p()
        if self.path != "/send":
            return self.respond(404, t("Not found"))
        if not self.authorized():
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return self.respond(400, t("Nothing arrived. Check that the clipboard isn’t empty."))
        if length > MAX_BYTES_WEB:
            return self.respond(413, t("The file is over the 10 GB limit"))
        folder = get_downloads_folder("iphone")
        try:
            if shutil.disk_usage(folder).free < length + 20 * 1024 * 1024:
                self.close_connection = True
                return self.respond(507, t("Not enough disk space on the PC"))
        except OSError:
            pass
        parts = None
        try:
            # Split while it arrives: large parts go straight to disk and never through RAM.
            self.connection.settimeout(self.BODY_WAIT)
            parts = receiving.read_parts(self.rfile.read, length, self.headers.get("Content-Type", ""), folder)
            message = self.receive(parts)
            STATE["last"] = (datetime.now(), message.replace(" " + t("on the PC"), ""))
            self.respond(200, message)
        except receiving.CutSend as error:
            # Less than announced arrived: it's discarded whole instead of saving a truncated file.
            logging.warning("Transfer from %s cut off: %s", self.client_address[0], error)
            self.close_connection = True
            self.respond(400, t("The transfer arrived incomplete (the connection dropped). Try again."))
        except receiving.MalformedSend as error:
            logging.warning("Malformed transfer from %s: %s", self.client_address[0], error)
            self.close_connection = True
            self.respond(400, t("Invalid request"))
        except Exception as error:
            if _full_disk(error):
                # There was enough space at the start, but another program filled the disk halfway.
                logging.warning("Disk full while a transfer from %s was arriving", self.client_address[0])
                self.close_connection = True
                self.respond(507, t("Not enough disk space on the PC"))
            else:
                logging.exception("Error while receiving")
                self.respond(500, t("CopyOver had a problem on the PC. Try again; if it keeps happening, restart CopyOver."))
        finally:
            receiving.discard(parts)

    BODY_WAIT = 60  # seconds without receiving anything before the upload is considered cut

    def _receive_local_send(self):
        """Files sent by «Send with CopyOver» from Explorer, through another CopyOver.exe --send."""
        if not self._local_origin() or not self._fetches_local_secret():
            return self.respond(403, t("Only available locally"))
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not 0 < length <= 1_000_000:
            return self.respond(400, t("Invalid request"))
        try:
            paths = json.loads(self.rfile.read(length).decode("utf-8"))["paths"]
            if not isinstance(paths, list) or not paths or len(paths) > 1000 or not all(isinstance(r, str) for r in paths):
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return self.respond(400, t("Invalid request"))
        join_to_send([Path(r) for r in paths])
        return self.respond(200, "ok")

    def _serve_local_notice(self):
        """The button of a file notification, through another CopyOver.exe --notification (see
        windows_system.NOTICE_PROTOCOL)."""
        if not self._local_origin() or not self._fetches_local_secret():
            return self.respond(403, t("Only available locally"))
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not 0 < length <= 4096:
            return self.respond(400, t("Invalid request"))
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            code, action = data["code"], data["action"]
            if not isinstance(code, str) or not isinstance(action, str):
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return self.respond(400, t("Invalid request"))
        if not so.serve_notice(code, action):
            return self.respond(404, t("Not found"))
        return self.respond(200, "ok")

    def _read_pairing_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 16384:
            raise ValueError("length")
        return from_wire(json.loads(self.rfile.read(length).decode("utf-8")))

    def _pair_v2(self):
        """Protocol 2: the token and the CAs travel encrypted and the code comes out of the key exchange."""
        import pc_pairing as v2

        certs = getattr(self.server, "certificates", None) or own_certificates()
        if certs is None or not certs.ca_pem():
            # Without certificates the other PC falls back to protocol 1 (if both allow it).
            return self.respond(503, t("Encryption is not available on this PC"))
        receiver = self.pairing_manager.receiver

        def json_v2(code, data):
            return self.respond(code, json.dumps(to_wire({"v": v2.VERSION, **data})), "application/json; charset=utf-8")

        try:
            data = self._read_pairing_json()
            if self.path == v2.STARTUP_PATH:
                name = _ANNOUNCED_NAME.get("name") or socket.gethostname()
                # Relinking a PC already linked with encryption: only that PC, over TLS and with its current
                # token.
                authenticated = self.pairing_manager.key_pc(self.headers.get("X-Key", ""), self.by_tls())
                return json_v2(200, receiver.startup(data, self.identifier, name, self.client_address[0],
                                                    authenticated_as=authenticated))
            if self.path == v2.REVEAL_PATH:
                return json_v2(200, receiver.reveal(data, certs.ca_pem()))
            if self.path == v2.CONFIRM_PATH:
                return json_v2(200, receiver.confirm_session(data))
        except v2.ExistingLink as error:
            logging.warning("Refused to replace an encrypted link from %s", self.client_address[0])
            return json_v2(409, {"error": str(error)})
        except PermissionError as error:
            return json_v2(403 if self.path != v2.STARTUP_PATH else 429, {"error": str(error)})
        except LookupError:
            return json_v2(410, {"error": t("The link request expired. Try again.")})
        except (ValueError, UnicodeDecodeError):
            return json_v2(400, {"error": t("Invalid request")})
        return self.respond(404, t("Not found"))

    def _post_p2p(self):
        if self.path.startswith("/p2p/pair/v2/"):
            return self._pair_v2()
        if self.path == "/p2p/pair":
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 16384:
                return self.respond(400, t("Invalid request"))
            try:
                data = from_wire(json.loads(self.rfile.read(length).decode("utf-8")))
                name = str(data["name"])
                peer_id = str(data["id"])
                code_text = str(data.get("code", ""))
            except Exception:
                return self.respond(400, t("Invalid JSON"))
            ok, _, token = self.pairing_manager.start_pairing(name, peer_id, code_text)
            if not ok:
                return self.respond(403, t("Pairing rejected"))
            return self.respond(200, json.dumps({"token": token}), "application/json; charset=utf-8")

        if not self.p2p_authorized(accept_pcs=True):
            return
        if self.path == "/p2p/offer":
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024 * 1024:
                return self.respond(400, t("Invalid offer"))
            try:
                data = from_wire(json.loads(self.rfile.read(length).decode("utf-8")))
            except Exception:
                return self.respond(400, t("Invalid JSON"))
            request_key = self.headers.get('X-Key', '')
            peer_id = self.pairing_manager.id_for_key(request_key)
            sender = data.get('sender') if isinstance(data, dict) else None
            if peer_id and (not isinstance(sender, dict) or sender.get('id') != peer_id):
                return self.respond(403, t('The identity doesn’t match the pairing'))
            code, resp = self.p2p_manager.process_offer(data, owner=request_key,
                authorize=lambda: self._valid_key(accept_pcs=True, accept_devices=False))
            if isinstance(resp, dict):
                return self.respond(code, json.dumps(to_wire(resp)), "application/json; charset=utf-8")
            return self.respond(code, resp)

        if self.path.startswith('/p2p/resume/'):
            id_trans = self.path.removeprefix('/p2p/resume/').strip('/')
            code, resp = self.p2p_manager.resume_state(id_trans, self.headers.get('X-Key', ''))
            return self.respond(code, json.dumps(to_wire(resp)), 'application/json; charset=utf-8')

        if self.path.startswith("/p2p/cancel/"):
            id_trans = self.path.removeprefix("/p2p/cancel/").strip("/")
            ok = self.p2p_manager.cancel_transfer(id_trans, owner=self.headers.get('X-Key', ''))
            return self.respond(200 if ok else 404, t('Canceled') if ok else t('Transfer not found'))

        return self.respond(404, t("Not found"))

    def do_PUT(self):
        if urlsplit(self.path).path == "/android/transfers" or urlsplit(self.path).path.startswith("/android/transfers/"):
            return self.serve_android_transfer("PUT")
        if not self.path.startswith("/p2p/"):
            return self.respond(404, t("Not found"))
        if not self.p2p_authorized(accept_pcs=True):
            return
        path_parts = self.path.strip("/").split("/")
        if len(path_parts) != 4 or path_parts[:2] != ["p2p", "upload"]:
            return self.respond(404, t("Not found"))
        id_trans = path_parts[2]
        try:
            index = int(path_parts[3])
            length = int(self.headers.get("Content-Length", -1))
            offset = int(self.headers.get('X-CopyOver-Offset', '0'))
        except ValueError:
            return self.respond(400, t("Invalid parameters"))
        if length < 0:
            return self.respond(400, t("Missing Content-Length"))

        self.connection.settimeout(15)

        trans = self.p2p_manager.get_transfer(id_trans)  # removed from the manager once complete
        code, msg, final_path, complete = self.p2p_manager.receive_file(
            id_trans, index, self.rfile, length, offset=offset, owner=self.headers.get('X-Key', '')
        )
        if code == 200 and final_path:
            STATE["last"] = (datetime.now(), t("Received: {name}", name=final_path.name))
            try:
                weight_r = final_path.stat().st_size if final_path.exists() else max(0, length)
            except Exception:
                weight_r = max(0, length)
            register_history("received", f"PC ({self.client_address[0]})", final_path.name, size=weight_r, local_path=str(final_path))
            if complete and self.notices:
                notify_received([a["final_path"] for a in trans["files"] if a.get("final_path")] if trans else [final_path],
                                 self._sender_name())
        try:
            return self.respond(code, msg)
        except ConnectionError:
            # The other PC reconnects and asks how far it got.
            return

    def _write_received_clipboard(self, writer, value):
        if self.changes is None:
            return writer(value)
        from sync import content_fingerprint
        expected = None
        if writer == so.set_text:
            expected = content_fingerprint(value.encode('utf-8'), 'text/plain')
        elif writer == so.set_image:
            expected = content_fingerprint(value.read_bytes(), 'image/png')
        return self.changes.write(self.headers.get('X-Key') or '', writer, value, expected=expected)

    def receive(self, parts):
        # Large parts are already on disk (receiving.PartOnDisk): only the in-memory ones need space.
        total_bytes = sum(len(d) for _, _, d in parts if isinstance(d, bytes))
        try:
            free = shutil.disk_usage(get_downloads_folder("iphone")).free
            if free < total_bytes + 20 * 1024 * 1024:
                logging.warning("Not enough disk space to save %d bytes", total_bytes)
                return t("Error: Not enough disk space")
        except OSError:
            pass
        texts, saved, images, empty = [], [], [], []
        for name, kind, data in parts:
            if not data:
                if name:
                    empty.append(name)
                continue
            if not isinstance(data, receiving.PartOnDisk) and rich_text.is_from_clipboard(name, data):
                # Text copied in Notes or Mail arrives as «Clipboard … .rtf»: it goes to the clipboard as text.
                rtf_text = rich_text.rtf_to_text(data)
                if rtf_text.strip():
                    texts.append(rtf_text)
                    continue
            in_disk = isinstance(data, receiving.PartOnDisk)
            head = data.head if in_disk else data
            img = image_type(head)
            if in_disk and img and len(data) <= MAX_IMAGE_IN_RAM:
                # A large photo is still converted (HEIC to JPG, rotation); a huge file that starts like an
                # image is saved as is.
                data, in_disk = data.read(), False
            elif in_disk:
                img = None
            suffix = Path(name).suffix.lower() if name else ""
            # Loose text (clipboard) goes to the clipboard; a named .txt/.csv/etc. is a file.
            text = None if img or in_disk else as_text(data)
            if text is not None and (not name or (kind == "text/plain" and suffix in ("", ".txt"))):
                texts.append(text)
                continue
            base = Path(name).stem if name else f"photo_{datetime.now():%Y%m%d_%H%M%S}"
            path = None
            if img in ("heic", "jpg"):
                path = photo_as_jpg(data, base)
                # If it can't be decoded (damaged, or a format the PC doesn't read), it stays as a file.
                img = img if path else None
            if img:
                if path:
                    pass  # already saved as JPG
                elif img == 'webp':
                    import io
                    from PIL import Image, ImageOps
                    path = free_path(base + '.png')
                    with Image.open(io.BytesIO(data)) as image:
                        ImageOps.exif_transpose(image).save(path, 'PNG')
                else:
                    path = free_path(f"{base}.{img}")
                    path.write_bytes(data)
                images.append(path)
            else:
                path = free_path(name or f"file_{datetime.now():%Y%m%d_%H%M%S}{likely_extension(head)}")
                if in_disk:
                    data.move_a(path)
                else:
                    path.write_bytes(data)
            saved.append(path)

        if not saved and texts:
            joined_text = "\n".join(texts)
            if rich_text.is_just_a_clipboard_name(joined_text):
                # The photo or file never left the phone: only the clipboard's name did.
                return t("The PC only got the name of what you copied, not the photo or file. Share it from the app instead (Share → Send PC).")
            self._write_received_clipboard(so.set_text, joined_text)
            link = links.web_link(joined_text)
            register_history("received", self._identify_client("Text copied"), f"Texto: {joined_text[:40]}", size=len(joined_text.encode("utf-8")), link=link)
            if self.notices:
                notify_text(joined_text, link, self._sender_name())
            return t("Text copied on the PC")
        if len(saved) == 1 and images:
            self._write_received_clipboard(so.set_image, images[0])
            img_weight = images[0].stat().st_size if images[0].exists() else 0
            register_history("received", self._identify_client("Photo received"), images[0].name, size=img_weight, local_path=str(images[0]))
            if self.notices and load_config().get("clipboard_notifications", False):
                notify_received([images[0]], self._sender_name())
            return t("Photo copied on the PC")
        if saved:
            self._write_received_clipboard(so.set_files, saved)
            names = ", ".join(r.name for r in saved)
            weight_g = sum(r.stat().st_size for r in saved if r.exists())
            register_history("received", self._identify_client("File received"), names, size=weight_g, local_path=str(saved[0]))
            if self.notices:
                notify_received(saved, self._sender_name())
            return t("Saved on the PC: {names}", names=names)
        if empty:
            # A genuinely empty file: the shortcut is fine, the file has nothing in it.
            return t("{name} is empty (0 bytes): there’s nothing to save", name=Path(empty[0]).name)
        logging.warning("Request with no content: the shortcut's 'file' field is empty")
        return t("Nothing arrived: in the shortcut, the 'file' field must contain Shortcut Input")


def register_announcement(cfg, ip, extras=()):
    """Renames only the instance on a collision; keeps the shortcuts' host.

    The A record of <nombre>.local carries the main IP first and then the PC's other real IPs (`extras`), so a
    phone connected through another adapter (Ethernet and Wi-Fi at the same time) also gets through."""
    from zeroconf import IPVersion, ServiceInfo, Zeroconf
    from linking import mdns_properties

    name = cfg.get("name", "puente-pc")
    addresses = []
    for address in (ip, *extras):
        if address and address not in addresses:
            addresses.append(address)
    info = ServiceInfo(
        # The «Puente » prefix is part of the protocol: the Android app looks for it to recognize the PC.
        "_http._tcp.local.", f"Puente {name}._http._tcp.local.",
        addresses=[socket.inet_aton(d) for d in addresses], port=cfg["port"], server=f"{name}.local.",
        properties=mdns_properties(cfg),
    )
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    try:
        zc.register_service(info, allow_name_change=True)
    except Exception:
        zc.close()
        raise
    return zc, info


_FIND_NOW = threading.Event()
_ANNOUNCED_NAME: dict = {}
IP_WIFI_DIRECT = "192.168.137.1"
NAMESAKE_CHECK_INTERVAL = 600.0


def find_pcs_now():
    """Asks the announcement thread to query the network again for PCs running CopyOver."""
    _FIND_NOW.set()


def change_announced_name(name: str) -> None:
    """The new name from Settings is announced right away, without restarting CopyOver."""
    if "name" in _ANNOUNCED_NAME and _ANNOUNCED_NAME["name"] != name:
        _ANNOUNCED_NAME["name"] = name
        STATE["repeated_name"] = False
        _FIND_NOW.set()


def announcement_key(name: str, ip: str, active_wfd: bool, has_path: bool, items) -> str:
    """What forces a new announcement when it changes. The IP isn't enough: when moving from one Wi-Fi to
    another that hands out the same one (192.168.1.x in two different places) zeroconf's sockets stay bound
    to the old network. So the real connection (adapter, mask and network name) counts too, and whether
    there's an outbound route: when switching Wi-Fi there are a few seconds without a route, and when it
    comes back we announce again."""
    return f"{name}|{ip}|wfd:{int(active_wfd)}|ruta:{int(has_path)}|{network.announcement_signature(items)}"


def foreign_ips(found, own) -> list:
    """The IPs that answer to our <nombre>.local and don't belong to this PC."""
    foreign = []
    for ip in found or ():
        if ip not in own and not str(ip).startswith("127.") and ip not in foreign:
            foreign.append(ip)
    return foreign


def find_namesakes(zc, name: str, own, ms_wait: int = 3000, resolve_cls=None) -> list:
    """Asks the network who answers to <nombre>.local and returns the IPs that aren't this PC's.

    zeroconf only renames the service instance on a clash: the <nombre>.local host is shared, and the phone
    could reach the other PC and get «Clave incorrecta»."""
    if resolve_cls is None:
        from zeroconf import AddressResolve as resolve_cls
    resolve = resolve_cls(f"{name}.local.")
    resolve.request(zc, ms_wait)
    # request() returns with the first answer, which is usually our own: give the others time.
    time.sleep(min(ms_wait, 1500) / 1000)
    resolve.load_from_cache(zc)
    return foreign_ips(resolve.parsed_addresses(), own)


def _check_namesakes(zc, name: str) -> None:
    """In a separate thread, so the announcement isn't held up. If our own IPs can't be found, nothing is
    claimed."""
    items = network.interfaces()
    if items is None:
        return
    own = network.all_ips(items) | {STATE.get("ip") or "", IP_WIFI_DIRECT}
    try:
        foreign = find_namesakes(zc, name, own)
    except Exception as error:
        logging.debug("Couldn't check whether another PC uses %s.local: %s", name, error)
        return
    if _ANNOUNCED_NAME.get("name", name) != name or _ANNOUNCED_NAME.get("zc") is not zc:
        return  # the name or the network changed meanwhile: the result no longer applies
    if foreign and not STATE.get("repeated_name"):
        logging.warning("Another PC on the network also answers for %s.local", name)
    STATE["repeated_name"] = bool(foreign)


def announce_in_network(cfg):
    """Publishes <nombre>.local over mDNS and looks for other CopyOvers on the network."""
    from zeroconf import ServiceBrowser

    _ANNOUNCED_NAME.update(name=cfg.get("name", "puente-pc"), zc=None)
    my_id = cfg["id"]
    zc, announced_ip, browser = None, None, None
    path_before, last_review = None, None

    def on_change_pcs(pcs):
        STATE["pcs"] = pcs
        with _CONFIG_LOCK:
            current_cfg = load_config()
            if linked_pcs.remember_names(current_cfg, pcs):
                save_config(current_cfg)

    observer = p2p.PcsObserver(my_id, on_change=on_change_pcs)

    while True:
        name = _ANNOUNCED_NAME["name"]
        has_path = network.output_ip() is not None
        if path_before is not None and has_path != path_before:
            network.forget()  # the connection dropped or came back: the cached interface list is stale
        path_before = has_path
        ip = ip_local()
        active_wfd = bool(STATE.get("wifi_direct", {}).get("active"))
        ip_key = announcement_key(name, ip, active_wfd, has_path, network.interfaces())
        STATE["ip"] = None if ip == "127.0.0.1" else ip
        STATE["announced"] = zc is not None and ip_key == announced_ip
        if ip_key != announced_ip:
            if announced_ip is not None:
                # The network changed: the PCs seen on the previous one are out of reach (they lingered as
                # «ghosts» and sending to them took 21 s to fail). The new browser fills the list again.
                observer.clean()
                STATE["pcs"] = []
            if zc:
                try:
                    if browser:
                        browser.cancel()
                    zc.unregister_all_services()
                    zc.close()
                except Exception:
                    logging.exception("Error while stopping the announcement")
                zc, browser = None, None
            STATE["repeated_name"] = False
            if ip != "127.0.0.1":
                try:
                    extras = [i for i in network.own_ips() if i != ip]
                    if active_wfd and ip != IP_WIFI_DIRECT:
                        extras.append(IP_WIFI_DIRECT)
                    zc, info_http = register_announcement({**cfg, "name": name}, ip, extras)
                    info_p2p = p2p.create_service(name, cfg["port"], my_id, ip,
                                                  pairing_level=2 if own_certificates() is not None else 1)
                    if active_wfd and ip != IP_WIFI_DIRECT:
                        # Other PCs connect to the first IP: only the main one and the Wi-Fi Direct one.
                        info_p2p.addresses = [socket.inet_aton(ip), socket.inet_aton(IP_WIFI_DIRECT)]
                    try:
                        zc.register_service(info_p2p, allow_name_change=True)
                    except Exception as error:
                        # Without this announcement the other PCs don't see this one; it used to fail without
                        # a trace.
                        logging.warning("Couldn't announce CopyOver to the other PCs: %s", error)
                    browser = ServiceBrowser(zc, p2p.SERVICE_TYPE, observer)
                    _ANNOUNCED_NAME["zc"] = zc
                    STATE["announced"] = True
                    logging.info("Announcing %s.local -> %s", name, ip)
                    certs = own_certificates()
                    if certs is not None:
                        certs.update(name)  # the certificate carries the name and the IPs: in the background
                    last_review = None
                except Exception:
                    logging.exception("Couldn't announce on the network; retrying in 10 s")
                    zc, browser = None, None
                    time.sleep(10)
                    continue
            else:
                observer.clean()
            announced_ip = ip_key
        if zc and (last_review is None or time.monotonic() - last_review >= NAMESAKE_CHECK_INTERVAL):
            # Does another PC also answer to <nombre>.local? Checked when announcing and every 10 minutes
            # after.
            last_review = time.monotonic()
            threading.Thread(target=_check_namesakes, args=(zc, name), daemon=True).start()
        if _FIND_NOW.wait(3):
            _FIND_NOW.clear()
            if zc and browser:
                # A new browser sends its mDNS queries right away instead of waiting for its next turn.
                try:
                    browser.cancel()
                    browser = ServiceBrowser(zc, p2p.SERVICE_TYPE, observer)
                except Exception:
                    logging.exception("Couldn't search for PCs again")


def _responds_ping(ip: str, ms_wait: int = 600) -> bool:
    """A short ping to the phone's last IP: if it answers, it's on and within reach."""
    if sys.platform == "win32":
        command = ["ping", "-n", "1", "-w", str(ms_wait), ip]
        options: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        command = ["ping", "-c", "1", "-W", "1", ip]
        options = {}
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=ms_wait / 1000 + 2, **options)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # On Windows the exit code is 0 even when it says «unreachable»: the text has to be checked.
    text = (output.stdout or "").lower()
    return output.returncode == 0 and "ttl=" in text


def _check_online():
    """Marks whether each linked phone answers right now (only those on one of the PC's networks)."""
    networks = pc_networks()
    for data in list(STATE.get("apple_devices", {}).values()):
        ip = data.get("ip")
        if not ip or not network.same_network(ip, networks):
            data["online"] = False
            continue
        # If it just sent something it counts as online even if the network blocks ping.
        recent = time.time() - data.get("timestamp", 0) < 300
        data["online"] = recent or _responds_ping(ip)


def _watch_online():
    time.sleep(5)  # so announce_in_network already knows the PC's IP
    while True:
        try:
            _check_online()
        except Exception:
            logging.exception("Checking which devices are online failed")
        time.sleep(30)


def _same_subnet(ip_a: str, ip_b: str) -> bool:
    """Whether ip_a is in ip_b's network, using the interface's real mask (not assuming /24)."""
    return network.same_subnet(ip_a, ip_b)


def pc_networks() -> list:
    """All of the PC's networks, including Wi-Fi Direct (192.168.137.1) if it's on."""
    extra = [STATE.get("ip")]
    wfd = STATE.get("wifi_direct") or {}
    if wfd.get("active"):
        extra.append(wfd.get("ip") or "192.168.137.1")
    return network.own_networks(extra)


def network_signature() -> str:
    """Which networks the PC was on: saved when a phone is seen, to know later whether the PC changed
    networks."""
    return network.signature(pc_networks())


def device_network(data: dict) -> str:
    """«same», «other» or «unknown». «other» is only claimed if the PC was on the same networks as now when
    it saw the phone: if the PC changed networks since then, the phone's old IP says nothing."""
    ip = (data or {}).get("ip")
    networks = pc_networks()
    if not ip or not networks:
        return "unknown"
    if network.same_network(ip, networks):
        return "same"
    if data.get("pc_network") and data["pc_network"] == network.signature(networks):
        return "other"
    return "unknown"


def _dedupe_apple_devices(items: list[dict], ip_pc: str | None = None) -> list[dict]:
    ip_ref = ip_pc or STATE.get("ip") or ip_local()
    current_phone_ip = STATE.get("phone_ip")
    by_key: dict[str, tuple[tuple, dict]] = {}
    for d in items:
        if not isinstance(d, dict) or d.get("pending"):
            continue  # scanned but not linked yet
        name = (d.get("name") or t("Apple device")).strip()
        kind = (d.get("type") or ("ipad" if "ipad" in name.lower() else "iphone")).lower()
        # Two linked devices with the same name are two devices; only guessed ones are merged.
        key = f"id:{d['id']}" if d.get("id") else (name.lower() or kind)
        ip_d = str(d.get("ip", ""))
        score = (
            1 if d.get("in_app") else 0,
            1 if ip_d and ip_d == current_phone_ip else 0,
            1 if _same_subnet(ip_d, ip_ref) or ip_d.startswith("192.168.137.") else 0,
            1 if d.get("last_seen") and d.get("last_seen") != "ready" else 0,
        )
        prev = by_key.get(key)
        if prev is None or score >= prev[0]:
            by_key[key] = (score, d)
    return [pair[1] for pair in by_key.values()]


def _dedupe_p2p_pcs(pcs: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for eq in pcs or []:
        if not isinstance(eq, dict):
            continue
        key = (eq.get("id") or eq.get("name") or eq.get("ip") or "").strip().lower()
        if key:
            seen[key] = eq
    return list(seen.values())


def _repeated_info(registry: dict, data: dict):
    """What the card needs to ask «is this the same one as …?», or None if there's no suspicion."""
    previous = registry.get(data.get("repeats") or "")
    if not previous:
        return None
    try:
        date = datetime.strptime(previous.get("linked_at", ""), "%Y-%m-%d %H:%M")
        linked = t("Linked on {date} at {time}", date=f"{date:%d/%m}", time=f"{date:%H:%M}")
    except (TypeError, ValueError):
        linked = t("Linked")
    return {
        "id": data["repeats"],
        "name": previous.get("name") or devices.default_name(previous.get("type", "mobile")),
        "linked": linked,
    }


def _readable_linked(mark: str) -> str:
    """«2026-10-25 16:34» -> «Linked on 25/10 at 16:34» (what the device card shows)."""
    try:
        date = datetime.strptime(mark, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "Linked"
    return f"Linked on {date:%d/%m} at {date:%H:%M}"


# A «Connected» (the routine ping) is saved at most every 10 minutes; actions always.
MINUTES_BETWEEN_SAVED_USES = 10


def _must_save_use(saved, action: str) -> bool:
    if action != "Connected" or not isinstance(saved, dict):
        return True
    try:
        before = datetime.strptime(saved.get("time", ""), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return True
    return (datetime.now() - before).total_seconds() >= MINUTES_BETWEEN_SAVED_USES * 60


def _last_use_today(saved):
    """(«HH:MM», «Action at HH:MM») if its last action was today; otherwise None."""
    if not isinstance(saved, dict):
        return None
    try:
        mark = datetime.strptime(saved.get("time", ""), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    if mark.date() != datetime.now().date():
        return None
    time = mark.strftime("%H:%M")
    return time, f"{saved.get('action') or 'Connected'} at {time}"


def _last_activity(data: dict) -> float:
    """Timestamp of its last saved use, or of the link: lets a keyless request from its IP right after a
    restart be recognized as it."""
    marks = []
    for mark in ((data.get("last_use") or {}).get("time"), data.get("linked_at")):
        try:
            marks.append(datetime.strptime(mark or "", "%Y-%m-%d %H:%M").timestamp())
        except (TypeError, ValueError):
            pass
    return max(marks, default=0)


def _linked_card(device_id: str, d_info: dict, registry: dict) -> dict:
    """The panel's card for a linked device, from what the config knows about it."""
    card = {
        "id": device_id,
        "ip": d_info.get("last_ip", ""),
        "name": d_info.get("name") or devices.default_name(d_info.get("type", "mobile")),
        "type": d_info.get("type", "mobile"),
        "last_seen": "ready",
        "last_event": _readable_linked(d_info.get("linked_at", "")),
        "linked": True,
        "repeats": _repeated_info(registry, d_info),
        "timestamp": _last_activity(d_info),
    }
    if devices.unused(d_info):
        card["pending"] = True
    from_today = _last_use_today(d_info.get("last_use"))
    if from_today:
        card["last_seen"], card["last_event"] = from_today
    return card


def recover_last_phone_from_log(cfg: dict):
    """Loads the Apple devices from the config and the log to have their details live."""
    for device_id, d_info in cfg.get("devices", {}).items():
        STATE["apple_devices"][device_id] = _linked_card(device_id, d_info, cfg.get("devices", {}))
    cfg_device = cfg.get("apple_devices", {})
    for ip_c, d_info in cfg_device.items():
        if is_this_pc(ip_c):
            continue  # a test run from this same PC isn't a device
        name = d_info.get("name") or t("Apple device")
        kind = d_info.get("type", "ipad" if "ipad" in name.lower() else "iphone")
        STATE["apple_devices"][ip_c] = {
            "ip": ip_c,
            "name": name,
            "type": kind,
            "last_seen": "ready",
            "last_event": "On the network",
        }
    if not LOG.exists():
        return
    try:
        lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-250:]
        pattern = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}):\d{2}),\d+\s+(\d+\.\d+\.\d+\.\d+)\s+\"(GET|POST)\s+(/enviar|/traer|/send|/receive|/ping)")
        for line in reversed(lines):
            m = pattern.search(line)
            if m and m.group(3) != "127.0.0.1":
                str_time, phone_ip, method, path = m.group(2), m.group(3), m.group(4), m.group(5)
                action = "Sent" if path in ("/send", "/enviar") else ("Copied" if path in ("/receive", "/traer") else "Connected")
                linked = next((d for d in STATE["apple_devices"].values() if d.get("id") and d.get("ip") == phone_ip), None)
                if linked:
                    if linked["last_seen"] == "ready":
                        linked["last_seen"] = str_time
                        linked["last_event"] = f"{action} at {str_time}"
                elif phone_ip in STATE["apple_devices"]:
                    if STATE["apple_devices"][phone_ip]["last_seen"] == "ready":
                        STATE["apple_devices"][phone_ip]["last_seen"] = str_time
                        STATE["apple_devices"][phone_ip]["last_event"] = f"{action} at {str_time}"
                # An IP that belongs to nobody known doesn't become a card: phones change IP, and a linked
                # phone now on another network would leave a second ghost «My iPhone».
    except Exception:
        pass
    dedup = _dedupe_apple_devices(list(STATE["apple_devices"].values()))
    # Linked devices are keyed by id (so the X and the requests find them); guessed ones by IP.
    STATE["apple_devices"] = {(d.get("id") or d.get("ip")): d for d in dedup if d.get("id") or d.get("ip")}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
PHONE_CLIPBOARD_OPTIONS = ("images", "images_videos", "files")


def phone_clipboard(cfg=None) -> str:
    """What the shortcut leaves in the phone's clipboard instead of saving it to Files (config.json key)."""
    value = (cfg or load_config()).get("phone_clipboard", "images_videos")
    return value if value in PHONE_CLIPBOARD_OPTIONS else "images_videos"


def _goes_to_clipboard(path) -> bool:
    if not path.is_file():
        return False
    mode = phone_clipboard()
    suffix = path.suffix.lower()
    if mode == "files":
        return False
    if suffix in IMAGE_EXTENSIONS:
        return True
    return mode == "images_videos" and suffix in VIDEO_EXTENSIONS


def files_for_phone() -> list[Path]:
    """What Receive PC brings as files: the ones dropped on the panel, or the ones copied on the PC.

    The panel also copies its files to the PC clipboard. While they're still there they win; once the user copies
    something else (text with Ctrl+C, an image, other files) the phone gets that and not the same files again.
    It only uses read_files, so it works the same on Windows and Linux."""
    prepared = [Path(r) for r in STATE.get("prepared_files", []) if Path(r).exists()]
    copied = STATE.get("prepared_on_clipboard")
    if prepared and copied:
        try:
            follow = {_same_path(r) for r in so.read_files()} == {_same_path(r) for r in copied}
        except Exception:
            follow = True
        if not follow:
            STATE["prepared_files"] = []
            STATE["prepared_on_clipboard"] = None
            prepared = []
    return prepared or [r for r in so.read_files() if r.exists()]


def _same_path(path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _weigh_more_than(paths, limit: int) -> bool:
    total = 0
    for path in paths:
        try:
            total += sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else path.stat().st_size
        except OSError:
            pass
    return total > limit


_ROUTINE_PATHS = ("/app/", "/favicon")
_EXACT_ROUTINE_PATHS = ("/app", "/ping", "/p2p/status", "/p2p/estado")


def is_routine_request(path: str, code) -> bool:
    """The panel's 2xx, /ping, the favicon and /p2p/status aren't logged; errors are."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    if not 200 <= code < 300:
        return False
    path = (path or "").split("?", 1)[0]
    return path in _EXACT_ROUTINE_PATHS or path.startswith(_ROUTINE_PATHS)


# Query values that authorize something: the guest and setup tokens (t) and any key a client puts in the URL.
_SECRET_IN_URL = re.compile(r"([?&](?:t|token|clave|key|k)=)[^&#\s\"']+", re.IGNORECASE)


def cover_secrets(text: str) -> str:
    """The text with the token and key values of any URL in it replaced by ***."""
    return _SECRET_IN_URL.sub(r"\1***", text)


class _CoverSecrets(logging.Filter):
    """Keeps tokens out of copyover.log: request lines carry them in the query (/guest?t=...)."""

    def filter(self, record):
        message = record.getMessage()
        covered = cover_secrets(message)
        if covered != message:
            record.msg, record.args = covered, None
        return True


class _PrivateLog(logging.handlers.RotatingFileHandler):
    """copyover.log (and each one it rotates to) readable only by this user on Linux."""

    def _open(self):
        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors,
                    opener=private_files.open_private)


def log_handlers() -> list[logging.Handler]:
    """copyover.log, plus the console when there is one, both without tokens."""
    for suffix in ("", ".1", ".2"):
        configuration.move_if_missing(LOG.with_name(OLD_LOG + suffix), LOG.with_name(LOG.name + suffix))
    handlers: list[logging.Handler] = [
        _PrivateLog(LOG, maxBytes=10_000_000, backupCount=2, encoding="utf-8"),
    ]
    if sys.stdout is not None:  # no console under pythonw
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.addFilter(_CoverSecrets())
    return handlers


class _ZeroconfWithoutTraces(logging.Filter):
    """zeroconf logs a full traceback for each «WinError 59/1231» (the network dropped or changed): one line
    is enough."""

    _NETWORK_ERRORS = ("WinError 59", "WinError 1231", "[Errno 59]", "[Errno 1231]")

    def filter(self, registry):
        text = registry.getMessage()
        if registry.exc_info and registry.exc_info[1] is not None:
            text += f" {registry.exc_info[1]}"
        if any(e in text for e in self._NETWORK_ERRORS):
            registry.msg, registry.args = "zeroconf: the network didn't answer (%s)", (text.strip().splitlines()[-1][:200],)
            registry.exc_info = registry.exc_text = registry.stack_info = None
            registry.levelno, registry.levelname = logging.WARNING, "WARNING"
        return True


def mute_zeroconf_traces():
    logger = logging.getLogger("zeroconf")
    if not any(isinstance(f, _ZeroconfWithoutTraces) for f in logger.filters):
        logger.addFilter(_ZeroconfWithoutTraces())


class BridgeServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second CopyOver listen on the same port and take some of the requests.
    # Without it, the second one can't start.
    allow_reuse_address = sys.platform != "win32"
    # The per-run secret the panel and the local helpers must send (servir() sets it). Empty: /app is closed.
    local_secret = ""
    # TLS and HTTP on the same port: PCs paired with encryption come in over TLS; iPhone, Android and the
    # panel stay on HTTP. The first byte decides (0x16 = TLS ClientHello).
    certificates = None
    FIRST_BYTE_WAIT = 15.0
    TLS_GREETING_WAIT = 10.0

    def server_bind(self):
        # On Windows, without SO_EXCLUSIVEADDRUSE another program (even of another user) can bind 127.0.0.1 on
        # this same port with SO_REUSEADDR and receive what the panel and the local helpers send.
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def finish_request(self, request, client_address):
        # Runs in the request's thread: a client that sends nothing only ties up its own, and briefly.
        try:
            request.settimeout(self.FIRST_BYTE_WAIT)
            first = request.recv(1, socket.MSG_PEEK)
        except (TimeoutError, OSError):
            return
        context = self.certificates.server_context() if self.certificates is not None else None
        if first != b"\x16" or context is None:
            request.settimeout(None)
            return super().finish_request(request, client_address)
        try:
            request.settimeout(self.TLS_GREETING_WAIT)
            encrypted = context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, TimeoutError, OSError) as error:
            logging.info("Failed TLS connection from %s: %s", client_address[0], error)
            return
        try:
            encrypted.settimeout(None)
            super().finish_request(encrypted, client_address)
        finally:
            try:
                encrypted.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        # socketserver's handler prints to stderr, which doesn't exist under pythonw: the error was lost.
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            logging.info("%s closed the connection (%s)", client_address[0], type(error).__name__)
        else:
            logging.exception("Error while handling a request from %s", client_address[0])


def register_uncaught_errors():
    """An uncaught exception (in the main thread or another) ends up in the log with its traceback."""
    def in_main(kind, value, trace):
        if issubclass(kind, KeyboardInterrupt):
            return sys.__excepthook__(kind, value, trace)
        logging.critical("Uncaught error", exc_info=(kind, value, trace))

    def in_thread(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        logging.error("Uncaught error in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = in_main
    threading.excepthook = in_thread


def open_open_instance_panel(port: int) -> bool:
    """If CopyOver is already running (opened again from Start), it's asked to show its panel."""
    import http.client
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: so its window can come to the front
    # A running copy of 1.0.6 or earlier only knows the Spanish route, so that one is tried when the English one is missing.
    for path in ("/app/action", "/app/accion"):
        try:
            con = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                con.request("POST", path, body=json.dumps({"action": "open_panel"}),
                            headers={"Content-Type": "application/json", local_secret.HEADER: local_secret.read()})
                status = con.getresponse().status
            finally:
                con.close()
        except OSError:
            return False
        if status != 404:
            return status == 200
    return False


def _port_is_denied(port: int) -> bool:
    """WSAEACCES instead of «in use»: usually a port Windows reserved (Hyper-V, WSL, Docker), which no process holds."""
    if sys.platform != "win32":
        return False
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError as error:
            return error.winerror == 10013
    return False


def notify_busy_port(port: int) -> None:
    """Under pythonw nobody reads the console: if the port is taken, a window says what's going on and what to
    do."""
    if _port_is_denied(port):
        logging.error("Windows denies port %s: CopyOver can't start", port)
        text = t("Windows isn’t letting CopyOver use port {port}. It may be reserved by Windows (Hyper-V, WSL or "
                 "Docker) or held by another program.\n\nRestart the PC. If it keeps happening, change «port» in "
                 "config.json to another number and set up your phones again.",
                 port=port)
    else:
        logging.error("Port %s is used by another program: CopyOver can't start", port)
        text = t("Port {port} is being used by another program, so CopyOver can’t start.\n\nIf it’s another CopyOver "
                 "that was left open, close it from its icon next to the clock (or from Task Manager) and open "
                 "CopyOver again. If you don’t know which program it is, restart the PC.",
                 port=port)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10 | 0x40000)  # MB_ICONERROR | MB_TOPMOST
    elif sys.stderr is not None:
        print(text, file=sys.stderr)


def _server_with_retries(port: int, attempts: int = 30, wait: float = 0.5):
    """When restarting from the tray, the old process takes a moment to release the port: keep trying for up
    to 15 s."""
    for attempt in range(attempts):
        try:
            return BridgeServer(("0.0.0.0", port), Handler)
        except OSError as error:
            if attempt == attempts - 1:
                logging.error("Couldn't open port %s: %r", port, error)
                return None
            time.sleep(wait)
    return None


def _automatic_startup():
    """(is it on?, turn on/off) for the tray's «Start with Windows» checkbox."""
    def change(enable):
        try:
            if enable:
                so.enable_startup(Path(__file__).resolve())
            else:
                so.disable_startup()
        except Exception:
            logging.exception("Couldn't change the automatic startup")
    return so.installed, change


def relaunch():
    """Starts another CopyOver identical to this one; the current one exits right away and the new one takes
    the port."""
    if sys.platform.startswith("linux") and os.environ.get("INVOCATION_ID"):
        # As a systemd service, a child process dies with this one: systemd does the restart.
        subprocess.Popen(["systemctl", "--user", "--no-block", "restart", so.SERVICE.name])
        return
    options: dict = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(bundle.command("bridge", executable=sys.executable, script=__file__), cwd=str(FOLDER),
                     close_fds=True, **options)


def prepare_certificates(cfg):
    """Creates or loads the CA and the server certificate. If it fails, CopyOver keeps going without TLS (as
    before)."""
    import certificates
    certs = certificates.Certificates(name=cfg.get("name", "puente-pc"), ips=network.own_ips)
    try:
        certs.ensure()
        logging.info("Encryption between PCs ready (CA fingerprint %s)", certs.ca_fingerprint()[:16])
        return certs
    except Exception:
        logging.exception("Couldn't prepare the certificates: connections between PCs will be unencrypted")
        return None


def announce_new_version(tag: str) -> None:
    """Shows a new version in the panel and notifies once. It doesn't depend on the tray: Linux desktops may have none."""
    import updates

    STATE["new_version"] = tag
    if sys.platform != "win32":
        so.notify("CopyOver", t("A new version is available: {version}. To update, run install.sh again.", version=tag))
    elif updates.self_updates():
        so.notify("CopyOver", t("A new version is available: {version}. Install it from the panel.", version=tag))
    else:
        so.notify("CopyOver", t("A new version is available: {version}. Download it from the panel.", version=tag),
                  link=updates.RELEASES_PAGE)


def serve(cfg):
    from sync import ClipboardChanges
    from updates import UpdateCache, self_updates
    from pairing import PairingManager, confirm_in_desktop

    # Port first: if another program has it, there's no point starting the rest.
    server = _server_with_retries(cfg["port"])
    if server is None:
        # Opened twice while the first one was starting: at the first check it couldn't answer yet.
        if open_open_instance_panel(cfg["port"]):
            logging.info("CopyOver had just started: its panel was shown")
            sys.exit(0)
        notify_busy_port(cfg["port"])
        sys.exit(1)
    # Only now that the port is ours: a second CopyOver that can't start doesn't replace the first one's secret.
    server.local_secret = local_secret.create()
    if sys.platform == "win32":
        so._register_notice_protocol()
    temporary()
    recover_last_phone_from_log(cfg)
    load_history()
    threading.Thread(target=_clean_periodically, daemon=True).start()
    threading.Thread(target=_watch_online, daemon=True).start()
    Handler.key = cfg["key"]
    Handler.identifier = cfg["id"]
    Handler.links = PairingManager(cfg, confirm_in_desktop, issue_key=issue_android_key,
                                             ca_pem=own_ca)
    Handler.notices = cfg.get("notifications", True)
    Handler.changes = ClipboardChanges(so.clipboard_version) if sys.platform == 'win32' else None
    Handler.updates = UpdateCache(FOLDER / '.android-updates',
                                            on_view_version=announce_new_version)
    Handler.updates.start()
    Handler.p2p_manager = _create_p2p_manager(cfg)
    Handler.pairing_manager = _create_pairing_manager(cfg)
    server.certificates = Handler.certificates = prepare_certificates(cfg)
    threading.Thread(target=announce_in_network, args=(cfg,), daemon=True).start()
    server.service_actions = Handler.clean_expired_transfers
    logging.info("CopyOver listening on http://%s:%s", ip_local(), cfg["port"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        so.start_global_shortcut(lambda: tray_actions.send_phone(cfg['port'], linked_types=linked_types))
    except Exception:
        pass

    if cfg.get("tray_icon", True):
        import tray
        import preview

        name = cfg.get("name", "puente-pc")
        actions = {
            "send_phone": lambda: tray_actions.send_phone(cfg['port'], linked_types=linked_types),
            "send_pc": lambda: tray_actions.send_pc(load_config(), list(STATE['pcs']), send_command, save_pc_key,
                                                            own_ca=own_ca()),
            "open_panel": lambda: dialogs.launch_web_window(cfg['port']),
            "link_iphone": lambda: open_panel_in("shortcut", cfg['port']),
            "link_android": lambda: open_panel_in("android", cfg['port']),
            "open_folder": lambda: so.open_stream(get_downloads_folder()),
            "manage_pcs": lambda: tray_actions.manage_pcs(load_config, lambda: list(STATE["pcs"]), modify_pc),
            "connect_pc": lambda: tray_actions.connect_pc(load_config(), list(STATE['pcs']), save_pc_key,
                                                           own_ca=own_ca()),
        }
        Handler.actions = actions
        if open_panel_on_start(cfg):
            # First run: show something, and the welcome card explains the three steps.
            threading.Timer(1.5, actions["open_panel"]).start()
        # The quick view is pywebview on WebView2: Windows only. On Linux the icon opens the native menu.
        view = preview.Launcher(cfg['port']) if sys.platform == "win32" else None
        if view:
            threading.Thread(target=view.warm_up, daemon=True).start()
        try:
            if tray.show(
                STATE,
                name,
                actions["open_folder"],
                lambda: so.open_stream(LOG),
                open_p2p_folder=lambda: so.open_stream(get_downloads_folder("p2p")),
                send_phone=actions["send_phone"],
                link_iphone=actions["link_iphone"],
                link_android=actions["link_android"],
                connect_pc=actions["connect_pc"],
                send_pc=actions["send_pc"],
                manage_pcs=actions["manage_pcs"],
                open_panel=actions["open_panel"],
                open_preview=view.show if view else None,
                restart=lambda: STATE.__setitem__("restart", True),
                automatic_startup=_automatic_startup() if sys.platform == "win32" else None,
                update=Handler.updates.install_new_version if self_updates() else None,
            ):
                # Nothing is left orphaned on quit or restart: neither the quick view nor the panel window.
                if view:
                    view.close()
                dialogs.close_web_window()
                server.shutdown()
                server.server_close()
                if STATE.get("restart"):
                    logging.info("Restarting CopyOver from the tray icon")
                    relaunch()
                else:
                    logging.info("CopyOver closed from the tray icon")
                return
        finally:
            if view:
                view.close()
        logging.info("No system tray available; CopyOver keeps running without an icon")
    thread.join()


def _matches_destination(pc, destination):
    aliases = (pc.get('id'), pc.get('name'), pc.get('host'), pc.get('ip'),
               f"{pc.get('ip')}:{pc.get('port')}")
    return destination.casefold() in {str(alias).casefold() for alias in aliases if alias}


def rediscover_pc(peer_id):
    cfg = load_config()
    nearby = p2p.query_local_pcs(cfg.get('port', 8765), local_secret.read()) or []
    pc = next((pc for pc in nearby if pc.get('id') == peer_id), None)
    return (pc['ip'], int(pc['port'])) if pc else None


def _send_canceller(cfg, peer_id):
    event = threading.Event()
    review = [float('-inf'), False]
    with _CONFIG_LOCK:
        _PC_SENDS.setdefault(peer_id, set()).add(event)

    def cancelled():
        if event.is_set():
            return True
        if peer_id not in cfg.get('pcs', {}):
            return False
        if time.monotonic() - review[0] >= 0.5:
            current = load_config().get('pcs', {}).get(peer_id)
            review[:] = [time.monotonic(), current != cfg['pcs'][peer_id]]
        return review[1]

    return event, cancelled


def _resolve_send_destination(cfg, arg_destination: str) -> dict | None:
    """Keeps the announcement's identity and key when choosing by name, IP or identifier."""
    port = cfg["port"]
    pcs = p2p.query_local_pcs(port, local_secret.read())
    if pcs is None:
        pcs = p2p.discover_network_pcs(cfg["id"])
    pcs = linked_pcs.with_alias(cfg, pcs)
    if arg_destination:
        known = next((pc for pc in pcs if _matches_destination(pc, arg_destination)), None)
        if known:
            return known
        host = arg_destination
        if ":" in arg_destination:
            host, port_text = arg_destination.rsplit(":", 1)
            port = int(port_text)
        return {"ip": host, "port": port, "name": host, "id": ""}
    if not pcs:
        return None
    if len(pcs) == 1:
        return pcs[0]
    return dialogs.choose_pc(pcs)


# With several files selected, Explorer opens one «CopyOver.exe --send» per file. Each one hands its file to
# the running CopyOver, which sends them together. If none is running, each process sends on its own.
LOCAL_SEND_PATH = "/local/send"
LOCAL_NOTICE_PATH = "/local/notice"
BATCH_WAIT = 1.0  # seconds to wait for the other files in the same selection
_BATCH = {"paths": [], "timer": None}
_LOCK_BATCH = threading.Lock()


def join_to_send(paths: list[Path]) -> None:
    with _LOCK_BATCH:
        for path in paths:
            if path not in _BATCH["paths"]:
                _BATCH["paths"].append(path)
        if _BATCH["timer"]:
            _BATCH["timer"].cancel()
        timer = threading.Timer(BATCH_WAIT, _send_batch)
        timer.daemon = True
        _BATCH["timer"] = timer
        timer.start()


def _send_batch() -> None:
    with _LOCK_BATCH:
        paths, _BATCH["paths"], _BATCH["timer"] = _BATCH["paths"], [], None
    if not paths:
        return
    logging.info("Sending %d file(s) chosen in File Explorer", len(paths))
    try:
        send_command(load_config(), [str(r) for r in paths])
    except Exception:
        logging.exception("Couldn't send what was chosen in File Explorer")


def resend_to_open_instance(cfg, args: list[str]) -> bool:
    """CopyOver.exe --send file: if CopyOver is already running, hands it the file to batch with the others.
    With «--to <pc>» (the tray or a script) it sends directly, as always."""
    if any(a in ("--a", "-a", "--to") for a in args) or not args:
        return False
    paths = [str(Path(a).resolve()) for a in args]
    if not all(Path(r).exists() for r in paths):
        return False  # so the direct send explains which file is missing
    return _request_to_open_instance(cfg, route_to_old(LOCAL_SEND_PATH), {"paths": paths})


def pass_notice_to_open_instance(cfg, args: list[str]) -> bool:
    """CopyOver.exe --notification copyover-notice:<code>/<action>: the «Open» or «Show in folder» button of a
    notification. The running CopyOver handles it, since it's the one that knows which file the code
    belongs to."""
    _, _, rest = (args[0] if args else "").partition(":")
    code, _, action = rest.strip("/").partition("/")
    return bool(code) and _request_to_open_instance(cfg, route_to_old(LOCAL_NOTICE_PATH), {"code": code, "action": action.strip("/")})


def _request_to_open_instance(cfg, path: str, data: dict) -> bool:
    import http.client
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # so the window it opens can come to the front
    port = cfg["port"]
    body = json.dumps(data).encode("utf-8")
    try:
        con = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            # The local secret, never the shared key: if another program holds the port, it gets nothing useful.
            con.request("POST", path, body=body, headers={
                "Host": f"127.0.0.1:{port}", local_secret.HEADER: local_secret.read(),
                "Content-Type": "application/json"})
            return con.getresponse().status == 200
        finally:
            con.close()
    except OSError:
        return False


def send_command(cfg, args: list[str]) -> int:
    arg_destination = ""
    str_paths = []
    i = 0
    while i < len(args):
        if args[i] in ("--a", "-a", "--to") and i + 1 < len(args):
            arg_destination = args[i + 1]
            i += 2
        else:
            str_paths.append(args[i])
            i += 1

    paths = [Path(r).resolve() for r in str_paths]
    missing = [r for r in paths if not r.exists()]
    if not paths or missing:
        msg = t("File not found: {path}", path=missing[0]) if missing else t("Specify at least one file to send.")
        if sys.stdout is not None:
            print(msg, file=sys.stderr)
        return 1

    dest = _resolve_send_destination(cfg, arg_destination)
    if not dest:
        msg = t("No nearby computers with CopyOver running were found.")
        if sys.stdout is not None:
            print(msg, file=sys.stderr)
        so.notify(t("Couldn’t send"), msg)
        return 1

    ip = dest["ip"]
    port = int(dest["port"])
    dest_name = dest["name"]
    peer_id = dest.get("id", "")
    # Only to a linked PC, with that link's token. No key is sent to any other: this PC's key would work for
    # anyone announcing themselves as CopyOver on the network, and the other PC would reject it anyway.
    send_key = cfg.get("pcs", {}).get(peer_id, "") if peer_id else ""
    if not send_key:
        msg = t("Link {name} first: in the tray, Link > Another PC.", name=dest_name)
        if sys.stdout is not None:
            print(msg, file=sys.stderr)
        so.notify(t("Couldn’t send to {name}", name=dest_name), msg)
        return 1
    my_name = cfg.get("name", socket.gethostname())
    try:
        destination_ca = linked_pcs.ca_of(cfg, peer_id) if peer_id in cfg.get("pcs", {}) else None
    except ValueError:
        msg = t("The saved encryption data for {name} is damaged. Unlink it and link it again.", name=dest_name)
        if sys.stdout is not None:
            print(msg, file=sys.stderr)
        so.notify(t("Couldn’t send to {name}", name=dest_name), msg)
        return 1
    if peer_id in cfg.get("pcs", {}) and not destination_ca:
        logging.warning("The transfer to %s is unencrypted: they were linked with an older version of CopyOver. "
                        "Link them again.", dest_name)

    if sys.stdout is not None:
        print(t("Offering {count} file(s) to {name} ({address})...", count=len(paths), name=dest_name, address=f"{ip}:{port}"))
        monitor = None
    else:
        monitor = dialogs.ProgressMonitor(dest_name)

    last_pct = [-1]

    def show_progress(sent: int, total: int, file: str):
        if monitor:
            monitor.update(sent, total, file)
        if sys.stdout is None or total <= 0:
            return
        pct = int((sent * 100) / total)
        if pct != last_pct[0] and (pct % 10 == 0 or pct == 100):
            last_pct[0] = pct
            print(f"  {file}: {pct}% ({p2p.format_size(sent)} / {p2p.format_size(total)})")

    send_event, cancelled = _send_canceller(cfg, peer_id)
    try:
        ok, detail = p2p.send_to_pc(
            ip=ip,
            port=port,
            key=send_key,
            my_name=my_name,
            my_id=cfg["id"],
            paths=paths,
            to_progress=show_progress,
            resolve_destination=(lambda: rediscover_pc(peer_id)) if peer_id else None,
            cancel=cancelled,
            destination_name=dest_name,
            ca_pem=destination_ca,
        )
    finally:
        with _CONFIG_LOCK:
            _PC_SENDS.get(peer_id, set()).discard(send_event)
            if not _PC_SENDS.get(peer_id):
                _PC_SENDS.pop(peer_id, None)
        if monitor:
            monitor.close()

    if sys.stdout is not None:
        print(detail if ok else t("Error: {detail}", detail=detail), file=sys.stdout if ok else sys.stderr)
    so.notify(t("Sent to {name}", name=dest_name) if ok else t("Couldn’t send to {name}", name=dest_name), detail)
    return 0 if ok else 1


def pair_command(cfg, args: list[str]) -> int:
    arg_destination = args[0] if args else ""
    dest = _resolve_send_destination(cfg, arg_destination)
    if not dest:
        print(t("No nearby computers found to pair with."), file=sys.stderr)
        return 1
    print(t("Pairing with {name} ({ip})...", name=dest["name"], ip=dest["ip"]))

    def confirm(code):
        print(t("Verification code: {code}", code=f"{code[:3]} {code[3:]}"))
        if sys.stdin is not None and sys.stdin.isatty():
            return input(t("Do you see the same code on the other PC? (y/n): ")).strip().lower() in ("s", "si", "sí", "y", "yes")
        return dialogs.confirm_pairing(dest["name"], code)

    certs = prepare_certificates(cfg)
    already_encrypted = bool(dest.get("id")) and bool(cfg.get("pcs_info", {}).get(dest["id"], {}).get("ca"))
    try:
        current_ca = linked_pcs.ca_of(cfg, dest["id"]) if already_encrypted else None
    except ValueError:
        current_ca = None
    ok, _, token_o_err, remote_ca = p2p.pair_with_pc(
        ip=dest["ip"],
        port=int(dest["port"]),
        my_name=cfg.get("name", socket.gethostname()),
        my_id=cfg["id"],
        confirm_code=confirm,
        my_ca_pem=certs.ca_pem() if certs else "",
        expected_id=dest.get("id", ""),
        allow_unencrypted=allow_unencrypted(cfg) and not already_encrypted,
        announces_v2=str(dest.get("pairing", "")) == "2",
        current_ca=current_ca,
        current_token=cfg.get("pcs", {}).get(dest.get("id", ""), "") if current_ca else "",
    )
    if not ok:
        print(t("Couldn’t pair: {error}", error=token_o_err), file=sys.stderr)
        return 1
    if dest.get("id"):
        save_pc_key(dest["id"], token_o_err, remote_ca)
    print(t("Done: paired with {name}.", name=dest["name"]))
    if not remote_ca:
        print(t("Note: the connection with {name} is not encrypted because it has an older version of CopyOver. "
                "Update it and link them again.", name=dest["name"]))
    return 0



def show_qr(cfg):
    from linking import save_qr

    private = Path(tempfile.mkdtemp(prefix="copyover-link-"))
    path = save_qr(cfg, ip_local(), private / "link-qr.png")
    so.open_stream(path)

def _run_mode(mode):
    """CopyOver.exe --preview|--panel|--dialog: the same exe acts as the helper window."""
    del sys.argv[1]
    if mode == "preview":
        import preview
        return preview.main()
    if mode == "panel":
        import panel_window
        return panel_window.main()
    return dialogs.main()


# The commands are in English. The Spanish names of 1.0.6 and earlier still work, so menus, shortcuts
# and installers written by an older version keep working.
OLD_NAMES = {"--enviar": "--send", "--emparejar": "--pair", "--instalar": "--install",
                  "--desinstalar": "--uninstall", "--vincular": "--link", "--aviso": "--notification"}


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = OLD_NAMES.get(arg, arg)
    if arg in bundle.MODES:
        return _run_mode(bundle.MODES[arg])
    cfg = load_config()
    if arg == "--info":
        host = f"{cfg.get('name', 'puente-pc')}.local"
        print(t("URL to send:     {url}", url=f"http://{host}:{cfg['port']}/send"))
        print(t("URL to fetch:    {url}", url=f"http://{host}:{cfg['port']}/receive"))
        print(t("Header:          X-Key = {key}", key=cfg["key"]))
        print(t("(Current PC IP: {ip})", ip=ip_local()))
        return
    if arg == "--link":
        print(t("Open CopyOver on Android, choose this PC and accept the request on the desktop."))
        return
    if arg == "--qr":  # Compatibility with the first Android versions.
        show_qr(cfg)
        return
    if arg == "--send":
        if resend_to_open_instance(cfg, sys.argv[2:]):
            sys.exit(0)
        sys.exit(send_command(cfg, sys.argv[2:]))
    if arg == "--notification":
        sys.exit(0 if pass_notice_to_open_instance(cfg, sys.argv[2:]) else 1)
    if arg == "--pair":
        sys.exit(pair_command(cfg, sys.argv[2:]))
    if arg == "--install":
        return so.install(Path(__file__).resolve().with_name("copyover.py"))
    if arg == "--uninstall":
        return so.uninstall()

    private_files.close_data_folder(FOLDER)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=log_handlers())
    register_uncaught_errors()
    mute_zeroconf_traces()
    logging.info("%s v%s starting (pid %s)", APP_NAME, VERSION, os.getpid())
    atexit.register(lambda: logging.info("%s v%s closed (pid %s)", APP_NAME, VERSION, os.getpid()))
    if open_open_instance_panel(cfg["port"]):
        logging.info("CopyOver was already running: its panel was shown")
        return
    serve(cfg)


if __name__ == "__main__":
    main()
