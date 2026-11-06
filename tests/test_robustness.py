"""Errors show up and no data is lost: port in use, exceptions, cut bodies, log."""
import ctypes
import http.client
import logging
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import i18n
import bridge
sys.path.insert(0, str(Path(__file__).resolve().parent))
from server_fixture import TestServer


class BusyPortTests(unittest.TestCase):
    def test_second_launch_shows_the_panel_of_the_one_that_won_the_port(self):
        with patch.object(bridge, "_server_with_retries", return_value=None), \
                patch.object(bridge, "open_open_instance_panel", return_value=True) as panel, \
                patch.object(bridge, "notify_busy_port") as notify, \
                self.assertRaises(SystemExit) as output:
            bridge.serve({"port": 8765, "key": "x", "id": "y"})
        self.assertEqual(output.exception.code, 0)
        panel.assert_called_once_with(8765)
        notify.assert_not_called()

    def test_busy_port_is_reported_and_nothing_else_starts(self):
        with patch.object(bridge, "_server_with_retries", return_value=None), \
                patch.object(bridge, "open_open_instance_panel", return_value=False), \
                patch.object(bridge, "notify_busy_port") as notify, \
                patch.object(bridge, "recover_last_phone_from_log") as recover, \
                self.assertRaises(SystemExit) as output:
            bridge.serve({"port": 8765, "key": "x", "id": "y"})
        self.assertEqual(output.exception.code, 1)
        notify.assert_called_once_with(8765)
        recover.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Solo Windows")
    def test_notice_is_a_window_that_explains_what_to_do(self):
        with patch.object(ctypes.windll.user32, "MessageBoxW") as box, self.assertLogs(level="ERROR"), \
                patch.object(bridge, "_port_is_denied", return_value=False), patch.object(i18n, "_current", "es"):
            bridge.notify_busy_port(8765)
        _, text, title, _ = box.call_args.args
        self.assertEqual(title, "CopyOver")
        self.assertIn("8765", text)
        self.assertIn("otro programa", text)

    @unittest.skipUnless(sys.platform == "win32", "Solo Windows")
    def test_port_reserved_by_windows_has_its_own_notice(self):
        with patch.object(ctypes.windll.user32, "MessageBoxW") as box, self.assertLogs(level="ERROR"), \
                patch.object(bridge, "_port_is_denied", return_value=True), patch.object(i18n, "_current", "es"):
            bridge.notify_busy_port(8765)
        text = box.call_args.args[1]
        self.assertIn("8765", text)
        self.assertIn("Hyper-V", text)
        self.assertNotIn("otro CopyOver", text)

    @unittest.skipUnless(sys.platform == "win32", "Solo Windows")
    def test_only_a_denied_bind_counts_as_reserved(self):
        for winerror, expected in ((10013, True), (10048, False)):
            with patch("bridge.socket.socket") as fake:
                fake.return_value.__enter__.return_value.bind.side_effect = OSError(None, "x", None, winerror)
                self.assertEqual(bridge._port_is_denied(8765), expected, winerror)
        with patch("bridge.socket.socket"):
            self.assertFalse(bridge._port_is_denied(8765))


class CutSendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i18n.pin("es")
        cls.server = TestServer(("127.0.0.1", 0), bridge.Handler)
        cls.server.RequestHandlerClass.key = "test-key"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def send_halfway(self, close: bool) -> bytes:
        s = socket.create_connection(self.server.server_address, timeout=5)
        try:
            s.sendall(b"POST /enviar HTTP/1.1\r\nHost: x\r\nX-Key: test-key\r\n"
                      b"Content-Type: text/plain; charset=utf-8\r\nContent-Length: 1000\r\n\r\nsolo un pedazo")
            if close:
                s.shutdown(socket.SHUT_WR)
            response = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    return response
                response += chunk
        finally:
            s.close()

    def test_interrupted_send_saves_nothing_halfway(self):
        before = len(bridge.STATE["history"])
        for close, wait in ((True, 60), (False, 0.3)):
            with self.subTest(close=close), patch.object(bridge.Handler, "BODY_WAIT", wait), \
                    patch.object(bridge.so, "set_text") as clipboard, self.assertLogs(level="WARNING"):
                response = self.send_halfway(close)
            self.assertRegex(response[:20], rb"^HTTP/1\.[01] 400")
            self.assertIn("incompleto".encode(), response)
            clipboard.assert_not_called()
        self.assertEqual(len(bridge.STATE["history"]), before, "no history entry")


