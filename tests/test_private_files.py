"""Secrets on disk and temporary files: only the current user can read them."""
import json
import logging
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import private_files
import certificates
import configuration
import bridge

POSIX = sys.platform != "win32"


def permissions(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


class BaseTemporary(unittest.TestCase):
    def setUp(self):
        self._base = tempfile.TemporaryDirectory()
        self.base = Path(self._base.name)
        self.addCleanup(self._base.cleanup)
        patcher = patch.object(private_files.tempfile, "gettempdir", return_value=str(self.base))
        patcher.start()
        self.addCleanup(patcher.stop)
        private_files._backup = None
        self.addCleanup(setattr, private_files, "_respaldo", None)


@unittest.skipUnless(POSIX, "the shared /tmp is a Linux problem")
class TestLinuxTemporaryFolder(BaseTemporary):
    def test_creates_folder_by_user_with_0700(self):
        folder = private_files.temporary_folder()
        self.assertEqual(folder, self.base / f"copyover-{os.getuid()}")
        self.assertEqual(permissions(folder), 0o700)
        self.assertEqual(os.lstat(folder).st_uid, os.getuid())

    def test_subfolders_remain_inside(self):
        sub = private_files.temporary_subfolder("puente_arrastrados")
        self.assertEqual(sub.parent, self.base / f"copyover-{os.getuid()}")
        self.assertTrue(sub.is_dir())
        self.assertEqual(bridge.temporary().parent, sub.parent)

    def test_closes_own_folder_too_many_open(self):
        folder = self.base / f"copyover-{os.getuid()}"
        folder.mkdir()
        os.chmod(folder, 0o777)
        self.assertEqual(private_files.temporary_folder(), folder)
        self.assertEqual(permissions(folder), 0o700)

    def test_does_not_follow_a_symlink_left_by_someone_else(self):
        destination = self.base / "de-otro"
        destination.mkdir()
        os.chmod(destination, 0o777)
        (self.base / f"copyover-{os.getuid()}").symlink_to(destination)
        with self.assertLogs(level="WARNING"):
            folder = private_files.temporary_folder()
        self.assertNotEqual(folder.resolve(), destination.resolve())
        self.assertFalse(folder.is_symlink())
        self.assertEqual(permissions(folder), 0o700)
        self.assertEqual(permissions(destination), 0o777)  # the other folder isn't touched
        # The fallback is reused while it lasts, not a new one per call.
        self.assertEqual(private_files.temporary_folder(), folder)

    def test_rejects_user_other_folder(self):
        other = os.getuid() + 1
        foreign = self.base / f"copyover-{other}"
        foreign.mkdir(mode=0o700)  # owned by the real uid, which isn't the one CopyOver «runs as» here
        with patch.object(private_files.os, "getuid", return_value=other), self.assertLogs(level="WARNING"):
            folder = private_files.temporary_folder()
        self.assertNotEqual(folder, foreign)
        self.assertTrue(folder.name.startswith("copyover-"))
        self.assertEqual(permissions(folder), 0o700)

    def test_rejects_a_file_using_the_folder_name(self):
        (self.base / f"copyover-{os.getuid()}").write_text("x")
        with self.assertLogs(level="WARNING"):
            folder = private_files.temporary_folder()
        self.assertTrue(folder.is_dir())

    def test_launch_notice_goes_in_own_folder(self):
        import dialogs
        dialogs._mark_launch()
        self.assertTrue((self.base / f"copyover-{os.getuid()}" / "copyover_launch.lock").exists())
        self.assertFalse((self.base / "airbridge_launch.lock").exists())


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class TestWindowsTemporaryFolder(BaseTemporary):
    def test_follows_being_temp(self):
        # %TEMP% is already per user on Windows: nothing changes.
        self.assertEqual(private_files.temporary_folder(), self.base)
        self.assertEqual(private_files.temporary_subfolder("puente_portapapeles"),
                         self.base / "puente_portapapeles")


class TestSecretsInDisk(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.folder = Path(self._folder.name)
        self.addCleanup(self._folder.cleanup)

    def test_write_json_saves_whole_content(self):
        path = self.folder / "config.json"
        configuration.write_json(path, {"key": "abc"}, indent=2)
        configuration.write_json(path, {"key": "def"}, indent=2)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"key": "def"})
        self.assertFalse(path.with_name("config.json.tmp").exists())

    @unittest.skipUnless(POSIX, "mode bits are a Linux thing")
    def test_config_remains_0600_even_if_before_outside_0644(self):
        path = self.folder / "config.json"
        path.write_text("{}")
        os.chmod(path, 0o644)
        extra = path.with_name("config.json.tmp")  # a .tmp left by a cut, with open permissions
        extra.write_text("{}")
        os.chmod(extra, 0o666)
        configuration.write_json(path, {"key": "abc"})
        self.assertEqual(permissions(path), 0o600)

    @unittest.skipUnless(POSIX, "mode bits are a Linux thing")
    def test_saved_config_is_not_readable_by_others(self):
        path = self.folder / "config.json"
        with patch.object(configuration, "CONFIG", path):
            configuration.save_config({"key": "abc"})
        self.assertEqual(permissions(path), 0o600)

    @unittest.skipUnless(POSIX, "mode bits are a Linux thing")
    def test_on_start_closes_folder_and_old_secrets(self):
        os.chmod(self.folder, 0o755)
        for name in ("config.json", "history.json", "copyover.log", "copyover.log.1"):
            (self.folder / name).write_text("x")
            os.chmod(self.folder / name, 0o644)
        (self.folder / "puente.ico").write_text("x")
        os.chmod(self.folder / "puente.ico", 0o644)
        private_files.close_data_folder(self.folder)
        self.assertEqual(permissions(self.folder), 0o700)
        for name in ("config.json", "history.json", "copyover.log", "copyover.log.1"):
            self.assertEqual(permissions(self.folder / name), 0o600, name)
        self.assertEqual(permissions(self.folder / "puente.ico"), 0o644)  # not a secret

    @unittest.skipUnless(POSIX, "mode bits are a Linux thing")
    def test_log_and_rotations_remain_0600(self):
        log = self.folder / "copyover.log"
        with patch.object(bridge, "LOG", log):
            handler = bridge.log_handlers()[0]
        self.addCleanup(handler.close)
        handler.maxBytes = 50
        registry = logging.getLogger("prueba-registro-privado")
        registry.propagate = False
        registry.addHandler(handler)
        self.addCleanup(registry.removeHandler, handler)
        for _ in range(5):
            registry.warning("una línea bastante larga para que el registro rote")
        self.assertEqual(permissions(log), 0o600)
        self.assertTrue((self.folder / "copyover.log.1").exists())
        self.assertEqual(permissions(self.folder / "copyover.log.1"), 0o600)

    def test_ca_key_warns_when_permissions_cannot_be_restricted(self):
        path = self.folder / "ca.key"
        path.write_bytes(b"x")
        if POSIX:
            with patch.object(certificates.os, "chmod", side_effect=PermissionError("no")), \
                    self.assertLogs(level="WARNING") as registry:
                self.assertFalse(certificates._restrict_permissions(path))
        else:
            import subprocess
            failure = subprocess.CompletedProcess([], 5, stdout=b"", stderr=b"Acceso denegado")
            with patch.object(certificates.subprocess, "run", return_value=failure), \
                    self.assertLogs(level="WARNING") as registry:
                self.assertFalse(certificates._restrict_permissions(path))
        self.assertIn("ca.key", "\n".join(registry.output))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_ca_key_notifies_without_user(self):
        with patch.dict(os.environ, {"USERNAME": ""}), self.assertLogs(level="WARNING"):
            self.assertFalse(certificates._restrict_permissions(self.folder / "ca.key"))


