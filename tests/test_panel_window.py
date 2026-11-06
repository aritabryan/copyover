import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dialogs  # noqa: E402
import panel_window  # noqa: E402
import local_secret  # noqa: E402


class FakeProcess:
    def __init__(self, alive=True, code=None):
        self.pid = 4321
        self.alive = alive
        self.returncode = code
        self.written = []
        process = self

        class Input:
            def write(self, data):
                process.written.append(data)

            def flush(self):
                pass

        self.stdin = Input()

    def poll(self):
        return None if self.alive else self.returncode

    def wait(self, _=None):
        self.alive = False
        return 0

    def kill(self):
        self.alive = False


class FakePopen:
    def __init__(self, process=None):
        self.calls = []
        self.process = process or FakeProcess()

    def __call__(self, command, **_):
        self.calls.append(command)
        return self.process


class TestPanelLauncher(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(panel_window, "available_pywebview", return_value=True),
            patch.object(panel_window, "_visible_window", return_value=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_show_launches_process_with_port(self):
        popen = FakePopen()
        launcher = panel_window.PanelLauncher(8765, executable="python", popen=popen)
        self.assertTrue(launcher.show())
        command = popen.calls[0]
        self.assertTrue(any(str(c).endswith("panel_window.py") for c in command), command)
        self.assertIn("8765", command)

    def test_show_does_not_relaunch_a_live_process(self):
        popen = FakePopen()
        launcher = panel_window.PanelLauncher(8765, executable="python", popen=popen)
        launcher.show()
        self.assertTrue(launcher.show())
        self.assertEqual(len(popen.calls), 1)
        self.assertIn(b"mostrar\n", popen.process.written)

    def test_show_returns_false_without_pywebview(self):
        popen = FakePopen()
        with patch.object(panel_window, "available_pywebview", return_value=False):
            self.assertFalse(panel_window.PanelLauncher(8765, popen=popen).show())
        self.assertEqual(popen.calls, [])

    def test_show_returns_false_if_process_dies_on_start(self):
        popen = FakePopen(FakeProcess(alive=False, code=1))
        with patch.object(panel_window, "_visible_window", return_value=False):
            launcher = panel_window.PanelLauncher(8765, executable="python", popen=popen, wait=0.3)
            self.assertFalse(launcher.show())

    def test_close_asks_exit(self):
        popen = FakePopen()
        launcher = panel_window.PanelLauncher(8765, executable="python", popen=popen)
        launcher.show()
        launcher.close()
        self.assertIn(b"salir\n", popen.process.written)


class TestWindowSize(unittest.TestCase):
    def test_fits_in_1080p_laptop_to_150(self):
        self.assertEqual(panel_window.window_size((1280, 688)), (1180, 664))

    def test_in_large_screens_uses_normal_size(self):
        self.assertEqual(panel_window.window_size((2560, 1392)), (1180, 760))
        self.assertEqual(panel_window.window_size((1536, 816)), (1180, 760))  # 1080p at 125 %

    def test_never_minimum_low(self):
        self.assertEqual(panel_window.window_size((800, 500)), panel_window.MINIMUM)


class TestNativeSelectors(unittest.TestCase):
    def test_selector_does_not_close_while_the_user_is_browsing(self):
        caps = []

        def run(cmd, **options):
            caps.append(options.get("timeout"))
            raise OSError("sin selector en la prueba")

        with patch.object(dialogs.subprocess, "run", side_effect=run):
            self.assertEqual(dialogs.select_native_files(), [])
            self.assertIsNone(dialogs.select_native_folder())
        self.assertEqual(len(caps), 2)
        for cap in caps:
            self.assertTrue(cap is None or cap >= 600, cap)


class TestLaunchWebWindow(unittest.TestCase):
    def test_launch_web_window_falls_back_to_the_browser_without_pywebview(self):
        opened = []
        with patch.object(dialogs, "focus_existing_window", return_value=False), \
                patch.object(dialogs, "_recently_launched", return_value=False), \
                patch.object(dialogs, "_mark_launch"), \
                patch.object(panel_window.PanelLauncher, "show", return_value=False), \
                patch.object(dialogs, "_open_in_browser", side_effect=lambda url: opened.append(url) or True), \
                patch.object(local_secret, "read", return_value=""):
            self.assertTrue(dialogs.launch_web_window(8765))
        self.assertEqual(opened, ["http://127.0.0.1:8765/app"])

    def test_launch_web_window_uses_own_window(self):
        with patch.object(dialogs, "focus_existing_window", return_value=False), \
                patch.object(dialogs, "_recently_launched", return_value=False), \
                patch.object(dialogs, "_mark_launch"), \
                patch.object(panel_window.PanelLauncher, "show", return_value=True), \
                patch.object(dialogs, "_open_in_browser") as browser:
            self.assertTrue(dialogs.launch_web_window(8765))
        browser.assert_not_called()



@unittest.skipUnless(sys.platform.startswith("linux"), "Solo Linux")
@unittest.skipUnless(importlib.util.find_spec("gi"), "Needs PyGObject (GTK)")
class ApiLinuxTests(unittest.TestCase):
    def test_dragged_delivers_once_and_as_paths(self):
        api = panel_window.ApiLinux()

        class Data:
            def get_uris(self):
                return ["file:///home/ana/Fotos/foto%20uno.jpg", "file:///home/ana/Fotos"]

        api._on_receive_drag(None, None, 0, 0, Data(), 0, 0)
        self.assertEqual(api.last_drag(), ["/home/ana/Fotos/foto uno.jpg", "/home/ana/Fotos"])
        self.assertEqual(api.last_drag(), [], "un arrastre viejo no se reusa en el siguiente")


if __name__ == "__main__":
    unittest.main()
