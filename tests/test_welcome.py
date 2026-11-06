"""First run: the panel greets you until something is linked or the welcome card is closed."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_ui  # noqa: E402
import configuration  # noqa: E402
import bridge  # noqa: E402


class WelcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.json"
        # The state reads the real network adapters, which on a CI runner can take longer than the request timeout.
        self.patches = [patch.object(configuration, "CONFIG", self.config),
                        patch.object(bridge, "ip_local", return_value="192.168.1.10"),
                        patch.object(bridge, "pc_networks", return_value=[])]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        self.tmp.cleanup()

    def _save(self, cfg):
        self.config.write_text(json.dumps({"port": 8765, "key": "k", "id": "x" * 32, **cfg}), encoding="utf-8")

    def _request(self, method, path, body=None):
        host = f"127.0.0.1:{self.server.server_address[1]}"
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            conn.request(method, path, body, {"Host": host, "Content-Type": "application/json"})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def _state(self):
        status, body = self._request("GET", "/app/state")
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_state_asks_welcome_without_devices_nor_mark(self):
        self._save({})
        self.assertTrue(self._state()["welcome"])

    def test_state_skips_welcome_with_linked_devices(self):
        self._save({"devices": {"a": {"name": "iPhone de Ana"}}})
        self.assertFalse(self._state()["welcome"])

    def test_view_welcome_saves(self):
        self._save({})
        status, _ = self._request("POST", "/app/welcome-seen", b"{}")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(self.config.read_text(encoding="utf-8"))["welcome_seen"])
        self.assertFalse(self._state()["welcome"])

    def test_panel_has_welcome_card(self):
        self.assertIn('id="welcome"', web_ui.HTML_APP)
        self.assertIn("/app/welcome-seen", web_ui.HTML_APP)

    def test_open_panel_on_start_only_without_mark(self):
        self.assertTrue(bridge.open_panel_on_start({}))
        self.assertFalse(bridge.open_panel_on_start({"welcome_seen": True}))
        self.assertFalse(bridge.open_panel_on_start({"devices": {"a": {}}}))


if __name__ == "__main__":
    unittest.main()
