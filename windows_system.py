"""Clipboard, notifications, context menu and autostart on Windows."""

import base64
import ctypes
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import bundle
from i18n import t

HELPER = bundle.resource("helper.ps1")
DOWNLOADS = Path.home() / "Downloads"
STARTUP_NAME = "CopyOver.lnk"
START_MENU_NAME = "CopyOver.lnk"
SENDTO_NAME = "Enviar con CopyOver.lnk"  # the Spanish name; the shortcut is created with sendto_name()
MENU_KEY = r"Software\Classes\*\shell\SendWithCopyOver"
# Names from earlier versions, deleted on install or uninstall.
OLD_STARTUP = ("Puente Portapapeles.lnk",)
OLD_START_MENU = ("Puente.lnk",)
OLD_SENDTO = ("Enviar con Puente.lnk",)
OLD_MENU_KEYS = (r"Software\Classes\*\shell\EnviarConPuente", r"Software\Classes\*\shell\EnviarConCopyOver")


def _documents_folder() -> Path:
    """Documents, wherever Windows keeps it (OneDrive can move it out of the user folder)."""
    path = ctypes.create_unicode_buffer(260)
    try:
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, path) == 0 and path.value:  # 5: CSIDL_PERSONAL
            return Path(path.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


# Received files go in Documents unless another folder was chosen in Settings.
DOCUMENTS = _documents_folder()

CF_BITMAP, CF_DIB, CF_UNICODETEXT, CF_HDROP = 2, 8, 13, 15
GMEM_MOVEABLE = 0x0002

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT


def clipboard_version():
    return int(user32.GetClipboardSequenceNumber())


def open_clipboard():
    # Another app may have it open for a moment; retry briefly.
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise RuntimeError(t("The clipboard is in use by another app"))


def set_text(text):
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-16-le") + b"\x00\x00"
    open_clipboard()
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise RuntimeError(t("Couldn’t write to the clipboard"))
    finally:
        user32.CloseClipboard()


def read_text():
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(ptr).replace("\r\n", "\n")
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def read_files():
    """Paths of the files copied in Explorer (Ctrl+C)."""
    if not user32.IsClipboardFormatAvailable(CF_HDROP):
        return []
    open_clipboard()
    try:
        h = user32.GetClipboardData(CF_HDROP)
        if not h:
            return []
        paths = []
        for i in range(shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)):
            length = shell32.DragQueryFileW(h, i, None, 0) + 1
            buf = ctypes.create_unicode_buffer(length)
            shell32.DragQueryFileW(h, i, buf, length)
            paths.append(Path(buf.value))
        return paths
    finally:
        user32.CloseClipboard()


def helper(mode, *values, wait=True):
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
           "-File", str(HELPER), mode, *map(str, values)]
    if not wait:
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        logging.error("helper.ps1 %s failed: %s", mode, r.stderr.strip())
    return r.returncode == 0


def set_image(path):
    return helper("set-image", path)


def read_image(path):
    """Saves the copied image to `ruta` as PNG, if there is one."""
    if not (user32.IsClipboardFormatAvailable(CF_DIB) or user32.IsClipboardFormatAvailable(CF_BITMAP)):
        return False
    return helper("get-image", path)


def set_files(paths):
    return helper("set-files", *paths)


NOTICES_ID = "CopyOver.Escritorio"  # same AppUserModelID as the tray
NOTICES_ID_KEY = rf"Software\Classes\AppUserModelId\{NOTICES_ID}"
_registered_notices_id = False


def _register_notices_id():
    """Windows takes each notification's name and icon from this key (without it they showed as «Windows
    PowerShell»)."""
    global _registered_notices_id
    if _registered_notices_id:
        return True
    try:
        import winreg
        import tray

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, NOTICES_ID_KEY) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "CopyOver")
            icon = tray.ensure_ico()
            if icon.exists():
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(icon))
        _registered_notices_id = True
    except Exception:
        logging.exception("Couldn't register the notifications' name")
    return _registered_notices_id


