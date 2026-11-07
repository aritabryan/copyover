"""Wait for Windows clipboard changes without reading clipboard data on every tick."""
import hashlib
import hmac
import threading
import secrets
import time
import tempfile
from pathlib import Path


def automatic_content(clipboard):
    """Only text and images are sent automatically; files require a user action."""
    if clipboard.read_files():
        return None, 'application/octet-stream'
    text = clipboard.read_text()
    if text:
        data = text.encode('utf-8')
        return (data if len(data) <= 256 * 1024 else None), 'text/plain; charset=utf-8'
    with tempfile.TemporaryDirectory(prefix='copyover-sync-') as directory:
        image = Path(directory) / 'image.png'
        if clipboard.read_image(image) and image.stat().st_size <= 32 * 1024 * 1024:
            return image.read_bytes(), 'image/png'
    return None, 'application/octet-stream'


def content_fingerprint(data, mime):
    if mime.startswith('text/plain'):
        text = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        return hashlib.sha256(b'text:' + text.encode('utf-8')).digest()
    if mime.startswith('image/'):
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            with image.convert('RGBA') as pixels:
                digest = hashlib.sha256(f'image:{pixels.size}:'.encode())
                digest.update(pixels.tobytes())
                return digest.digest()
    return None


class ClipboardChanges:
    def __init__(self, sequence, clock=time.monotonic, sleep=time.sleep):
        self.sequence = sequence
        self.clock = clock
        self.sleep = sleep
        self.session = secrets.token_hex(16)
        self._lock = threading.RLock()
        self._origin_secret = secrets.token_bytes(32)
        self._origin = None

    def _identity(self, key):
        return hmac.digest(self._origin_secret, key.encode('utf-8'), hashlib.sha256)

    def write(self, key, writer, *args, expected=None):
        """Publish origin only after a successful write, together with its clipboard cursor."""
        with self._lock:
            self._origin = None
            result = writer(*args)
            if result is not False:
                self._origin = (self.current(), self._identity(key), expected)
            return result

    def frame(self, key, clipboard):
        """Read one stable clipboard version; a later local copy never inherits its origin."""
        with self._lock:
            for _ in range(3):
                token = self.current()
                data, mime = automatic_content(clipboard)
                if token == self.current():
                    if (data is not None and self._origin is not None
                            and self._origin[:2] == (token, self._identity(key))
                            and self._origin[2] == content_fingerprint(data, mime)):
                        return token, None, mime
                    return token, data, mime
            raise OSError('Clipboard changed while reading')

    def current(self):
        return f'{self.session}:{self.sequence()}'

    def wait(self, previous, timeout=20, still_allowed=None, check_every=1.0):
        """(cursor, changed). `changed` is None when `still_allowed()` turned false while waiting (the device
        was removed or the key changed): the caller must not send anything. It's checked every
        `check_every` seconds and always right before reporting a change."""
        deadline = self.clock() + timeout
        next_check = self.clock() + check_every
        while True:
            current = self.current()
            if current != previous:
                if still_allowed is not None and not still_allowed():
                    return current, None
                return current, True
            if self.clock() >= deadline:
                return current, False
            if still_allowed is not None and self.clock() >= next_check:
                if not still_allowed():
                    return current, None
                next_check = self.clock() + check_every
            self.sleep(0.2)
