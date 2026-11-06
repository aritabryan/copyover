import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import p2p
import bridge
from android_offers import AndroidOffers, OfferPreparing
from wire_keys import from_wire


class AndroidStreamingTests(unittest.TestCase):
    def setUp(self):
        for target in ['register_history', 'so.set_files']:
            mocked = patch('bridge.' + target)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.data = b'0123456789' * 100
        self.offer = {'files': [{'name': 'video.mp4', 'size': len(self.data),
                      'sha256': hashlib.sha256(self.data).hexdigest()}]}
        self.manager = p2p.TransfersManager(self.folder / 'received', ask_user=lambda _: 'accept', max_bytes=None)
        class Handler(bridge.Handler):
            key = 'secret'
            notices = False
            android_manager = self.manager
            def log_message(self, *args):
                pass
        self.server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path='/android/transferencias', body=None, key='secret', headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            conn.request(method, path, body, {'X-Key': key, **(headers or {})})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def create(self):
        code, body, _ = self.request('POST', body=json.dumps(self.offer))
        self.assertEqual(code, 200, body)
        result = json.loads(body)
        self.assertTrue(result['resume'])
        return '/android/transferencias/' + result['id']

    def test_auth_capability_and_invalid_json(self):
        self.assertEqual(self.request('GET', key='')[0], 401)
        self.assertEqual(self.request('POST', body='invalid', key='')[0], 401)
        self.assertEqual(self.request('GET')[0:2], (200, b'{"version": 1}'))
        self.assertEqual(self.request('POST', body='invalid')[0], 400)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_cut_resume_and_hash_failure(self):
        route = self.create()
        tid = route.rsplit('/', 1)[1]
        self.assertEqual(self.manager.receive_file(tid, 0, io.BytesIO(self.data[:123]), len(self.data), owner='secret')[0], 503)
        code, body, _ = self.request('POST', route + '/estado')
        self.assertEqual(code, 200)
        self.assertEqual(from_wire(json.loads(body))['files'], [{'offset': 123, 'received': False}])
        self.assertEqual(self.create(), route)
        self.assertEqual(self.request('PUT', route + '/0', self.data[123:], headers={'X-Puente-Offset': '0'})[0], 400)
        with patch.object(bridge, 'register_history') as history:
            self.assertEqual(self.request('PUT', route + '/0', self.data[123:], headers={'X-Puente-Offset': '123'})[0], 200)
            history.assert_called_once()
        self.assertEqual((self.folder / 'received/video.mp4').read_bytes(), self.data)
        self.assertEqual(from_wire(json.loads(self.request('POST', route + '/estado')[1]))['files'][0], {'offset': len(self.data), 'received': True})
        self.offer['files'][0]['name'] = 'bad.mp4'
        route = self.create()
        self.assertEqual(self.request('PUT', route + '/0', b'x' * len(self.data))[0], 400)
        self.assertFalse((self.folder / 'received/bad.mp4').exists())

    def test_key_binding_revocation_and_cancel(self):
        route = self.create()
        with patch.object(self.server.RequestHandlerClass, '_valid_key', return_value=True):
            for method, suffix in [('POST', '/estado'), ('POST', '/cancelar'), ('PUT', '/0')]:
                self.assertEqual(self.request(method, route + suffix, self.data if method == 'PUT' else None, key='other')[0], 404)
        self.assertEqual(self.request('PUT', route + '/0', self.data, key='revoked')[0], 401)
        self.assertEqual(self.request('POST', route + '/cancelar')[0], 200)
        self.assertEqual(self.request('POST', route + '/estado')[0], 404)

    def test_large_metadata_and_no_space(self):
        self.offer['files'][0]['size'] = 600 * 1024 * 1024
        with patch('p2p.shutil.disk_usage') as usage:
            usage.return_value.free = 2**40
            self.create()
            usage.return_value.free = 0
            self.offer['files'][0]['name'] = 'other.mp4'
            self.assertEqual(self.request('POST', body=json.dumps(self.offer))[0], 507)
        default = p2p.TransfersManager(self.folder)
        self.offer['files'][0]['size'] = 11 * 1024**3
        self.assertEqual(default.validate_offer({'sender': {'id': 'pc', 'name': 'PC'}, **self.offer})[1], 413)

    def test_range_and_snapshot_hash(self):
        source = self.folder / 'source.txt'
        source.write_bytes(b'original')
        state = {'phone_offer': {'id': 'one', 'type': 'file'}, 'prepared_files': [source]}
        with AndroidOffers() as offers, patch.object(bridge.Handler, 'android_offers', offers), patch.object(bridge, 'STATE', state):
            code, body, _ = self.request('GET', '/android/ofertas')
            for _ in range(100):
                if code != 503:
                    break
                time.sleep(0.01)
                code, body, _ = self.request('GET', '/android/ofertas')
            self.assertEqual(code, 200)
            meta = json.loads(body)['offer']
            self.assertEqual(meta['sha256'], hashlib.sha256(b'original').hexdigest())
            route = '/android/ofertas/' + meta['id']
            for invalid in ['bytes=8-', 'bytes=-3', 'bytes=0-2', 'garbage']:
                code, _, headers = self.request('GET', route + '/archivo', headers={'Range': invalid})
                self.assertEqual(code, 416)
                self.assertEqual(headers['Content-Range'], 'bytes */8')
            self.assertEqual(self.request('POST', route + '/confirmar')[0], 409)
            code, body, headers = self.request('GET', route + '/archivo', headers={'Range': 'bytes=3-'})
            self.assertEqual((code, body), (206, b'ginal'))
            self.assertEqual(headers['Content-Range'], 'bytes 3-7/8')
            self.assertEqual(headers['Content-Length'], '5')
            self.assertEqual(self.request('POST', route + '/confirmar')[0], 200)

    def test_snapshot_checks_real_disk(self):
        source = self.folder / 'source'
        source.write_bytes(b'content')
        state = {'phone_offer': {'id': 'disk', 'type': 'file'}, 'prepared_files': [source]}
        with AndroidOffers() as offers, patch('android_offers.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaises(OSError):
                offers.current(state, 'PC')
            self.assertEqual(list(Path(offers._temp.name).iterdir()), [])

    def test_receiver_bounds_reads_and_rechecks_authorization_after_hashing(self):
        route = self.create()
        tid = route.rsplit('/', 1)[1]
        outer = self
        class Bounded(io.BytesIO):
            def read(self, size=-1):
                outer.assertGreater(size, 0)
                outer.assertLessEqual(size, p2p.BYTES_BLOCK)
                return super().read(size)
        authorized = [True]
        original = p2p.compute_sha256
        def revoke(path):
            authorized[0] = False
            return original(path)
        with patch.object(p2p, 'compute_sha256', side_effect=revoke):
            code, _, path, _ = self.manager.receive_file(tid, 0, Bounded(self.data), len(self.data),
                owner='secret', authorize=lambda: authorized[0])
        self.assertEqual(code, 404)
        self.assertIsNone(path)
        self.assertEqual(list((self.folder / 'received').iterdir()), [])

    def test_default_snapshot_allows_metadata_above_500_mib(self):
        source = self.folder / 'source'
        source.write_bytes(b'content')
        state = {'phone_offer': {'id': 'large', 'type': 'file'}, 'prepared_files': [source]}
        with AndroidOffers() as offers, patch.object(offers, '_snapshot', return_value=('source', 600 * 1024**2)), patch.object(offers, '_hash', return_value='a' * 64):
            self.assertIsNone(offers.max_bytes)
            self.assertEqual(offers.current(state, 'PC')['size'], 600 * 1024**2)

    def test_revoked_while_snapshot_created_is_not_returned(self):
        with AndroidOffers() as offers, patch.object(bridge.Handler, 'android_offers', offers):
            def revoke(*args):
                self.server.RequestHandlerClass.key = 'changed'
                return {'id': 'private'}
            with patch.object(offers, 'query', side_effect=revoke):
                self.assertEqual(self.request('GET', '/android/ofertas')[0], 401)

    def test_completed_batch_copies_all_files(self):
        self.offer['files'].append({**self.offer['files'][0], 'name': 'other.mp4'})
        route = self.create()
        with patch.object(bridge.so, 'set_files') as clipboard:
            for index in range(2):
                self.assertEqual(self.request('PUT', route + '/' + str(index), self.data)[0], 200)
            self.assertEqual([path.name for path in clipboard.call_args.args[0]], ['video.mp4', 'other.mp4'])

    def test_side_effect_errors_keep_receipt(self):
        self.offer['files'] = [{**self.offer['files'][0], 'name': 'saved.mp4'}]
        route = self.create()
        with patch.object(bridge, 'register_history', side_effect=RuntimeError('side effect failed')), self.assertLogs(level='ERROR'):
            self.assertEqual(self.request('PUT', route + '/0', self.data)[0], 200)
        self.assertTrue(json.loads(self.request('POST', route + '/estado')[1])['files'][0]['received'])

    def test_slow_snapshot_is_busy_without_blocking_or_duplicate_workers(self):
        source = self.folder / 'slow'
        source.write_bytes(b'content')
        state = {'phone_offer': {'id': 'slow', 'type': 'file'}, 'prepared_files': [source]}
        started = threading.Event()
        release = threading.Event()
        with AndroidOffers() as offers, patch.object(bridge.Handler, 'android_offers', offers), patch.object(bridge, 'STATE', state):
            original = offers._snapshot
            def slow(*args):
                started.set()
                release.wait(5)
                return original(*args)
            with patch.object(offers, '_snapshot', side_effect=slow) as snapshot:
                try:
                    code, body, headers = self.request('GET', '/android/ofertas')
                    self.assertEqual(code, 503)
                    self.assertEqual(from_wire(json.loads(body)), {'preparing': True})
                    self.assertEqual(headers['Retry-After'], '1')
                    self.assertTrue(started.wait(1))
                    for _ in range(3):
                        self.assertEqual(self.request('GET', '/android/ofertas')[0], 503)
                    self.assertEqual(snapshot.call_count, 1)
                finally:
                    release.set()
                for _ in range(100):
                    code, body, _ = self.request('GET', '/android/ofertas')
                    if code != 503:
                        break
                    time.sleep(0.01)
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)['offer']['sha256'], hashlib.sha256(b'content').hexdigest())

    def test_old_offer_remains_readable_while_new_snapshot_hashes(self):
        source = self.folder / 'source'
        source.write_bytes(b'content')
        state = {'phone_offer': {'id': 'old', 'type': 'file'}, 'prepared_files': [source]}
        started, release, readable = threading.Event(), threading.Event(), threading.Event()
        with AndroidOffers() as offers:
            meta = offers.current(state, 'PC')
            original = offers._hash
            def slow(path):
                started.set()
                release.wait(5)
                return original(path)
            def read_old():
                self.assertEqual(offers.metadata(meta['id']), meta)
                with offers.open_stream(meta['id']) as stream:
                    self.assertEqual(stream.read(), b'content')
                readable.set()
            state['phone_offer']['id'] = 'new'
            with patch.object(offers, '_hash', side_effect=slow):
                with self.assertRaises(OfferPreparing):
                    offers.query(state, 'PC')
                self.assertTrue(started.wait(1))
                reader = threading.Thread(target=read_old)
                reader.start()
                try:
                    self.assertTrue(readable.wait(0.5), 'Old downloads blocked behind the new snapshot hash')
                finally:
                    release.set()
                    reader.join(5)

    def test_periodic_cleanup_includes_android_and_pc_transfers(self):
        with patch.object(bridge.Handler.android_manager, 'clean_expired') as android, patch.object(bridge.Handler.p2p_manager, 'clean_expired') as pc:
            bridge.Handler.clean_expired_transfers()
            android.assert_called_once_with()
            pc.assert_called_once_with()
