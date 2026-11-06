"""Each phone has its own key: the PC recognizes it by that, not by IP or name."""
import base64
import hashlib
import http.client
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import devices
import i18n
import bridge
sys.path.insert(0, str(Path(__file__).resolve().parent))
from server_fixture import TestServer
from pairing import PairingManager

UA_IPHONE = "Shortcuts/1254 CFNetwork/1498.700.2 Darwin/23.6.0"
UA_ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 Chrome/120.0 Mobile"


def setUpModule():
    # The example IPs (192.168.1.20, 10.0.0.9...) could be this machine's own, and requests from the PC
    # itself don't create cards: without this, the tests would depend on the network they run on.
    global _without_own_ips
    _without_own_ips = patch("bridge.network.interfaces", return_value=[])
    _without_own_ips.start()


def tearDownModule():
    _without_own_ips.stop()


class DevicesModuleTests(unittest.TestCase):
    def test_register_saves_only_hash_and_key_recognizes(self):
        cfg = {}
        device_id, key = devices.register(cfg, "Mi iPhone", "iphone")
        saved = cfg["devices"][device_id]
        self.assertNotIn(key, json.dumps(cfg))
        self.assertEqual(saved["key_hash"], hashlib.sha256(key.encode()).hexdigest())
        self.assertEqual(saved["type"], "iphone")
        self.assertFalse(saved["name_locked"])
        self.assertEqual(devices.find_by_key(cfg, key), device_id)
        self.assertIsNone(devices.find_by_key(cfg, key + "x"))
        self.assertIsNone(devices.find_by_key(cfg, ""))

    def test_remove_key_invalid(self):
        cfg = {}
        device_id, key = devices.register(cfg, "Tablet", "ipad")
        self.assertTrue(devices.remove(cfg, device_id))
        self.assertIsNone(devices.find_by_key(cfg, key))
        self.assertFalse(devices.remove(cfg, device_id))

    def test_type_from_agent(self):
        self.assertEqual(devices.type_from_agent(UA_ANDROID), "android")
        self.assertEqual(devices.type_from_agent("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"), "ipad")
        self.assertEqual(devices.type_from_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X) Mobile/15E148"), "ipad")
        self.assertEqual(devices.type_from_agent(UA_IPHONE, "Ana iPhone"), "iphone")
        self.assertEqual(devices.type_from_agent(UA_IPHONE), "mobile")

    def test_name_cleans(self):
        self.assertEqual(devices.clean_name("  Mi\x00 iPad\n "), "Mi iPad")
        self.assertEqual(devices.clean_name("", "Algo"), "Algo")
        self.assertEqual(len(devices.clean_name("x" * 100)), 60)

    def test_pc_same_name_and_type_is_suspects_from_repeated(self):
        cfg = {"devices": {
            "viejo": {"type": "iphone", "device_name": "Ana iPhone", "linked_at": "2026-10-01 10:00"},
            "ipad": {"type": "ipad", "device_name": "Ana iPhone", "linked_at": "2026-10-02 10:00"},
            "otro": {"type": "iphone", "device_name": "Luis iPhone", "linked_at": "2026-10-03 10:00"},
            "nuevo": {"type": "iphone", "device_name": "ana iphone", "linked_at": "2026-10-04 10:00"},
        }}
        self.assertEqual(devices.find_repeated(cfg, "nuevo"), "viejo")
        self.assertIsNone(devices.find_repeated(cfg, "viejo"), "el más viejo no se marca contra el nuevo")
        self.assertIsNone(devices.find_repeated(cfg, "otro"))
        cfg["devices"]["nuevo"]["distinct_from"] = ["viejo"]
        self.assertIsNone(devices.find_repeated(cfg, "nuevo"), "once told they're different, it doesn't ask again")

    def test_replace_removes_old_and_inherits_name(self):
        cfg = {}
        old_id, old_key = devices.register(cfg, "Ana phone", "iphone", pinned_name=True)
        new_id, generated_key = devices.register(cfg, "Mi iPhone", "iphone")
        cfg["devices"][new_id]["repeats"] = old_id
        self.assertEqual(devices.resolve_repeated(cfg, new_id, True), old_id)
        self.assertIsNone(devices.find_by_key(cfg, old_key))
        self.assertEqual(devices.find_by_key(cfg, generated_key), new_id)
        self.assertEqual(cfg["devices"][new_id]["name"], "Ana phone")
        self.assertNotIn("repeats", cfg["devices"][new_id])

    def test_are_different_leaves_two(self):
        cfg = {}
        old_id, _ = devices.register(cfg, "Mi iPhone", "iphone")
        new_id, _ = devices.register(cfg, "Mi iPhone", "iphone")
        cfg["devices"][new_id]["repeats"] = old_id
        self.assertIsNone(devices.resolve_repeated(cfg, new_id, False))
        self.assertEqual(set(cfg["devices"]), {old_id, new_id})
        self.assertEqual(cfg["devices"][new_id]["distinct_from"], [old_id])
        self.assertIsNone(devices.resolve_repeated(cfg, new_id, True), "sin sospecha no hace nada")


