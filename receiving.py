"""Splits an upload (multipart, form or raw file) in chunks as it arrives.

Small things (the clipboard text, a photo) stay in memory. Large ones are written to a temporary file in the
destination folder, so a multi-GB video never goes through RAM and at the end it's just renamed.
"""

import logging
import os
import re
import time
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs

PIECE = 1 << 20
PART_IN_RAM_LIMIT = 16 * 1024 * 1024
FORM_LIMIT = 64 * 1024 * 1024  # text sent as a form field (no file)
HEADERS_LIMIT = 64 * 1024
HEAD_SIZE = 64  # what's needed to recognize the type (photo, PDF, video)
TEMPORARY_PREFIX = ".copyover-recibiendo-"


class CutSend(Exception):
    """The connection dropped or stopped sending before the upload was complete."""


class MalformedSend(Exception):
    """The upload doesn't have the shape its Content-Type says."""


class PartOnDisk:
    """A large part already saved to a temporary file."""

    def __init__(self, path: Path, size: int, head: bytes):
        self.path = path
        self.size = size
        self.head = head

    def __len__(self):
        return self.size

    def read(self) -> bytes:
        return self.path.read_bytes()

    def move_a(self, destination: Path) -> None:
        os.replace(self.path, destination)

    def discard(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def discard(parts) -> None:
    """Deletes the temporary files nobody used (does nothing if they were already moved)."""
    for _, _, data in parts or ():
        if isinstance(data, PartOnDisk):
            data.discard()


def delete_old_temporary(folder: Path, age: float = 3600, now: float | None = None) -> int:
    """Deletes the temporary files left by an upload that was cut off (CopyOver closed or the PC turned off
    halfway).

    A live upload writes every so often and is considered cut after a minute without data: a temporary file
    untouched for an hour won't be finished by anyone. Returns how many it deleted.
    """
    limit = (now or time.time()) - age
    deleted = 0
    try:
        temp_paths = list(folder.glob(f"{TEMPORARY_PREFIX}*.part"))
    except OSError:
        return 0
    for path in temp_paths:
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted


class _Destination:
    """Collects a part's bytes in memory and moves to disk when it grows."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.memory = bytearray()
        self.file = None
        self.path = None
        self.size = 0
        self.head = b""

    def write(self, data) -> None:
        if not data:
            return
        if len(self.head) < HEAD_SIZE:
            self.head += bytes(data[:HEAD_SIZE - len(self.head)])
        self.size += len(data)
        if self.file is None:
            self.memory += data
            if len(self.memory) > PART_IN_RAM_LIMIT:
                self.folder.mkdir(parents=True, exist_ok=True)
                self.path = self.folder / f"{TEMPORARY_PREFIX}{uuid.uuid4().hex}.part"
                self.file = open(self.path, "wb")
                self.file.write(self.memory)
                self.memory = bytearray()
        else:
            self.file.write(data)

    def close(self):
        if self.file is None:
            return bytes(self.memory)
        self.file.close()
        return PartOnDisk(self.path, self.size, self.head)

    def abort(self) -> None:
        if self.file is not None:
            self.file.close()
            try:
                self.path.unlink()
            except OSError:
                pass


class _Input:
    """Reads exactly `largo` bytes from `leer`, in chunks."""

    def __init__(self, read, length: int):
        self.read = read
        self.remaining = length

    def piece(self) -> bytes:
        if self.remaining <= 0:
            return b""
        try:
            data = self.read(min(PIECE, self.remaining))
        except (TimeoutError, OSError) as error:
            raise CutSend(str(error)) from error
        if not data:
            raise CutSend(f"faltaron {self.remaining} bytes")
        self.remaining -= len(data)
        return data


def _border(kind: str) -> bytes:
    m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', kind, re.IGNORECASE)
    if not m:
        raise MalformedSend("multipart with no boundary")
    return (m.group(1) or m.group(2)).encode("latin-1")


def _headers(block: bytes):
    msg = BytesParser(policy=policy.default).parsebytes(block + b"\r\n\r\n", headersonly=True)
    return msg.get_filename(), msg.get_content_type()


def read_parts(read, length: int, kind: str, folder: Path) -> list:
    """[(name, content_type, bytes | PartOnDisk)] of the upload, read from `leer` (like rfile.read).

    Raises CutSend if fewer than `largo` bytes arrive and MalformedSend if it can't be parsed; in both
    cases it deletes whatever it managed to write to disk.
    """
    entry = _Input(read, length)
    if kind.startswith("multipart/form-data"):
        return _multipart(entry, _border(kind), folder)
    base_type = kind.split(";")[0].strip().lower()
    if base_type == "application/x-www-form-urlencoded":
        # Shortcuts sends the form this way when the field is text (for example, the clipboard).
        if length > FORM_LIMIT:
            raise MalformedSend("form too large")
        body = bytearray()
        while entry.remaining:
            body += entry.piece()
        return [(None, "text/plain", value.encode("utf-8"))
                for _, values in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()
                for value in values]
    destination = _Destination(folder)
    try:
        while entry.remaining:
            destination.write(entry.piece())
    except BaseException:
        destination.abort()
        raise
    return [(None, base_type, destination.close())]


def _multipart(entry: _Input, border: bytes, folder: Path) -> list:
    separator = b"\r\n--" + border
    # With a line break in front, the first boundary is found like the others.
    buf = bytearray(b"\r\n")
    parts = []
    destination = None

    def mas() -> bool:
        data = entry.piece() if entry.remaining else b""
        buf.extend(data)
        return bool(data)

    try:
        # Anything before the first boundary is ignored.
        while (i := buf.find(separator)) < 0:
            del buf[:max(0, len(buf) - len(separator))]
            if not mas():
                raise MalformedSend("boundary not found")
        del buf[:i + len(separator)]
        while True:
            while len(buf) < 2 and mas():
                pass
            if buf[:2] == b"--":
                break  # closing boundary
            # Part headers, up to the blank line.
            while (end := buf.find(b"\r\n\r\n")) < 0:
                if len(buf) > HEADERS_LIMIT:
                    raise MalformedSend("headers too long")
                if not mas():
                    raise MalformedSend("part with no headers")
            name, part_type = _headers(bytes(buf[:end]).lstrip(b" \t\r\n"))
            del buf[:end + 4]
            # Content, up to the next boundary; whatever surely isn't a boundary gets written as it goes.
            destination = _Destination(folder)
            since = 0
            while (i := buf.find(separator, since)) < 0:
                safe = len(buf) - len(separator) + 1
                if safe > 0:
                    destination.write(buf[:safe])
                    del buf[:safe]
                since = max(0, len(buf) - len(separator) + 1)
                if not mas():
                    # No closing boundary: what arrived is the content (like the email parser did).
                    i = len(buf)
                    break
            destination.write(buf[:i])
            del buf[:i + len(separator)]
            data = destination.close()
            destination = None
            logging.debug("  part: name=%r type=%s bytes=%d", name, part_type, len(data))
            parts.append((name, part_type, data))
            if not buf and not entry.remaining:
                break
        # Whatever comes after the closing boundary (epilogue) is read and ignored.
        while entry.remaining:
            entry.piece()
        return parts
    except BaseException:
        if destination is not None:
            destination.abort()
        discard(parts)
        raise
