"""Files written by versions before 1.0.7 (Spanish names and keys) are migrated on load, never lost."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import certificates
import configuration
import bridge
import local_secret


class OldConfigTests(unittest.TestCase):
    def test_preserve_clipboard_notification_choice(self):
        for enabled in (False, True):
            cfg = {"avisos_portapapeles": enabled}
            self.assertTrue(configuration.migrate_config(cfg))
            self.assertIs(cfg["clipboard_notifications"], enabled)
            self.assertNotIn("avisos_portapapeles", cfg)
    def test_old_keys_rename_and_save(self):
        old_value = {
            "puerto": 9000, "clave": "k", "nombre": "mi-pc", "id": "x" * 32, "carpeta_descargas": "D:/Bajadas",
            "conservar_dias": 7, "idioma": "es", "tema": "claro", "avisos": False, "nombre_celular": "Mi iPhone",
            "equipos": {"p": "t"}, "equipos_info": {"p": {"nombre": "Laptop", "alias": "L", "huella_ca": "ab"}},
            "dispositivos": {"d1": {"nombre": "iPhone de Ana", "tipo": "iphone", "clave_hash": "h", "origen": "qr",
                                    "vinculado": "2026-10-01 10:00", "nombre_fijado": True, "ultima_ip": "10.0.0.9",
                                    "usado": True, "nombre_equipo": "Ana", "ultimo_uso": {"accion": "Envio",
                                                                                         "marca": "2026-10-02 10:00"}}},
            "dispositivos_apple": {"10.0.0.8": {"nombre": "Mi iPad", "tipo": "ipad"}},
        }
        with tempfile.TemporaryDirectory() as td, patch.object(configuration, "CONFIG", Path(td) / "config.json"):
            configuration.CONFIG.write_text(json.dumps(old_value), encoding="utf-8")
            cfg = configuration.load_config()
            in_disk = json.loads(configuration.CONFIG.read_text(encoding="utf-8"))
        for data in (cfg, in_disk):
            self.assertEqual(data["port"], 9000)
            self.assertEqual(data["key"], "k")
            self.assertEqual(data["name"], "mi-pc")
            self.assertEqual(data["download_folder"], "D:/Bajadas")
            self.assertEqual(data["keep_days"], 7)
            self.assertEqual(data["language"], "es")
            self.assertEqual(data["theme"], "light")
            self.assertIs(data["notifications"], False)
            self.assertEqual(data["phone_name"], "Mi iPhone")
            self.assertEqual(data["pcs"], {"p": "t"})
            self.assertEqual(data["pcs_info"]["p"], {"name": "Laptop", "alias": "L", "ca_fingerprint": "ab"})
            device = data["devices"]["d1"]
            self.assertEqual((device["name"], device["type"], device["key_hash"], device["origin"]), ("iPhone de Ana", "iphone", "h", "qr"))
            self.assertEqual((device["linked_at"], device["last_ip"], device["device_name"]), ("2026-10-01 10:00", "10.0.0.9", "Ana"))
            self.assertTrue(device["name_locked"] and device["used"])
            self.assertEqual(device["last_use"], {"action": "Sent", "time": "2026-10-02 10:00"})
            self.assertEqual(data["apple_devices"]["10.0.0.8"], {"name": "Mi iPad", "type": "ipad"})
            for old in ("puerto", "clave", "nombre", "dispositivos", "equipos", "carpeta_descargas"):
                self.assertNotIn(old, data)

    def test_new_key_already_present_wins(self):
        cfg = {"port": 1, "puerto": 2}
        self.assertTrue(configuration.migrate_config(cfg))
        self.assertEqual(cfg, {"port": 1})

    def test_up_to_date_config_does_not_change(self):
        cfg = {"port": 1, "key": "k", "devices": {"a": {"name": "x"}}}
        self.assertFalse(configuration.migrate_config(cfg))


class OldHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for patcher in (patch.object(bridge, "HISTORY", self.base / "history.json"),
                       patch.dict(bridge.STATE, {"historial": [], "historial_persistente": False})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_json_history_passes_to_json_history_with_new_keys(self):
        photo = self.base / "foto.jpg"
        photo.write_bytes(b"x")
        old = [{"id": "a1", "hora": "10:00", "fecha": "01/10", "momento": 1.0, "tipo": "recepcion",
                  "direccion": "recepcion", "origen": "Mi iPhone", "detalle": "foto.jpg", "nombre": "foto.jpg",
                  "tamano": 1, "tamano_texto": "1 B", "ruta": str(photo), "fijado": True, "enlace": ""}]
        (self.base / "historial.json").write_text(json.dumps(old), encoding="utf-8")
        bridge.load_history()
        self.assertFalse((self.base / "historial.json").exists())
        for entries in (bridge.STATE["history"], json.loads((self.base / "history.json").read_text(encoding="utf-8"))):
            entry = entries[0]
            self.assertEqual(entry["time"], "10:00")
            self.assertEqual(entry["origin"], "Mi iPhone")
            self.assertEqual(entry["detail"], "foto.jpg")
            self.assertEqual(entry["path"], str(photo))
            self.assertTrue(entry["pinned"])
            self.assertEqual(entry["size_text"], "1 B")
            self.assertNotIn("ruta", entry)

    def test_paths_inside_old_folder_already_moved_fix_on_load(self):
        new_value = self.base / "From iPhone"
        new_value.mkdir()
        (new_value / "foto.jpg").write_bytes(b"x")
        old = [{"id": "a1", "ruta": str(self.base / "Desde iPhone" / "foto.jpg"), "detalle": "foto.jpg"}]
        (self.base / "history.json").write_text(json.dumps(old), encoding="utf-8")
        bridge.load_history()
        self.assertEqual(bridge.STATE["history"][0]["path"], str(new_value / "foto.jpg"))

    def test_old_folders_stay_with_their_files_and_history(self):
        for name in ("Desde otra PC", "From iPhone"):
            (self.base / name).mkdir()
            (self.base / name / "a.txt").write_bytes(b"x")
        path = str(self.base / "From iPhone" / "a.txt")
        bridge.STATE["history"] = [{"id": "a1", "path": path}]
        with patch.object(bridge, "load_config", return_value={"download_folder": str(self.base)}):
            folder = bridge.get_downloads_folder("p2p")
        self.assertEqual(folder, self.base / "From CopyOver" / "From another PC")
        self.assertTrue((self.base / "Desde otra PC" / "a.txt").exists())
        self.assertEqual(bridge.STATE["history"][0]["path"], path)

class OldFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_log_bridge_and_copies_pass_to_log_copyover(self):
        for name in ("puente.log", "puente.log.1"):
            (self.base / name).write_text(name, encoding="utf-8")
        with patch.object(bridge, "LOG", self.base / "copyover.log"):
            handlers = bridge.log_handlers()
        for handler in handlers:
            handler.close()
        self.assertEqual((self.base / "copyover.log").read_text(encoding="utf-8"), "puente.log")
        self.assertEqual((self.base / "copyover.log.1").read_text(encoding="utf-8"), "puente.log.1")
        self.assertFalse((self.base / "puente.log").exists())

    def test_old_server_certificates_rename(self):
        (self.base / "servidor.pem").write_text("cert", encoding="utf-8")
        (self.base / "servidor.key").write_text("clave", encoding="utf-8")
        certificates.Certificates(folder=self.base)
        self.assertEqual((self.base / "server.pem").read_text(encoding="utf-8"), "cert")
        self.assertEqual((self.base / "server.key").read_text(encoding="utf-8"), "clave")
        self.assertFalse((self.base / "servidor.pem").exists())

    def test_certificates_folder_passes_to_certificates(self):
        (self.base / "certificados").mkdir()
        (self.base / "certificados" / "ca.pem").write_text("ca", encoding="utf-8")
        with patch.object(certificates, "FOLDER", self.base / "certificates"):
            certs = certificates.Certificates()
        self.assertEqual(certs.folder, self.base / "certificates")
        self.assertEqual((self.base / "certificates" / "ca.pem").read_text(encoding="utf-8"), "ca")

    def test_old_copyover_secret_still_reads(self):
        with patch.object(local_secret, "path", return_value=self.base / "local-secret"), \
                patch.dict(os.environ, {local_secret.VARIABLE: ""}):
            (self.base / "secreto-local").write_text("viejo", encoding="ascii")
            self.assertEqual(local_secret.read(), "viejo")
            local_secret.create()
            self.assertFalse((self.base / "secreto-local").exists())
            self.assertNotEqual(local_secret.read(), "viejo")

    def test_does_not_overwrite_a_new_file_that_exists(self):
        (self.base / "viejo").write_text("v", encoding="utf-8")
        (self.base / "nuevo").write_text("n", encoding="utf-8")
        self.assertEqual(configuration.move_if_missing(self.base / "viejo", self.base / "nuevo"), self.base / "nuevo")
        self.assertEqual((self.base / "nuevo").read_text(encoding="utf-8"), "n")
        self.assertTrue((self.base / "viejo").exists())


if __name__ == "__main__":
    unittest.main()
