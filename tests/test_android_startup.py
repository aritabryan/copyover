"""Check permissions needed by jobs scheduled when Android starts the app."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ANDROID_NAME = "{http://schemas.android.com/apk/res/android}name"


class AndroidStartupPermissions(unittest.TestCase):
    def test_network_update_job_declares_its_required_permissions(self):
        manifest = ET.parse(ROOT / "android/app/src/main/AndroidManifest.xml").getroot()
        permissions = {item.get(ANDROID_NAME) for item in manifest.findall("uses-permission")}
        for permission in ("ACCESS_NETWORK_STATE", "INTERNET", "RECEIVE_BOOT_COMPLETED"):
            with self.subTest(permission=permission):
                self.assertIn("android.permission." + permission, permissions)
