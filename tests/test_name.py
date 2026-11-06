import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = r"\b(?:" + "bry" + "an|ar" + "ita)"  # built in pieces so it doesn't match itself
sys.path.insert(0, str(ROOT))

import configuration  # noqa: E402
import bridge  # noqa: E402
import version  # noqa: E402


class TestName(unittest.TestCase):
    def test_app_name_is_copyover(self):
        self.assertEqual(version.APP_NAME, "CopyOver")

    def test_no_module_mentions_airbridge(self):
        for file in ROOT.glob("*.py"):
            self.assertNotIn("AirBridge", file.read_text(encoding="utf-8"), file.name)

    def test_no_tracked_file_carries_a_personal_name(self):
        import subprocess
        try:
            files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                                      check=True).stdout.splitlines()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("sin git")
        for name in files:
            if name == "LICENSE" or not (ROOT / name).is_file():
                continue
            try:
                text = (ROOT / name).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line in text.splitlines():
                if version.REPOSITORY in line or "aritabryan/copyover" in line:
                    continue
                self.assertNotRegex(line.lower(), FORBIDDEN, f"{name}: {line.strip()[:80]}")

    def test_new_config_calls_as_pc_in_network(self):
        # Every new install used to be copyover-pc.local: two new PCs on the same network shared the host and
        # the phone could reach the wrong one.
        with tempfile.TemporaryDirectory() as td, patch.object(configuration, "CONFIG", Path(td) / "config.json"),                 patch("configuration.socket.gethostname", return_value="ESCRITORIO-Oficina 2"):
            self.assertEqual(bridge.load_config()["name"], "copyover-escritorio-oficina-2")

    def test_default_name_is_valid_dns_name(self):
        self.assertEqual(configuration.default_network_name("Portátil de Ñandú"), "copyover-portatil-de-nandu")
        self.assertEqual(configuration.default_network_name("pc__casa..2"), "copyover-pc-casa-2")
        length = configuration.default_network_name("x" * 100)
        self.assertLessEqual(len(length), 63)
        self.assertRegex(length, r"^[a-z0-9-]+$")
        for empty in ("", "***", "日本"):
            self.assertRegex(configuration.default_network_name(empty), r"^copyover-[0-9a-f]{4}$")

    def test_config_with_a_name_keeps_it(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text('{"puerto": 8765, "clave": "x", "id": "abc", "nombre": "copyover-pc"}', encoding="utf-8")
            with patch.object(configuration, "CONFIG", path),                     patch("configuration.socket.gethostname", return_value="other"):
                self.assertEqual(bridge.load_config()["name"], "copyover-pc")

    def test_other_pcs_see_pc_name_if_network_is_factory(self):
        import p2p
        with patch("p2p.socket.gethostname", return_value="ESCRITORIO-1"):
            for name in ("puente-pc", "copyover-pc", "copyover-escritorio-1"):
                info = p2p.create_service(name, 8765, "a" * 32, "192.168.0.10")
                self.assertEqual(info.properties[b"nombre"], b"ESCRITORIO-1", name)
            info = p2p.create_service("estudio", 8765, "a" * 32, "192.168.0.10")
            self.assertEqual(info.properties[b"nombre"], b"estudio")

    def test_old_config_without_a_name_gets_no_new_name(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text('{"puerto": 8765, "clave": "x", "id": "abc"}', encoding="utf-8")
            with patch.object(configuration, "CONFIG", path):
                self.assertNotIn("name", bridge.load_config())

    def test_saved_wifi_direct_network_with_old_name_renames(self):
        class Process:
            class stdout:
                lines = ["OK:wfd\n"]

                @classmethod
                def readline(cls):
                    return cls.lines.pop(0) if cls.lines else ""

            def terminate(self):
                pass

        cfg = {"port": 8765, "wifi_direct_ssid": "AirBridge-pc", "wifi_direct_password": "x" * 16}
        with patch.object(bridge.sys, "platform", "win32"), \
                patch.object(bridge, "load_config", return_value=cfg), \
                patch.object(bridge, "save_config"), \
                patch.object(bridge, "stop_wifi_direct"), \
                patch.object(bridge, "_get_or_create_mobile_token", return_value={"url": "u", "qr_svg": ""}), \
                patch.object(bridge.subprocess, "Popen", return_value=Process()), \
                patch("bridge.socket.gethostname", return_value="Mi-PC"):
            res = bridge.start_wifi_direct()
        self.assertEqual(res["ssid"], "CopyOver-MiPC")
        self.assertEqual(cfg["wifi_direct_ssid"], "CopyOver-MiPC")
        bridge.STATE["wifi_direct"] = {"active": False, "ssid": "", "key": "", "ip": "192.168.137.1"}
        bridge._PROC_WIFI_DIRECT = None

    def test_phones_save_in_from_copyover_and_pcs_and_guests_in_their_subfolder(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(bridge, "load_config", return_value={"download_folder": td}):
            received = Path(td) / "From CopyOver"
            self.assertEqual(bridge.get_downloads_folder("iphone"), received)
            self.assertEqual(bridge.get_downloads_folder("android"), received)
            self.assertEqual(bridge.get_downloads_folder(), received)
            self.assertEqual(bridge.get_downloads_folder("p2p"), received / "From another PC")
            self.assertEqual(bridge.get_downloads_folder("guests"), received / "From guests")
            self.assertEqual(bridge.get_downloads_folder("root"), Path(td))



if __name__ == "__main__":
    unittest.main()
