"""A secret that only the user running CopyOver can read, new on every start.

The panel (/app/*) and the local helpers (the tray, «CopyOver.exe --send», «--notification», reopening CopyOver) talk
to the server over 127.0.0.1. Being on this PC isn't enough: another account on the same computer also reaches
127.0.0.1. So each request must also carry this secret.

The running CopyOver creates it once it has the port, keeps it in an environment variable (the panel window and
the quick view inherit it; unlike the command line, other users can't read it) and writes it to a file in the
data folder that only this user can open, for the helpers that Explorer or the file manager start on their own.
The panel window opens /app/enter with it once and the server answers with a cookie that only /app sees, so the
panel's own page never handles it.
"""
import logging
import os
import secrets
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

import bundle

VARIABLE = "COPYOVER_LOCAL_SECRET"
HEADER = "X-Local-Secret"
COOKIE = "copyover_local"
FILE = "local-secret"
OLD_FILE = "secreto-local"  # name before 1.0.7
# Pages the panel window can be sent to after /app/enter.
DESTINATIONS = ("/app", "/app/preview")


def path() -> Path:
    return bundle.data_folder() / FILE


def create(destination: Path | None = None) -> str:
    """A new secret for this run: in the environment for the child windows and in a private file."""
    secret = secrets.token_urlsafe(32)
    os.environ[VARIABLE] = secret
    try:
        write_private(destination or path(), secret)
        if destination is None:
            path().with_name(OLD_FILE).unlink(missing_ok=True)  # a previous run's, under the old name
    except OSError:
        # The panel and the quick view still work (they get it from the environment); only the helpers that
        # Explorer starts fall back to sending directly.
        logging.warning("Couldn't save the local secret", exc_info=True)
    return secret


def read() -> str:
    """The secret of the running CopyOver: from the environment in its child windows, otherwise from the file."""
    value = os.environ.get(VARIABLE, "")
    if value:
        return value
    try:
        file = path()
        if not file.exists() and file.with_name(OLD_FILE).exists():
            file = file.with_name(OLD_FILE)  # a CopyOver from before 1.0.7 is the one running
        return file.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def write_private(destination: Path, text: str) -> None:
    """Writes the file so that only this user can open it, and only puts the secret in once it's private.

    On Linux the file is born 0600. On Windows its inherited permissions are removed while it's still empty
    (certificates._restrict_permissions), and then it's filled and swapped in one go."""
    temporary = destination.with_name(f"{destination.name}.{secrets.token_hex(6)}.tmp")
    binary = getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary, 0o600)
    try:
        if sys.platform == "win32":
            os.close(descriptor)
            descriptor = -1
            import certificates
            certificates._restrict_permissions(temporary)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_TRUNC | binary)
        os.write(descriptor, text.encode("ascii"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def input_url(port: int, destination: str = "/app", secret: str | None = None) -> str:
    """What the panel window opens: /app/enter sets the cookie and moves on to `destino`."""
    secret = read() if secret is None else secret
    if not secret:
        return f"http://127.0.0.1:{int(port)}{destination}"
    return f"http://127.0.0.1:{int(port)}/app/enter?s={quote(secret)}&a={quote(destination, safe='/')}"


def link_for_browser(port: int) -> str:
    """For opening the panel in a browser: a private local page that jumps to /app/enter.

    The secret never goes on the browser's command line, where other users of the PC can read it (on Linux,
    /proc/<pid>/cmdline). The page is deleted after a couple of minutes."""
    destination = input_url(port)
    if "/app/enter" not in destination:
        return destination
    try:
        folder = Path(tempfile.mkdtemp(prefix="copyover-panel-"))  # 0700 on Linux, the user's TEMP on Windows
        page = folder / "open.html"
        write_private(page, '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                                 f'<meta http-equiv="refresh" content="0;url={destination}">')
    except OSError:
        logging.warning("Couldn't prepare the page that opens the panel", exc_info=True)
        return f"http://127.0.0.1:{int(port)}/app"

    def delete():
        for path_to_delete in (page, folder):
            try:
                path_to_delete.unlink() if path_to_delete.is_file() else path_to_delete.rmdir()
            except OSError:
                pass

    timer = threading.Timer(120, delete)
    timer.daemon = True
    timer.start()
    return page.as_uri()
