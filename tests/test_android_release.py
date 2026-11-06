"""Exercise release signing setup with dummy values, without compiling an APK."""
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def step_script(name):
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    step = workflow.split("      - name: " + name + "\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


@unittest.skipUnless(POWERSHELL, "PowerShell is unavailable")
class AndroidReleaseSigning(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.env = dict(os.environ, RUNNER_TEMP=str(self.root), GITHUB_ENV=str(self.root / "environment"),
                        KEYSTORE_BASE64=base64.b64encode(b"dummy-keystore").decode(),
                        STORE_PASSWORD=" test\\password:=\nñ", KEY_ALIAS="test-alias", KEY_PASSWORD="test-key-password")

    def run_step(self, name):
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", step_script(name)],
                              env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=15, **options)

    def test_preserves_properties_and_keeps_values_out_of_output(self):
        result = self.run_step("Configure official Android signing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "copyover-release.jks").read_bytes(), b"dummy-keystore")
        values = {}
        for line in (self.root / "copyover-signing.properties").read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            units = re.findall(r"\\u([a-f0-9]{4})", value)
            self.assertEqual("".join("\\u" + unit for unit in units), value)
            values[key] = bytes.fromhex("".join(units)).decode("utf-16-be")
        self.assertEqual(values, {"storeFile": str(self.root / "copyover-release.jks"),
                                 "storePassword": self.env["STORE_PASSWORD"], "keyAlias": self.env["KEY_ALIAS"],
                                 "keyPassword": self.env["KEY_PASSWORD"]})
        output = result.stdout + result.stderr
        for name in ("KEYSTORE_BASE64", "STORE_PASSWORD", "KEY_ALIAS", "KEY_PASSWORD"):
            self.assertNotIn(self.env[name], output)
        self.assertIn("COPYOVER_SIGNING_PROPERTIES=", (self.root / "environment").read_text(encoding="utf-8-sig"))

    def test_missing_secret_fails_before_creating_signing_files(self):
        self.env["STORE_PASSWORD"] = ""
        result = self.run_step("Configure official Android signing")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "copyover-release.jks").exists())
        self.assertFalse((self.root / "copyover-signing.properties").exists())

    def test_cleanup_removes_both_files_and_also_runs_on_failure(self):
        result = self.run_step("Configure official Android signing")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_step("Remove Android signing files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "copyover-release.jks").exists())
        self.assertFalse((self.root / "copyover-signing.properties").exists())
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        cleanup = workflow.split("      - name: Remove Android signing files\n", 1)[1]
        self.assertIn("if: always()", cleanup)
