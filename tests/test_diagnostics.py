import json
import base64
import re
import shutil
import unittest.mock
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import diagnostics  # noqa: E402


def firewall(rules, networks=(1,)):
    return json.dumps({"rules": rules, "networks": [{"NetworkCategory": c} for c in networks]})


def run_with(output="{}", code=0):
    calls = []

    def run(command, **_):
        calls.append(command)
        return SimpleNamespace(returncode=code, stdout=output, stderr="")

    run.calls = calls
    return run


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _check(self, run, platform="win32", ip="192.168.1.5", announced=True, attributes=0):
        return diagnostics.check({"port": 8765}, run=run, platform=platform, folder=self.folder,
                                   ip=ip, announced=announced, exe="C:/x/pythonw.exe",
                                   attributes=lambda _: attributes)

    def _keys(self, problems):
        return [p["key"] for p in problems]

    def test_detects_firewall_without_rule(self):
        problems = self._check(run_with(firewall([])))
        self.assertIn("firewall", self._keys(problems))
        self.assertTrue(next(p for p in problems if p["key"] == "firewall")["fix"])

    def test_detects_lock_rule(self):
        rules = firewall([{"Action": 2, "Profile": 2}, {"Action": 4, "Profile": 2}])
        self.assertIn("firewall", self._keys(self._check(run_with(rules))))

    def test_firewall_with_rule_reports_nothing(self):
        self.assertEqual(self._check(run_with(firewall([{"Action": 2, "Profile": 6}]))), [])

    def test_public_only_rule_does_not_cover_a_private_network(self):
        self.assertIn("firewall", self._keys(self._check(run_with(firewall([{"Action": 2, "Profile": 4}], [1])))))

    def test_rule_only_for_public_network_serves_in_public_network(self):
        self.assertEqual(self._check(run_with(firewall([{"Action": 2, "Profile": 4}], [0]))), [])

    def test_only_the_network_the_phone_uses_counts(self):
        # Public Wi-Fi with permission and private Tailscale without it: the phone comes in through the Wi-Fi.
        networks = [{"NetworkCategory": 1, "Name": "Tailscale", "Ips": ["100.64.0.2"]},
                 {"NetworkCategory": 0, "Name": "Casa", "Ips": "192.168.1.5"}]
        output = json.dumps({"rules": [{"Action": 2, "Profile": 4}], "networks": networks})
        self.assertEqual(self._check(run_with(output)), [])

    def test_notice_names_network(self):
        networks = [{"NetworkCategory": 1, "Name": "Casa", "Ips": ["192.168.1.5"]}]
        output = json.dumps({"rules": [{"Action": 2, "Profile": 4}], "networks": networks})
        problem = next(p for p in self._check(run_with(output)) if p["key"] == "firewall")
        self.assertIn("«Casa»", problem["detail"])

    def test_block_in_another_profile_does_not_count(self):
        rules = [{"Action": 2, "Profile": 2}, {"Action": 4, "Profile": 4}]
        self.assertEqual(self._check(run_with(firewall(rules, [1]))), [])

    def test_detects_folder_only_in_cloud(self):
        problems = self._check(run_with(firewall([{"Action": 2}])), attributes=0x400000)
        self.assertEqual(self._keys(problems), ["onedrive"])

    def test_detects_without_network_and_without_announcement(self):
        problems = self._check(run_with(firewall([{"Action": 2}])), ip=None, announced=False)
        self.assertEqual(self._keys(problems), ["no_network", "mdns"])

    def test_check_returns_empty_list_when_all_well(self):
        self.assertEqual(self._check(run_with(firewall([{"Action": 2}]))), [])

    def test_powershell_failure_does_not_invent_problems(self):
        def fails(*_, **__):
            raise subprocess.TimeoutExpired("powershell", 10)
        self.assertEqual(self._check(fails), [])

    def test_fedora_with_firewalld_and_closed_port(self):
        def run(command, **_):
            if "--state" in command:
                return SimpleNamespace(returncode=0, stdout="running", stderr="")
            return SimpleNamespace(returncode=1, stdout="no", stderr="")
        with unittest.mock.patch("shutil.which", return_value="/usr/bin/firewall-cmd"):
            problems = self._check(run, platform="linux")
        self.assertEqual(self._keys(problems), ["firewall"])

    def test_fedora_without_firewalld_reports_nothing(self):
        with unittest.mock.patch("shutil.which", return_value=None):
            self.assertEqual(self._check(run_with(), platform="linux"), [])

    def test_fix_firewall_in_windows_elevates_powershell(self):
        run = run_with()
        self.assertTrue(diagnostics.fix("firewall", run=run, platform="win32", exe="C:/x/pythonw.exe",
                                             port=8765))
        command = " ".join(run.calls[0])
        self.assertIn("RunAs", command)

    def test_fix_unknown_key(self):
        self.assertFalse(diagnostics.fix("onedrive", run=run_with(), platform="win32", exe="x",
                                              port=8765))

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "Windows PowerShell required")
    def test_real_query_schema_recognizes_an_existing_allow_rule(self):
        stubs = """
function Get-NetFirewallApplicationFilter { [pscustomobject]@{Program='test.exe'} }
function Get-NetFirewallRule { process { [pscustomobject]@{Enabled='True';Direction='Inbound';Action=2;Profile=6} } }
function Get-NetConnectionProfile { [pscustomobject]@{NetworkCategory=1;Name='Test network';InterfaceIndex=123} }
function Get-NetIPAddress { [pscustomobject]@{IPAddress='192.168.1.5'} }
"""
        def run(command, **kwargs):
            return subprocess.run([*command[:-1], stubs + command[-1]], **kwargs)
        self.assertEqual(self._check(run), [])

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "Windows PowerShell required")
    def test_cancelled_elevation_is_not_reported_as_success(self):
        command = diagnostics._windows_elevated_command("C:/test/CopyOver.exe")
        stub = "function Start-Process { Write-Error 'Elevation cancelled' }; "
        result = subprocess.run([*command[:-1], stub + command[-1]], capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "Windows PowerShell required")
    def test_failure_to_remove_a_block_cannot_be_hidden_by_later_success(self):
        command = diagnostics._windows_elevated_command("C:/test/CopyOver.exe")
        encoded = re.search(r"'-EncodedCommand','([^']+)'", command[-1])[1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        stubs = """
function Get-NetFirewallApplicationFilter { [pscustomobject]@{Program='test.exe'} }
function Get-NetFirewallRule { process { [pscustomobject]@{Direction='Inbound';Action='Block'} } }
function Remove-NetFirewallRule { process { Write-Error 'Rule cannot be removed' } }
function New-NetFirewallRule { }
"""
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", stubs + script],
                                capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode, 0)



if __name__ == "__main__":
    unittest.main()
