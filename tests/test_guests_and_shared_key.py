"""Guest links, cutting off guests, changing the shared key and the /sincronizar wait that rechecks the key."""
import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge
from sync import ClipboardChanges
from tests.server_fixture import TestServer

KEY = "clave-comun-de-prueba"


class _WithServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key_before = bridge.Handler.key
        cls.server = TestServer(("127.0.0.1", 0), bridge.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        bridge.Handler.key = cls.key_before

    def setUp(self):
        bridge.Handler.key = KEY
        self.cfg = {"key": KEY, "port": 8765, "id": "yo", "name": "pc"}
        patches = [patch.object(bridge, "load_config", side_effect=lambda: json.loads(json.dumps(self.cfg))),
                   patch.object(bridge, "save_config", side_effect=lambda cfg: self.cfg.update(cfg))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tokens_before = (dict(bridge.STATE.get("guest_tokens", {})), dict(bridge.STATE.get("phone_tokens", {})))
        bridge.STATE["guest_tokens"] = {}
        bridge.STATE["phone_tokens"] = {}
        self.addCleanup(self._restore_tokens)

    def _restore_tokens(self):
        self.server.wait()
        bridge.STATE["guest_tokens"], bridge.STATE["phone_tokens"] = self.tokens_before

    def request(self, method, path, body=None, request_headers=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        con.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **(request_headers or {})})
        response = con.getresponse()
        data = response.read()
        con.close()
        self.server.wait()
        return response.status, data

    def request_json(self, method, path, data=None):
        state, body = self.request(method, path, json.dumps(data or {}) if method == "POST" else None,
                                    {"Content-Type": "application/json"} if method == "POST" else None)
        return state, json.loads(body)


class OwnerTokensTests(_WithServer):
    def test_send_to_iphone_neither_reuses_nor_extends_a_guest_link(self):
        _, guest = self.request_json("GET", "/app/guest-token")
        guest_expires = bridge.STATE["guest_tokens"][guest["token"]]
        self.assertLessEqual(guest_expires, time.time() + bridge.GUEST_VALIDITY + 1)
        own = bridge._get_or_create_mobile_token(port=self.port)
        self.assertNotEqual(own["token"], guest["token"])
        self.assertNotIn(guest["token"], own["url"])
        self.assertEqual(bridge.STATE["guest_tokens"][guest["token"]], guest_expires, "el del invitado no se alarga")
        self.assertNotIn(own["token"], bridge.STATE["guest_tokens"])
        # The owner's own link is reused between sends.
        self.assertEqual(bridge._get_or_create_mobile_token(port=self.port)["token"], own["token"])
        self.assertEqual(bridge.web_token_type(own["token"]), "mobile")
        self.assertEqual(bridge.web_token_type(guest["token"]), "guest")

    def test_opening_the_page_renews_guests_for_30_minutes_without_shortening_the_owner(self):
        bridge.STATE["guest_tokens"]["inv"] = time.time() + 60
        bridge.STATE["phone_tokens"]["mio"] = time.time() + bridge.MOBILE_VALIDITY
        my_length = bridge.STATE["phone_tokens"]["mio"]
        with patch.object(bridge.so, "read_text", return_value=""):
            self.assertEqual(self.request("GET", "/invitado/estado?t=inv")[0], 200)
            self.assertEqual(self.request("GET", "/invitado/estado?t=mio")[0], 200)
        self.assertLessEqual(bridge.STATE["guest_tokens"]["inv"], time.time() + bridge.GUEST_VALIDITY + 1)
        self.assertGreater(bridge.STATE["guest_tokens"]["inv"], time.time() + bridge.GUEST_VALIDITY - 60)
        self.assertGreaterEqual(bridge.STATE["phone_tokens"]["mio"], my_length)

    def test_expired_or_unknown_link_does_not_open_the_page(self):
        bridge.STATE["guest_tokens"]["viejo"] = time.time() - 1
        for token in ("viejo", "inventado", ""):
            self.assertEqual(self.request("GET", f"/invitado?t={token}")[0], 403, token)
            self.assertEqual(self.request("GET", f"/invitado/estado?t={token}")[0], 403, token)


class WhatTheGuestSeesTests(_WithServer):
    def setUp(self):
        super().setUp()
        before = (bridge.STATE.get("phone_offer"), dict(bridge.STATE.get("guests_since", {})),
                 list(bridge.STATE.get("prepared_files", [])))
        self.addCleanup(lambda: bridge.STATE.update(phone_offer=before[0], guests_since=before[1], prepared_files=before[2]))
        bridge.STATE["phone_offer"] = None
        bridge.STATE["prepared_files"] = []
        self.token = "invitado1"
        bridge.STATE["guest_tokens"] = {self.token: time.time() + 600}
        bridge.STATE["guests_since"] = {self.token: time.time() - 1}
        before_device = dict(bridge.STATE.get("apple_devices", {}))
        self.addCleanup(lambda: bridge.STATE.update(apple_devices=before_device))
        bridge.STATE["apple_devices"] = {"192.168.1.68": {"ip": "192.168.1.68", "guest": self.token, "name": "Invitado 1"}}
        self.theirs = bridge.guest_destination("192.168.1.68")

    def test_guest_sees_neither_the_pc_clipboard_nor_an_offer(self):
        with patch.object(bridge.so, "read_text", return_value="mi contraseña del banco"),              patch.object(bridge.so, "read_files", return_value=[]),              patch.object(bridge.so, "read_image", return_value=False):
            state, body = self.request_json("GET", f"/invitado/estado?t={self.token}")
            self.assertEqual(state, 200)
            self.assertEqual(body["clipboard_text"], "")
            self.assertIsNone(body["offer"])
            _, downloaded = self.request("GET", f"/invitado/descargar?t={self.token}")
        self.assertNotIn("contraseña", downloaded.decode("utf-8"))

    def test_receives_only_what_offers_after_arrive(self):
        bridge.STATE["phone_offer"] = {"id": "a", "ts": time.time() - 60, "type": "text", "text": "de antes", "destination": self.theirs, "delivered": False}
        self.assertIsNone(self.request_json("GET", f"/invitado/estado?t={self.token}")[1]["offer"])
        bridge.STATE["phone_offer"] = {"id": "b", "ts": time.time(), "type": "text", "text": "para ti", "destination": self.theirs, "delivered": False}
        self.assertEqual(self.request_json("GET", f"/invitado/estado?t={self.token}")[1]["offer"]["id"], "b")
        self.assertEqual(self.request("GET", f"/invitado/descargar?t={self.token}")[1].decode("utf-8"), "para ti")
        self.assertTrue(bridge.STATE["phone_offer"]["delivered"])

    def test_exits_in_panel_with_time_and_goes_on_cut(self):
        bridge.STATE["apple_devices"] = {}
        with patch.object(bridge, "is_this_pc", return_value=False):
            self.request_json("GET", f"/invitado/estado?t={self.token}&tipo=ipad&disp=iPad%20Air")
        active = bridge.active_guests()
        self.assertEqual([i["name"] for i in active], ["Guest 1"])
        self.assertGreater(active[0]["expires"], time.time() + 1700)
        self.assertNotIn("127.0.0.1", self.cfg.get("apple_devices", {}), "a guest isn't saved in the config")
        self.assertEqual(self.request_json("POST", "/app/cut-guests")[0], 200)
        self.assertEqual(bridge.active_guests(), [])
        self.assertFalse(any(d.get("guest") for d in bridge.STATE["apple_devices"].values()))

    def test_each_guest_has_number_and_reuse_free(self):
        for token in ("a", "b", "c"):
            bridge.STATE["guest_tokens"][token] = time.time() + 600
        self.assertEqual([bridge.guest_number(k) for k in ("a", "b", "c", "a")], [1, 2, 3, 1])
        del bridge.STATE["guest_tokens"]["a"]
        bridge.clean_guest_tokens()
        bridge.STATE["guest_tokens"]["d"] = time.time() + 600
        self.assertEqual(bridge.guest_number("d"), 1)

    def test_what_goes_to_your_phones_or_another_guest_does_not_reach_this_guest(self):
        for destination in ("android", "iphone", "ipad", "", bridge.guest_destination("192.168.1.99")):
            with self.subTest(destination=destination):
                bridge.STATE["phone_offer"] = {"id": "c", "ts": time.time(), "type": "text", "text": "privado",
                                                 "destination": destination, "delivered": False}
                self.assertIsNone(self.request_json("GET", f"/invitado/estado?t={self.token}")[1]["offer"])
                self.assertNotIn("privado", self.request("GET", f"/invitado/descargar?t={self.token}")[1].decode("utf-8"))


class CutGuestsTests(_WithServer):
    def test_cutting_guests_keeps_the_owner_phone_connected(self):
        _, first = self.request_json("GET", "/app/guest-token")
        _, other = self.request_json("GET", "/app/guest-token")
        own = bridge._get_or_create_mobile_token(port=self.port)
        self.assertEqual(self.request("GET", f"/invitado?t={first['token']}")[0], 200)

        state, data = self.request_json("POST", "/app/cut-guests")
        self.assertEqual(state, 200)
        self.assertEqual(data, {"ok": True, "cut": 2})
        for token in (first["token"], other["token"]):
            self.assertEqual(self.request("GET", f"/invitado?t={token}")[0], 403)
            self.assertEqual(self.request("GET", f"/invitado/estado?t={token}")[0], 403)
            self.assertEqual(self.request("POST", f"/invitado/texto?t={token}", json.dumps({"text": "hola"}))[0], 403)
        self.assertEqual(self.request("GET", f"/invitado?t={own['token']}")[0], 200)
        # A new invitation works again.
        _, new_item = self.request_json("GET", "/app/guest-token")
        self.assertEqual(self.request("GET", f"/invitado?t={new_item['token']}")[0], 200)

    def test_cutting_guests_stops_an_upload_in_progress(self):
        _, guest = self.request_json("GET", "/app/guest-token")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(bridge, "get_downloads_folder", return_value=Path(tmp)), \
                patch.object(bridge.so, "set_files"), patch.object(bridge, "notify_received"):
            length = 4 * 262144
            con = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            try:
                con.sendall((f"POST /invitado/subir?t={guest['token']} HTTP/1.1\r\n"
                             f"Host: 127.0.0.1:{self.port}\r\nContent-Length: {length}\r\n"
                             "X-Nombre-Archivo: grande.bin\r\n\r\n").encode() + b"x" * 262144)
                deadline = time.time() + 5
                while not list(Path(tmp).iterdir()) and time.time() < deadline:
                    time.sleep(0.05)
                time.sleep(0.2)
                # Called directly: a panel request would wait for the upload that is still open.
                self.assertEqual(bridge.cut_guests(), 1)
                try:
                    con.sendall(b"x" * 262144)
                except OSError:
                    pass
                response = b""
                while b"\r\n\r\n" not in response:
                    chunk = con.recv(4096)
                    if not chunk:
                        break
                    response += chunk
            finally:
                con.close()
            self.server.wait()
            self.assertTrue(response.startswith(b"HTTP/1.0 403") or response.startswith(b"HTTP/1.1 403"), response[:60])
            self.assertEqual(list(Path(tmp).iterdir()), [], "lo que llegó a medias se borra")


class _FakeLinks:
    def __init__(self):
        self.cfg = {"key": KEY}


class ChangeCommonKeyTests(_WithServer):
    def test_without_confirmation_nothing_changes(self):
        state, _ = self.request_json("POST", "/app/change-common-key", {})
        self.assertEqual(state, 400)
        self.assertEqual(self.cfg["key"], KEY)
        self.assertEqual(bridge.Handler.key, KEY)

    def test_new_key_works_right_away_and_the_old_one_stops(self):
        links = _FakeLinks()
        with patch.object(bridge.Handler, "links", links):
            self.assertEqual(self.request("GET", "/p2p/estado", request_headers={"X-Key": KEY})[0], 200)
            state, data = self.request_json("POST", "/app/change-common-key", {"confirm": True})
        self.assertEqual(state, 200)
        new_value = data["key"]
        self.assertNotEqual(new_value, KEY)
        self.assertGreaterEqual(len(new_value), 12)
        self.assertEqual(self.cfg["key"], new_value, "se guarda en config.json")
        self.assertEqual(bridge.Handler.key, new_value)
        self.assertEqual(links.cfg["key"], new_value, "el QR de Android usa la nueva")
        self.assertEqual(self.request("GET", "/p2p/estado", request_headers={"X-Key": KEY})[0], 403)
        self.assertEqual(self.request("GET", "/p2p/estado", request_headers={"X-Key": new_value})[0], 200)
        # The panel shows the new one.
        _, profile = self.request_json("GET", "/app/profile")
        self.assertEqual(profile["key"], new_value)

    def test_devices_with_own_key_follow_working(self):
        import devices
        _, own = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.request_json("POST", "/app/change-common-key", {"confirm": True})
        self.assertTrue(devices.find_by_key(self.cfg, own))
        # Without clipboard watching /sincronizar answers 501, but only after accepting the key.
        with patch.object(bridge.Handler, "changes", None):
            self.assertEqual(self.request("GET", "/sincronizar", request_headers={"X-Key": own})[0], 501)
            self.assertEqual(self.request("GET", "/sincronizar", request_headers={"X-Key": KEY})[0], 401)


class SyncWaitTests(_WithServer):
    def test_wait_short_if_key_stops_work(self):
        changes = ClipboardChanges(lambda: 5)
        startup = time.monotonic()
        cursor, change = changes.wait(changes.current(), timeout=10, still_allowed=lambda: False, check_every=0.1)
        self.assertIsNone(change)
        self.assertLess(time.monotonic() - startup, 2)
        # Even a change that is already there isn't handed over without permission.
        self.assertIsNone(changes.wait("", still_allowed=lambda: False)[1])
        self.assertTrue(changes.wait("", still_allowed=lambda: True)[1])

    def _wait_cut(self, cut, client_key):
        sequence = [5]
        with patch.object(bridge.Handler, "changes", ClipboardChanges(lambda: sequence[0])), \
                patch.object(bridge.Handler, "links", _FakeLinks()):
            cursor = bridge.Handler.changes.current()
            result = {}

            def wait():
                con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
                con.request("GET", f"/sincronizar?desde={cursor}",
                            headers={"Host": f"127.0.0.1:{self.port}", "X-Key": client_key})
                response = con.getresponse()
                result["respuesta"] = (response.status, response.read())
                con.close()

            thread = threading.Thread(target=wait)
            startup = time.monotonic()
            thread.start()
            time.sleep(0.5)
            self.assertNotIn("respuesta", result, "sigue esperando un cambio")
            cut()
            sequence[0] = 6  # a change arrives right after: it must not reach the cut device
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - startup, 8, "no espera los 20 segundos completos")
            self.server.wait()
            return result["respuesta"]

    def test_change_open_wait_short_common_key(self):
        state, body = self._wait_cut(bridge.rotate_common_key, KEY)
        self.assertEqual(state, 401)
        self.assertNotIn(b"hola", body)

    def test_remove_open_wait_short_device(self):
        import devices
        id_d, own = devices.register(self.cfg, "Mi Android", "android")

        def remove():
            self.cfg["devices"].pop(id_d)

        state, _ = self._wait_cut(remove, own)
        self.assertEqual(state, 401)


if __name__ == "__main__":
    unittest.main()
