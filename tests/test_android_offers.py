import tempfile
import unittest
import http.client
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

from android_offers import AndroidOffers


class AndroidOfferTests(unittest.TestCase):
    def test_http_auth_download_and_confirmation(self):
        import bridge
        with tempfile.TemporaryDirectory() as folder, AndroidOffers() as offers:
            source = Path(folder) / 'a.txt'
            source.write_bytes(b'original')
            state = {'phone_offer': {'id': 'one', 'type': 'file'}, 'prepared_files': [source]}
            server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method, route, key='secret', with_headers=False):
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                try:
                    connection.request(method, route, headers={'X-Key': key})
                    response = connection.getresponse()
                    result = (response.status, response.read())
                    return (*result, dict(response.getheaders())) if with_headers else result
                finally:
                    connection.close()
            try:
                with patch.object(bridge.Handler, 'key', 'secret'), patch.object(bridge.Handler, 'android_offers', offers), patch.object(bridge, 'STATE', state):
                    self.assertEqual(request('GET', '/android/ofertas', '')[0], 401)
                    self.assertEqual(request('GET', '/android/ofertas?t=guest', '')[0], 401)
                    code, body = request('GET', '/android/ofertas')
                    for _ in range(100):
                        if code != 503:
                            break
                        time.sleep(0.01)
                        code, body = request('GET', '/android/ofertas')
                    self.assertEqual(code, 200)
                    offer_id = json.loads(body)['offer']['id']
                    route = '/android/ofertas/' + offer_id
                    self.assertEqual(request('POST', route + '/confirmar')[0], 409)
                    source.write_bytes(b'changed')
                    self.assertEqual(request('GET', route + '/archivo'), (200, b'original'))
                    self.assertIn("filename*=UTF-8''a.txt", request('GET', route + '/archivo', with_headers=True)[2].get('Content-Disposition', ''))
                    self.assertEqual(request('POST', route + '/confirmar')[0], 200)
                    self.assertEqual(request('GET', route + '/archivo', '')[0], 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_snapshot_is_immutable_and_metadata_has_no_paths(self):
        with tempfile.TemporaryDirectory() as folder, AndroidOffers() as offers:
            source = Path(folder) / 'document.txt'
            source.write_bytes(b'original')
            state = {'phone_offer': {'id': 'one', 'type': 'file'}, 'prepared_files': [source]}
            meta = offers.current(state, 'My PC')
            source.write_bytes(b'replaced')
            with offers.open_stream(meta['id']) as stream:
                self.assertEqual(stream.read(), b'original')
            self.assertEqual(set(meta), {'id', 'name', 'mime', 'size', 'pc', 'sha256'})
            state['phone_offer']['id'] = 'two'
            next_offer = offers.current(state, 'My PC')
            self.assertNotEqual(meta['id'], next_offer['id'])
            with offers.open_stream(meta['id']) as stream:
                self.assertEqual(stream.read(), b'original')

    def test_partial_download_cannot_confirm_and_receipts_are_key_bound(self):
        with tempfile.TemporaryDirectory() as folder, AndroidOffers() as offers:
            source = Path(folder) / 'a.txt'
            source.write_bytes(b'content')
            state = {'phone_offer': {'id': 'one', 'type': 'file'}, 'prepared_files': [source]}
            offer_id = offers.current(state, 'PC')['id']
            self.assertFalse(offers.confirm(offer_id, 'key'))
            offers.downloaded(offer_id, 'key')
            self.assertFalse(offers.confirm(offer_id, 'other-key'))
            self.assertTrue(offers.confirm(offer_id, 'key'))

    def test_limits_and_text_are_not_file_offers(self):
        with tempfile.TemporaryDirectory() as folder, AndroidOffers(max_bytes=3) as offers:
            source = Path(folder) / 'a.txt'
            source.write_bytes(b'four')
            state = {'phone_offer': {'id': 'one', 'type': 'file'}, 'prepared_files': [source]}
            with self.assertRaises(ValueError):
                offers.current(state, 'PC')
            state['phone_offer']['type'] = 'text'
            self.assertIsNone(offers.current(state, 'PC'))

    def test_many_files_are_a_safe_zip(self):
        import zipfile
        with tempfile.TemporaryDirectory() as folder, AndroidOffers() as offers:
            files = [Path(folder) / name for name in ['a.txt', 'b.txt']]
            for file in files:
                file.write_bytes(b'abc')
            meta = offers.current({'phone_offer': {'id': 'zip', 'type': 'file'}, 'prepared_files': files}, 'PC')
            self.assertEqual(meta['mime'], 'application/zip')
            with offers.open_stream(meta['id']) as stream, zipfile.ZipFile(stream) as archive:
                self.assertEqual(archive.namelist(), ['a.txt', 'b.txt'])


if __name__ == '__main__':
    unittest.main()
