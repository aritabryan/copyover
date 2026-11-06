"""Feedback for firewall permission changes in the desktop panel."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
class FirewallUI(unittest.TestCase):
    def test_reports_success_failure_and_remaining_problem(self):
        source = (Path(__file__).resolve().parents[1] / "web/panel.js").read_text(encoding="utf-8")
        start = source.index("  async function fixProblem")
        end = source.index("  // SMIL", start)
        script = """
const assert = require('assert/strict');
let data, message, painted, fail = false, httpOk = true;
const fetch = async () => {
  if (fail) throw Error('offline');
  return {ok:httpOk, json:async()=>data};
};
const paintDiagnostics = value => painted = value;
const showToast = value => message = value;
""" + source[start:end] + """
(async () => {
  const button = {disabled:false,textContent:'Allow in firewall',dataset:{key:'firewall'}};
  data = {ok:true,problems:[]};
  await fixProblem(button);
  assert.equal(message, 'Firewall permission updated. Try connecting again.');
  assert.deepEqual(painted, []);
  data = {ok:false,problems:[]};
  await fixProblem(button);
  assert.equal(message, 'Couldn’t apply the firewall change. Accept the Windows prompt and try again.');
  data = {ok:true,problems:[{key:'firewall'}]};
  await fixProblem(button);
  assert.equal(message, 'The firewall still blocks CopyOver. Check the Windows rule or your network policy.');
  httpOk = false;
  await fixProblem(button);
  assert.equal(message, 'Couldn’t apply the firewall change. Accept the Windows prompt and try again.');
  fail = true;
  await fixProblem(button);
  assert.equal(message, 'Couldn’t contact CopyOver. Try again.');
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Allow in firewall');
})().catch(error => {console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