class AndroidLinkingTests(unittest.TestCase):
    cfg = {"id": "a" * 32, "key": "common-key", "port": 8765, "name": "my-pc"}

    def link(self, manager):
        private = X25519PrivateKey.generate()
        public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ticket = manager.request("Ana Galaxy", hashlib.sha256(public).hexdigest(), "192.168.1.7")
        manager.reveal(ticket["session"], base64.b64encode(public).decode())
        self.assertTrue(manager.wait_for_decision(ticket["session"], 3))
        response = manager.poll(ticket["session"])
        if response["status"] != "approved":
            return response, None
        server_public = X25519PublicKey.from_public_bytes(base64.b64decode(ticket["server_public"]))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"puente-pair-v1").derive(private.exchange(server_public))
        plaintext = AESGCM(key).decrypt(base64.b64decode(response["nonce"]), base64.b64decode(response["ciphertext"]), ticket["session"].encode())
        return response, json.loads(plaintext)

    def test_android_gets_its_own_key_and_not_the_common_one(self):
        issued = []

        def issue(name):
            issued.append(name)
            return "galaxy-key"

        manager = PairingManager(self.cfg, lambda *_: True, issue_key=issue)
        _, content = self.link(manager)
        self.assertEqual(content["key"], "galaxy-key")
        self.assertNotIn("common-key", json.dumps(content))
        self.assertEqual(issued, ["Ana Galaxy"])

    def test_rejection_issues_no_key(self):
        issued = []
        manager = PairingManager(self.cfg, lambda *_: False, issue_key=lambda n: issued.append(n) or "x")
        response, content = self.link(manager)
        self.assertEqual(response["status"], "rejected")
        self.assertIsNone(content)
        self.assertEqual(issued, [])

    def test_without_sender_follows_common_key(self):
        _, content = self.link(PairingManager(self.cfg, lambda *_: True))
        self.assertEqual(content["key"], "common-key")
        self.assertNotIn("ca", content)

    def test_android_receives_pc_valid_ca(self):
        import certificates
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            certs = certificates.Certificates(folder, "my-pc", ips=lambda: ["192.168.1.7"])
            # The manager is created before the certificates, as in servir(): the CA is read when accepting.
            manager = PairingManager(self.cfg, lambda *_: True, ca_pem=certs.ca_pem)
            certs.ensure()
            _, content = self.link(manager)
        self.assertEqual(content["ca"], certs.ca_pem())
        self.assertEqual(content["ca_sha256"], certs.ca_fingerprint())

    def test_without_android_certificates_follows_by_http(self):
        _, content = self.link(PairingManager(self.cfg, lambda *_: True, ca_pem=lambda: ""))
        self.assertNotIn("ca", content)
        self.assertTrue(content["server"].startswith("http://"))

    def test_rejection_does_not_ask_for_the_ca(self):
        ca = Mock(return_value="no-deberia-usarse")
        response, _ = self.link(PairingManager(self.cfg, lambda *_: False, ca_pem=ca))
        self.assertEqual(response["status"], "rejected")
        ca.assert_not_called()


