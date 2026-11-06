"""The panel (/app/*) and the local helpers need this run's local secret, not just being on 127.0.0.1: another
account on the same PC also reaches 127.0.0.1."""
import http.client
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dialogs  # noqa: E402
import p2p  # noqa: E402
import bridge  # noqa: E402
import local_secret  # noqa: E402
from server_fixture import TestServer  # noqa: E402

SECRET = "secreto-local-de-prueba"
KEY = "clave-comun-de-prueba"
CFG = {"key": KEY, "port": 8765, "id": "yo", "name": "pc"}


def _request(port, method, path, request_headers=None, body=None, host=None):
    con = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        con.request(method, path, body=body, headers={"Host": host or f"127.0.0.1:{port}", **(request_headers or {})})
        response = con.getresponse()
        return response.status, response.read(), response.headers
    finally:
        con.close()


class PanelWithSecretTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key_before = bridge.Handler.key
        bridge.Handler.key = KEY
        cls.server = TestServer(("127.0.0.1", 0), bridge.Handler)
        cls.server.local_secret = SECRET
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        bridge.Handler.key = cls.key_before

    def setUp(self):
        config = patch.object(bridge, "load_config", return_value=dict(CFG))
        config.start()
        self.addCleanup(config.stop)
        self.addCleanup(self.server.wait)

    def request(self, method, path, request_headers=None, body=None, host=None):
        return _request(self.port, method, path, request_headers, body, host)

    def test_from_is_pc_and_with_host_and_correct_origin_without_secret_is_403(self):
        origin = {"Origin": f"http://127.0.0.1:{self.port}"}
        for path in ("/app", "/app/state", "/app/profile", "/app/diagnostics", "/app/qr", "/app/guest-token",
                     "/app/history", "/app/preview"):
            state, body, _ = self.request("GET", path, request_headers=origin)
            self.assertEqual(state, 403, path)
            self.assertNotIn(KEY.encode(), body)
        for path, data in (("/app/action", {"action": "open_panel"}), ("/app/send", {"paths": [__file__]}),
                            ("/app/profile", {"name": "x"}), ("/app/quit", {})):
            state, _, _ = self.request("POST", path, request_headers={**origin, "Content-Type": "application/json"},
                                      body=json.dumps(data))
            self.assertEqual(state, 403, path)

    def test_wrong_secret_does_not_get_in_either(self):
        for request_headers in ({local_secret.HEADER: "otro"}, {"Cookie": f"{local_secret.COOKIE}=otro"},
                          {"X-Key": KEY}, {"Cookie": f"otra={SECRET}"}):
            self.assertEqual(self.request("GET", "/app/state", request_headers=request_headers)[0], 403, request_headers)

    def test_with_secret_in_cookie_or_in_header_responds(self):
        for request_headers in ({local_secret.HEADER: SECRET}, {"Cookie": f"tema=oscuro; {local_secret.COOKIE}={SECRET}"}):
            state, body, _ = self.request("GET", "/app/state", request_headers=request_headers)
            self.assertEqual(state, 200, request_headers)
            self.assertEqual(json.loads(body)["key"], KEY)

    def test_with_secret_but_from_other_host_follows_being_403(self):
        state, _, _ = self.request("GET", "/app/state", request_headers={local_secret.HEADER: SECRET},
                                  host=f"192.168.1.20:{self.port}")
        self.assertEqual(state, 403)

    def test_enter_leaves_cookie_and_passes_to_panel(self):
        state, body, request_headers = self.request("GET", f"/app/enter?s={SECRET}&a=/app/preview")
        self.assertEqual(state, 200)
        cookie = request_headers["Set-Cookie"]
        self.assertTrue(cookie.startswith(f"{local_secret.COOKIE}={SECRET};"), cookie)
        for attribute in ("HttpOnly", "SameSite=Strict", "Path=/app"):
            self.assertIn(attribute, cookie)
        self.assertIn(b'url=/app/preview"', body)
        self.assertNotIn(SECRET.encode(), body)

    def test_enter_without_secret_or_redirecting_away_sets_no_cookie(self):
        for path in ("/app/enter", "/app/enter?s=otro", f"/app/enter?s={SECRET}&a=https://ejemplo.com",
                     f"/app/enter?s={SECRET}&a=//ejemplo.com"):
            state, _, request_headers = self.request("GET", path)
            self.assertEqual(state, 403, path)
            self.assertIsNone(request_headers["Set-Cookie"], path)
        state, _, request_headers = self.request("GET", f"/app/enter?s={SECRET}", host=f"otro.local:{self.port}")
        self.assertEqual(state, 403)
        self.assertIsNone(request_headers["Set-Cookie"])

    def test_nearby_pcs_need_the_secret_and_not_the_key(self):
        self.assertEqual(self.request("GET", "/p2p/equipos", request_headers={local_secret.HEADER: SECRET})[0], 200)
        self.assertEqual(self.request("GET", "/p2p/equipos", request_headers={local_secret.HEADER: "otro"})[0], 403)

    def test_send_and_local_notice_need_the_secret_and_the_key_is_not_enough(self):
        body = json.dumps({"paths": [__file__]})
        with patch.object(bridge, "join_to_send") as join:
            state, _, _ = self.request("POST", bridge.LOCAL_SEND_PATH, body=body,
                                      request_headers={"X-Key": KEY, "Content-Type": "application/json"})
            self.assertEqual(state, 403)
            join.assert_not_called()
            state, _, _ = self.request("POST", bridge.LOCAL_SEND_PATH, body=body,
                                      request_headers={local_secret.HEADER: SECRET, "Content-Type": "application/json"})
            self.assertEqual(state, 200)
            join.assert_called_once()
        state, _, _ = self.request("POST", bridge.LOCAL_NOTICE_PATH, body=json.dumps({"code": "a", "action": "open"}),
                                  request_headers={"X-Key": KEY, "Content-Type": "application/json"})
        self.assertEqual(state, 403)


