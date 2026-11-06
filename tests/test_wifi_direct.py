import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("COPYOVER_LANGUAGE", "es")

import bridge  # noqa: E402
import wifi_direct  # noqa: E402


class Script(unittest.TestCase):
    def test_key_and_name_go_through_the_environment_and_not_the_command(self):
        env = wifi_direct.environment("CopyOver-x'; Remove-Item C:\\", "clave'$(calc)", base={})
        self.assertEqual(env["COPYOVER_WFD_SSID"], "CopyOver-x'; Remove-Item C:\\")
        self.assertEqual(env["COPYOVER_WFD_CLAVE"], "clave'$(calc)")
        command = " ".join(wifi_direct.command())
        self.assertNotIn("CopyOver-x", command)
        self.assertLess(len(command), 32000)  # Windows' command-line limit

    def test_command_carries_complete_script(self):
        args = wifi_direct.command()
        script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16-le")
        self.assertIn("$env:COPYOVER_WFD_SSID", script)
        self.assertIn("StopRequested", script)

    def test_accepts_phones_joining_with_the_key(self):
        # Without accepting ConnectionRequested, Windows drops legacy clients (the iPhone can't join).
        cs = wifi_direct._CSHARP
        self.assertIn("ConnectionRequested += OnConnectionRequested", cs)
        self.assertIn("WiFiDirectDevice.FromIdAsync", cs)
        self.assertIn("devices[id] = dev", cs)

    def test_publisher_error_exits_from_event(self):
        self.assertIn("StatusChanged +=", wifi_direct._CSHARP)
        self.assertIn("a.Error", wifi_direct._CSHARP)


class Result(unittest.TestCase):
    def test_only_fail_ok_and_error_are_result(self):
        self.assertTrue(wifi_direct.is_result("OK:wfd\n"))
        self.assertTrue(wifi_direct.is_result("FAIL:Aborted:Success:On:Unknown"))
        self.assertTrue(wifi_direct.is_result("ERROR:algo"))
        self.assertFalse(wifi_direct.is_result("STATUS:attempt1:Aborted:Success"))
        self.assertFalse(wifi_direct.is_result("CLIENT:connected"))

    def test_ok(self):
        self.assertEqual(wifi_direct.parse("OK:wfd\n"), ("wfd", ""))
        self.assertEqual(wifi_direct.parse("OK:hotspot"), ("hotspot", ""))

    def test_off_wifi(self):
        for line in ("FAIL:Aborted:RadioNotAvailable:On:Skipped", "FAIL:Aborted:Success:Off:Skipped",
                      "FAIL:Aborted:Success:On:WiFiDeviceOff"):
            mode, message = wifi_direct.parse(line)
            self.assertIsNone(mode)
            self.assertEqual(message, "Enciende el Wi-Fi de la PC; no hace falta que esté conectado a una red.")

    def test_busy_wifi(self):
        _, message = wifi_direct.parse("FAIL:Aborted:ResourceInUse:On:InUse")
        self.assertIn("ocupado", message)

    def test_without_adapter(self):
        _, message = wifi_direct.parse("FAIL:Aborted:Success:NoWiFi:Skipped")
        self.assertIn("no tiene un adaptador Wi-Fi", message)

    def test_aborted_without_a_reason_does_not_show_the_raw_state(self):
        _, message = wifi_direct.parse("FAIL:Aborted:Success:On:NetworkLimitedConnectivity")
        self.assertNotIn("STATUS", message)
        self.assertNotIn("Aborted", message)
        self.assertIn("Apaga y enciende el Wi-Fi", message)

    def test_script_error_and_without_response(self):
        self.assertEqual(wifi_direct.parse("ERROR:compile CS0012 x")[1],
                         "No se pudo iniciar Wi-Fi Direct (compile CS0012 x)")
        self.assertEqual(wifi_direct.parse("")[1], "No se pudo iniciar Wi-Fi Direct (sin respuesta)")

    def test_in_english(self):
        with patch.dict(os.environ, {"COPYOVER_LANGUAGE": "en"}):
            import i18n
            previous = i18n._current
            i18n.pin("en")
            try:
                _, message = wifi_direct.parse("FAIL:Aborted:RadioNotAvailable:On:Skipped")
            finally:
                i18n.pin(previous or "es")
        self.assertEqual(message, "Turn on the PC’s Wi-Fi; it doesn’t need to be connected to a network.")


