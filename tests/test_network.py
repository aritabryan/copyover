"""The PC's networks: which IP is announced, with which mask, and whether the phone is on the same network."""
import http.client
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge
import network
from network import Interface

WIFI = Interface("Wi-Fi", "192.168.50.10", 24)
WSL = Interface("vEthernet (WSL (Hyper-V firewall))", "172.25.64.1", 20)
HYPERV = Interface("vEthernet (Default Switch)", "172.30.0.1", 20)
TAILSCALE = Interface("Tailscale", "100.101.102.103", 32)
LOOPBACK = Interface("Loopback Pseudo-Interface 1", "127.0.0.1", 8)


class IpLocalTests(unittest.TestCase):
    def test_announces_wifi_and_not_virtual_adapters(self):
        with patch("network.interfaces", return_value=[WSL, HYPERV, WIFI, TAILSCALE, LOOPBACK]), \
                patch("network.output_ip", return_value=None):
            self.assertEqual(network.ip_local(), "192.168.50.10")
        # Even if internet traffic goes through the VPN, the phone comes in through the Wi-Fi.
        with patch("network.interfaces", return_value=[WIFI, TAILSCALE]), \
                patch("network.output_ip", return_value="100.101.102.103"):
            self.assertEqual(network.ip_local(), "192.168.50.10")

    def test_with_only_virtual_adapters_does_not_announce_a_useless_ip(self):
        with patch("network.interfaces", return_value=[WSL, HYPERV, TAILSCALE, LOOPBACK]), \
                patch("network.output_ip", return_value=None):
            self.assertEqual(network.ip_local(), "127.0.0.1")

    def test_linux_names_also_recognize(self):
        output = (
            "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
            "2: wlp2s0    inet 10.20.30.40/22 brd 10.20.31.255 scope global dynamic wlp2s0\\       valid_lft 1\n"
            "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft 1\n"
        )
        items = network.read_ip_output(output)
        self.assertEqual([(i.name, i.prefix, i.virtual) for i in items],
                         [("lo", 8, True), ("wlp2s0", 22, False), ("docker0", 16, True)])


class SubnetTests(unittest.TestCase):
    def setUp(self):
        self.wfd = dict(bridge.STATE["wifi_direct"])
        self.ip = bridge.STATE.get("ip")

    def tearDown(self):
        bridge.STATE["wifi_direct"] = self.wfd
        bridge.STATE["ip"] = self.ip

    def test_uses_interface_real_mask(self):
        wide = Interface("Ethernet", "10.10.0.5", 20)   # 10.10.0.0 - 10.10.15.255
        narrow = Interface("Wi-Fi", "172.16.1.5", 24)
        with patch("network.interfaces", return_value=[wide, narrow]):
            self.assertTrue(bridge._same_subnet("10.10.12.7", "10.10.0.5"), "with /20 it's the same network")
            self.assertFalse(bridge._same_subnet("10.10.16.7", "10.10.0.5"))
            self.assertFalse(bridge._same_subnet("172.16.9.9", "172.16.1.5"), "ya no se junta todo 172.x")

    def test_phone_by_wifi_direct_is_in_same_network(self):
        bridge.STATE["ip"] = "192.168.50.10"
        bridge.STATE["wifi_direct"] = {"active": True, "ip": "192.168.137.1"}
        with patch("network.interfaces", return_value=[WIFI]):
            self.assertEqual(bridge.device_network({"ip": "192.168.137.42"}), "same")
            bridge.STATE["wifi_direct"] = {"active": False, "ip": "192.168.137.1"}
            self.assertNotEqual(bridge.device_network({"ip": "192.168.137.42"}), "same")
        # If Windows already lists the Wi-Fi Direct adapter, it counts like any other network of the PC.
        with patch("network.interfaces", return_value=[WIFI, Interface("Conexión de área local* 10", "192.168.137.1", 24)]):
            self.assertEqual(bridge.device_network({"ip": "192.168.137.42"}), "same")


class DeviceNetworkTests(unittest.TestCase):
    def setUp(self):
        self.ip = bridge.STATE.get("ip")
        bridge.STATE["ip"] = None

    def tearDown(self):
        bridge.STATE["ip"] = self.ip

    def test_after_the_pc_changes_network_does_not_claim_another_network(self):
        home = Interface("Wi-Fi", "192.168.1.5", 24)
        office = Interface("Wi-Fi", "172.16.40.9", 24)
        with patch("network.interfaces", return_value=[home]):
            seen = {"ip": "192.168.1.30", "pc_network": bridge.network_signature()}
            self.assertEqual(bridge.device_network(seen), "same")
        with patch("network.interfaces", return_value=[office]):
            self.assertEqual(bridge.device_network(seen), "unknown",
                             "la PC se fue a otra red: la IP vieja del celular no dice dónde está ahora")
            self.assertEqual(bridge.device_network({"ip": "192.168.1.30"}), "unknown",
                             "cargado de la config, sin saber dónde estaba la PC")
            from_other = {"ip": "10.8.0.4", "pc_network": bridge.network_signature()}
            self.assertEqual(bridge.device_network(from_other), "other",
                             "con la PC en la misma red de entonces, sí es otra red")
            self.assertEqual(bridge.device_network({}), "unknown")

    def test_on_request_phone_something_remains_recorded_with_pc_network(self):
        class Fake(bridge.Handler):
            def __init__(self):
                self.headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
                self.client_address = ("192.168.50.77", 50000)

        cfg = {"devices": {}, "apple_devices": {}}
        with patch("network.interfaces", return_value=[WIFI]), patch("bridge.load_config", return_value=cfg), \
                patch("bridge.save_config"):
            Fake()._identify_client("Conectado")
            data = bridge.STATE["apple_devices"]["192.168.50.77"]
            self.assertEqual(data["pc_network"], bridge.network_signature())
            self.assertEqual(bridge.device_network(data), "same")
        bridge.STATE["apple_devices"].pop("192.168.50.77", None)


class StateTests(unittest.TestCase):
    def test_state_app_fetches_network_each_device(self):
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        before = dict(bridge.STATE["apple_devices"])
        bridge.STATE["apple_devices"] = {
            "a": {"id": "a", "ip": "192.168.50.20", "name": "iPhone", "type": "iphone", "pc_network": "x"},
            "b": {"id": "b", "ip": "", "name": "iPad", "type": "ipad"},
        }
        try:
            with patch("network.interfaces", return_value=[WIFI]):
                con = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                con.request("GET", "/app/state", headers={"Host": f"127.0.0.1:{port}"})
                data = json.loads(con.getresponse().read())
                con.close()
            by_id = {d["id"]: d for d in data["apple_devices"]}
            self.assertEqual(by_id["a"]["network"], "same")
            self.assertEqual(by_id["b"]["network"], "unknown")
            self.assertNotIn("pc_network", by_id["a"])
        finally:
            bridge.STATE["apple_devices"] = before
            server.shutdown()
            server.server_close()


class CacheTests(unittest.TestCase):
    def test_does_not_call_powershell_on_every_query(self):
        network.forget()
        try:
            with patch("network._read_windows", return_value=[WIFI]) as read_w, \
                    patch("network._read_linux", return_value=[WIFI]) as read_l:
                for _ in range(20):
                    network.interfaces()
                    network.prefix_of("192.168.50.10")
                self.assertEqual(read_w.call_count + read_l.call_count, 1)
        finally:
            network.forget()


if __name__ == "__main__":
    unittest.main()
