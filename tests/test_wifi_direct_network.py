"""Wi-Fi Direct network name and password from the panel, the cleanup when it's turned off and the owner's
«Which one are you?» on the web page."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import devices
import bridge
from tests.test_guests_and_shared_key import _WithServer

UA_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"


class _SeparateState(unittest.TestCase):
    def setUp(self):
        super().setUp()
        before = (dict(bridge.STATE.get("apple_devices", {})), dict(bridge.STATE.get("wifi_direct", {})))
        self.addCleanup(lambda: bridge.STATE.update(apple_devices=before[0], wifi_direct=before[1]))


class SuffixTests(unittest.TestCase):
    def test_numbers_letters_valid_and_dashes_1_to_20(self):
        for good in ("A", "Sala-2", "x" * 20, "PC-de-Ana"):
            self.assertIsNone(bridge.wifi_direct_suffix_error(good), good)
        for bad in ("", " ", "x" * 21, "con espacio", "Año", "a;b", "a'b", "a_b", "a:b"):
            self.assertIsNotNone(bridge.wifi_direct_suffix_error(bad), bad)

    def test_complete_name_fits_in_32_bytes(self):
        self.assertLessEqual(len((bridge.WIFI_DIRECT_SSID_PREFIX + "x" * 20).encode()), 32)


class WifiDirectNetworkPathTests(_SeparateState, _WithServer):
    def setUp(self):
        super().setUp()
        self.cfg.update(wifi_direct_ssid="CopyOver-PC", wifi_direct_password="clave-vieja-de-16-car")
        bridge.STATE["wifi_direct"] = {"active": False, "ssid": "", "key": "", "ip": "192.168.137.1"}

    def test_without_fields_only_reports_name(self):
        state, body = self.request_json("POST", "/app/wifi-direct-network", {})
        self.assertEqual(state, 200)
        self.assertEqual((body["prefix"], body["suffix"], body["ssid"]), ("CopyOver-", "PC", "CopyOver-PC"))
        self.assertEqual(self.cfg["wifi_direct_password"], "clave-vieja-de-16-car")

    def test_rejects_an_invalid_name_without_changing_anything(self):
        for suffix in ("con espacio", "x" * 21, "", "ñandú", 5, ["a"]):
            state, body = self.request_json("POST", "/app/wifi-direct-network", {"suffix": suffix})
            self.assertEqual(state, 400, suffix)
            self.assertFalse(body["ok"])
        self.assertEqual(self.cfg["wifi_direct_ssid"], "CopyOver-PC")

    def test_renames_with_fixed_prefix(self):
        state, body = self.request_json("POST", "/app/wifi-direct-network", {"suffix": "  Sala-2 "})
        self.assertEqual(state, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.cfg["wifi_direct_ssid"], "CopyOver-Sala-2")
        self.assertEqual(body["suffix"], "Sala-2")
        self.assertEqual(self.cfg["wifi_direct_password"], "clave-vieja-de-16-car", "renombrar no cambia la clave")

    def test_change_password_generates_other(self):
        state, body = self.request_json("POST", "/app/wifi-direct-network", {"new_key": True})
        self.assertEqual(state, 200)
        self.assertTrue(body["ok"])
        self.assertNotEqual(self.cfg["wifi_direct_password"], "clave-vieja-de-16-car")
        self.assertGreaterEqual(len(self.cfg["wifi_direct_password"]), 16)
        self.assertEqual(self.cfg["wifi_direct_ssid"], "CopyOver-PC")

    def test_if_is_on_restarts_with_new(self):
        bridge.STATE["wifi_direct"] = {"active": True, "ssid": "CopyOver-PC", "key": "x", "ip": "192.168.137.1"}
        seen = []

        def start():
            seen.append(self.cfg["wifi_direct_ssid"])
            return {"ok": True, "active": True, "ssid": self.cfg["wifi_direct_ssid"], "qr_wifi_svg": "<svg/>"}

        with patch.object(bridge, "stop_wifi_direct") as stop, patch.object(bridge, "start_wifi_direct", side_effect=start):
            _, body = self.request_json("POST", "/app/wifi-direct-network", {"suffix": "Nueva"})
            stop.assert_called_once()
            self.assertEqual(seen, ["CopyOver-Nueva"])
            self.assertEqual(body["qr_wifi_svg"], "<svg/>")
            # The same name again doesn't drop the devices that are connected.
            self.request_json("POST", "/app/wifi-direct-network", {"suffix": "Nueva"})
            self.assertEqual(seen, ["CopyOver-Nueva"])

    def test_stays_off_when_it_was_off(self):
        with patch.object(bridge, "start_wifi_direct") as start:
            self.request_json("POST", "/app/wifi-direct-network", {"suffix": "Otra", "new_key": True})
        start.assert_not_called()


class TurnOffWifiDirectTests(_SeparateState):
    def test_removes_cards_without_network_key_and_leaves_linked(self):
        bridge.STATE["apple_devices"] = {
            "192.168.137.5": {"ip": "192.168.137.5", "name": "iPhone"},
            "192.168.137.6": {"ip": "192.168.137.6", "name": "Invitado 1", "guest": "tk"},
            "d1": {"id": "d1", "ip": "192.168.137.7", "name": "iPhone de Ana"},
            "192.168.1.20": {"ip": "192.168.1.20", "name": "Tablet"},
            "192.168.13.7": {"ip": "192.168.13.7", "name": "Parecida"},
        }
        with patch.object(bridge, "_PROC_WIFI_DIRECT", None):
            bridge.stop_wifi_direct()
        self.assertEqual(sorted(bridge.STATE["apple_devices"]), ["192.168.1.20", "192.168.13.7", "d1"])
        self.assertFalse(bridge.STATE["wifi_direct"]["active"])


class WhoAreYouTests(_SeparateState, _WithServer):
    def setUp(self):
        super().setUp()
        self.id_ana, _ = devices.register(self.cfg, "iPhone de Ana", "iphone", pinned_name=True)
        self.cfg["devices"][self.id_ana]["used"] = True
        self.pending_id, _ = devices.register(self.cfg, "Sin usar", "iphone")  # scanned, never used
        bridge.STATE["phone_tokens"]["mio"] = time.time() + 600
        bridge.STATE["guest_tokens"]["inv"] = time.time() + 600
        bridge.STATE["apple_devices"] = {}
        patcher = patch.object(bridge, "modify_config", side_effect=lambda change: change(self.cfg))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_owner_link_receives_list(self):
        self.assertIsNone(bridge.linked_to_choose("inv"))
        self.assertIsNone(bridge.linked_to_choose("inventado"))
        self.assertEqual(bridge.linked_to_choose("mio"), [{"id": self.id_ana, "name": "iPhone de Ana", "type": "iphone"}])
        state, body = self.request("GET", "/invitado/quien?t=inv")
        self.assertEqual(state, 403)
        self.assertNotIn("Ana", body.decode("utf-8"))
        state, body = self.request_json("GET", "/invitado/quien?t=mio")
        self.assertEqual(state, 200)
        self.assertEqual([d["name"] for d in body["devices"]], ["iPhone de Ana"])

    def test_guest_cannot_pose_as_a_linked_device(self):
        self.assertFalse(bridge.associate_web_to_linked("inv", self.id_ana, "192.168.137.20"))
        with patch.object(bridge.so, "read_text", return_value=""):
            _, body = self.request_json("GET", f"/invitado/estado?t=inv&soy={self.id_ana}")
        self.assertFalse(body["is_valid_self"])
        self.assertNotIn(self.id_ana, bridge.STATE["apple_devices"])

    def test_rejects_a_missing_or_unlinked_device(self):
        with patch.object(bridge, "is_this_pc", return_value=False):
            self.assertFalse(bridge.associate_web_to_linked("mio", "no-existe", "192.168.137.20"))
            self.assertFalse(bridge.associate_web_to_linked("mio", self.pending_id, "192.168.137.20"))
        self.assertEqual(bridge.STATE["apple_devices"], {})

    def test_choose_joins_page_with_card(self):
        ip = "192.168.137.20"

        class Handler:
            _identify_client = bridge.Handler._identify_client
            _identify_linked = bridge.Handler._identify_linked
            headers = {"User-Agent": UA_IPHONE}
            client_address = (ip, 50000)
            path = "/invitado/estado?t=mio"

        with patch.object(bridge, "is_this_pc", return_value=False), patch.object(bridge, "network_signature", return_value=""):
            # Before choosing, Safari (no key, new IP) is a generic card.
            Handler()._identify_client("App en vivo", {"t": ["mio"], "type": ["iphone"], "disp": ["iPhone"]})
            self.assertEqual(list(bridge.STATE["apple_devices"]), [ip])
            self.assertTrue(bridge.associate_web_to_linked("mio", self.id_ana, ip))
            self.assertEqual(list(bridge.STATE["apple_devices"]), [self.id_ana])
            self.assertNotIn(ip, self.cfg.get("apple_devices", {}))
            # From then on, the page's requests without a key land on Ana's card.
            name = Handler()._identify_client("App en vivo", {"t": ["mio"], "type": ["iphone"], "disp": ["iPhone"]})
        self.assertEqual(name, f"iPhone de Ana ({ip})")
        self.assertEqual(list(bridge.STATE["apple_devices"]), [self.id_ana])
        self.assertEqual(bridge.STATE["apple_devices"][self.id_ana]["ip"], ip)
        self.assertFalse(self.cfg["devices"][self.pending_id]["used"])


if __name__ == "__main__":
    unittest.main()