class Process:
    def __init__(self, lines):
        self.lines = list(lines)
        self.written = []
        self.closed = False
        proc = self

        class Output:
            def readline(self):
                return proc.lines.pop(0) if proc.lines else ""

        class Input:
            def write(self, text):
                proc.written.append(text)

            def flush(self):
                pass

            def close(self):
                proc.closed = True

        self.stdout = Output()
        self.stdin = Input()

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return None if not self.closed else 0


class StartAndStop(unittest.TestCase):
    def setUp(self):
        self.cfg = {"port": 8765, "wifi_direct_ssid": "CopyOver-PC", "wifi_direct_password": "x" * 16}

    def tearDown(self):
        bridge._PROC_WIFI_DIRECT = None
        bridge.STATE["wifi_direct"] = {"active": False, "ssid": "", "key": "", "ip": "192.168.137.1"}

    def start(self, proc):
        with patch.object(bridge.sys, "platform", "win32"), \
                patch.object(bridge, "load_config", return_value=self.cfg), \
                patch.object(bridge, "save_config"), \
                patch.object(bridge, "_get_or_create_mobile_token", return_value={"url": "u", "qr_svg": ""}), \
                patch.object(bridge.subprocess, "Popen", return_value=proc) as popen:
            return bridge.start_wifi_direct(), popen

    def test_fails_shows_light_message_and_closes_process(self):
        proc = Process(["STATUS:attempt1:Aborted:RadioNotAvailable\n", "FAIL:Aborted:RadioNotAvailable:Off:Skipped\n"])
        res, _ = self.start(proc)
        self.assertFalse(res["ok"])
        self.assertEqual(res["message"], "Enciende el Wi-Fi de la PC; no hace falta que esté conectado a una red.")
        self.assertEqual(proc.written, ["STOP\n"])
        self.assertIsNone(bridge._PROC_WIFI_DIRECT)

    def test_skips_status_lines_and_uses_the_hotspot(self):
        proc = Process(["STATUS:attempt1:Aborted:Success\n", "OK:hotspot\n"])
        res, popen = self.start(proc)
        self.assertTrue(res["ok"])
        self.assertEqual(res["ip"], "192.168.137.1")
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["COPYOVER_WFD_SSID"], "CopyOver-PC")
        self.assertEqual(env["COPYOVER_WFD_CLAVE"], "x" * 16)

    def test_on_turn_off_asks_stop_by_input(self):
        proc = Process([])
        bridge._PROC_WIFI_DIRECT = proc
        bridge.STATE["wifi_direct"] = {"active": True, "ssid": "s", "key": "c", "ip": "192.168.137.1"}
        bridge.stop_wifi_direct()
        self.assertEqual(proc.written, ["STOP\n"])
        self.assertTrue(proc.closed)
        self.assertFalse(bridge.STATE["wifi_direct"]["active"])

    def test_if_network_falls_only_remains_off(self):
        proc = Process(["CLIENT:connected\n", "STATUS:ended:Aborted:RadioNotAvailable\n"])
        bridge._PROC_WIFI_DIRECT = proc
        bridge.STATE["wifi_direct"] = {"active": True, "ssid": "s", "key": "c", "ip": "192.168.137.1"}
        bridge._follow_wifi_direct(proc)
        self.assertIsNone(bridge._PROC_WIFI_DIRECT)
        self.assertFalse(bridge.STATE["wifi_direct"]["active"])


if __name__ == "__main__":
    unittest.main()
