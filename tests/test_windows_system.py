"""Windows install (shortcuts, right-click menu, firewall), without touching the real registry or firewall."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import i18n


class WindowsInstallerProtocolTests(unittest.TestCase):
    def test_notification_protocol_is_independent_of_optional_startup(self):
        installer = (Path(__file__).resolve().parents[1] / "installer.iss").read_text(encoding="utf-8")
        registry = installer.split("[Registry]\n", 1)[1].split("\n[", 1)[0]
        entries = [line for line in registry.splitlines() if line.startswith("Root:")]
        self.assertEqual(len(entries), 3)
        for entry in entries:
            self.assertTrue(entry.startswith('Root: HKCU; Subkey: "Software\\Classes\\copyover-notice'))
            self.assertNotIn("Tasks:", entry)
        self.assertIn('ValueName: "URL Protocol"; ValueData: ""', registry)
        self.assertIn('ValueData: """{app}\\CopyOver.exe"" --notification ""%1"""', registry)
        self.assertIn("ChangesAssociations=yes", installer)


@unittest.skipUnless(sys.platform == "win32", "Solo Windows")
class WindowsInstallationTests(unittest.TestCase):
    def setUp(self):
        import windows_system

        self.so = windows_system
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.startup, self.programs, self.sendto = base / "Startup", base / "Programs", base / "SendTo"
        for folder in (self.startup, self.programs, self.sendto):
            folder.mkdir()
        self.winreg = MagicMock()
        self.patches = [
            patch.object(windows_system, "startup_folder", return_value=self.startup),
            patch.object(windows_system, "programs_folder", return_value=self.programs),
            patch.object(windows_system, "folder_send_to", return_value=self.sendto),
            patch.object(windows_system, "_create_shortcut", side_effect=lambda lnk, *_: Path(lnk).write_text("lnk")),
            patch.object(windows_system, "shell32", MagicMock()),
            patch("tray.ensure_ico", return_value=base / "puente.ico"),
            patch.dict(sys.modules, {"winreg": self.winreg}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        i18n.pin("es")
        self.tmp.cleanup()

    def test_start_with_windows_creates_what_promises_readme(self):
        self.so.enable_startup("C:/x/bridge.py")
        self.assertTrue((self.startup / "CopyOver.lnk").exists())
        self.assertTrue((self.programs / "CopyOver.lnk").exists(), "acceso del menú Inicio")
        self.assertTrue((self.sendto / "Enviar con CopyOver.lnk").exists(), "«Enviar a»")
        keys = [c.args[1] for c in self.winreg.CreateKey.call_args_list]
        self.assertIn(self.so.MENU_KEY, keys, "right-click")
        self.so.disable_startup()
        self.assertFalse((self.startup / "CopyOver.lnk").exists())
        self.assertTrue((self.programs / "CopyOver.lnk").exists(), "desactivar solo quita el arranque")
        self.assertTrue((self.sendto / "Enviar con CopyOver.lnk").exists())

    def test_start_with_windows_leaves_the_firewall_alone(self):
        with patch.object(self.so, "_allow_in_firewall") as firewall:
            self.so.enable_startup("C:/x/bridge.py")
        firewall.assert_not_called()

    def test_notice_protocol_registers_quoted_command_and_refreshes_shell(self):
        with patch.object(self.so, "_registered_notice_protocol", False), \
                patch.object(self.so.bundle, "command", return_value=["C:/Program Files/CopyOver.exe"]):
            self.assertTrue(self.so._register_notice_protocol())
            self.assertTrue(self.so._register_notice_protocol())
        values = [c.args[1:] for c in self.winreg.SetValueEx.call_args_list]
        self.assertIn(("URL Protocol", 0, self.winreg.REG_SZ, ""), values)
        self.assertIn(("", 0, self.winreg.REG_SZ,
                       '"C:/Program Files/CopyOver.exe" --notification "%1"'), values)
        self.so.shell32.SHChangeNotify.assert_called_once_with(0x08000000, 0, None, None)

    def test_install_registers_notice_protocol_without_waiting_for_a_file(self):
        with patch.object(self.so, "_register_notice_protocol", return_value=True) as register, \
                patch.object(self.so, "_allow_in_firewall"):
            self.so.install("C:/x/bridge.py")
        register.assert_called_once()

    def test_protocol_registration_retries_after_registry_failure(self):
        self.winreg.CreateKey.side_effect = [OSError("registry unavailable"), MagicMock(), MagicMock()]
        with patch.object(self.so, "_registered_notice_protocol", False), \
                self.assertLogs(level="ERROR"):
            self.assertFalse(self.so._register_notice_protocol())
            self.assertTrue(self.so._register_notice_protocol())
        self.so.shell32.SHChangeNotify.assert_called_once()

    def test_access_send_to_goes_in_copyover_language(self):
        i18n.pin("en")
        (self.sendto / "Enviar con CopyOver.lnk").write_text("viejo")
        self.so.install_context_menu("C:/x/bridge.py")
        self.assertEqual(sorted(p.name for p in self.sendto.iterdir()), ["Send with CopyOver.lnk"])
        self.so.remove_context_menu()
        self.assertEqual(list(self.sendto.iterdir()), [])

    def test_firewall_checks_with_pc_real_ip(self):
        import diagnostics

        with patch("network.ip_local", return_value="192.168.50.10"), \
                patch.object(diagnostics, "check", return_value=[]) as check, \
                patch.object(diagnostics, "fix") as fix:
            self.so._allow_in_firewall()
        self.assertEqual(check.call_args.kwargs["ip"], "192.168.50.10")
        fix.assert_not_called()


if __name__ == "__main__":
    unittest.main()
