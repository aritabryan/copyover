"""P2P upload with progress queries and limited retries after network drops."""
import http.client
import json
import ssl
import time

import p2p
from i18n import t
from wire_keys import from_wire, headers_to_wire, route_to_old


class TransferError(Exception):
    pass


class Retry(Exception):
    pass


def _response(conn):
    resp = conn.getresponse()
    body = resp.read().decode('utf-8', errors='replace')
    if resp.status in (409, 408, 502, 503, 504):
        raise Retry(body)
    if resp.status != 200:
        raise TransferError(t('Error {code}: {detail}', code=resp.status, detail=body))
    return body


def _state(ip, port, key, tid, timeout, ca_pem=None):
    conn = p2p.open_connection(ip, port, timeout, ca_pem)
    try:
        conn.request('POST', route_to_old(f'/p2p/resume/{tid}'), body=b'', headers=headers_to_wire({'X-Key': key}))
        return from_wire(json.loads(_response(conn)))['files']
    finally:
        conn.close()


def _progress(callback, sent, total, name, startup, offset=0):
    if not callback:
        return
    speed = max(0, sent - offset) / max(0.001, time.monotonic() - startup)
    metrics = {'type': 'sent', 'speed_mb_s': round(speed / 1048576, 2),
                'percent': round(sent * 100 / max(1, total), 1),
                'eta_seconds': round((total - sent) / max(1, speed), 1)}
    try:
        callback(sent, total, name, metrics)
    except TypeError:
        callback(sent, total, name)


def _check_cancellation(cancel):
    if cancel and cancel():
        raise TransferError(t('Sending canceled: the computer was unpaired.'))


def _upload(ip, port, key, tid, index, path, size, offset, progress, timeout, cancel=None, ca_pem=None):
    conn = p2p.open_connection(ip, port, timeout, ca_pem)
    try:
        conn.putrequest('PUT', route_to_old(f'/p2p/upload/{tid}/{index}'))
        for name, value in headers_to_wire({'X-Key': key, 'Content-Type': 'application/octet-stream',
                                           'Content-Length': str(size-offset), 'X-CopyOver-Offset': str(offset)}).items():
            conn.putheader(name, value)
        conn.endheaders()
        sent = offset
        with path.open('rb') as file:
            file.seek(offset)
            while sent < size:
                _check_cancellation(cancel)
                block = file.read(min(p2p.BYTES_BLOCK, size-sent))
                if not block:
                    raise TransferError(t('The source file changed while it was being sent.'))
                conn.send(block)
                sent += len(block)
                progress(sent)
        _response(conn)
    finally:
        conn.close()


def _send_file(ip, port, key, tid, index, path, info, resumable, progress,
                    wait, interval, resolve, cancel=None, destination=None, ca_pem=None, name=''):
    destination = destination if destination is not None else [ip, port]
    limit = None
    offset = 0
    while True:
        _check_cancellation(cancel)
        try:
            timeout = 60 if limit is None else max(0.1, min(10, limit-time.monotonic()))
            if resumable:
                state = _state(*destination, key, tid, timeout, ca_pem)[index]
                offset = state['offset']
                if not isinstance(offset, int) or not 0 <= offset <= info['size']:
                    raise TransferError(t('The receiver returned invalid progress.'))
                if state['received']:
                    progress(info['size'])
                    return
            _upload(*destination, key, tid, index, path, info['size'], offset, progress, min(15, timeout), cancel, ca_pem)
            return
        except ssl.SSLCertVerificationError as error:
            # Another PC answered at that address on reconnect: no retry and nothing is sent to it.
            raise TransferError(p2p.tls_error_message(error, name or destination[0])) from error
        except (OSError, http.client.HTTPException, Retry) as error:
            if not resumable:
                raise TransferError(t('The connection dropped. Update both PCs to be able to resume.')) from error
            limit = _wait_reconnection(limit, wait, interval, progress, offset)
            _update_destination(destination, resolve)


def _wait_reconnection(limit, wait, interval, progress, offset):
    if limit is None:
        limit = time.monotonic() + wait
    if time.monotonic() >= limit:
        raise TransferError(t('The connection didn’t come back. The receiver kept the progress: send the same files again without closing CopyOver.'))
    progress(offset, reconnecting=True)
    time.sleep(min(interval, max(0, limit-time.monotonic())))
    return limit


def _update_destination(destination, resolve):
    if resolve:
        new_item = resolve()
        if new_item:
            destination[:] = new_item


def upload_files(ip, port, key, tid, paths, offer, resumable, callback,
                    wait, interval, resolve=None, cancel=None, ca_pem=None, destination_name=''):
    total = sum(a['size'] for a in offer['files'])
    base = 0
    startup = time.monotonic()
    destination = [ip, port]
    try:
        for index, path in enumerate(paths):
            info = offer['files'][index]

            def progress(file_bytes, reconnecting=False):
                name = t('Reconnecting: {name}', name=path.name) if reconnecting else path.name
                _progress(callback, base + file_bytes, total, name, startup)

            _send_file(ip, port, key, tid, index, path, info, resumable, progress,
                            wait, interval, resolve, cancel, destination, ca_pem, destination_name)
            base += info['size']
        return True, t('Sent ({n} file(s))', n=len(paths))
    except (TransferError, ValueError, KeyError, IndexError) as error:
        if cancel and cancel():
            _update_destination(destination, resolve)
            p2p._try_cancel(*destination, key, tid, ca_pem)
        return False, str(error)
