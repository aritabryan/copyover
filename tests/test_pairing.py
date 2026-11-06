import json
import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import configuration  # noqa: E402
import bridge


class PairingTests(unittest.TestCase):
    def test_existing_config_keeps_key_and_gets_persistent_id(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"port": 9000, "key": "existing-key", "name": "mi-pc"}))
            with patch.object(configuration, "CONFIG", config):
                first = bridge.load_config()
                second = bridge.load_config()
            self.assertIn("id", first)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(first["key"], "existing-key")
            self.assertEqual(first["port"], 9000)
            self.assertEqual(first["name"], "mi-pc")

    def test_qr_payload_and_mdns_have_same_identity_but_only_qr_has_key(self):
        import linking
        cfg = {"id": "a" * 32, "key": "secret-key", "port": 8765, "name": "mi-pc"}
        payload = json.loads(linking.qr_content(cfg, "192.168.1.2"))
        self.assertEqual(payload["server"], "http://mi-pc.local:8765")
        self.assertEqual(payload["fallback"], "http://192.168.1.2:8765")
        self.assertEqual(payload["key"], cfg["key"])
        self.assertEqual(payload["id"], cfg["id"])
        properties = linking.mdns_properties(cfg)
        self.assertEqual(properties, {"app": "puente-portapapeles", "id": cfg["id"]})
        self.assertNotIn(cfg["key"], str(properties))

    def test_qr_carries_ca_and_fingerprint_without_change_what_reads_old_app(self):
        import certificates
        import linking
        cfg = {"id": "a" * 32, "key": "secret-key", "port": 8765, "name": "mi-pc"}
        with tempfile.TemporaryDirectory() as directory:
            certs = certificates.Certificates(directory, "mi-pc", ips=lambda: ["192.168.1.2"])
            certs.ensure()
            ca = certs.ca_pem()
        without_ca = json.loads(linking.qr_content(cfg, "192.168.1.2"))
        with_ca = json.loads(linking.qr_content(cfg, "192.168.1.2", ca_pem=ca))
        self.assertEqual(with_ca["ca"], ca)
        self.assertEqual(with_ca["ca_sha256"], certs.ca_fingerprint())
        self.assertEqual(with_ca["ca_sha256"], certificates.pem_fingerprint(ca))
        # What an old app already read doesn't change: same version, same http addresses and same key.
        self.assertEqual({k: v for k, v in with_ca.items() if k not in ("ca", "ca_sha256")}, without_ca)
        self.assertEqual(with_ca["version"], 1)
        self.assertTrue(with_ca["server"].startswith("http://"))
        # Without a CA (PC without TLS) no empty fields are added.
        self.assertNotIn("ca", without_ca)
        self.assertNotIn("ca_sha256", without_ca)
        self.assertNotIn("ca", json.loads(linking.qr_content(cfg, "192.168.1.2", ca_pem="")))
        # Fits easily within the 4096-character limit the Android app applies.
        self.assertLess(len(linking.qr_content(cfg, "192.168.1.2", "k" * 32, ca)), 2048)

    def test_generated_qr_is_a_png(self):
        import linking
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vincular.png"
            linking.save_qr({"id": "b" * 32, "key": "secret", "port": 8765}, "192.168.1.2", path)
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_qr_does_not_overwrite_an_existing_file(self):
        import linking
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.png"
            path.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                linking.save_qr({"id": "b" * 32, "key": "secret", "port": 8765}, "192.168.1.2", path)
            self.assertEqual(path.read_bytes(), b"keep")

    @unittest.skipUnless(os.name == "posix", "Permisos POSIX; Windows usa ACL del usuario")
    def test_qr_is_private_on_linux(self):
        import linking
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vincular.png"
            linking.save_qr({"id": "b" * 32, "key": "secret", "port": 8765}, "192.168.1.2", path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_identity_proof_covers_id_nonce_and_bound_address(self):
        import linking
        nonce = "a" * 64
        expected = hmac.new(b"secret", f"puente-v1:pc-id:{nonce}:192.168.1.2".encode(), hashlib.sha256).hexdigest()
        proof = linking.identity_test("pc-id", "secret", nonce, "192.168.1.2")
        self.assertEqual(proof["proof"], expected)
        self.assertEqual(proof["id"], "pc-id")
        self.assertNotIn("secret", json.dumps(proof))
