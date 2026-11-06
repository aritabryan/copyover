"""«Send with CopyOver» with several files: a single transfer even though Explorer opens one process per file."""
import os
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
import local_secret

SECRET = "secreto-local-de-prueba"


class SendSeveralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        cls.server.RequestHandlerClass.key = "test-key"
        cls.server.local_secret = SECRET
        cls.cfg = {"port": cls.server.server_address[1], "key": "test-key"}
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        # The running CopyOver's secret, as a process started by Explorer would read it.
        environment = patch.dict(os.environ, {local_secret.VARIABLE: SECRET})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.files = []
        for name in ("a.txt", "b.txt", "c.txt"):
            path = Path(self.tmp.name) / name
            path.write_text(name)
            self.files.append(path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_several_processes_join_in_only_send(self):
        sent = threading.Event()
        calls = []

        def send_command(cfg, args):
            calls.append(args)
            sent.set()
            return 0

        with patch.object(bridge, "BATCH_WAIT", 0.3), patch.object(bridge, "send_command", side_effect=send_command), \
                patch.object(bridge, "load_config", return_value=self.cfg):
            threads = [threading.Thread(target=lambda r=r: self.assertTrue(
                bridge.resend_to_open_instance(self.cfg, [str(r)]))) for r in self.files]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertTrue(sent.wait(3))
            time.sleep(0.5)
        self.assertEqual(len(calls), 1, "un solo envío")
        self.assertEqual(sorted(Path(r).name for r in calls[0]), ["a.txt", "b.txt", "c.txt"])

    def test_without_open_copyover_or_with_destination_sends_direct(self):
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        free_port = free.getsockname()[1]
        free.close()
        self.assertFalse(bridge.resend_to_open_instance({"port": free_port, "key": "x"}, [str(self.files[0])]))
        with patch.object(bridge, "join_to_send") as join:
            self.assertFalse(bridge.resend_to_open_instance(self.cfg, ["--a", "Oficina", str(self.files[0])]))
            self.assertFalse(bridge.resend_to_open_instance(self.cfg, [str(Path(self.tmp.name) / "no-existe")]))
        join.assert_not_called()

    def test_rejects_a_request_without_the_local_secret_even_with_the_pc_key(self):
        with patch.object(bridge, "join_to_send") as join, \
                patch.dict(os.environ, {local_secret.VARIABLE: "otro"}):
            self.assertFalse(bridge.resend_to_open_instance(self.cfg, [str(self.files[0])]))
        join.assert_not_called()

    def test_notice_button_serves_open_copyover(self):
        with patch.object(bridge.so, "serve_notice", side_effect=lambda code, action: code == "abc") as serve:
            self.assertTrue(bridge.pass_notice_to_open_instance(self.cfg, ["copyover-notice:abc/open/"]))
            serve.assert_called_once_with("abc", "open")
            self.assertFalse(bridge.pass_notice_to_open_instance(self.cfg, ["copyover-notice:otro/folder"]))
            serve.reset_mock()
            with patch.dict(os.environ, {local_secret.VARIABLE: "otro"}):
                self.assertFalse(bridge.pass_notice_to_open_instance(self.cfg, ["copyover-notice:abc/open"]))
            self.assertFalse(bridge.pass_notice_to_open_instance(self.cfg, ["copyover-notice:"]))
            serve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
