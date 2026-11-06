"""The panel (/app/*) and the PC's shared key can't be read from another computer on the network."""
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge

KEY = "clave-comun-de-prueba"


class _ServerFromOtherIp(bridge.ThreadingHTTPServer):
    """Listens on 127.0.0.1 but each connection arrives as if it came from a phone on the network."""
    client_ip = "192.168.50.77"

    def get_request(self):
        connection, _ = super().get_request()
        return connection, (self.client_ip, 50000)


class LocalOnlyPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key_before = bridge.Handler.key
        bridge.Handler.key = KEY
        cls.server = _ServerFromOtherIp(("127.0.0.1", 0), bridge.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        bridge.Handler.key = cls.key_before

    def request(self, method, path, host=None, request_headers=None, body=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        con.request(method, path, body=body,
                    headers={"Host": host or f"127.0.0.1:{self.port}", **(request_headers or {})})
        response = con.getresponse()
        data = response.read()
        con.close()
        return response.status, data

    def test_from_panel_other_ip_responds_403_and_without_key(self):
        cfg = {"key": KEY, "port": 8765, "id": "yo", "name": "pc"}
        with patch.object(bridge, "load_config", return_value=cfg):
            # Even if it sends the localhost Host (DNS rebinding or a forged Host), it isn't this PC.
            for path in ("/app/state", "/app/profile", "/app/diagnostics", "/app/qr", "/app/guest-token", "/app"):
                for host in (None, f"localhost:{self.port}", f"192.168.50.10:{self.port}"):
                    state, body = self.request("GET", path, host=host)
                    self.assertEqual(state, 403, f"{path} con Host {host}")
                    self.assertNotIn(KEY.encode(), body)
            state, body = self.request("POST", "/app/profile", body=json.dumps({"name": "x"}),
                                        request_headers={"Content-Type": "application/json"})
            self.assertEqual(state, 403)

    def test_pc_to_pc_paths_need_the_key_and_state_is_not_returned(self):
        cfg = {"key": KEY, "port": 8765, "id": "yo", "name": "pc"}
        with patch.object(bridge, "load_config", return_value=cfg):
            self.assertEqual(self.request("GET", "/p2p/estado")[0], 403)
            self.assertEqual(self.request("GET", "/p2p/perfil", request_headers={"X-Key": "other"})[0], 403)
            state, body = self.request("GET", "/p2p/estado", request_headers={"X-Key": KEY})
            self.assertEqual(state, 200)
            self.assertEqual(json.loads(body)["key"], "", "a otro equipo nunca se le devuelve la clave")


if __name__ == "__main__":
    unittest.main()
