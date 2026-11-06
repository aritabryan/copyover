"""Exclusive reception of a file, keeping partials of resumable offers."""
import shutil
import time

import p2p
from i18n import t


def _validate_length(info, length, offset):
    if offset < 0 or length < 0 or offset + length != info['size']:
        return 400, t('The announced size doesn’t match Content-Length')
    return None


def _validate_file(trans, info, length, offset):
    if info.get('busy'):
        return 409, t('File in use')
    if info['received']:
        return 200, t('Already received')
    error = _validate_length(info, length, offset)
    if error:
        return error
    if offset and not trans.get('resumable'):
        return 400, t('This transfer can’t be resumed')
    partial = info.get('partial_path')
    current = partial.stat().st_size if partial and partial.exists() else 0
    if current != offset:
        return 409, t('Check the receiver’s progress again')
    return None


def _reserve_paths(manager, info, length):
    manager.destination.mkdir(parents=True, exist_ok=True)
    try:
        if shutil.disk_usage(manager.destination).free < length + 20 * 1024 * 1024:
            return 507, t('Not enough disk space on the receiving computer')
    except OSError:
        pass
    if not info.get('partial_path'):
        final = p2p.free_path(manager.destination, info['name'])
        info['final_path'] = final
        info['partial_path'] = final.with_name(final.name + '.parcial')
        # Created before releasing the lock: two files with the same name can't write to the same .parcial.
        info['partial_path'].touch(exist_ok=False)
    return None


def _prepare(manager, tid, index, length, offset, owner):
    trans = manager._transfers.get(tid)
    if not trans or trans.get('cancelled') or trans.get('owner') != manager._owner_hash(owner):
        return 404, t('Transfer not found')
    if not 0 <= index < len(trans['files']):
        return 404, t('File index out of range')
    info = trans['files'][index]
    error = _validate_file(trans, info, length, offset)
    if error:
        return error
    error = _reserve_paths(manager, info, length)
    if error:
        return error
    info['busy'] = True
    trans['activity'] = time.monotonic()
    return trans, info


def _copy(manager, trans, info, stream, offset):
    received = offset
    startup = time.monotonic()
    with info['partial_path'].open('ab') as file:
        while received < info['size']:
            if trans['cancelled']:
                break
            try:
                block = stream.read(min(p2p.BYTES_BLOCK, info['size'] - received))
            except OSError:
                break
            if not block:
                break
            file.write(block)
            received += len(block)
            trans['activity'] = time.monotonic()
            speed = (received - offset) / max(0.001, time.monotonic() - startup)
            manager._notify_progress({'id': trans['id'], 'type': 'received', 'direction': 'received',
                'peer': str(trans.get('sender', {}).get('name') or ''), 'file': info['name'],
                'received_bytes': received, 'total': info['size'],
                'speed_mb_s': round(speed / 1048576, 2),
                'percent': round(received * 100 / max(1, info['size']), 1),
                'eta_seconds': round((info['size'] - received) / max(1, speed), 1)})
    return received


def _discard(manager, info):
    manager._delete_safe(info.get('partial_path'))
    info['partial_path'] = None
    info['final_path'] = None


def _finish(manager, trans, info, received, correct_hash):
    if trans['cancelled']:
        _discard(manager, info)
        return 404, t('Transfer canceled'), None, False
    if received != info['size']:
        if not trans.get('resumable'):
            _discard(manager, info)
            return 400, t('Incomplete transfer'), None, False
        return 503, t('Connection interrupted; progress was kept'), None, False
    if not correct_hash:
        _discard(manager, info)
        return 400, t('The SHA-256 hash doesn’t match'), None, False
    if info['final_path'].exists():
        info['final_path'] = p2p.free_path(manager.destination, info['name'])
    info['partial_path'].replace(info['final_path'])
    info['partial_path'] = None
    info['received'] = True
    complete = all(a['received'] for a in trans['files'])
    if complete and not trans.get('resumable'):
        manager._transfers.pop(trans['id'], None)
    return 200, 'OK', info['final_path'], complete


def receive(manager, tid, index, stream, length, offset, owner, authorize=None):
    try:
        with manager._lock:
            prepared = _prepare(manager, tid, index, length, offset, owner)
    except OSError:
        return 500, t('Couldn’t prepare the destination file'), None, False
    if isinstance(prepared[0], int):
        return *prepared, None, False
    trans, info = prepared
    try:
        try:
            received = _copy(manager, trans, info, stream, offset)
        except (ConnectionError, TimeoutError):
            received = info['partial_path'].stat().st_size
        correct_hash = False
        if received == info['size'] and not trans['cancelled']:
            # Outside the lock: hashing several GB doesn't hold up the other transfers.
            correct_hash = p2p.compute_sha256(info['partial_path']) == info['sha256']
        with manager._lock:
            if authorize and not authorize():
                trans['cancelled'] = True
                manager._transfers.pop(tid, None)
            return _finish(manager, trans, info, received, correct_hash)
    except OSError:
        _discard(manager, info)
        return 500, t('Couldn’t save the file'), None, False
    finally:
        with manager._lock:
            info['busy'] = False
            trans['activity'] = time.monotonic()
            if trans['cancelled']:
                _discard(manager, info)
        manager._notify_progress(None)
