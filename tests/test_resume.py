import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import p2p
import bridge


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / 'received_bytes'
        self.data = b'0123456789' * 1000
        self.manager = p2p.TransfersManager(self.destination, ask_user=lambda _: 'accept')
        self.offer = {'sender': {'id': 'pc', 'name': 'Laptop'}, 'resume': True,
                      'files': [{'name': 'video.mp4', 'size': len(self.data),
                                    'sha256': hashlib.sha256(self.data).hexdigest()}]}

    def send_offer(self):
        code, result = self.manager.process_offer(self.offer, owner='owner')
        self.assertEqual(code, 200)
        self.assertTrue(result['resume'])
        return result['id']

    def test_cut_keeps_partial_and_continues_without_duplicate(self):
        tid = self.send_offer()
        code, _, _, _ = self.manager.receive_file(tid, 0, io.BytesIO(self.data[:1234]), len(self.data), owner='owner')
        self.assertEqual(code, 503)
        code, state = self.manager.resume_state(tid, 'owner')
        self.assertEqual(code, 200)
        self.assertEqual(state['files'][0]['offset'], 1234)
        self.assertEqual(self.send_offer(), tid)  # accepted offer reused, no second dialog
        code, _, file, done = self.manager.receive_file(tid, 0, io.BytesIO(self.data[1234:]), len(self.data)-1234, offset=1234, owner='owner')
        self.assertEqual(code, 200)
        self.assertTrue(done)
        self.assertEqual(file.read_bytes(), self.data)
        self.assertTrue(self.manager.resume_state(tid, 'owner')[1]['files'][0]['received'])
        self.assertEqual(len(list(self.destination.iterdir())), 1)

    def test_another_link_cannot_query_modify_or_cancel(self):
        tid = self.send_offer()
        self.assertEqual(self.manager.resume_state(tid, 'other')[0], 404)
        self.assertEqual(self.manager.receive_file(tid, 0, io.BytesIO(self.data), len(self.data), owner='other')[0], 404)
        self.assertFalse(self.manager.cancel_transfer(tid, owner='other'))
        self.assertTrue(self.manager.cancel_transfer(tid, owner='owner'))

    def test_wrong_offset_and_corrupt_hash_do_not_publish_the_file(self):
        tid = self.send_offer()
        self.manager.receive_file(tid, 0, io.BytesIO(self.data[:100]), len(self.data), owner='owner')
        self.assertEqual(self.manager.receive_file(tid, 0, io.BytesIO(self.data), len(self.data)-1, offset=1, owner='owner')[0], 409)
        code, _, _, _ = self.manager.receive_file(tid, 0, io.BytesIO(b'x'*(len(self.data)-100)), len(self.data)-100, offset=100, owner='owner')
        self.assertEqual(code, 400)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertEqual(self.manager.resume_state(tid, 'owner')[1]['files'][0]['offset'], 0)

    def test_expiry_and_unlink_clean_partial(self):
        tid = self.send_offer()
        self.manager.receive_file(tid, 0, io.BytesIO(self.data[:10]), len(self.data), owner='owner')
        with patch.object(p2p.time, 'monotonic', return_value=p2p.time.monotonic() + 86401):
            self.assertEqual(self.manager.resume_state(tid, 'owner')[0], 404)
        self.assertEqual(list(self.destination.iterdir()), [])
        tid = self.send_offer()
        self.manager.receive_file(tid, 0, io.BytesIO(self.data[:10]), len(self.data), owner='owner')
        self.manager.forget_pc('pc')
        self.assertEqual(self.manager.resume_state(tid, 'owner')[0], 404)
        self.assertEqual(list(self.destination.iterdir()), [])

    def start_server(self):
        class Handler(bridge.Handler):
            key = 'owner'
            notices = False
            p2p_manager = self.manager
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_port

    def test_sender_resumes_socket_real_cut(self):
        port = self.start_server()
        original = http.client.HTTPConnection
        interrupted = []
        manager = self.manager
        class Connection(original):
            def putrequest(self, method, url, *args, **kwargs):
                self.upload = method == 'PUT'
                return super().putrequest(method, url, *args, **kwargs)
            def send(self, data):
                if getattr(self, 'upload', False) and data == b'0123456789'*100:
                    if not interrupted:
                        interrupted.append(True)
                        super().send(data)
                        self.close()
                        raise ConnectionResetError('Wi-Fi desconectado')
                return super().send(data)
        file = Path(self.temp.name) / 'video.mp4'
        file.write_bytes(self.data)
        with patch.object(p2p.http.client, 'HTTPConnection', Connection), patch.object(p2p, 'BYTES_BLOCK', 1000):
            ok, message = p2p.send_to_pc('127.0.0.1', port, 'owner', 'Laptop', 'pc', [file], reconnection_wait=3, retry_interval=0.01)
        self.assertTrue(ok, message)
        self.assertTrue(interrupted)
        self.assertEqual((self.destination/'video.mp4').read_bytes(), self.data)
        self.assertEqual(len(list(self.destination.iterdir())), 1)

    def test_lost_final_response_does_not_duplicate_the_file(self):
        port = self.start_server()
        original = http.client.HTTPConnection
        lost = []
        class Connection(original):
            def putrequest(self, method, url, *args, **kwargs):
                self.upload = method == 'PUT'
                return super().putrequest(method, url, *args, **kwargs)
            def getresponse(self):
                response = super().getresponse()
                if getattr(self, 'upload', False) and not lost:
                    lost.append(True)
                    response.read()
                    raise ConnectionResetError('Se perdió la confirmación final')
                return response
        file = Path(self.temp.name) / 'video.mp4'
        file.write_bytes(self.data)
        with patch.object(p2p.http.client, 'HTTPConnection', Connection):
            ok, message = p2p.send_to_pc('127.0.0.1', port, 'owner', 'Laptop', 'pc', [file], reconnection_wait=2, retry_interval=0.01)
        self.assertTrue(ok, message)
        self.assertEqual(len(list(self.destination.iterdir())), 1)

    def test_two_simultaneous_uploads_do_not_write_the_same_partial_file(self):
        tid = self.send_offer()
        started, release = threading.Event(), threading.Event()
        class Stream(io.BytesIO):
            def read(inner, size):
                started.set()
                release.wait(3)
                return super().read(size)
        result = []
        thread = threading.Thread(target=lambda: result.append(self.manager.receive_file(tid, 0, Stream(self.data), len(self.data), owner='owner')))
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.manager.resume_state(tid, 'owner')[0], 409)
            self.assertEqual(self.manager.receive_file(tid, 0, io.BytesIO(self.data), len(self.data), owner='owner')[0], 409)
        finally:
            release.set()
            thread.join(4)
        self.assertEqual(result[0][0], 200)
        self.assertEqual((self.destination/'video.mp4').read_bytes(), self.data)

    def test_exhaust_retries_keeps_partial_for_resend(self):
        from p2p_send import upload_files
        tid = self.send_offer()
        self.manager.receive_file(tid, 0, io.BytesIO(self.data[:1234]), len(self.data), owner='owner')
        with patch('p2p_send._state', side_effect=OSError('Sin Wi-Fi')):
            ok, msg = upload_files('127.0.0.1', 1, 'owner', tid, [Path('video.mp4')], self.offer, True, None, 0, 0)
        self.assertFalse(ok)
        self.assertIn('conserva el progreso', msg)
        self.assertEqual(self.manager.resume_state(tid, 'owner')[1]['files'][0]['offset'], 1234)

    def test_resumes_after_address_change(self):
        from p2p_send import _send_file
        info = self.offer['files'][0]
        with patch('p2p_send._state', side_effect=[OSError('IP anterior'), [{'offset': len(self.data), 'received': True}]]) as state, patch('p2p_send._upload') as upload:
            _send_file('old', 1, 'owner', 'id', 0, Path('video.mp4'), info, True, lambda *a, **k: None, 1, 0, lambda: ('new', 2))
        self.assertEqual(state.call_args.args[:2], ('new', 2))
        upload.assert_not_called()

    def test_reserved_paths_before_start_to_read(self):
        from p2p_receive import _prepare
        first = self.send_offer()
        another = dict(self.offer, sender={'id': 'another', 'name': 'Otra PC'})
        _, response = self.manager.process_offer(another, owner='another')
        with self.manager._lock:
            a = _prepare(self.manager, first, 0, len(self.data), 0, 'owner')
            b = _prepare(self.manager, response['id'], 0, len(self.data), 0, 'another')
        self.assertNotEqual(a[1]['partial_path'], b[1]['partial_path'])

    def test_reoffering_a_partial_does_not_need_space_for_two_copies(self):
        tid = self.send_offer()
        self.manager.receive_file(tid, 0, io.BytesIO(self.data[:1234]), len(self.data), owner='owner')
        from types import SimpleNamespace
        with patch.object(p2p.shutil, 'disk_usage', return_value=SimpleNamespace(free=50*1024*1024+len(self.data)-1234+100)):
            code, response = self.manager.process_offer(self.offer, owner='owner')
        self.assertEqual(code, 200)
        self.assertEqual(response['id'], tid)

    def test_unlinking_during_confirmation_does_not_restore_the_link(self):
        def confirm(_):
            self.manager.forget_pc('pc')
            return 'always'
        self.manager.ask_user = confirm
        code, _ = self.manager.process_offer(self.offer, owner='owner')
        self.assertEqual(code, 403)
        self.assertNotIn('pc', self.manager.trusted_pcs)
        self.assertEqual(self.manager._transfers, {})

    def test_sender_cancellation_cleans_remote_offer(self):
        from p2p_send import upload_files
        tid = self.send_offer()
        with patch.object(p2p, '_try_cancel') as cancel:
            ok, _ = upload_files('host', 1, 'owner', tid, [Path('video.mp4')], self.offer, True, None, 1, 0, cancel=lambda: True)
        self.assertFalse(ok)
        cancel.assert_called_once_with('host', 1, 'owner', tid, None)

    def test_unlinking_before_the_confirmation_does_not_register_the_offer(self):
        def space(_):
            self.manager.forget_pc('pc')
            return True, 200, 'OK'
        with patch.object(self.manager, '_check_space', side_effect=space):
            code, _ = self.manager.process_offer(self.offer, owner='owner')
        self.assertEqual(code, 403)

    def test_authorization_revalidates_before_register(self):
        valid = [True]
        def confirm(_):
            valid[0] = False
            return 'always'
        self.manager.ask_user = confirm
        code, _ = self.manager.process_offer(self.offer, owner='owner', authorize=lambda: valid[0])
        self.assertEqual(code, 403)
        self.assertEqual(self.manager._transfers, {})

    def test_cancellation_uses_current_address(self):
        from p2p_send import upload_files
        tid = self.send_offer()
        with patch.object(p2p, '_try_cancel') as cancel:
            ok, _ = upload_files('old', 1, 'owner', tid, [Path('video.mp4')], self.offer, True, None, 1, 0,
                                   resolve=lambda: ('new', 2), cancel=lambda: True)
        self.assertFalse(ok)
        cancel.assert_called_once_with('new', 2, 'owner', tid, None)
