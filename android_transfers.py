"""Authenticated, resumable Android uploads backed by the shared disk receiver."""
import hashlib
import json
import logging
from urllib.parse import urlsplit

from i18n import t
from wire_keys import from_wire, to_wire


class _AuthorizedFlow:
    def __init__(self, handler, tid):
        self.handler = handler
        self.tid = tid

    def _validate(self):
        if not self.handler._valid_key():
            self.handler.android_manager.cancel_transfer(self.tid, self.handler.headers.get('X-Key', ''))
            raise ConnectionError(t('The pairing is no longer authorized'))

    def read(self, size):
        self._validate()
        chunk = self.handler.rfile.read(size)
        self._validate()
        return chunk


class AndroidTransfers:
    def serve_android_transfer(self, method):
        # Key first: before reading the body or touching the disk.
        if not self.authorized():
            return
        parts = urlsplit(self.path).path.strip('/').split('/')
        if parts[:2] != ['android', 'transfers']:
            return self.respond(404, t('Not found'))
        if len(parts) == 2:
            if method == 'GET':
                return self.respond(200, json.dumps({'version': 1}), 'application/json')
            if method == 'POST':
                return self._offer_android()
        if len(parts) == 4:
            return self._android_transfer_action(method, *parts[2:])
        return self.respond(404, t('Not found'))

    def _android_transfer_action(self, method, tid, action):
        key = self.headers.get('X-Key', '')
        if method == 'POST' and action == 'status':
            code, response = self.android_manager.resume_state(tid, key)
            return self.respond(code, json.dumps(to_wire(response)), 'application/json')
        if method == 'POST' and action == 'cancel':
            ok = self.android_manager.cancel_transfer(tid, key)
            return self.respond(200 if ok else 404, 'OK' if ok else t('Transfer not found'))
        if method == 'PUT':
            return self._upload_android(tid, action, key)
        return self.respond(404, t('Not found'))

    def _offer_android(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 1024 * 1024 or self.headers.get('Transfer-Encoding'):
                return self.respond(400, t('Invalid offer'))
            data = from_wire(json.loads(self.rfile.read(length)))
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            return self.respond(400, t('Invalid JSON'))
        key = self.headers.get('X-Key', '')
        data = {'sender': {'id': hashlib.sha256(key.encode()).hexdigest(), 'name': 'Android'},
                 'files': data.get('files'), 'resume': True}
        code, response = self.android_manager.process_offer(
            data, owner=key, authorize=lambda: self._valid_key())
        return self.respond(code, json.dumps(to_wire(response)), 'application/json')

    def _upload_android(self, tid, index, key):
        try:
            index = int(index)
            length = int(self.headers.get('Content-Length', '-1'))
            offset = int(self.headers.get('X-CopyOver-Offset', '0'))
            if length < 0 or self.headers.get('Transfer-Encoding'):
                raise ValueError()
        except ValueError:
            return self.respond(400, t('Invalid parameters'))
        self.connection.settimeout(15)
        code, message, path, complete = self.android_manager.receive_file(
            tid, index, _AuthorizedFlow(self, tid), length, offset=offset, owner=key,
            authorize=lambda: self._valid_key())
        if code == 200 and path:
            try:
                self._register_android_file(tid, path, complete)
            except Exception:
                logging.exception('The Android file is saved; a desktop action failed')
        try:
            return self.respond(code, message)
        except ConnectionError:
            return

    def _register_android_file(self, tid, path, complete):
        from datetime import datetime
        import bridge
        bridge.STATE['last'] = (datetime.now(), t('Received: {name}', name=path.name))
        bridge.register_history('received', f'Android ({self.client_address[0]})', path.name,
                                  size=path.stat().st_size, local_path=str(path))
        files = [path]
        if complete:
            trans = self.android_manager.get_transfer(tid)
            if trans:
                files = [a['final_path'] for a in trans['files'] if a['received'] and a['final_path']]
        # If the clipboard fails, the file was still saved: it isn't reported as an error.
        try:
            bridge.so.set_files(files)
        except Exception:
            logging.exception("Couldn't copy the Android file to the clipboard")
        if complete and self.notices:
            bridge.notify_received(files, self._sender_name())
