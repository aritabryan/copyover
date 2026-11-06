"""Restarting from the tray: the port is retried, the panel closes and the same script is relaunched."""
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dialogs
import bridge


class PortTests(unittest.TestCase):
    def test_if_port_is_busy_retries_and_to_final_gives_up(self):
        busy = socket.socket()
        busy.bind(("0.0.0.0", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        try:
            with patch("bridge.time.sleep") as wait, self.assertLogs(level="ERROR") as logs:
                self.assertIsNone(bridge._server_with_retries(port, attempts=3, wait=0.01))
            self.assertIn(str(port), logs.output[0])
            self.assertEqual(wait.call_count, 2, "espera entre intentos, no después del último")
        finally:
            busy.close()

    def test_when_port_frees_starts(self):
        busy = socket.socket()
        busy.bind(("0.0.0.0", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        attempts = {"n": 0}

        def release(_):
            attempts["n"] += 1
            if attempts["n"] == 2:
                busy.close()

        with patch("bridge.time.sleep", side_effect=release):
            server = bridge._server_with_retries(port, attempts=10, wait=0.01)
        self.assertIsNotNone(server)
        server.server_close()


class RelaunchTests(unittest.TestCase):
    def test_relaunches_same_script_with_same_python(self):
        # Without INVOCATION_ID: under systemd (as on the GitHub Linux runners) systemctl restarts it instead.
        with patch("bridge.subprocess.Popen") as popen, patch.dict(os.environ):
            os.environ.pop("INVOCATION_ID", None)
            bridge.relaunch()
        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("bridge.py"))
        self.assertEqual(popen.call_args.kwargs["cwd"], str(bridge.FOLDER))


class ClosePanelTests(unittest.TestCase):
    def test_closes_the_panel_it_opened_and_tolerates_it_being_gone(self):
        proc = Mock()
        proc.poll.return_value = None
        with patch.object(dialogs, "_PROC_PANEL", proc):
            self.assertTrue(dialogs.close_web_window())
            proc.terminate.assert_called_once()
            self.assertFalse(dialogs.close_web_window(), "the second time there's nothing left to close")
        with patch.object(dialogs, "_PROC_PANEL", None):
            self.assertFalse(dialogs.close_web_window())


if __name__ == "__main__":
    unittest.main()
