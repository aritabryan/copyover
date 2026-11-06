import ast
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

HAS_PACKAGING = importlib.util.find_spec("packaging") is not None


def collection_mode() -> dict:
    """module_collection_mode as written in copyover.spec."""
    tree = ast.parse((ROOT / "copyover.spec").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "module_collection_mode":
            return ast.literal_eval(node.value)
    return {}


@unittest.skipUnless(HAS_PACKAGING, "packaging comes with requirements-dev.txt")
class ThirdPartyNoticesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import third_party_notices
        cls.module = third_party_notices
        cls.text = third_party_notices.generate()

    def test_each_lgpl_library_exits_with_license_and_goes_loose_in_exe(self):
        modes = collection_mode()
        for name in self.module.LGPL:
            with self.subTest(name):
                self.assertRegex(self.text, rf"(?m)^{name} \S+\nLicense: [^\n]*LGPL")
                self.assertEqual(modes.get(name), "py", "copyover.spec has to collect it as loose .py files")
                self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", self.text)

    @unittest.skipUnless(sys.platform == "win32", "only the Windows build is packaged; Linux installs with pip")
    def test_all_installed_lgpl_library_is_declared(self):
        declared = set(self.module.LGPL)
        for dist in self.module.included_packages(ROOT / "requirements.txt"):
            name = dist.metadata["Name"].lower()
            if "LGPL" in self.module.license_of(dist).upper():
                self.assertIn(name, declared, f"{name} is LGPL: add it to LGPL and to copyover.spec")

    def test_includes_dependencies_dependencies_and_python_license(self):
        self.assertRegex(self.text, r"(?m)^ifaddr \S+$")  # it comes with zeroconf, not from requirements.txt
        if sys.platform == "win32":  # only the Windows build is packaged, with python.org's LICENSE.txt
            self.assertIn("PYTHON SOFTWARE FOUNDATION LICENSE", self.text)


class BundledTests(unittest.TestCase):
    def test_bundle_generates_notices_together_to_exe(self):
        script = (ROOT / "build.ps1").read_text(encoding="utf-8")
        self.assertIn(r"python tools\third_party_notices.py 'dist\CopyOver\THIRD-PARTY-NOTICES.txt'", script)
        self.assertLess(script.index("THIRD-PARTY-NOTICES"), script.index("Compress-Archive"),
                        "it has to exist before the zip and the installer are built")


if __name__ == "__main__":
    unittest.main()