# A notification's buttons open their address through a protocol (Windows launches it even when the
# notification is already in the notification center). A file can't be opened that way (Windows doesn't launch
# file:), and PowerShell 5.1 can't wait for the click: the notification opens copyover-notice:<code>/<action>,
# which launches «CopyOver --notification», and that passes the code to the running CopyOver. The code is random and
# for a single notification: a web page that calls copyover-notice: doesn't have a valid one, so it can't open
# anything.
NOTICE_PROTOCOL = "copyover-notice"
NOTICE_PROTOCOL_KEY = rf"Software\Classes\{NOTICE_PROTOCOL}"
OLD_NOTICE_PROTOCOL_KEY = r"Software\Classes\copyover-aviso"  # registered by earlier versions
_registered_notice_protocol = False
_FILE_NOTICES: dict = {}  # code -> (path, can be opened); the most recent ones
MAX_FILE_NOTICES = 50
# What runs when it's opened: from the notification it's only shown in its folder.
NO_OPEN_FROM_NOTICE = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".cpl", ".msc", ".ps1", ".vbs",
                        ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".jar", ".appref-ms"}


def _delete_notice_protocol(key):
    import winreg

    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key + sub)
        except OSError:
            pass


def _register_notice_protocol():
    global _registered_notice_protocol
    if _registered_notice_protocol:
        return True
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, NOTICE_PROTOCOL_KEY) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:CopyOver")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, NOTICE_PROTOCOL_KEY + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _between_quotes(bundle.command("bridge")) + ' --notification "%1"')
        _delete_notice_protocol(OLD_NOTICE_PROTOCOL_KEY)
        shell32.SHChangeNotify(0x08000000, 0, None, None)
        _registered_notice_protocol = True
    except Exception:
        logging.exception("Couldn't register the notifications' protocol")
    return _registered_notice_protocol


def _file_buttons(file, open_file):
    """The «Open» and «Show in folder» addresses for a received file (none without the protocol)."""
    if not _register_notice_protocol():
        return "", []
    path = Path(file).resolve()
    can_open = open_file and path.suffix.lower() not in NO_OPEN_FROM_NOTICE
    code = secrets.token_urlsafe(16)
    _FILE_NOTICES[code] = (path, can_open)
    while len(_FILE_NOTICES) > MAX_FILE_NOTICES:
        _FILE_NOTICES.pop(next(iter(_FILE_NOTICES)))
    folder = f"{NOTICE_PROTOCOL}:{code}/folder"
    if not can_open:
        return folder, [[t("Show in folder"), folder]]
    open_dir = f"{NOTICE_PROTOCOL}:{code}/open"
    return open_dir, [[t("Open"), open_dir], [t("Show in folder"), folder]]


def serve_notice(code, action):
    """What a file notification's button asked for. False if the code isn't from a notification in this
    session."""
    path, can_open = _FILE_NOTICES.get(code, (None, False))
    if path is None or action not in ("open", "folder"):
        return False
    if action == "open" and can_open and path.is_file():
        open_stream(path)
    else:
        highlight_in_explorer(path)
    return True


def notify(title, text, link="", note="", file="", open_file=True):
    """Windows notification. With an http(s) link, tapping the notification or «Open» opens it in the browser;
    with a received file, «Open» and «Show in folder» (with open_file=False, only the latter, e.g. for
    several files)."""
    import links

    data = {"title": title, "text": text, "note": note, "app": NOTICES_ID if _register_notices_id() else ""}
    link = links.web_link(link)
    if link:
        data.update(launch=link, buttons=[[t("Open"), link]])
    elif file:
        data["launch"], data["buttons"] = _file_buttons(file, open_file)
    encrypted = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
    # In a thread, waiting for the helper: if a notification doesn't show, the log says why.
    threading.Thread(target=helper, args=("notify", encrypted), daemon=True).start()


def open_stream(path):
    """Opens a file or folder with the Windows default app."""
    p = Path(path).resolve()
    if not p.suffix or p.is_dir():
        p.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["explorer.exe", str(p)])
            return True
        except Exception:
            pass
    # No «cmd /c start» fallback: the file name comes from another device and cmd would interpret & | ^.
    try:
        os.startfile(str(p))
        return True
    except OSError:
        return False


