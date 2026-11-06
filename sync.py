"""Wait for Windows clipboard changes without reading clipboard data on every tick."""
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


class ClipboardChanges:
    def __init__(self, sequence, clock=time.monotonic, sleep=time.sleep):
        self.sequence = sequence
        self.clock = clock
        self.sleep = sleep
        self.session = secrets.token_hex(16)

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
