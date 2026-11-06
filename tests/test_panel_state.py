"""What the panel and the quick view read from CopyOver: the pending modal, the history and the window."""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge
import preview


class PanelStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        con.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
        response = con.getresponse()
        data = json.loads(response.read())
        con.close()
        return data

    def test_reset_re_default_folder(self):
        from unittest.mock import patch
        cfg = {"port": 8765, "download_folder": "D:/Otra"}
        with patch.object(bridge, "load_config", side_effect=lambda: dict(cfg)), \
                patch.object(bridge, "save_config", side_effect=cfg.update):
            self.assertFalse(self.request("/app/profile")["download_folder_is_default"])
            con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            con.request("POST", "/app/reset-downloads-folder", b"{}",
                        {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"})
            data = json.loads(con.getresponse().read())
            con.close()
            self.assertEqual(data, {"ok": True, "folder": str(bridge.so.DOCUMENTS)})
            profile = self.request("/app/profile")
        self.assertTrue(profile["download_folder_is_default"])
        # Settings that were saved but did nothing.
        for dead in ("auto_aceptar_p2p", "avisos_envio", "al_cerrar", "modo_seguridad",
                       "confirmar_celular", "portapapeles_celular"):
            self.assertNotIn(dead, profile)

    def test_pending_modal_only_carries_panel(self):
        bridge.STATE["open_modal"] = "qr"
        try:
            self.assertIsNone(self.request("/app/state")["open_modal"], "la vista rápida no lo consume")
            self.assertEqual(self.request("/app/state?panel=1&_t=1")["open_modal"], "qr")
            self.assertIsNone(self.request("/app/state?panel=1")["open_modal"], "se entrega una vez")
        finally:
            bridge.STATE.pop("open_modal", None)

    def test_images_flag_history_files_that_are_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            follows = Path(tmp) / "sigue.png"
            follows.write_bytes(b"png")
            before = bridge.STATE["history"]
            bridge.STATE["history"] = [
                {"id": "1", "type": "file", "name": "sigue.png", "path": str(follows)},
                {"id": "2", "type": "file", "name": "borrada.jpg", "path": str(Path(tmp) / "borrada.jpg")},
                {"id": "3", "type": "text", "name": "hola"},
                {"id": "4", "type": "file", "name": "doc.pdf", "path": str(Path(tmp) / "doc.pdf")},
            ]
            try:
                by_id = {h["id"]: h for h in self.request("/app/history")}
            finally:
                bridge.STATE["history"] = before
        self.assertNotIn("exists", by_id["1"])
        self.assertIs(by_id["2"]["exists"], False)
        self.assertNotIn("exists", by_id["3"])
        self.assertNotIn("exists", by_id["4"], "only images, the ones with a thumbnail")

    def test_expired_guest_links_clean(self):
        tokens = bridge.STATE.setdefault("guest_tokens", {})
        tokens.clear()
        tokens["viejo"] = time.time() - 10
        tokens["vigente"] = time.time() + 600
        data = self.request("/app/guest-token")
        self.assertNotIn("viejo", bridge.STATE["guest_tokens"])
        self.assertIn("vigente", bridge.STATE["guest_tokens"])
        self.assertIn(data["token"], bridge.STATE["guest_tokens"])
        tokens.clear()


class QuickViewWindowTests(unittest.TestCase):
    def test_on_windows_uses_its_own_cursor_position_and_not_the_one_sent(self):
        own = Mock(return_value=(3000, 1800))
        order = ["mostrar", "1500", "900"]
        if sys.platform == "win32":
            self.assertEqual(preview.point_to_show(order, own), (3000, 1800))
            own.side_effect = OSError("sin cursor")
        self.assertEqual(preview.point_to_show(order, own), (1500, 900))

    def test_notifies_to_page_if_is_visible(self):
        self.assertEqual(preview.visibility_notice(True),
                         "window.copyoverVisible && window.copyoverVisible(true)")
        self.assertEqual(preview.visibility_notice(False),
                         "window.copyoverVisible && window.copyoverVisible(false)")


if __name__ == "__main__":
    unittest.main()
