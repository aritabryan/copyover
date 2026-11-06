"""The network announcement: when it's redone, which IP it carries and whether another PC uses the same
.local."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import diagnostics
import i18n
import bridge
import network
from network import Interface

HOME = Interface("Wi-Fi", "192.168.1.5", 24, "Red de la casa")
U = Interface("Wi-Fi", "192.168.1.5", 24, "Red de la universidad")
CABLE = Interface("Ethernet", "10.0.0.8", 24, "Red de la oficina")
WSL = Interface("vEthernet (WSL)", "172.25.64.1", 20)


class _End(BaseException):
    pass


def run_announcement(steps, cfg=None, on_wait=None):
    """Runs the announcement loop: each step is (interfaces, has_path). Returns the calls to
    register_announcement."""
    steps = iter(steps)
    current = {}
    logs = []

    def advance():
        try:
            current["lista"], current["path"] = next(steps)
        except StopIteration:
            raise _End

    def output_ip():
        advance()
        return current["lista"][0].ip if current["path"] else None

    def register(used_cfg, ip, extras=()):
        logs.append((used_cfg["name"], ip, list(extras)))
        return MagicMock(), MagicMock(addresses=[])

    def wait(_):
        if on_wait:
            on_wait(len(logs))
        return False

    cfg = cfg or {"id": "yo", "name": "pc", "port": 8765}
    with patch("network.output_ip", side_effect=output_ip), \
            patch("network.interfaces", side_effect=lambda refresh=False: current["lista"]), \
            patch("network.forget"), \
            patch.object(bridge, "ip_local", side_effect=lambda: current["lista"][0].ip), \
            patch.object(bridge, "register_announcement", side_effect=register), \
            patch.object(bridge, "_check_namesakes"), \
            patch("zeroconf.ServiceBrowser", return_value=MagicMock()), \
            patch.object(bridge, "_FIND_NOW", MagicMock(wait=wait)), \
            patch.object(bridge, "load_config", return_value={}), patch.object(bridge, "save_config"):
        try:
            bridge.announce_in_network(cfg)
        except _End:
            pass
    return logs


class ReannounceTests(unittest.TestCase):
    def setUp(self):
        self.state = {k: bridge.STATE.get(k) for k in ("pcs", "ip", "announced", "repeated_name")}

    def tearDown(self):
        bridge.STATE.update(self.state)

    def test_on_change_from_wifi_re_announce_even_if_ip_is_same(self):
        logs = run_announcement([([HOME], True), ([HOME], True), ([U], True), ([U], True)])
        self.assertEqual([ip for _, ip, _ in logs], ["192.168.1.5", "192.168.1.5"])

    def test_offline_gap_on_change_from_wifi_forces_to_announce_again(self):
        # Linux doesn't give the network name: what shows is the stretch without an outbound route.
        without_name = Interface("wlp2s0", "192.168.1.5", 24)
        logs = run_announcement([([without_name], True), ([without_name], False), ([without_name], True),
                                    ([without_name], True)])
        self.assertEqual(len(logs), 3, "al anunciar, al perder la ruta y al recuperarla")

    def test_new_virtual_adapter_does_not_reannounce(self):
        logs = run_announcement([([HOME], True), ([HOME, WSL], True), ([HOME, WSL], True)])
        self.assertEqual(len(logs), 1)

    def test_the_a_record_also_carries_the_other_real_ips(self):
        logs = run_announcement([([HOME, CABLE, WSL], True)])
        self.assertEqual(logs, [("pc", "192.168.1.5", ["10.0.0.8"])])

    def test_change_name_in_settings_announces_without_restart(self):
        def on_wait(count):
            if count == 1:
                bridge.change_announced_name("estudio")
        logs = run_announcement([([HOME], True)] * 3, on_wait=on_wait)
        self.assertEqual([name for name, _, _ in logs], ["pc", "estudio"])


class LogTests(unittest.TestCase):
    def test_main_ip_goes_first_and_without_repeat(self):
        client = MagicMock()
        with patch("zeroconf.Zeroconf", return_value=client):
            _, info = bridge.register_announcement({"name": "pc", "port": 8765, "id": "a" * 32}, "192.168.1.5",
                                               ["10.0.0.8", "192.168.1.5", "192.168.137.1"])
        self.assertEqual(info.parsed_addresses(), ["192.168.1.5", "10.0.0.8", "192.168.137.1"])
        self.assertEqual(info.server, "pc.local.")

    def test_announcement_key(self):
        base = bridge.announcement_key("pc", "192.168.1.5", False, True, [HOME])
        self.assertNotEqual(base, bridge.announcement_key("pc", "192.168.1.5", False, True, [U]))
        self.assertNotEqual(base, bridge.announcement_key("pc", "192.168.1.5", False, False, [HOME]))
        self.assertNotEqual(base, bridge.announcement_key("otro", "192.168.1.5", False, True, [HOME]))
        self.assertEqual(base, bridge.announcement_key("pc", "192.168.1.5", False, True, [WSL, HOME]))


class InterfacesTests(unittest.TestCase):
    def test_reads_windows_network_name(self):
        data = [{"InterfaceAlias": "Wi-Fi", "IPAddress": "192.168.1.5", "PrefixLength": 24, "Red": "Casa"},
                 {"InterfaceAlias": "Ethernet", "IPAddress": "10.0.0.8", "PrefixLength": 24, "Red": None}]
        self.assertEqual(network.json_interfaces(data),
                         [Interface("Wi-Fi", "192.168.1.5", 24, "Casa"), Interface("Ethernet", "10.0.0.8", 24, "")])
        self.assertEqual(len(network.json_interfaces(data[0])), 1)

    def test_own_ips_without_virtual(self):
        self.assertEqual(network.own_ips([HOME, WSL, CABLE, HOME]), ["192.168.1.5", "10.0.0.8"])
        self.assertIn("172.25.64.1", network.all_ips([HOME, WSL]))


class _Resolve:
    responses: list = []

    def __init__(self, name):
        self.name = name

    def request(self, zc, wait):
        return True

    def load_from_cache(self, zc):
        return True

    def parsed_addresses(self):
        return list(self.responses)


class NamesakesTests(unittest.TestCase):
    def setUp(self):
        self.before = dict(bridge._ANNOUNCED_NAME)
        self.repeated = bridge.STATE.get("repeated_name")

    def tearDown(self):
        bridge._ANNOUNCED_NAME.clear()
        bridge._ANNOUNCED_NAME.update(self.before)
        bridge.STATE["repeated_name"] = self.repeated
        i18n.pin("es")

    def _find(self, responses, own):
        _Resolve.responses = responses
        with patch("bridge.time.sleep"):
            return bridge.find_namesakes(MagicMock(), "pc", own, resolve_cls=_Resolve)

    def test_counts_only_ips_from_other_pcs(self):
        self.assertEqual(self._find(["192.168.1.5"], {"192.168.1.5"}), [])
        self.assertEqual(self._find(["192.168.1.5", "192.168.1.9", "192.168.1.9"], {"192.168.1.5"}),
                         ["192.168.1.9"])
        self.assertEqual(self._find(["127.0.0.1"], set()), [])

    def test_other_pc_with_same_name_exits_in_diagnostics(self):
        zc = MagicMock()
        bridge._ANNOUNCED_NAME.update(name="pc", zc=zc)
        with patch("network.interfaces", return_value=[HOME]), \
                patch.object(bridge, "find_namesakes", return_value=["192.168.1.9"]):
            bridge._check_namesakes(zc, "pc")
        self.assertTrue(bridge.STATE["repeated_name"])
        problems = diagnostics.check({"name": "pc"}, platform="linux", ip="192.168.1.5",
                                        run=MagicMock(side_effect=OSError), repeated_name=True)
        notice = next(p for p in problems if p["key"] == "repeated_name")
        self.assertEqual(notice["title"], "Otra PC de esta red también se llama «pc»")
        self.assertIn("Ajustes", notice["detail"])
        i18n.pin("en")
        problems = diagnostics.check({"name": "pc"}, platform="linux", ip="192.168.1.5",
                                        run=MagicMock(side_effect=OSError), repeated_name=True)
        notice = next(p for p in problems if p["key"] == "repeated_name")
        self.assertEqual(notice["title"], "Another PC on this network is also called “pc”")
        self.assertIn("Settings", notice["detail"])

    def test_claims_nothing_without_own_ip_or_after_a_network_change(self):
        zc = MagicMock()
        bridge.STATE["repeated_name"] = False
        bridge._ANNOUNCED_NAME.update(name="pc", zc=zc)
        with patch("network.interfaces", return_value=None), \
                patch.object(bridge, "find_namesakes", return_value=["192.168.1.9"]) as find:
            bridge._check_namesakes(zc, "pc")
        find.assert_not_called()
        bridge._ANNOUNCED_NAME["zc"] = MagicMock()  # announced again meanwhile
        with patch("network.interfaces", return_value=[HOME]), \
                patch.object(bridge, "find_namesakes", return_value=["192.168.1.9"]):
            bridge._check_namesakes(zc, "pc")
        self.assertFalse(bridge.STATE["repeated_name"])

    def test_failed_query_breaks_nothing(self):
        zc = MagicMock()
        bridge.STATE["repeated_name"] = False
        bridge._ANNOUNCED_NAME.update(name="pc", zc=zc)
        with patch("network.interfaces", return_value=[HOME]), \
                patch.object(bridge, "find_namesakes", side_effect=OSError("sin red")):
            bridge._check_namesakes(zc, "pc")
        self.assertFalse(bridge.STATE["repeated_name"])


if __name__ == "__main__":
    unittest.main()
