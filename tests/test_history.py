"""The received history survives a restart and old items are removed according to «conservar_dias»."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cfg = {"keep_days": 0}
        self.patches = [
            patch.object(bridge, "HISTORY", self.base / "history.json"),
            patch("bridge.load_config", side_effect=lambda: self.cfg),
            patch("bridge.get_downloads_folder", side_effect=self._folder),
            # The real Recycle Bin isn't touched in the tests: it's simulated by deleting.
            patch.object(bridge.so, "to_trash", side_effect=self._to_trash),
        ]
        self.to_trash = []
        self.trash_fails = False
        for p in self.patches:
            p.start()
        bridge.STATE["history"] = []
        bridge.STATE["history_persistent"] = True  # this one does write, but to the temporary file

    def tearDown(self):
        for p in self.patches:
            p.stop()
        bridge.STATE["history"] = []
        bridge.STATE["history_persistent"] = False
        self.tmp.cleanup()

    def _folder(self, kind="iphone"):
        folder = self.base / {"iphone": "From iPhone", "p2p": "From another PC", "guests": "From guests"}.get(kind, "base")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _to_trash(self, paths):
        if self.trash_fails:
            return False
        for path in paths:
            self.to_trash.append(Path(path).name)
            Path(path).unlink()
        return True

    def _file(self, kind, name, days_ago):
        path = self._folder(kind) / name
        path.write_bytes(b"x")
        old = time.time() - days_ago * 86400
        os.utime(path, (old, old))
        return path

    def test_history_saves_and_re_load(self):
        bridge.register_history("received", "Mi iPhone", "Texto: hola", size=4)
        saved = json.loads(bridge.HISTORY.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["detail"], "Texto: hola")
        bridge.STATE["history"] = []
        bridge.load_history()
        self.assertEqual(bridge.STATE["history"][0]["detail"], "Texto: hola")

    def test_keep_forever_deletes_nothing(self):
        old = self._file("iphone", "foto.jpg", 400)
        self.assertEqual(bridge.clean_old_received(), 0)
        self.assertTrue(old.exists())

    def test_deletes_only_own_folders_old_stuff_and_takes_from_history(self):
        self.cfg["keep_days"] = 7
        old = self._file("iphone", "viejo.jpg", 10)
        recent = self._file("p2p", "reciente.pdf", 2)
        foreign = self.base / "ajeno.txt"
        foreign.write_text("mío")
        bridge.register_history("received", "Mi iPhone", "viejo.jpg", local_path=str(old))
        bridge.STATE["history"][0]["timestamp"] = time.time() - 10 * 86400
        bridge.register_history("received", "Mi iPhone", "reciente.pdf", local_path=str(recent))
        bridge.register_history("received", "Mi iPhone", "Texto: ayer")
        self.assertEqual(bridge.clean_old_received(), 1)
        self.assertEqual(self.to_trash, ["viejo.jpg"])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(foreign.exists(), "what CopyOver didn't save is left alone")
        self.assertEqual([e["detail"] for e in bridge.STATE["history"]], ["Texto: ayer", "reciente.pdf"])

    def test_cleanup_covers_folders_from_earlier_versions(self):
        self.cfg["keep_days"] = 7
        with patch("bridge.get_downloads_folder", side_effect=lambda kind="x": self.base if kind == "root" else self._folder(kind)):
            old_folder = self.base / "Desde iPhone"
            old_folder.mkdir()
            old = old_folder / "viejo.jpg"
            old.write_bytes(b"x")
            os.utime(old, (0, 0))
            self.assertEqual(bridge.clean_old_received(), 1)
        self.assertFalse(old.exists())

    def test_without_a_trash_deletes_nothing(self):
        self.cfg["keep_days"] = 7
        self.trash_fails = True
        old = self._file("iphone", "viejo.jpg", 10)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(bridge.clean_old_received(), 0)
        self.assertTrue(old.exists())

    def test_writes_from_once_without_leave_temporary(self):
        bridge.register_history("received", "Mi iPhone", "Texto: hola")
        self.assertTrue(bridge.HISTORY.exists())
        self.assertFalse(bridge.HISTORY.with_name("history.json.tmp").exists())

    def test_damaged_history_saves_separate_and_records(self):
        bridge.HISTORY.write_text('[{"id": "a", "deta', encoding="utf-8")
        with self.assertLogs(level="ERROR") as registry:
            bridge.load_history()
        self.assertEqual(bridge.STATE["history"], [])
        copy = bridge.HISTORY.with_name("history.json.bak")
        self.assertEqual(copy.read_text(encoding="utf-8"), '[{"id": "a", "deta')
        self.assertIn("history.json.bak", "\n".join(registry.output))

    def test_damaged_config_does_not_silently_lose_the_key(self):
        import configuration

        path = self.base / "config.json"
        path.write_text('{"clave": "secreta", "dispositivos": {', encoding="utf-8")
        with patch.object(configuration, "CONFIG", path), self.assertLogs(level="ERROR"):
            cfg = configuration.load_config()
        self.assertNotEqual(cfg.get("key"), "secreta")
        self.assertIn('"secreta"', (self.base / "config.json.bak").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["id"], cfg["id"])
        self.assertFalse((self.base / "config.json.tmp").exists())

    def test_setting_only_accepts_valid_options(self):
        self.cfg["keep_days"] = 5
        self.assertEqual(bridge.days_to_keep(), 0)
        self.cfg["keep_days"] = 30
        self.assertEqual(bridge.days_to_keep(), 30)


if __name__ == "__main__":
    unittest.main()
