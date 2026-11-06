"""Bounded private snapshots for authenticated Android file offers."""
import hashlib
import mimetypes
from pathlib import Path
import secrets
import shutil
import errno
import tempfile
import threading
import zipfile

from i18n import t


class OfferPreparing(Exception):
    """The immutable snapshot is being prepared in a background worker."""


class AndroidOffers:
    def __init__(self, max_bytes=None):
        self.max_bytes = max_bytes
        self._temp = tempfile.TemporaryDirectory(prefix='copyover-android-')
        self._lock = threading.RLock()
        self._offers = {}
        self._sources = {}
        self._completed = set()
        self._lock_preparation = threading.Lock()
        self._worker = None
        self._result = None
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        with self._lock_preparation:
            self._closed = True
            worker = self._worker
        if worker:
            worker.join()
        self._temp.cleanup()

    def query(self, state, pc):
        offer = state.get('phone_offer')
        if not offer or offer.get('type') != 'file':
            return None
        source_id = str(offer.get('id', ''))
        with self._lock_preparation:
            if self._closed:
                raise OSError('The offer store is closed')
            if self._result and self._result[0] == source_id:
                _, metadata, error = self._result
                if error:
                    raise error
                return dict(metadata) if metadata else None
            if self._worker and self._worker.is_alive():
                raise OfferPreparing()
            frozen = {'phone_offer': dict(offer),
                      'prepared_files': list(state.get('prepared_files', []))}
            self._worker = threading.Thread(target=self._prepare_async,
                args=(source_id, frozen, pc), daemon=True)
            self._worker.start()
        raise OfferPreparing()

    def _prepare_async(self, source_id, state, pc):
        metadata, error = None, None
        try:
            metadata = self.current(state, pc)
        except Exception as failure:
            error = failure
        with self._lock_preparation:
            self._result = source_id, metadata, error

    def current(self, state, pc):
        offer = state.get('phone_offer')
        if not offer or offer.get('type') != 'file':
            return None
        source_id = str(offer.get('id', ''))
        with self._lock:
            if source_id in self._sources:
                return dict(self._offers[self._sources[source_id]][0])
        # Copying and hashing can take minutes: the existing downloads stay available meanwhile.
        paths = self._available_paths(state)
        if not paths:
            return None
        offer_id = secrets.token_hex(16)
        target = Path(self._temp.name) / offer_id
        name, size = self._snapshot(paths, target)
        try:
            sha256 = self._hash(target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        mime = 'application/zip' if len(paths) > 1 else mimetypes.guess_type(name)[0] or 'application/octet-stream'
        metadata = dict(id=offer_id, name=name, mime=mime, size=size, pc=pc, sha256=sha256)
        return self._save_snapshot(source_id, target, metadata)

    def _save_snapshot(self, source_id, target, metadata):
        with self._lock:
            # Two calls can get here at once: only one copy is published.
            if source_id in self._sources:
                target.unlink(missing_ok=True)
                return dict(self._offers[self._sources[source_id]][0])
            try:
                self._trim(metadata['size'])
            except OSError:
                target.unlink(missing_ok=True)
                raise
            self._offers[metadata['id']] = (metadata, target)
            self._sources[source_id] = metadata['id']
            return dict(metadata)

    def _available_paths(self, state):
        paths = [Path(path) for path in state.get('prepared_files', [])]
        if not paths or any(not path.is_file() for path in paths):
            return []
        size = sum(path.stat().st_size for path in paths)
        if self.max_bytes is not None and size > self.max_bytes:
            raise ValueError(t('The offer exceeds the allowed limit'))
        self._check_space(size + len(paths) * 4096)
        return paths

    def _snapshot(self, paths, target):
        try:
            if len(paths) == 1:
                name = paths[0].name
                with paths[0].open('rb') as src, target.open('wb') as dst:
                    self._copy(src, dst)
            else:
                name = 'files.zip'
                self._snapshot_zip(paths, target)
            size = target.stat().st_size
            if self.max_bytes is not None and size > self.max_bytes:
                raise ValueError(t('The offer is over 500 MiB'))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return name, size

    def _snapshot_zip(self, paths, target):
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as archive:
            for index, path in enumerate(paths):
                safe_name = f'{index + 1}-{path.name}' if sum(p.name == path.name for p in paths) > 1 else path.name
                with path.open('rb') as src, archive.open(safe_name, 'w', force_zip64=True) as dst:
                    self._copy(src, dst)
                if self.max_bytes is not None and target.stat().st_size > self.max_bytes:
                    raise ValueError(t('The offer exceeds the allowed limit'))

    def _trim(self, size):
        # Three copies at most, and together they stay under the Android cap.
        while self._offers and (len(self._offers) >= 3 or (self.max_bytes is not None and sum(item[0]['size'] for item in self._offers.values()) + size > self.max_bytes)):
            old_id = next(iter(self._offers))
            _, old_path = self._offers[old_id]
            old_path.unlink(missing_ok=True)
            self._offers.pop(old_id)
            self._sources = {key: value for key, value in self._sources.items() if value != old_id}
            self._completed = {entry for entry in self._completed if entry[0] != old_id}

    def _copy(self, source, target):
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if self.max_bytes is not None and total > self.max_bytes:
                raise ValueError(t('The offer is over 500 MiB'))
            self._check_space(len(chunk))
            target.write(chunk)

    def _check_space(self, required):
        if shutil.disk_usage(self._temp.name).free < required + 20 * 1024 * 1024:
            raise OSError(errno.ENOSPC, 'Not enough disk space')

    @staticmethod
    def _hash(path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def metadata(self, offer_id):
        with self._lock:
            item = self._offers.get(offer_id)
            return dict(item[0]) if item else None

    def open_stream(self, offer_id):
        with self._lock:
            item = self._offers.get(offer_id)
            return item[1].open('rb') if item else None

    def downloaded(self, offer_id, key):
        with self._lock:
            if offer_id in self._offers:
                self._completed.add((offer_id, hashlib.sha256(key.encode()).digest()))

    def confirm(self, offer_id, key):
        with self._lock:
            return (offer_id, hashlib.sha256(key.encode()).digest()) in self._completed


def download_range(header, size):
    """Only the open-ended resume form is supported; return its first byte."""
    import re
    if header is None:
        return 0
    match = re.fullmatch(r'bytes=(\d+)-', header)
    if not match or int(match[1]) >= size:
        raise ValueError('Invalid range')
    return int(match[1])


def copy_download(source, target, remaining, authorize=None):
    """A receipt is only safe when the entire advertised remainder was written."""
    while remaining:
        if authorize and not authorize():
            return False
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            return False
        target.write(chunk)
        remaining -= len(chunk)
    target.flush()
    return authorize is None or authorize()