def highlight_in_explorer(path):
    """Opens Windows Explorer with the file selected."""
    p = Path(path).resolve()
    if p.is_file():
        subprocess.Popen(["explorer.exe", f"/select,{p}"])
    elif p.is_dir():
        subprocess.Popen(["explorer.exe", str(p)])
    else:
        if p.parent.exists():
            subprocess.Popen(["explorer.exe", str(p.parent)])


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def to_trash(paths) -> bool:
    """Sends the files to the Recycle Bin without asking or showing progress."""
    paths = [str(Path(r).resolve()) for r in paths]
    if not paths:
        return True
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    operation = _SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom="\0".join(paths) + "\0",
                                 fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    return result == 0 and not operation.fAnyOperationsAborted


# --- Start with Windows and context menu -------------------------------------


def startup_folder():
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"


def folder_send_to():
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/SendTo"


def programs_folder():
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"


def _executable_without_window() -> Path:
    return Path(bundle.gui_executable())


def _between_quotes(parts) -> str:
    return " ".join(f'"{p}"' for p in parts)


def context_menu_command(script) -> str:
    return _between_quotes(bundle.command("bridge", script=script)) + ' --send "%1"'


def access_send_to(script) -> tuple[str, str]:
    """Program and arguments of the «Send to» shortcut. With the .exe, bridge.py's path isn't passed."""
    parts = bundle.command("bridge", script=script)
    return parts[0], " ".join([_between_quotes(parts[1:]), "--send"]).strip()


def _create_shortcut(lnk_path: Path, exe: Path, arguments: str, work_folder: Path):
    import tray

    icon = tray.ensure_ico()
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:P_SHORTCUT);"
        "$s.TargetPath=$env:P_EXE;$s.Arguments=$env:P_ARGS;"
        "$s.WorkingDirectory=$env:P_FOLDER;"
        "if(Test-Path $env:P_ICON){$s.IconLocation=$env:P_ICON};"
        "$s.Save()"
    )
    env = dict(
        os.environ,
        P_SHORTCUT=str(lnk_path),
        P_EXE=str(exe),
        P_ARGS=arguments,
        P_FOLDER=str(work_folder),
        P_ICON=str(icon),
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True)


def sendto_name() -> str:
    """The «Send to» shortcut is named like the menu, in CopyOver's language."""
    return t("Send with CopyOver") + ".lnk"


def sendto_names() -> tuple:
    """The shortcut's names in every language, to remove the one left over after a language change."""
    return tuple(dict.fromkeys((SENDTO_NAME, "Send with CopyOver.lnk", sendto_name())))


def install_context_menu(script):
    import winreg
    import tray

    script = Path(script).resolve()
    icon = tray.ensure_ico()
    cmd = context_menu_command(script)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, MENU_KEY) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, t("Send with CopyOver"))
        if icon.exists():
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, str(icon))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, MENU_KEY + r"\command") as k_cmd:
        winreg.SetValueEx(k_cmd, "", 0, winreg.REG_SZ, cmd)

    sendto = folder_send_to()
    if sendto.is_dir():
        name = sendto_name()
        _delete_accesses(sendto, [n for n in sendto_names() if n != name])
        exe, arguments = access_send_to(script)
        _create_shortcut(sendto / name, Path(exe), arguments, script.parent)


def _delete_menu_keys(keys):
    import winreg

    for key in keys:
        for sub in (key + r"\command", key):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass


def _delete_accesses(folder: Path, names):
    for name in names:
        access = folder / name
        try:
            if access.exists():
                access.unlink()
        except OSError:
            logging.warning("Couldn't delete %s", access)


def remove_old_versions():
    """Deletes the shortcuts and the context menu with the names from before CopyOver."""
    _delete_menu_keys(OLD_MENU_KEYS)
    _delete_accesses(startup_folder(), OLD_STARTUP)
    _delete_accesses(programs_folder(), OLD_START_MENU)
    _delete_accesses(folder_send_to(), OLD_SENDTO)


