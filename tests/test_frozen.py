"""Packaged CopyOver (sys.frozen): everything is launched with the same CopyOver.exe and a mode."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dialogs  # noqa: E402
import bundle  # noqa: E402
import panel_window  # noqa: E402
import preview  # noqa: E402

EXE = "C:/Programas/CopyOver/CopyOver.exe"


class Frozen(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", EXE),
                        patch.object(sys, "_MEIPASS", "C:/Programas/CopyOver/_internal", create=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_executables_point_to_exe_when_is_frozen(self):
        command = preview.Launcher(8765)._command()
        self.assertEqual(command[:3], [EXE, "--preview", "8765"])

    def test_panel_launches_with_exe(self):
        command = panel_window.PanelLauncher(8765)._command()
        self.assertEqual(command[:3], [EXE, "--panel", "8765"])

    def test_dialogs_launch_with_exe(self):
        run = Mock(return_value=Mock(stdout="accept", returncode=0))
        with patch.object(dialogs.subprocess, "run", run):
            dialogs.request_confirmation({"name": "x"}, timeout=1)
        self.assertEqual(run.call_args.args[0][:3], [EXE, "--dialog", "confirm"])

    @unittest.skipUnless(sys.platform == "win32", "Solo Windows")
    def test_context_menu_uses_exe(self):
        import windows_system
        self.assertEqual(windows_system.context_menu_command("C:/x/bridge.py"), f'"{EXE}" --send "%1"')

    @unittest.skipUnless(sys.platform == "win32", "Solo Windows")
    def test_send_to_uses_exe_without_script_path(self):
        import windows_system
        self.assertEqual(windows_system.access_send_to("C:/x/bridge.py"), (EXE, "--send"))

    def test_data_together_to_exe_and_resources_inside_package(self):
        self.assertEqual(bundle.data_folder(), Path(EXE).resolve().parent)
        self.assertEqual(bundle.resource("helper.ps1"), Path("C:/Programas/CopyOver/_internal/helper.ps1"))


class FromCode(unittest.TestCase):
    def test_from_code_launches_each_script(self):
        command = bundle.command("panel", 8765, executable="pythonw")
        self.assertEqual(command[0], "pythonw")
        self.assertTrue(command[1].endswith("panel_window.py"))
        self.assertEqual(bundle.data_folder(), bundle.ROOT)

    def test_bridge_distributes_modes(self):
        self.assertEqual(bundle.MODES, {"--vista": "preview", "--panel": "panel", "--dialogo": "dialog", "--preview": "preview", "--dialog": "dialog"})


class OldNames(unittest.TestCase):
    def test_commands_in_spanish_follow_working(self):
        # Menus, notifications and installers written by 1.0.6 or earlier still call these.
        import bridge
        self.assertEqual(bridge.OLD_NAMES["--enviar"], "--send")
        self.assertEqual(bridge.OLD_NAMES["--aviso"], "--notification")
        self.assertEqual(bridge.OLD_NAMES["--instalar"], "--install")
        self.assertEqual(bridge.OLD_NAMES["--desinstalar"], "--uninstall")
        self.assertEqual(bundle.MODES["--vista"], "preview")
        self.assertEqual(bundle.MODES["--dialogo"], "dialog")


if __name__ == "__main__":
    unittest.main()
