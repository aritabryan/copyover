"""PCs that are gone: forgotten on a network change, and sending to them fails fast with a clear message."""
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import i18n
import p2p
import bridge


class _End(BaseException):
    pass


class NetworkChangeTests(unittest.TestCase):
    def test_on_change_from_network_forget_previous_pcs(self):
        ips = iter(["192.168.50.10", "192.168.50.10", "10.20.0.7"])
        seen = []

        def ip_local():
            try:
                return next(ips)
            except StopIteration:
                raise _End

        def wait(_):
            seen.append(list(bridge.STATE["pcs"]))
            return False

        def browser(zc, kind, observer):
            # The first browser «sees» a PC on the home network.
            if not seen:
                observer._pcs["other"] = {"id": "other", "name": "PC vieja", "ip": "192.168.50.20", "port": 8765}
                observer._notify()
            return MagicMock()

        before = bridge.STATE.get("pcs")
        with patch.object(bridge, "ip_local", side_effect=ip_local),                 patch("network.interfaces", return_value=[]), patch("network.output_ip", return_value=None),                 patch.object(bridge, "_check_namesakes"), \
                patch.object(bridge, "register_announcement", return_value=(MagicMock(), MagicMock(addresses=[]))), \
                patch("zeroconf.ServiceBrowser", side_effect=browser), \
                patch.object(bridge, "_FIND_NOW", MagicMock(wait=wait)), \
                patch.object(bridge, "load_config", return_value={}), patch.object(bridge, "save_config"), \
                self.assertRaises(_End):
            bridge.announce_in_network({"id": "yo", "name": "pc", "port": 8765})
        bridge.STATE["pcs"] = before
        self.assertEqual([e["id"] for e in seen[0]], ["other"])
        self.assertEqual([e["id"] for e in seen[1]], ["other"], "misma red: se mantiene")
        self.assertEqual(seen[2], [], "en la red nueva no queda la PC de la anterior")


class SendToMissingPcTests(unittest.TestCase):
    def tearDown(self):
        i18n.pin("es")

    def test_fails_in_seconds_and_names_the_pc_not_found(self):
        file = Path(__file__)
        with patch("socket.create_connection", side_effect=TimeoutError("timed out")) as connect:
            startup = time.monotonic()
            ok, detail = p2p.send_to_pc("192.0.2.1", 8765, "k", "yo", "id", [file], destination_name="Oficina")
        self.assertFalse(ok)
        self.assertEqual(detail, "No se encontró a Oficina en esta red")
        self.assertNotIn("WinError", detail)
        self.assertLess(time.monotonic() - startup, 2)
        self.assertEqual(connect.call_args.args[1], 5.0, "conectar no espera los 65 s de la oferta")

    def test_pc_not_listening_also_explains(self):
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()  # nobody is listening there
        i18n.pin("en")
        ok, detail = p2p.send_to_pc("127.0.0.1", port, "k", "me", "id", [Path(__file__)], destination_name="Office")
        self.assertFalse(ok)
        self.assertEqual(detail, "Office wasn’t found on this network")


if __name__ == "__main__":
    unittest.main()