def remove_context_menu():
    _delete_menu_keys((MENU_KEY,))
    _delete_accesses(folder_send_to(), sendto_names())


def _allow_in_firewall():
    """Creates the «CopyOver» firewall rule only if needed (so UAC only shows when it helps)."""
    import diagnostics
    import network
    exe = str(_executable_without_window())
    # The real IP: with a made-up one every network was checked (including Tailscale, which is «Public») and
    # UAC came up even when the network the phone uses was already allowed.
    problems = diagnostics.check({}, exe=exe, ip=network.ip_local(), announced=True, attributes=lambda _: 0)
    if any(p["key"] == "firewall" for p in problems):
        if diagnostics.fix("firewall", exe=exe):
            print(t("Firewall rule created: your phone can now connect to this PC."))
        else:
            print(t("Couldn’t create the firewall rule; you can do it later from the panel."))


def install(script):
    script = Path(script).resolve()
    _register_notice_protocol()
    remove_old_versions()
    exe, *arguments = bundle.command("bridge", script=script)
    access = startup_folder() / STARTUP_NAME
    _create_shortcut(access, Path(exe), _between_quotes(arguments), bundle.data_folder())
    menu_access = programs_folder() / START_MENU_NAME
    _create_shortcut(menu_access, Path(exe), _between_quotes(arguments), bundle.data_folder())
    install_context_menu(script)
    _allow_in_firewall()
    try:
        shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass
    print(t("Done: CopyOver will start automatically with Windows, appears in the Start menu and has the “Send with CopyOver” right-click option.")
          + f"\n  {access}\n  {menu_access}")


def installed() -> bool:
    return (startup_folder() / STARTUP_NAME).exists()


def enable_startup(script):
    """The «Start with Windows» checkbox: autostart, and also what the README promises (the Start menu
    shortcut, the «Send with CopyOver» right-click entry and «Send to»). Not the firewall: that asks for
    UAC and is separate."""
    script = Path(script).resolve()
    exe, *arguments = bundle.command("bridge", script=script)
    _create_shortcut(startup_folder() / STARTUP_NAME, Path(exe), _between_quotes(arguments), bundle.data_folder())
    try:
        _create_shortcut(programs_folder() / START_MENU_NAME, Path(exe), _between_quotes(arguments),
                              bundle.data_folder())
        install_context_menu(script)
        shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        # Autostart is already set: a failure in the Start menu or the right-click menu shouldn't undo it.
        logging.exception("Couldn't create the Start menu shortcut or the right-click entry")


def disable_startup():
    """Removes only the autostart: the Start menu and the right-click entries stay."""
    access = startup_folder() / STARTUP_NAME
    if access.exists():
        access.unlink()


def uninstall(remove_firewall=True):
    access = startup_folder() / STARTUP_NAME
    was = access.exists()
    if was:
        access.unlink()
    _delete_accesses(programs_folder(), (START_MENU_NAME,))
    remove_context_menu()
    remove_old_versions()
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, NOTICES_ID_KEY)
    except OSError:
        pass
    _delete_notice_protocol(NOTICE_PROTOCOL_KEY)
    _delete_notice_protocol(OLD_NOTICE_PROTOCOL_KEY)
    if remove_firewall:
        import diagnostics
        diagnostics.remove_windows_rule()
    if was:
        print(t("Startup entry and context menu removed."))
    else:
        print(t("It wasn’t installed."))


def start_global_shortcut(on_enable):
    """Registers Ctrl+Alt+O on Windows in a background thread without blocking."""
    import threading

    def _loop():
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_NOREPEAT = 0x4000
        VK_O = 0x4F  # the O in CopyOver; plain Ctrl+O is «open file» in almost every app
        HOTKEY_ID = 1887

        try:
            if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_O):
                return
        except Exception:
            return

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312 and msg.wParam == HOTKEY_ID:
                    try:
                        on_enable()
                    except Exception:
                        pass
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                pass

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