class RealServerTests(unittest.TestCase):
    def test_server_without_secret_does_not_open_the_panel(self):
        server = bridge.BridgeServer(("127.0.0.1", 0), bridge.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            for request_headers in ({}, {local_secret.HEADER: ""}, {"Cookie": f"{local_secret.COOKIE}="}):
                self.assertEqual(_request(port, "GET", "/app/state", request_headers)[0], 403, request_headers)
            self.assertEqual(_request(port, "GET", "/app/enter?s=")[0], 403)
        finally:
            server.shutdown()
            server.server_close()

    def test_secret_creates_only_if_port_is_our(self):
        with patch.object(bridge, "_server_with_retries", return_value=None), \
                patch.object(bridge, "open_open_instance_panel", return_value=False), \
                patch.object(bridge, "notify_busy_port"), \
                patch.object(local_secret, "create") as create, self.assertRaises(SystemExit):
            bridge.serve({"port": 8765, "key": "x", "id": "y"})
        create.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "SO_EXCLUSIVEADDRUSE solo existe en Windows")
    def test_another_program_cannot_listen_on_the_same_port(self):
        server = bridge.BridgeServer(("0.0.0.0", 0), bridge.Handler)
        try:
            port = server.server_address[1]
            for address in ("127.0.0.1", "0.0.0.0"):
                intruder = socket.socket()
                try:
                    intruder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    with self.assertRaises(OSError, msg=address):
                        intruder.bind((address, port))
                finally:
                    intruder.close()
        finally:
            server.server_close()
        # Once closed, the port is free again for the next CopyOver (restart from the tray).
        bridge.BridgeServer(("0.0.0.0", port), bridge.Handler).server_close()


class SecretFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / local_secret.FILE
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(local_secret.VARIABLE, None)

    def test_create_leaves_in_environment_and_in_file(self):
        secret = local_secret.create(self.path)
        self.assertGreaterEqual(len(secret), 40)
        self.assertEqual(os.environ[local_secret.VARIABLE], secret)
        self.assertEqual(self.path.read_text(encoding="ascii"), secret)
        self.assertNotEqual(local_secret.create(self.path), secret, "cada arranque tiene el suyo")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [self.path], "sin temporales sueltos")

    def test_read_prefers_the_environment_and_falls_back_to_the_file(self):
        self.path.write_text("del-archivo", encoding="ascii")
        with patch.object(local_secret, "path", return_value=self.path):
            self.assertEqual(local_secret.read(), "del-archivo")
            os.environ[local_secret.VARIABLE] = "del-entorno"
            self.assertEqual(local_secret.read(), "del-entorno")
        with patch.object(local_secret, "path", return_value=Path(self.tmp.name) / "no-existe"):
            os.environ.pop(local_secret.VARIABLE)
            self.assertEqual(local_secret.read(), "")

    @unittest.skipIf(sys.platform == "win32", "permisos POSIX")
    def test_in_linux_only_reads_user(self):
        previous = os.umask(0)  # even with a permissive umask
        try:
            local_secret.create(self.path)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    @unittest.skipUnless(sys.platform == "win32", "permisos de Windows")
    def test_in_windows_restricts_before_write_secret(self):
        import certificates
        seen = []

        def restrict(path):
            seen.append(path.stat().st_size)

        with patch.object(certificates, "_restrict_permissions", side_effect=restrict):
            local_secret.create(self.path)
        self.assertEqual(seen, [0], "se restringe una vez, todavía vacío")

    def test_window_open_enter_and_private_page_browser(self):
        os.environ[local_secret.VARIABLE] = SECRET
        self.assertEqual(local_secret.input_url(8765, "/app/preview"),
                         f"http://127.0.0.1:8765/app/enter?s={SECRET}&a=/app/preview")
        link = local_secret.link_for_browser(8765)
        self.assertTrue(link.startswith("file:"), link)
        self.assertNotIn(SECRET, link, "el secreto no va en la línea de comandos del navegador")
        from urllib.request import url2pathname
        from urllib.parse import urlsplit
        page = Path(url2pathname(urlsplit(link).path))
        self.addCleanup(lambda: (page.unlink(), page.parent.rmdir()))
        self.assertIn(f"/app/enter?s={SECRET}", page.read_text(encoding="utf-8"))
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(page.stat().st_mode), 0o600)
        opened = []
        with patch.object(dialogs, "focus_existing_window", return_value=False), \
                patch.object(dialogs, "_recently_launched", return_value=False), \
                patch.object(dialogs, "_mark_launch"), \
                patch("panel_window.PanelLauncher.show", return_value=False), \
                patch.object(local_secret, "link_for_browser", return_value="file:///x/open.html"), \
                patch.object(dialogs, "_open_in_browser", side_effect=lambda url: opened.append(url) or True):
            dialogs.launch_web_window(8765)
        self.assertEqual(opened, ["file:///x/open.html"])


class _Spy(BaseHTTPRequestHandler):
    """Whatever is listening on the port: records what the local helpers send it."""
    requests: list = []

    def _record(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        self.requests.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"[]")

    do_GET = do_POST = _record

    def log_message(self, *args):
        pass


class LocalClientsTests(unittest.TestCase):
    """If another program holds the port, it gets the run's local secret (useless once CopyOver starts and
    makes a new one), never the shared key."""

    @classmethod
    def setUpClass(cls):
        cls.spy = ThreadingHTTPServer(("127.0.0.1", 0), _Spy)
        threading.Thread(target=cls.spy.serve_forever, daemon=True).start()
        cls.cfg = {**CFG, "port": cls.spy.server_address[1]}

    @classmethod
    def tearDownClass(cls):
        cls.spy.shutdown()
        cls.spy.server_close()

    def setUp(self):
        _Spy.requests = []
        environment = patch.dict(os.environ, {local_secret.VARIABLE: SECRET})
        environment.start()
        self.addCleanup(environment.stop)

    def test_no_local_client_sends_the_common_key(self):
        file = Path(__file__).resolve()
        self.assertTrue(bridge.resend_to_open_instance(self.cfg, [str(file)]))
        self.assertTrue(bridge.pass_notice_to_open_instance(self.cfg, ["copyover-notice:abc/open"]))
        self.assertTrue(bridge.open_open_instance_panel(self.cfg["port"]))
        with patch.object(bridge, "load_config", return_value=self.cfg):
            bridge.rediscover_pc("otra-pc")
        self.assertEqual(p2p.query_local_pcs(self.cfg["port"], local_secret.read()), [])
        import tray_actions
        tray_actions.send_phone(self.cfg["port"], lambda: [{"path": str(file)}], lambda *a: None)
        paths = [path for path, _, _ in _Spy.requests]
        self.assertEqual(paths, ["/local/enviar", "/local/aviso", "/app/action", "/p2p/equipos",
                                 "/p2p/equipos", "/app/send"])
        for path, request_headers, body in _Spy.requests:
            self.assertEqual(request_headers.get(local_secret.HEADER), SECRET, path)
            self.assertNotIn("X-Key", request_headers, path)
            self.assertNotIn(KEY, json.dumps(request_headers) + body.decode("utf-8", "replace"), path)


if __name__ == "__main__":
    unittest.main()
