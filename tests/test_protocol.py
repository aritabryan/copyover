"""Contract shared by Shortcuts and Android; doesn't touch the real clipboard."""
import http.client
import http.server
import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import i18n
import bridge
import local_secret


class ProtocolTests(unittest.TestCase):
    def test_copying_text_is_silent_by_default_and_notices_can_be_enabled(self):
        for config, expected in (({"notifications": True}, 0), ({"notifications": True, "clipboard_notifications": True}, 1)):
            with patch.object(self.server.RequestHandlerClass, "notices", True), \
                    patch.object(bridge, "load_config", return_value=config), \
                    patch.object(bridge.so, "set_text") as copy, \
                    patch.object(bridge.so, "notify") as notice, \
                    patch.object(bridge, "register_history"), \
                    patch.object(self.server.RequestHandlerClass, "_identify_client", return_value="Mi Android"):
                state, _, _ = self.request("POST", "/send", b"Test text", {"X-Key": "test-key", "Content-Type": "text/plain"})
            self.assertEqual(state, 200)
            copy.assert_called_once_with("Test text")
            self.assertEqual(notice.call_count, expected)

    @classmethod
    def setUpClass(cls):
        i18n.pin("es")  # responses are compared in Spanish
        class Handler(bridge.Handler):
            key = "test-key"
            notices = False
        cls.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_panel_rejects_other_host_u_origin(self):
        port = self.server.server_address[1]
        own = f"127.0.0.1:{port}"
        self.assertEqual(self.request("GET", "/app/profile", headers={"Host": "evil.example:80"})[0], 403)
        self.assertEqual(self.request("GET", "/app/profile", headers={"Host": own})[0], 200)
        status = self.request("POST", "/app/clear-history", b"{}",
                              {"Host": own, "Origin": "http://evil.example", "Content-Type": "application/json"})[0]
        self.assertEqual(status, 403)

    def test_find_pcs_re_ask_in_network(self):
        own = f"127.0.0.1:{self.server.server_address[1]}"
        bridge._FIND_NOW.clear()
        status = self.request("POST", "/app/scan", b"{}", {"Host": own, "Content-Type": "application/json"})[0]
        self.assertEqual(status, 200)
        self.assertTrue(bridge._FIND_NOW.is_set())
        bridge._FIND_NOW.clear()

    def test_too_many_bad_keys_throttle_the_ip_for_a_while(self):
        throttle = bridge.KeysThrottle(attempts=3, window=60)
        clock = [1000.0]
        with patch.object(bridge, "KEYS_THROTTLE", throttle),                 patch.object(bridge.time, "monotonic", side_effect=lambda: clock[0]):
            for _ in range(3):
                self.assertEqual(self.request("GET", "/traer/tipo", headers={"X-Key": "mala"})[0], 401)
            status, request_headers, body = self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})
            self.assertEqual(status, 429, "frenada, ni la clave buena se prueba")
            self.assertEqual(request_headers["Retry-After"], "61")
            self.assertIn("demasiadas claves incorrectas", body.decode())
            self.assertEqual(self.request("GET", "/p2p/perfil", headers={"X-Key": "test-key"})[0], 429)
            clock[0] += 61
            self.assertEqual(self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})[0], 200)
            # A correct key clears the earlier failures.
            for _ in range(2):
                self.request("GET", "/traer/tipo", headers={"X-Key": "mala"})
            self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})
            self.assertEqual(throttle.wait("127.0.0.1"), 0)
            self.assertNotIn("127.0.0.1", throttle._failures)

    def test_ping_and_authentication_unchanged(self):
        self.assertEqual(self.request("GET", "/ping")[0], 200)
        self.assertEqual(self.request("GET", "/traer")[0], 401)
        self.assertEqual(self.request("POST", "/enviar", b"hola")[0], 401)

    def test_shortcuts_use_names_in_english_and_old_follow_working(self):
        self.assertEqual(self.request("GET", "/receive")[0], 401)
        self.assertEqual(self.request("POST", "/send", b"hola")[0], 401)
        self.assertEqual(self.request("GET", "/receive/type", headers={"X-Key": "mala"})[0], 401)
        cases = (([], "hola", False, "text"), ([], "", True, "image"), ([], "", False, "none"))
        for files, text, image, expected in cases:
            with patch.object(bridge.so, "read_files", return_value=files),                  patch.object(bridge.so, "read_text", return_value=text),                  patch.object(bridge.so, "read_image", return_value=image):
                english = self.request("GET", "/receive/type", headers={"X-Key": "test-key"})
                old = self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})
            self.assertEqual((english[0], english[2].decode()), (200, expected))
            self.assertEqual(old[2].decode(), {"text": "text", "image": "image", "none": "none"}[expected])
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "informe.pdf"
            pdf.write_bytes(b"%PDF")
            with patch.object(bridge.so, "read_files", return_value=[pdf]):
                self.assertEqual(self.request("GET", "/receive/type", headers={"X-Key": "test-key"})[2], b"file")
        with patch.object(bridge.so, "read_files", return_value=[]),              patch.object(bridge.so, "read_text", return_value="desde la PC"):
            status, _, body = self.request("GET", "/receive", headers={"X-Key": "test-key", "X-Device": "iPhone"})
        self.assertEqual((status, body.decode()), (200, "desde la PC"))

    def test_update_metadata_and_apk_require_linked_key(self):
        from updates import UpdateCache
        with tempfile.TemporaryDirectory() as folder:
            cache = UpdateCache(Path(folder))
            digest = 'a' * 64
            (Path(folder) / (digest + '.apk')).write_bytes(b'PK-test')
            with patch.object(bridge.Handler, 'updates', cache):
                self.assertEqual(self.request('GET', '/android/actualizacion')[0], 401)
                self.assertEqual(self.request('GET', '/android/apk/' + digest)[0], 401)
                status, _, body = self.request('GET', '/android/actualizacion', headers={'X-Key': 'test-key'})
                self.assertEqual(status, 200)
                self.assertIsNone(json.loads(body)['update'])
                status, headers, body = self.request('GET', '/android/apk/' + digest, headers={'X-Key': 'test-key'})
                self.assertEqual(status, 200)
                self.assertEqual(body, b'PK-test')
                self.assertEqual(headers['Content-Type'], 'application/vnd.android.package-archive')
                self.assertEqual(self.request('GET', '/android/apk/../config.json', headers={'X-Key': 'test-key'})[0], 404)

    def test_apk_link_from_the_qr_works_without_key_only_while_valid(self):
        from updates import UpdateCache
        with tempfile.TemporaryDirectory() as folder:
            cache = UpdateCache(Path(folder))
            digest = 'b' * 64
            (Path(folder) / (digest + '.apk')).write_bytes(b'PK-qr')
            cache.manifest = {'versionCode': 7, 'versionName': '1.0.6', 'size': 5, 'sha256': digest}
            tokens = bridge.STATE.setdefault('tokens_apk', {})
            tokens.clear()
            tokens['vigente'] = time.time() + 60
            tokens['vencido'] = time.time() - 1
            try:
                with patch.object(bridge.Handler, 'updates', cache):
                    status, headers, body = self.request('GET', '/app-android?t=vigente')
                    self.assertEqual((status, body), (200, b'PK-qr'))
                    self.assertEqual(headers['Content-Type'], 'application/vnd.android.package-archive')
                    self.assertEqual(self.request('GET', '/app-android?t=vencido')[0], 403)
                    self.assertEqual(self.request('GET', '/app-android?t=otro')[0], 403)
                    self.assertEqual(self.request('GET', '/app-android')[0], 403)
                    cache.manifest = None
                    self.assertEqual(self.request('GET', '/app-android?t=vigente')[0], 404)
            finally:
                tokens.clear()

    def test_automatic_sync_requires_key_and_returns_cursor_with_text(self):
        from sync import ClipboardChanges
        watcher = ClipboardChanges(lambda: 7)
        with patch.object(bridge.Handler, 'changes', watcher), \
                patch.object(bridge.so, 'read_files', return_value=[]), \
                patch.object(bridge.so, 'read_text', return_value='Desde Windows 👋'):
            self.assertEqual(self.request('GET', '/sincronizar')[0], 401)
            status, headers, body = self.request('GET', '/sincronizar', headers={'X-Key': 'test-key'})
            self.assertEqual(status, 200)
            self.assertEqual(headers['X-Puente-Version'], watcher.current())
            self.assertEqual(body.decode(), 'Desde Windows 👋')
            self.assertEqual(self.request('GET', '/sincronizar?desde=invalid', headers={'X-Key': 'test-key'})[0], 400)

    def test_no_change_does_not_read_clipboard_or_send_old_contents(self):
        from unittest.mock import Mock
        watcher = Mock()
        watcher.wait.return_value = ('a' * 32 + ':5', False)
        with patch.object(bridge.Handler, 'changes', watcher), patch.object(bridge.so, 'read_text') as read:
            status, headers, body = self.request('GET', '/sincronizar?desde=' + 'a' * 32 + ':5', headers={'X-Key': 'test-key'})
            self.assertEqual(status, 204)
            self.assertEqual(body, b'')
            self.assertEqual(headers['X-Puente-Version'], 'a' * 32 + ':5')
            read.assert_not_called()

    def test_automatic_sync_handles_images_and_skips_files(self):
        from sync import ClipboardChanges
        watcher = ClipboardChanges(lambda: 8)
        def image(path):
            path.write_bytes(b'\x89PNG\r\n\x1a\nimage-test')
            return True
        with patch.object(bridge.Handler, 'changes', watcher), \
                patch.object(bridge.so, 'read_files', return_value=[]), \
                patch.object(bridge.so, 'read_text', return_value=None), \
                patch.object(bridge.so, 'read_image', side_effect=image):
            status, headers, body = self.request('GET', '/sincronizar', headers={'X-Key': 'test-key'})
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'image/png')
            self.assertTrue(body.startswith(b'\x89PNG'))
        with patch.object(bridge.Handler, 'changes', watcher), \
                patch.object(bridge.so, 'read_files', return_value=[Path('large-file.zip')]):
            self.assertEqual(self.request('GET', '/sincronizar', headers={'X-Key': 'test-key'})[0], 204)

    def test_identity_proof_is_public_but_never_returns_key(self):
        nonce = "a" * 64
        with patch.object(bridge.Handler, "identifier", "b" * 32):
            status, headers, body = self.request("GET", f"/identidad?reto={nonce}")
            self.assertEqual(self.request("GET", "/identidad?reto=invalid")[0], 400)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["id"], "b" * 32)
        expected = hmac.new(b"test-key", f"puente-v1:{'b' * 32}:{nonce}:127.0.0.1".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(payload["proof"], expected)
        self.assertNotIn(b"test-key", body)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_identity_proof_signs_with_the_android_device_own_key(self):
        from linking import key_fingerprint
        nonce = "c" * 64
        phone_key = "clave-propia-del-celular"
        hash_key = hashlib.sha256(phone_key.encode()).hexdigest()
        cfg = {"devices": {"d1": {"name": "Pixel", "key_hash": hash_key}}}
        with patch.object(bridge.Handler, "identifier", "b" * 32), patch.object(bridge, "load_config", return_value=cfg):
            status, _, body = self.request("GET", f"/identidad?reto={nonce}&huella={key_fingerprint(hash_key)}")
            # A fingerprint no linked device has (one removed from the panel) gets no proof.
            other = key_fingerprint(hashlib.sha256(b"otra").hexdigest())
            self.assertEqual(self.request("GET", f"/identidad?reto={nonce}&huella={other}")[0], 403)
        self.assertEqual(status, 200)
        message = f"puente-v1:{'b' * 32}:{nonce}:127.0.0.1".encode()
        self.assertEqual(json.loads(body)["proof"], hmac.new(hash_key.encode(), message, hashlib.sha256).hexdigest())

    def test_raw_android_text_preserves_unicode(self):
        with patch.object(bridge.so, "set_text") as clipboard:
            status, _, body = self.request("POST", "/enviar", "Hola 👋\nAndroid".encode(),
                                          {"X-Key": "test-key", "Content-Type": "text/plain; charset=utf-8"})
        self.assertEqual(status, 200)
        self.assertEqual(body.decode(), "Texto copiado en la PC")
        clipboard.assert_called_once_with("Hola 👋\nAndroid")

    def test_copied_text_after_wins_to_prepared_file_in_panel(self):
        # The panel prepared a file (and copied it to the PC clipboard); then the user copied text with Ctrl+C.
        # Receive PC must bring the text, not the file again. Only read_files is used, so it holds on Linux too.
        clipboard = [Path(__file__)]
        request_headers = {"X-Key": "test-key"}
        with patch.dict(bridge.STATE, {"prepared_files": [Path(__file__)],
                                        "prepared_on_clipboard": [Path(__file__)]}),                 patch.object(bridge.so, "read_files", side_effect=lambda: list(clipboard)),                 patch.object(bridge.so, "read_text", return_value="copiado con Ctrl+C"):
            self.assertEqual(self.request("GET", "/receive/type", headers=request_headers)[2], b"file",
                             "mientras el portapapeles tenga el archivo, sigue preparado")
            clipboard.clear()
            self.assertEqual(self.request("GET", "/receive/type", headers=request_headers)[2], b"text")
            self.assertEqual(self.request("GET", "/receive", headers=request_headers)[2].decode(), "copiado con Ctrl+C")
            self.assertEqual(bridge.STATE["prepared_files"], [])

    def test_prepared_files_over_500_mb_stay_even_when_not_on_the_clipboard(self):
        # Over 500 MB the panel doesn't copy it to the clipboard: there's nothing to compare, so it stays.
        with patch.dict(bridge.STATE, {"prepared_files": [Path(__file__)], "prepared_on_clipboard": None}),                 patch.object(bridge.so, "read_files", return_value=[]),                 patch.object(bridge.so, "read_text", return_value="otra cosa"):
            self.assertEqual(self.request("GET", "/receive/type", headers={"X-Key": "test-key"})[2], b"file")

    def test_fetch_without_copied_nothing_explains_in_text(self):
        with patch.object(bridge.so, "read_files", return_value=[]), patch.object(bridge.so, "read_text", return_value=""),              patch.object(bridge.so, "read_image", return_value=False):
            status, _, body = self.request("GET", "/traer", headers={"X-Key": "test-key", "User-Agent": "Shortcuts/1254 CFNetwork/1498 Darwin/23"})
            self.assertEqual(status, 200)
            self.assertIn("no tiene nada copiado", body.decode())
            self.assertEqual(self.request("GET", "/traer", headers={"X-Key": "test-key"})[0], 204, "otros clientes siguen con 204")

    def test_receiving_only_a_clipboard_name_explains_instead_of_pasting(self):
        # Copying a photo and running Send PC sends «Clipboard 3 Nov 2026 at 2.29 AM» as text, not the photo.
        name = "Clipboard 3 Nov 2026 at 2.29 AM".encode("utf-8")
        payload = (b'--shortcut\r\nContent-Disposition: form-data; name="file"\r\n\r\n' + name + b'\r\n--shortcut--\r\n')
        with patch.object(bridge.so, "set_text") as clipboard:
            status, _, body = self.request("POST", "/send", payload,
                                             {"X-Key": "test-key", "Content-Type": "multipart/form-data; boundary=shortcut"})
        self.assertEqual(status, 200)
        self.assertIn("Compartir", body.decode())
        clipboard.assert_not_called()

    def test_ios_multipart_text_still_works(self):
        body = (b'--shortcut\r\nContent-Disposition: form-data; name="archivo"; filename="texto.txt"\r\n'
                b'Content-Type: text/plain\r\n\r\nhola iPhone\r\n--shortcut--\r\n')
        with patch.object(bridge.so, "set_text") as clipboard:
            status, _, _ = self.request("POST", "/enviar", body,
                                        {"X-Key": "test-key", "Content-Type": "multipart/form-data; boundary=shortcut"})
        self.assertEqual(status, 200)
        clipboard.assert_called_once_with("hola iPhone")

    def test_ios_urlencoded_form_text_is_pasted_as_text(self):
        # Shortcuts sends the Form this way when the «archivo» field is text (the clipboard).
        with patch.object(bridge.so, "set_text") as clipboard:
            status, _, _ = self.request("POST", "/enviar", b"archivo=si+somos+%F0%9F%91%8B",
                                        {"X-Key": "test-key", "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200)
        clipboard.assert_called_once_with("si somos 👋")

    def test_fetch_type_tells_the_shortcut_what_the_pc_has(self):
        bridge.STATE["prepared_files"] = []
        cases = [
            ([], "", False, "none"),
            ([], "hola", False, "text"),
            ([], "", True, "image"),
            ([Path(__file__)], "hola", False, "file"),
        ]
        for files, text, image, expected in cases:
            with patch.object(bridge.so, "read_files", return_value=files), \
                 patch.object(bridge.so, "read_text", return_value=text), \
                 patch.object(bridge.so, "read_image", return_value=image):
                status, _, body = self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})
            self.assertEqual((status, body.decode()), (200, expected))
        status, _, _ = self.request("GET", "/traer/tipo", headers={"X-Key": "mala"})
        self.assertEqual(status, 401)
        # A single prepared image is announced as an image: the shortcut puts it on the clipboard, not in
        # Files.
        with tempfile.TemporaryDirectory() as td:
            photo = Path(td) / "foto.jpg"
            photo.write_bytes(b"JPEG de prueba")
            with patch.object(bridge.so, "read_files", return_value=[photo]):
                status, _, body = self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})
                self.assertEqual((status, body.decode()), (200, "image"))
            video = Path(td) / "clip.mp4"
            video.write_bytes(b"MP4 de prueba")
            with patch.object(bridge.so, "read_files", return_value=[video]):
                with patch("bridge.load_config", return_value={}):
                    self.assertEqual(self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})[2].decode(), "image", "por defecto el video va al portapapeles")
                with patch("bridge.load_config", return_value={"phone_clipboard": "images"}):
                    self.assertEqual(self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})[2].decode(), "file", "con «imagenes», el video va a Files")
            with patch.object(bridge.so, "read_files", return_value=[photo]), patch("bridge.load_config", return_value={"phone_clipboard": "files"}):
                self.assertEqual(self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})[2].decode(), "file", "con «archivos», ni las fotos")
            with patch.object(bridge.so, "read_files", return_value=[photo, Path(__file__)]):
                status, _, body = self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})
                self.assertEqual(body.decode(), "file", "dos cosas van a Files")
        # A file too big for the shortcut is announced as text, and /traer sends the explanation instead of a
        # 413.
        with patch.object(bridge.so, "read_files", return_value=[Path(__file__)]), patch.object(bridge, "MAX_BYTES", 10):
            status, _, body = self.request("GET", "/traer/tipo", headers={"X-Key": "test-key"})
            self.assertEqual((status, body.decode()), (200, "text"))
            # From the Shortcuts app and from Spotlight or Control Center (another process, another agent).
            for agent in ("Shortcuts/1254", "BackgroundShortcutRunner/5037.111 CFNetwork/3896.100.1.2.1 Darwin/27.0.0"):
                status, _, body = self.request("GET", "/traer", headers={"X-Key": "test-key", "User-Agent": agent})
                self.assertEqual(status, 200, agent)
                self.assertIn("Vincular celular → Invitados y", body.decode())

    def test_android_large_download_is_an_error_not_clipboard_text(self):
        with patch.dict(bridge.STATE, {'prepared_files': []}), \
                patch.object(bridge.so, 'read_files', return_value=[Path(__file__)]), \
                patch.object(bridge, 'MAX_BYTES', 10):
            for agent in ('ureq/2', 'Puente Android', ''):
                status, _, _ = self.request('GET', '/traer', headers={'X-Key': 'test-key', 'User-Agent': agent})
                self.assertEqual(status, 413, agent)

    def test_received_files_use_neutral_notification(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bridge, "get_downloads_folder", lambda kind="iphone", d=folder: Path(d)), \
                patch.object(bridge.so, "set_files"), patch.object(bridge.so, "notify") as notification:
            # Other server tests can leave a queued burst; this assertion covers one new file.
            with bridge._NOTICES_LOCK:
                if bridge._notice_timer:
                    bridge._notice_timer.cancel()
                bridge._PENDING_NOTICES.clear()
            handler = object.__new__(bridge.Handler)
            handler.notices = True
            handler.receive([("informe.pdf", "application/pdf", b"%PDF-test")])
            self.assertEqual((Path(folder) / "informe.pdf").read_bytes(), b"%PDF-test")
            bridge.release_notices()
            self.assertEqual(notification.call_args.args[:2], ("Archivo recibido", "informe.pdf"))
            self.assertEqual(notification.call_args.kwargs["file"], Path(folder) / "informe.pdf")
            self.assertTrue(notification.call_args.kwargs["open_file"])

    def test_photos_burst_gives_only_notice(self):
        # The iPhone shortcut sends each photo in its own request.
        with patch.object(bridge.so, "notify") as notice, patch.object(bridge, "NOTICE_WAIT", 60):
            for i in range(10):
                bridge.notify_received([Path(f"IMG_{i}.jpg")], "iPhone de Ana")
            self.assertFalse(notice.called)
            bridge.release_notices()
            notice.assert_called_once_with("10 archivos de iPhone de Ana", "IMG_0.jpg, IMG_1.jpg, IMG_2.jpg y 7 más",
                                          note="Copiado al portapapeles. Pégalo con Ctrl+V",
                                          file=Path("IMG_0.jpg"), open_file=False)
            bridge._notice_timer.cancel()

    def test_title_says_what_arrived_and_from_whom(self):
        photo, pdf = Path("IMG_1.HEIC"), Path("informe.pdf")
        self.assertEqual(bridge.received_texts([photo], "iPhone de Ana"), ("Foto de iPhone de Ana", "IMG_1.HEIC"))
        self.assertEqual(bridge.received_texts([pdf], ""), ("Archivo recibido", "informe.pdf"))
        self.assertEqual(bridge.received_texts([photo, pdf], ""), ("2 archivos recibidos", "IMG_1.HEIC, informe.pdf"))

    def test_grouped_notice_from_two_devices_does_not_name_a_sender(self):
        with patch.object(bridge.so, "notify") as notice, patch.object(bridge, "NOTICE_WAIT", 60):
            bridge.notify_received([Path("a.jpg")], "iPhone de Ana")
            bridge.notify_received([Path("b.jpg")], "Pixel de Luis")
            bridge.release_notices()
            bridge._notice_timer.cancel()
        self.assertEqual(notice.call_args.args[0], "2 archivos recibidos")

    def test_opening_again_shows_the_running_panel(self):
        done = threading.Event()
        bridge.Handler.actions = {"open_panel": done.set}
        try:
            self.assertTrue(bridge.open_open_instance_panel(self.server.server_address[1]))
            self.assertTrue(done.wait(2))
        finally:
            bridge.Handler.actions = {}

    def test_the_old_spanish_route_still_opens_the_panel(self):
        """A copy of 1.0.6 or earlier opened again still asks the running one through /app/accion."""
        done = threading.Event()
        bridge.Handler.actions = {"open_panel": done.set}
        try:
            con = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
            try:
                con.request("POST", "/app/accion", body=json.dumps({"action": "open_panel"}),
                            headers={"Content-Type": "application/json", local_secret.HEADER: local_secret.read()})
                self.assertEqual(con.getresponse().status, 200)
            finally:
                con.close()
            self.assertTrue(done.wait(2))
        finally:
            bridge.Handler.actions = {}

    def test_open_again_falls_back_to_the_old_route_of_an_older_copy(self):
        """If the running copy doesn't know /app/action (1.0.6 or earlier), it's asked through /app/accion."""
        seen = []

        class OldCopy(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200 if self.path == "/app/accion" else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        old = http.server.HTTPServer(("127.0.0.1", 0), OldCopy)
        threading.Thread(target=old.serve_forever, daemon=True).start()
        try:
            self.assertTrue(bridge.open_open_instance_panel(old.server_address[1]))
        finally:
            old.shutdown()
            old.server_close()
        self.assertEqual(seen, ["/app/action", "/app/accion"])

    @unittest.skipUnless(sys.platform == "win32", "el doble bind solo pasa en Windows")
    def test_two_servers_cannot_start_on_the_same_port(self):
        first = bridge.BridgeServer(("127.0.0.1", 0), bridge.Handler)
        try:
            with self.assertRaises(OSError):
                bridge.BridgeServer(("127.0.0.1", first.server_address[1]), bridge.Handler)
        finally:
            first.server_close()

    def test_received_files_go_to_the_folder_chosen_in_settings(self):
        with tempfile.TemporaryDirectory() as folder,                 patch.object(bridge, "load_config", return_value={"download_folder": folder}),                 patch.object(bridge.so, "set_files"), patch.object(bridge.so, "notify"):
            handler = object.__new__(bridge.Handler)
            handler.notices = False
            handler.receive([("informe.pdf", "application/pdf", b"%PDF-test")])
            self.assertTrue((Path(folder) / "From CopyOver" / "informe.pdf").is_file())
            self.assertEqual(bridge._create_p2p_manager().destination, Path(folder) / "From CopyOver" / "From another PC")

    def test_android_named_text_file_is_saved_with_other_files(self):
        # Rust intentionally uses octet-stream for a shared text/plain file so
        # it does not trigger Shortcuts' legacy text-from-file behavior.
        body = (b'--android\r\nContent-Disposition: form-data; name="archivo"; filename="notas.txt"\r\n'
                b'Content-Type: application/octet-stream\r\n\r\nmis notas\r\n'
                b'--android\r\nContent-Disposition: form-data; name="archivo"; filename="informe.pdf"\r\n'
                b'Content-Type: application/pdf\r\n\r\n%PDF-test\r\n--android--\r\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(bridge, "get_downloads_folder", lambda kind="iphone", d=folder: Path(d)), \
                patch.object(bridge.so, "set_files") as helper, patch.object(bridge.so, "notify"):
            status, _, _ = self.request("POST", "/enviar", body,
                {"X-Key": "test-key", "Content-Type": "multipart/form-data; boundary=android"})
            self.assertEqual(status, 200)
            self.assertEqual((Path(folder) / "notas.txt").read_bytes(), b"mis notas")
            self.assertEqual((Path(folder) / "informe.pdf").read_bytes(), b"%PDF-test")
            self.assertEqual([path.name for path in helper.call_args.args[0]], ["notas.txt", "informe.pdf"])

    def test_receive_text_and_empty_clipboard(self):
        with patch.object(bridge.so, "read_files", return_value=[]), \
                patch.object(bridge.so, "read_text", return_value="Desde Windows 👋"):
            status, _, body = self.request("GET", "/traer", headers={"X-Key": "test-key"})
        self.assertEqual(status, 200)
        self.assertEqual(body.decode(), "Desde Windows 👋")
        with patch.object(bridge.so, "read_files", return_value=[]), \
                patch.object(bridge.so, "read_text", return_value=None), \
                patch.object(bridge.so, "read_image", return_value=False):
            self.assertEqual(self.request("GET", "/traer", headers={"X-Key": "test-key"})[0], 204)

    def test_receive_named_file_preserves_download_headers(self):
        # The server thread may still be closing the file when the response is read.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            file = Path(folder) / "informe español.pdf"
            file.write_bytes(b"%PDF-test")
            with patch.object(bridge.so, "read_files", return_value=[file]):
                status, headers, body = self.request("GET", "/traer", headers={"X-Key": "test-key"})
        self.assertEqual(status, 200)
        self.assertEqual(body, b"%PDF-test")
        self.assertIn("filename*=UTF-8''informe%20espa%C3%B1ol.pdf", headers["Content-Disposition"])

    def test_size_limit_before_reading_body(self):
        with patch.object(bridge, "MAX_BYTES_WEB", 3):
            self.assertEqual(self.request("POST", "/enviar", b"hola", {"X-Key": "test-key"})[0], 413)


if __name__ == "__main__":
    unittest.main()
