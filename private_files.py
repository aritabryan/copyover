"""Files and folders only the current user can read: secrets in the data folder and CopyOver's temporary files.

On Linux, /tmp is shared by every user: a folder with a fixed name there could be created first by someone
else, who could then read or swap what CopyOver leaves in it. So CopyOver's temporary files go in a folder of
its own per user (copyover-<uid>, 0700), checked before each use. On Windows, %TEMP% is already per user and
nothing changes.

Secrets in the data folder (config.json, history.json, copyover.log, the private keys) are written with 0600
on Linux. Windows doesn't use the mode bits; there the folder's permissions apply (see SECURITY.md).
"""
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# Files in the data folder that hold keys, tokens or what was copied.
SECRETS_FILES = ("config.json", "history.json", "copyover.log", "copyover.log.1", "copyover.log.2",
                     "panel_window.log", "historial.json", "puente.log", "puente.log.1", "puente.log.2",
                     "panel_ventana.log")


class ForeignFolder(RuntimeError):
    """The per-user temporary folder exists but isn't safe to use (another owner, a link, not a folder)."""


def _is_private(info: os.stat_result) -> bool:
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def _own_folder(path: Path) -> Path:
    """Creates ruta with 0700 or accepts it if it's already ours. Raises ForeignFolder otherwise."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)  # lstat: a symlink is refused, not followed
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ForeignFolder(f"{path} is not a folder")
    if info.st_uid != os.getuid():
        raise ForeignFolder(f"{path} belongs to another user")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)  # ours but too open (an older umask or someone else's chmod): close it
    return path


_backup: Path | None = None


def temporary_folder() -> Path:
    """Where CopyOver leaves its temporary files. Windows: %TEMP% as before. Linux: <tmp>/copyover-<uid>,
    0700 and owned by this user, checked on every call (a /tmp cleaner may have removed it). It stays under
    the temp folder rather than $XDG_RUNTIME_DIR because zips of whole folders go here, and that one is
    usually a small tmpfs. If someone else took the name, a fresh private folder with a random name is used."""
    global _backup
    base = Path(tempfile.gettempdir())
    if IS_WINDOWS:
        return base
    try:
        return _own_folder(base / f"copyover-{os.getuid()}")
    except (ForeignFolder, OSError) as error:
        if _backup is None or not _is_private_and_exists(_backup):
            _backup = Path(tempfile.mkdtemp(prefix="copyover-"))
            logging.warning("Not using CopyOver's temporary folder (%s): using %s", error, _backup)
        return _backup


def _is_private_and_exists(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return not stat.S_ISLNK(info.st_mode) and _is_private(info)


def temporary_subfolder(name: str) -> Path:
    """A folder inside temporary_folder(), created if missing."""
    path = temporary_folder() / name
    path.mkdir(exist_ok=True)
    return path


def only_for_user(path: Path, folder: bool = False) -> bool:
    """Leaves an existing file with 0600 (a folder with 0700) on Linux. False if it couldn't (it's logged);
    True on Windows, where this is a no-op."""
    if IS_WINDOWS:
        return True
    try:
        os.chmod(path, 0o700 if folder else 0o600)
        return True
    except FileNotFoundError:
        return True
    except OSError as error:
        logging.warning("Couldn't restrict the permissions of %s: %s", path, error)
        return False


def open_private(path, flags: int) -> int:
    """`opener` for open(): a file it creates is born with 0600, and one that existed is narrowed to 0600."""
    descriptor = os.open(path, flags, 0o600)
    if not IS_WINDOWS:
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            pass
    return descriptor


def write_private(path: Path, data: bytes) -> None:
    """Writes datos to ruta readable only by this user, in one go: a .tmp created anew with 0600 (never one
    left behind, nor a link) that replaces ruta. A cut halfway leaves the old file whole."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    _replace(temporary, path)


def _replace(origin: Path, destination: Path) -> None:
    import time
    for attempt in range(5):
        try:
            os.replace(origin, destination)
            return
        except PermissionError:
            # On Windows the antivirus or the indexer can have the file open for a moment.
            if attempt == 4:
                raise
            time.sleep(0.1)


def close_data_folder(folder: Path) -> None:
    """At startup on Linux: the data folder 0700 and the files with secrets already there 0600, so a config
    created by an older version (0644) is closed too. Only what belongs to this user is touched."""
    if IS_WINDOWS:
        return
    try:
        if os.stat(folder).st_uid == os.getuid():
            only_for_user(folder, folder=True)
    except OSError:
        pass
    for name in SECRETS_FILES:
        path = folder / name
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077:
            only_for_user(path)