class TestTokensOutsideLog(unittest.TestCase):
    def test_covers_tokens_and_url_keys(self):
        line = ('10.0.0.9 "GET /invitado/estado?t=Tok3n_-x&tipo=iphone&_t=1700 HTTP/1.1" 200 -  '
                 'http://192.168.1.2:8765/configurar?t=OTRO /x?token=a&clave=b&key=c&k=d')
        covered = bridge.cover_secrets(line)
        for secret in ("Tok3n_-x", "OTRO", "=a", "=b", "=c", "=d"):
            self.assertNotIn(secret, covered)
        self.assertIn("?t=***&tipo=iphone&_t=1700", covered)  # _t is a cache buster, not a token

    def test_log_hides_the_token_and_still_recognizes_the_phone(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "copyover.log"
            with patch.object(bridge, "LOG", log):
                handler = bridge.log_handlers()[0]
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            registry = logging.getLogger("prueba-tokens")
            registry.setLevel(logging.INFO)
            registry.propagate = False
            registry.addHandler(handler)
            try:
                # Same call as Manejador.log_message.
                registry.info("%s %s", "10.0.0.9", '"GET /traer?t=SECRETO123 HTTP/1.1" 200 -')
                registry.info("Vinculación rechazada en %s: %r", "/invitado?t=SECRETO456", ValueError())
            finally:
                registry.removeHandler(handler)
                handler.close()
            text = log.read_text(encoding="utf-8")
            self.assertNotIn("SECRETO123", text)
            self.assertNotIn("SECRETO456", text)
            self.assertIn("/traer?t=***", text)

            cfg = {"devices": {"d1": {"name": "Mi iPhone", "type": "iphone", "last_ip": "10.0.0.9"}}}
            bridge.STATE["apple_devices"] = {}
            with patch.object(bridge, "LOG", log), patch.object(bridge, "is_this_pc", return_value=False):
                bridge.recover_last_phone_from_log(cfg)
            self.assertIn("Copied at", bridge.STATE["apple_devices"]["d1"]["last_event"])


if __name__ == "__main__":
    unittest.main()