class ServerWithDevicesTests(unittest.TestCase):
    """Real in-memory server, with the configuration in a dict instead of config.json."""

    @classmethod
    def setUpClass(cls):
        cls.server = TestServer(("127.0.0.1", 0), bridge.Handler)
        cls.port = cls.server.server_address[1]
        bridge.Handler.key = "common-key"
        bridge.Handler.identifier = "a" * 32
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.cfg = {"id": "a" * 32, "key": "common-key", "port": self.port, "name": "my-pc", "devices": {}}
        self.patches = [patch("bridge.load_config", side_effect=lambda: self.cfg),
                        patch("bridge.save_config", side_effect=lambda cfg: self.cfg.update(cfg)),
                        patch("configuration.load_config", side_effect=lambda: self.cfg),
                        patch("configuration.save_config", side_effect=lambda cfg: self.cfg.update(cfg)),
                        patch.object(bridge.so, 'read_files', return_value=[]),
                        patch.object(bridge.so, 'read_text', return_value='Test text'),
                        patch.object(bridge.so, 'read_image', return_value=False)]
        for p in self.patches:
            p.start()
        bridge.STATE["apple_devices"] = {}
        bridge.STATE["qr_tokens"] = {}
        bridge.STATE["ip"] = "127.0.0.1"

    def tearDown(self):
        self.server.wait()
        for p in self.patches:
            p.stop()

    def request(self, method, path, headers=None, body=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = {"Host": f"127.0.0.1:{self.port}", "User-Agent": UA_IPHONE}
        request_headers.update(headers or {})
        con.request(method, path, body=body, headers=request_headers)
        response = con.getresponse()
        data = response.read()
        con.close()
        self.assertTrue(self.server.wait(), f"el servidor no terminó de atender {path}")
        return response.status, data

    def test_qr_delivers_own_key_only_time(self):
        bridge.STATE["qr_tokens"]["abc"] = time.time() + 300
        state, html = self.request("GET", "/configurar?t=abc")
        self.assertEqual(state, 200)
        self.assertEqual(len(self.cfg["devices"]), 1)
        device_id, data = next(iter(self.cfg["devices"].items()))
        self.assertEqual(data["origin"], "qr")
        self.assertNotIn("common-key", html.decode("utf-8"))
        self.assertAlmostEqual(bridge.STATE["apple_devices"][device_id]["timestamp"], time.time(), delta=60,
                               msg="recién vinculado: la web abierta sin clave desde su IP no crea otra tarjeta")
        # The device key shown is the only one that opens the server.
        key = next(line.split('id="key">')[1].split("<")[0] for line in html.decode("utf-8").splitlines() if 'id="key"' in line)
        self.assertEqual(devices.find_by_key(self.cfg, key), device_id)
        # The «Save to Shortcuts» button hands the shortcut the PC's key and address.
        import web_ui
        from html import unescape
        from urllib.parse import unquote
        page = html.decode("utf-8")
        link = unescape(next(l for l in page.splitlines() if "shortcuts://run-shortcut" in l).split('href="')[1].split('"')[0])
        self.assertTrue(link.startswith("shortcuts://run-shortcut?name=Link%20PC&input=text&text="))
        config = json.loads(unquote(link.split("&text=")[1]))
        self.assertEqual(config, {"key": key, "clave": key, "server": f"http://my-pc.local:{self.port}", "fallback": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(web_ui.shortcuts_config("my-pc", "http://1.2.3.4:8765", "k"), '{"key":"k","clave":"k","server":"http://my-pc.local:8765","fallback":"http://1.2.3.4:8765"}')
        self.assertEqual(self.request("GET", "/configurar?t=abc")[0], 403, "el QR es de un solo uso")
        # Scanning another QR from the same IP without having used the key replaces the earlier registration.
        bridge.STATE["qr_tokens"]["otro"] = time.time() + 300
        self.assertEqual(self.request("GET", "/configurar?t=otro")[0], 200)
        self.assertEqual(len(self.cfg["devices"]), 1)
        self.assertNotIn(device_id, self.cfg["devices"])
        self.assertNotIn(device_id, bridge.STATE["apple_devices"])
        # But if it was used, it's kept and the new QR adds another device.
        used_id = next(iter(self.cfg["devices"]))
        self.cfg["devices"][used_id]["used"] = True
        bridge.STATE["qr_tokens"]["tercero"] = time.time() + 300
        self.assertEqual(self.request("GET", "/configurar?t=tercero")[0], 200)
        self.assertEqual(len(self.cfg["devices"]), 2)
        bridge.STATE["qr_tokens"]["viejo"] = time.time() - 1
        self.assertEqual(self.request("GET", "/configurar?t=viejo")[0], 403)
        self.assertEqual(self.request("GET", "/configurar")[0], 403)

    def test_scanning_the_qr_is_not_enough_to_appear(self):
        bridge.STATE["qr_tokens"]["solo"] = time.time() + 300
        state, html = self.request("GET", "/configurar?t=solo")
        self.assertEqual(state, 200)
        device_id = next(iter(self.cfg["devices"]))
        visible_items = lambda: [d.get("id") for d in bridge._dedupe_apple_devices(list(bridge.STATE["apple_devices"].values()))]
        self.assertNotIn(device_id, visible_items(), "solo se escaneó: todavía no está vinculado")
        key = html.decode("utf-8").split('id="key">')[1].split("<")[0]
        # Link PC's /ping, from the phone's IP (the test server only sees 127.0.0.1, which isn't a device).
        handler = bridge.Handler.__new__(bridge.Handler)
        handler.headers = {"X-Key": key, "User-Agent": UA_IPHONE}
        handler.client_address = ("192.168.1.20", 50000)
        handler._identify_client(query_params={"disp": ["Ana’s iPhone"]})
        self.assertIn(device_id, visible_items(), "Link PC usó la clave: ya está vinculado")

    def test_unused_links_delete_to_time(self):
        old, _ = devices.register(self.cfg, "", "iphone")
        used, _ = devices.register(self.cfg, "", "iphone")
        from_before, _ = devices.register(self.cfg, "", "iphone")
        recent, _ = devices.register(self.cfg, "", "iphone")
        for id_d in (old, used, from_before):
            self.cfg["devices"][id_d]["linked_at"] = "2026-01-01 10:00"
        self.cfg["devices"][used]["used"] = True
        del self.cfg["devices"][from_before]["used"]  # linked before the field existed
        self.assertEqual(devices.remove_unused(self.cfg["devices"]), [old])
        self.assertEqual(set(self.cfg["devices"]), {used, from_before, recent})

    def test_qr_page_exits_in_phone_language(self):
        for language, expected in (("en-US,en;q=0.9", "Save to Shortcuts"), ("es-HN,es;q=0.9", "Guardar en Atajos")):
            with self.subTest(language=language):
                bridge.STATE["qr_tokens"][language] = time.time() + 300
                state, html = self.request("GET", f"/configurar?t={language}", {"Accept-Language": language})
                self.assertEqual(state, 200)
                self.assertIn(expected, html.decode("utf-8"))
        self.assertIsNone(i18n.from_browser("fr-FR,fr;q=0.9"))

    def test_ipad_that_safari_reports_as_mac_stays_an_ipad(self):
        bridge.STATE["qr_tokens"]["ipad"] = time.time() + 300
        ua_ipad = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
        state, html = self.request("GET", "/configurar?t=ipad", {"User-Agent": ua_ipad})
        self.assertEqual(state, 200)
        self.assertIn("/device/type", html.decode("utf-8"))
        device_id, data = next(iter(self.cfg["devices"].items()))
        self.assertEqual(data["type"], "mobile")
        key = html.decode("utf-8").split('id="key">')[1].split("<")[0]
        self.assertEqual(self.request("POST", "/device/type", {"X-Key": key}, b"ipad")[0], 200)
        self.assertEqual(self.cfg["devices"][device_id]["type"], "ipad")
        self.assertEqual(self.cfg["devices"][device_id]["name"], "Mi iPad")
        self.assertEqual(bridge.STATE["apple_devices"][device_id]["type"], "ipad")
        # Afterwards, the name the shortcut sends names it just like before.
        self.request("GET", "/traer", {"X-Key": key, "X-Device": "Ana iPad"})
        self.assertEqual(self.cfg["devices"][device_id]["name"], "Ana iPad")
        # Without a valid key, or with a type that doesn't exist, nothing changes.
        self.assertEqual(self.request("POST", "/dispositivo/tipo", {"X-Key": "other"}, b"ipad")[0], 401)
        self.assertEqual(self.request("POST", "/dispositivo/tipo", {"X-Key": key}, b"tostadora")[0], 400)

    def test_own_key_open_and_deleted_no(self):
        device_id, key = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.assertIn(self.request("GET", "/traer", {"X-Key": key})[0], (200, 204))
        self.assertEqual(self.request("GET", "/traer", {"X-Key": "other"})[0], 401)
        state, _ = self.request("POST", "/app/remove-device", {"Content-Type": "application/json"}, json.dumps({"id": device_id}))
        self.assertEqual(state, 200)
        self.assertEqual(self.cfg["devices"], {})
        self.assertEqual(self.request("GET", "/traer", {"X-Key": key})[0], 401)
        self.assertNotIn(device_id, bridge.STATE["apple_devices"])

    def test_remove_linked_carries_shadow_without_link(self):
        device_id, _ = devices.register(self.cfg, "Mi iPhone", "iphone")
        bridge.STATE["apple_devices"] = {
            device_id: {"id": device_id, "ip": "192.168.1.20", "name": "Mi iPhone", "type": "iphone", "linked": True},
            "192.168.1.20": {"ip": "192.168.1.20", "name": "Dispositivo Móvil", "type": "mobile", "linked": False},
        }
        self.cfg["apple_devices"] = {"192.168.1.20": {"name": "Dispositivo Móvil", "type": "mobile"}}
        state, _ = self.request("POST", "/app/remove-device", {"Content-Type": "application/json"}, json.dumps({"id": device_id}))
        self.assertEqual(state, 200)
        self.assertEqual(bridge.STATE["apple_devices"], {}, "one X removes both")
        self.assertEqual(self.cfg.get("apple_devices"), {})

    def test_phone_key_does_not_open_pc_to_pc_paths(self):
        _, key = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.assertIn(self.request("GET", "/traer", {"X-Key": key})[0], (200, 204))
        for path in ("/p2p/perfil", "/p2p/estado", "/p2p/equipos"):
            self.assertEqual(self.request("GET", path, {"X-Key": key})[0], 403, path)
        self.assertEqual(self.request("GET", "/p2p/perfil", {"X-Key": "common-key"})[0], 200)

    def test_removed_device_does_not_come_back_from_old_config(self):
        device_id, _ = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.cfg["apple_devices"] = {"192.168.1.20": {"name": "Mi iPhone", "type": "iphone"}}  # old entry guessed by IP
        bridge.STATE["apple_devices"] = {device_id: {"id": device_id, "ip": "192.168.1.20", "name": "Mi iPhone", "type": "iphone", "linked": True}}
        self.request("POST", "/app/remove-device", {"Content-Type": "application/json"}, json.dumps({"id": device_id}))
        state, body = self.request("GET", "/app/state")
        self.assertEqual(state, 200)
        self.assertEqual(json.loads(body)["apple_devices"], [], "con una X basta")

    def test_re_link_same_iphone_asks_and_replace_kills_old_key(self):
        old_id, old_key = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.request("GET", "/traer", {"X-Key": old_key, "X-Device": "Ana iPhone 17"})
        self.request("POST", "/app/change-device-type", {"Content-Type": "application/json"},
                   json.dumps({"id": old_id, "name": "Ana phone"}))
        new_id, generated_key = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.request("GET", "/traer", {"X-Key": generated_key, "X-Device": "Ana iPhone 17"})
        self.assertEqual(self.cfg["devices"][new_id]["repeats"], old_id)

        state, body = self.request("POST", "/app/resolve-repeated", {"Content-Type": "application/json"},
                                    json.dumps({"id": new_id, "replace": True}))
        self.assertEqual(state, 200)
        self.assertTrue(json.loads(body)["removed"])
        self.assertEqual(list(self.cfg["devices"]), [new_id])
        self.assertEqual(self.cfg["devices"][new_id]["name"], "Ana phone")
        self.assertEqual(self.request("GET", "/traer", {"X-Key": old_key})[0], 401)
        self.assertNotIn(old_id, bridge.STATE["apple_devices"])
        self.assertEqual(self.request("POST", "/app/resolve-repeated", {"Content-Type": "application/json"},
                                    json.dumps({"id": new_id, "replace": True}))[0], 404, "nothing left to resolve")

    def test_two_iphones_with_same_name_can_stay_two(self):
        id_a, key_a = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.request("GET", "/traer", {"X-Key": key_a, "X-Device": "iPhone"})
        id_b, key_b = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.request("GET", "/traer", {"X-Key": key_b, "X-Device": "iPhone"})
        self.assertEqual(self.request("POST", "/app/resolve-repeated", {"Content-Type": "application/json"},
                                    json.dumps({"id": id_b, "replace": False}))[0], 200)
        self.assertEqual(set(self.cfg["devices"]), {id_a, id_b})
        self.assertIn(self.request("GET", "/traer", {"X-Key": key_a})[0], (200, 204))
        self.request("GET", "/traer", {"X-Key": key_b, "X-Device": "iPhone again"})
        self.request("GET", "/traer", {"X-Key": key_b, "X-Device": "iPhone"})
        self.assertNotIn("repeats", self.cfg["devices"][id_b], "no se vuelve a preguntar")

    def test_linked_android_again_also_asks(self):
        with patch("bridge.modify_devices", side_effect=lambda change: change(self.cfg["devices"])):
            bridge.issue_android_key("Pixel 8")
            bridge.issue_android_key("Pixel 8")
        old, new_item = sorted(self.cfg["devices"].items(), key=lambda pair: "repeats" in pair[1])
        self.assertEqual(new_item[1]["repeats"], old[0])

    def test_just_linked_tablet_shows_up_with_its_offer_poll(self):
        with patch("bridge.modify_devices", side_effect=lambda change: change(self.cfg["devices"])):
            key = bridge.issue_android_key("ELN-L03")
            device_id = next(iter(self.cfg["devices"]))
            request = SimpleNamespace(client_address=("192.168.1.6", 5000), headers={"X-Key": key})
            mapping = {}
            with patch("bridge.load_config", return_value=self.cfg), patch.dict(bridge.STATE, {"apple_devices": mapping}):
                bridge.Handler._note_android_on_network(request)
                self.assertEqual(mapping[device_id]["name"], "ELN-L03")
                self.assertEqual(mapping[device_id]["ip"], "192.168.1.6")
                self.assertTrue(mapping[device_id]["online"])
                mapping[device_id]["last_event"] = "Envió texto a las 10:00"
                bridge.Handler._note_android_on_network(request)
                self.assertEqual(mapping[device_id]["last_event"], "Envió texto a las 10:00", "polling isn't an action")
        self.assertEqual(self.cfg["devices"][device_id]["last_ip"], "192.168.1.6")

    def test_rename_from_name_pinned_panel(self):
        device_id, _ = devices.register(self.cfg, "Mi iPhone", "iphone")
        state, _ = self.request("POST", "/app/change-device-type", {"Content-Type": "application/json"},
                               json.dumps({"id": device_id, "type": "ipad", "name": "Lounge iPad"}))
        self.assertEqual(state, 200)
        self.assertEqual(self.cfg["devices"][device_id]["name"], "Lounge iPad")
        self.assertEqual(self.cfg["devices"][device_id]["type"], "ipad")
        self.assertTrue(self.cfg["devices"][device_id]["name_locked"])


class LastSavedUseTests(unittest.TestCase):
    """Each device's last action comes from the config at startup (/ping no longer goes to the log)."""

    def setUp(self):
        self.previous_state = dict(bridge.STATE["apple_devices"])
        bridge.STATE["apple_devices"] = {}

    def tearDown(self):
        bridge.STATE["apple_devices"] = self.previous_state

    def test_ping_saves_each_10_minutes_and_actions_always(self):
        from datetime import datetime, timedelta
        does = lambda m: {"action": "Connected", "time": (datetime.now() - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M")}
        self.assertTrue(bridge._must_save_use(None, "Connected"))
        self.assertFalse(bridge._must_save_use(does(2), "Connected"))
        self.assertTrue(bridge._must_save_use(does(2), "Got something"))
        self.assertTrue(bridge._must_save_use(does(11), "Connected"))

    def test_on_start_shows_today_activity_and_not_yesterday(self):
        from datetime import datetime, timedelta
        today = datetime.now().replace(second=0, microsecond=0)
        yesterday = today - timedelta(days=1)
        cfg = {"devices": {
            "d1": {"name": "Test iPhone", "type": "iphone", "linked_at": "2026-01-01 10:00",
                   "last_use": {"action": "Got something", "time": today.strftime("%Y-%m-%d %H:%M")}},
            "d2": {"name": "Test iPad", "type": "ipad", "linked_at": "2026-01-01 10:00",
                   "last_use": {"action": "Got something", "time": yesterday.strftime("%Y-%m-%d %H:%M")}},
        }}
        import tempfile
        with patch.object(bridge, "LOG", Path(tempfile.gettempdir()) / "no-existe-copyover.log"):
            bridge.recover_last_phone_from_log(cfg)
        self.assertEqual(bridge.STATE["apple_devices"]["d1"]["last_event"], f"Got something at {today:%H:%M}")
        self.assertEqual(bridge.STATE["apple_devices"]["d2"]["last_event"], "Linked on 01/01 at 10:00")


    def test_on_start_the_log_does_not_invent_cards(self):
        import tempfile
        cfg = {"devices": {"d1": {"name": "Ana iPhone", "type": "iphone", "last_ip": "10.0.0.9"}},
               "apple_devices": {"192.168.1.5": {"name": "Dispositivo Móvil", "type": "mobile"}},
               "phone_name": "Ana iPhone 17"}
        log = Path(tempfile.mkdtemp()) / "puente.log"
        log.write_text('2026-10-30 02:27:01,000 192.168.1.251 "POST /enviar HTTP/1.1" 200 -\n'
                       '2026-10-30 09:00:01,000 10.0.0.9 "GET /traer HTTP/1.1" 200 -\n', encoding="utf-8")
        bridge.STATE["apple_devices"] = {}
        with patch.object(bridge, "LOG", log), patch.object(bridge, "is_this_pc", side_effect=lambda ip: ip == "192.168.1.5"):
            bridge.recover_last_phone_from_log(cfg)
        self.assertEqual(list(bridge.STATE["apple_devices"]), ["d1"])
        self.assertEqual(bridge.STATE["apple_devices"]["d1"]["last_seen"], "09:00")


class IdentifyClientTests(unittest.TestCase):
    class Handler:
        _identify_client = bridge.Handler._identify_client
        _identify_linked = bridge.Handler._identify_linked

        def __init__(self, headers, ip="192.168.1.20"):
            self.headers = headers
            self.client_address = (ip, 50000)

    def setUp(self):
        self.cfg = {"id": "a" * 32, "key": "common-key", "port": 8765, "name": "my-pc", "devices": {}}
        self.patches = [patch("bridge.load_config", side_effect=lambda: self.cfg),
                        patch("bridge.save_config", side_effect=lambda cfg: self.cfg.update(cfg)),
                        patch("configuration.load_config", side_effect=lambda: self.cfg),
                        patch("configuration.save_config", side_effect=lambda cfg: self.cfg.update(cfg))]
        for p in self.patches:
            p.start()
        bridge.STATE["apple_devices"] = {}

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_recognizes_by_key_even_if_change_ip_and_name(self):
        device_id, key = devices.register(self.cfg, "Ana iPhone", "iphone", pinned_name=True)
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE, "X-Device": "Other name"}, "192.168.1.20")._identify_client("Sent")
        result = self.Handler({"X-Key": key, "User-Agent": UA_ANDROID}, "10.0.0.9")._identify_client("Sent")
        self.assertEqual(result, "Ana iPhone (10.0.0.9)")
        live = bridge.STATE["apple_devices"]
        self.assertEqual(list(live), [device_id])
        self.assertEqual(live[device_id]["ip"], "10.0.0.9")
        self.assertEqual(live[device_id]["type"], "iphone")
        self.assertTrue(live[device_id]["linked"])
        self.assertEqual(self.cfg["devices"][device_id]["last_ip"], "10.0.0.9")

    def test_x_first_device_names_to_linked_by_qr(self):
        device_id, key = devices.register(self.cfg, "Mi iPhone", "mobile")
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE, "X-Device": "Ana iPhone 17"})._identify_client()
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE, "X-Device": "Nombre falso"})._identify_client()
        data = self.cfg["devices"][device_id]
        self.assertEqual(data["name"], "Ana iPhone 17")
        self.assertTrue(data["name_locked"])
        self.assertEqual(data["type"], "iphone", "el tipo se deduce del nombre cuando el UA de Atajos no lo dice")

    def test_pc_link_names_with_url_name_on_do_ping(self):
        # iOS cuts header values at the first non-ASCII character, so Link PC sends the name in ?device=.
        device_id, key = devices.register(self.cfg, "Mi iPhone", "iphone")
        handler = bridge.Handler.__new__(bridge.Handler)
        handler.path = "/ping?device=Ana%E2%80%99s%20iPhone"
        handler.headers = {"X-Key": key, "User-Agent": UA_IPHONE}
        handler.client_address = ("192.168.1.20", 50000)
        with patch.object(bridge.Handler, "respond") as respond:
            handler.do_GET()
        self.assertEqual(respond.call_args.args[:2], (200, "Puente activo"))
        self.assertEqual(self.cfg["devices"][device_id]["name"], "Ana’s iPhone")
        self.assertEqual(self.cfg["devices"][device_id]["device_name"], "Ana’s iPhone")

    def test_after_request_exits_online_without_wait_review(self):
        device_id, key = devices.register(self.cfg, "Ana iPhone", "iphone", pinned_name=True)
        bridge.STATE["apple_devices"][device_id] = {"id": device_id, "online": False}
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE}, "192.168.1.20")._identify_client("Got something")
        self.assertTrue(bridge.STATE["apple_devices"][device_id]["online"])

    def test_two_linked_iphones_do_not_overwrite_each_other(self):
        id_a, key_a = devices.register(self.cfg, "Mi iPhone", "iphone", pinned_name=True)
        id_b, key_b = devices.register(self.cfg, "Mi iPhone", "iphone", pinned_name=True)
        self.Handler({"X-Key": key_a, "User-Agent": UA_IPHONE}, "192.168.1.20")._identify_client()
        self.Handler({"X-Key": key_b, "User-Agent": UA_IPHONE}, "192.168.1.21")._identify_client()
        self.assertEqual(set(bridge.STATE["apple_devices"]), {id_a, id_b})
        items = bridge._dedupe_apple_devices(list(bridge.STATE["apple_devices"].values()), "192.168.1.2")
        self.assertEqual(len(items), 2)

    def test_linked_card_again_asks_by_previous(self):
        old_id, old_key = devices.register(self.cfg, "Ana phone", "iphone", pinned_name=True)
        self.Handler({"X-Key": old_key, "User-Agent": UA_IPHONE, "X-Device": "Ana iPhone 17"})._identify_client()
        new_id, generated_key = devices.register(self.cfg, "Mi iPhone", "mobile")
        self.Handler({"X-Key": generated_key, "User-Agent": UA_IPHONE, "X-Device": "Ana iPhone 17"},
                     "192.168.1.21")._identify_client()
        card = bridge.STATE["apple_devices"][new_id]
        self.assertEqual(card["repeats"]["id"], old_id)
        self.assertEqual(card["repeats"]["name"], "Ana phone")
        self.assertTrue(card["repeats"]["linked"].startswith("Vinculado el "))
        self.assertIsNone(bridge.STATE["apple_devices"][old_id]["repeats"])

    def test_same_iphone_with_the_common_key_does_not_create_another_card(self):
        device_id, key = devices.register(self.cfg, "Ana iPhone", "iphone", pinned_name=True)
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE, "X-Device": "Ana iPhone 17"},
                     "10.0.0.9")._identify_client("Sent")
        self.assertEqual(self.cfg["devices"][device_id]["device_name"], "Ana iPhone 17")
        result = self.Handler({"X-Key": "common-key", "User-Agent": UA_IPHONE, "X-Device": "Ana iPhone 17"},
                                 "192.168.1.30")._identify_client("Sent")
        self.assertEqual(result, "Ana iPhone (192.168.1.30)")
        self.assertEqual(list(bridge.STATE["apple_devices"]), [device_id])
        self.assertNotIn("192.168.1.30", self.cfg.get("apple_devices", {}))

    def test_name_alone_is_not_enough_without_the_common_key(self):
        device_id, key = devices.register(self.cfg, "Ana iPhone", "iphone", pinned_name=True)
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE, "X-Device": "Ana iPhone 17"})._identify_client()
        self.Handler({"User-Agent": UA_IPHONE, "X-Device": "Ana iPhone 17"}, "192.168.1.30")._identify_client()
        self.assertIn("192.168.1.30", bridge.STATE["apple_devices"])
        self.assertEqual(self.cfg["devices"][device_id]["last_ip"], "192.168.1.20")

    def test_request_from_this_pc_creates_no_card(self):
        with patch.object(bridge, "is_this_pc", side_effect=lambda ip: ip == "192.168.1.5"):
            self.Handler({"X-Key": "common-key", "User-Agent": "curl/8"}, "192.168.1.5")._identify_client("Connected")
        self.assertEqual(bridge.STATE["apple_devices"], {})
        self.assertNotIn("192.168.1.5", self.cfg.get("apple_devices", {}))

    def test_without_own_key_remains_as_without_link_and_replaces_on_link(self):
        self.Handler({"X-Key": "common-key", "User-Agent": UA_IPHONE, "X-Device": "Mi iPhone"})._identify_client()
        guessed = bridge.STATE["apple_devices"]["192.168.1.20"]
        self.assertFalse(guessed["linked"])
        self.assertIn("192.168.1.20", self.cfg["apple_devices"], "el adivinado también se guarda en la config")
        device_id, key = devices.register(self.cfg, "Mi iPhone", "iphone", pinned_name=True)
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE})._identify_client()
        self.assertEqual(list(bridge.STATE["apple_devices"]), [device_id])
        self.assertNotIn("192.168.1.20", self.cfg["apple_devices"], "y al vincularse desaparece de la config")

    def test_request_in_progress_does_not_revive_a_removed_device(self):
        import copy
        device_id, _ = devices.register(self.cfg, "Mi iPhone", "mobile")
        snapshot = copy.deepcopy(self.cfg)  # what an in-flight request had already read
        devices.remove(self.cfg, device_id)  # the panel removes it meanwhile
        self.Handler({}, "192.168.1.20")._identify_linked(device_id, snapshot, "Sent", "192.168.1.20", UA_IPHONE, "Ana iPhone", "")
        self.assertEqual(self.cfg["devices"], {})

    def test_online_only_test_in_same_network_and_watches_ttl(self):
        bridge.STATE["ip"] = "192.168.1.2"
        bridge.STATE["apple_devices"] = {
            "a": {"id": "a", "ip": "192.168.1.20"},
            "b": {"id": "b", "ip": "10.0.0.9"},
        }
        with patch("bridge._responds_ping", return_value=True) as ping, patch("network.interfaces", return_value=[]):
            bridge._check_online()
        self.assertEqual(ping.call_args_list[0].args, ("192.168.1.20",))
        self.assertEqual(ping.call_count, 1, "al de otra red no se le hace ping")
        self.assertTrue(bridge.STATE["apple_devices"]["a"]["online"])
        self.assertFalse(bridge.STATE["apple_devices"]["b"]["online"])
        inaccessible = Mock(returncode=0, stdout="Respuesta desde 192.168.1.2: Host de destino inaccesible.")
        with patch("bridge.subprocess.run", return_value=inaccessible):
            self.assertFalse(bridge._responds_ping("192.168.1.20"))
        answers = Mock(returncode=0, stdout="Respuesta desde 192.168.1.20: bytes=32 tiempo=4ms TTL=64")
        with patch("bridge.subprocess.run", return_value=answers):
            self.assertTrue(bridge._responds_ping("192.168.1.20"))

    def test_keyless_request_from_a_linked_ip_does_not_create_another_card(self):
        device_id, key = devices.register(self.cfg, "Ana iPhone", "iphone", pinned_name=True)
        self.Handler({"X-Key": key, "User-Agent": UA_IPHONE}, "192.168.1.20")._identify_client("Sent")
        result = self.Handler({"User-Agent": "Safari"}, "192.168.1.20")._identify_client("Connected")  # /ping without a key
        self.assertEqual(result, "Ana iPhone (192.168.1.20)")
        self.assertEqual(list(bridge.STATE["apple_devices"]), [device_id], "sin tarjeta «sin vincular» duplicada")
        # If it hasn't been seen for hours, that IP may belong to another device: then it is «not linked».
        bridge.STATE["apple_devices"][device_id]["timestamp"] = 0
        self.Handler({"User-Agent": "Safari"}, "192.168.1.20")._identify_client("Connected")
        self.assertIn("192.168.1.20", bridge.STATE["apple_devices"])

    def test_linked_load_on_start(self):
        device_id, _ = devices.register(self.cfg, "Lounge iPad", "ipad", pinned_name=True)
        self.cfg["devices"][device_id].update(last_ip="192.168.1.30", used=True)
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as log:
            log.write("2026-10-26 09:00:00,000 192.168.1.30 \"POST /enviar HTTP/1.1\" 200 -\n")
        with patch.object(bridge, "LOG", Path(log.name)):
            bridge.recover_last_phone_from_log(self.cfg)  # with a real log: reindexing must not lose the id
        Path(log.name).unlink()
        self.assertEqual(list(bridge.STATE["apple_devices"]), [device_id])
        loaded = bridge.STATE["apple_devices"][device_id]
        self.assertEqual((loaded["name"], loaded["type"], loaded["ip"]), ("Lounge iPad", "ipad", "192.168.1.30"))
        self.assertTrue(loaded["linked"])

    def test_after_restart_keyless_web_from_a_newly_linked_ip_creates_no_other_card(self):
        device_id, _ = devices.register(self.cfg, "Mi iPhone", "iphone")
        self.cfg["devices"][device_id]["last_ip"] = "192.168.1.30"
        with patch.object(bridge, "LOG", Path("no-existe.log")):
            bridge.recover_last_phone_from_log(self.cfg)
        result = self.Handler({"User-Agent": "Safari"}, "192.168.1.30")._identify_client("App en vivo")
        self.assertEqual(result, "Mi iPhone (192.168.1.30)")
        self.assertEqual(list(bridge.STATE["apple_devices"]), [device_id])


if __name__ == "__main__":
    unittest.main()