class RejectionWithoutReadBodyTests(unittest.TestCase):
    """A POST rejected before its body is read gets its answer, not a reset of the connection."""

    @classmethod
    def setUpClass(cls):
        i18n.pin("es")
        cls.server = TestServer(("127.0.0.1", 0), bridge.Handler)
        cls.server.local_secret = "s" * 43
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_client_receives_403_even_if_send_body_after(self):
        port = self.server.server_address[1]
        body = b'{"nombre": "x"}'
        for _ in range(3):
            s = socket.create_connection(self.server.server_address, timeout=5)
            try:
                # The body arrives after the server has already answered (as with http.client, which sends it
                # apart): if the server had closed for good, that body would come back as a reset that wipes out
                # the 403 before it's read.
                s.sendall(f"POST /app/profile HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                          f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode())
                time.sleep(0.3)
                s.sendall(body)
                time.sleep(0.3)
                response = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    response += chunk
            finally:
                s.close()
            self.assertRegex(response[:20], rb"^HTTP/1\.[01] 403")
            self.assertTrue(self.server.wait())


class FetchTests(unittest.TestCase):
    """«Traido» and «latest» only when /traer delivered something, and naming the device."""

    @classmethod
    def setUpClass(cls):
        i18n.pin("es")
        cls.server = TestServer(("127.0.0.1", 0), bridge.Handler)
        cls.server.RequestHandlerClass.key = "test-key"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.wait()
        self.last = bridge.STATE.get("last")
        bridge.STATE["last"] = None
        self.devices = dict(bridge.STATE["apple_devices"])

    def tearDown(self):
        bridge.STATE["last"] = self.last
        bridge.STATE["apple_devices"] = self.devices

    def fetch(self):
        con = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        con.request("GET", "/traer", headers={"X-Key": "test-key", "User-Agent": "Shortcuts/1254",
                                              "X-Device": "Test iPhone"})
        response = con.getresponse()
        body = response.read()
        con.close()
        self.assertTrue(self.server.wait(), "el servidor no terminó de atender /traer")
        return response.status, body

    def events(self):
        return [d.get("last_event", "") for d in bridge.STATE["apple_devices"].values()
                if d.get("ip") == "127.0.0.1"]

    def test_with_nothing_copied_does_not_report_a_fetch(self):
        with patch.object(bridge.so, "read_files", return_value=[]),                 patch.object(bridge.so, "read_text", return_value=""),                 patch.object(bridge.so, "read_image", return_value=False):
            self.assertEqual(self.fetch()[0], 200)
        self.assertIsNone(bridge.STATE["last"])
        self.assertFalse(any(e.startswith("Got something") for e in self.events()))

    def test_with_text_records_the_latest_with_name(self):
        with patch.object(bridge.so, "read_files", return_value=[]),                 patch.object(bridge.so, "read_text", return_value="hello"):
            self.assertEqual(self.fetch(), (200, b"hello"))
        self.assertIn("Texto enviado a", bridge.STATE["last"][1])

    def test_file_that_is_not_sent_is_not_recorded(self):
        with patch.object(bridge.so, "read_files", return_value=[Path(__file__)]),                 patch.object(bridge, "_weigh_more_than", return_value=False),                 patch.object(bridge, "MAX_BYTES", 1):
            self.assertEqual(self.fetch()[0], 413)
        self.assertIsNone(bridge.STATE["last"])

    def test_file_sent_in_full_is_recorded(self):
        with patch.object(bridge.so, "read_files", return_value=[Path(__file__)]),                 patch.object(bridge, "_weigh_more_than", return_value=False):
            state, body = self.fetch()
        self.assertEqual((state, body), (200, Path(__file__).read_bytes()))
        self.assertIn("Archivo enviado a", bridge.STATE["last"][1])


class ErrorsToLogTests(unittest.TestCase):
    def test_error_on_serve_remains_in_log_with_trace(self):
        server = bridge.BridgeServer.__new__(bridge.BridgeServer)
        try:
            raise KeyError("algo")
        except KeyError:
            with self.assertLogs(level="ERROR") as registry:
                server.handle_error(None, ("127.0.0.1", 5000))
        self.assertIn("Traceback", registry.output[0])
        try:
            raise ConnectionResetError()
        except ConnectionResetError:
            with self.assertLogs(level="INFO") as registry:
                server.handle_error(None, ("127.0.0.1", 5000))
        self.assertEqual(registry.records[0].levelname, "INFO", "a phone hanging up is not an error")

    def test_exceptions_without_catch_go_to_log(self):
        before = (sys.excepthook, threading.excepthook)
        try:
            bridge.register_uncaught_errors()
            with self.assertLogs(level="ERROR") as registry:
                thread = threading.Thread(target=lambda: 1 / 0, name="proof")
                thread.start()
                thread.join()
            self.assertIn("proof", registry.output[0])
            self.assertIn("ZeroDivisionError", registry.output[0])
            with self.assertLogs(level="CRITICAL"):
                try:
                    raise ValueError("x")
                except ValueError:
                    sys.excepthook(*sys.exc_info())
        finally:
            sys.excepthook, threading.excepthook = before


class LogNoiseTests(unittest.TestCase):
    def test_successful_routine_queries_are_not_logged(self):
        for path in ("/app/state?_t=1", "/app/history", "/ping", "/favicon.ico", "/p2p/estado"):
            with self.subTest(path=path):
                self.assertTrue(bridge.is_routine_request(path, 200))
                self.assertFalse(bridge.is_routine_request(path, 500), "los errores sí")
        self.assertFalse(bridge.is_routine_request("/enviar", 200))
        self.assertFalse(bridge.is_routine_request("/traer", 200))
        self.assertFalse(bridge.is_routine_request("/app/state", "-"))

    def test_zeroconf_network_cuts_are_line_without_trace(self):
        bridge.mute_zeroconf_traces()
        try:
            raise OSError(10065, "[WinError 1231] La ubicación de red no se puede alcanzar")
        except OSError:
            with self.assertLogs("zeroconf", level="DEBUG") as registry:
                logging.getLogger("zeroconf").exception("Error with socket %d (%s)): %s", 3, "x", "y")
        self.assertEqual(registry.records[0].levelname, "WARNING")
        self.assertIsNone(registry.records[0].exc_info)
        self.assertNotIn("Traceback", registry.output[0])
        # The rest of zeroconf stays the same.
        with self.assertLogs("zeroconf", level="DEBUG") as registry:
            logging.getLogger("zeroconf").error("another problem")
        self.assertEqual(registry.records[0].levelname, "ERROR")

    def test_received_file_names_go_in_debug(self):
        body = (b"--x\r\nContent-Disposition: form-data; name=\"archivo\"; filename=\"privado.txt\"\r\n"
                  b"Content-Type: text/plain\r\n\r\nhola\r\n--x--\r\n")
        with self.assertLogs(level="DEBUG") as registry:
            logging.getLogger().debug("marca")
            list(bridge.request_parts("multipart/form-data; boundary=x", body))
        with_name = [r.levelname for r in registry.records if "privado.txt" in r.getMessage()]
        self.assertEqual(with_name, ["DEBUG"])


if __name__ == "__main__":
    unittest.main()
