"""Behavior checks for the panel and quick preview JavaScript."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
class HistoryUI(unittest.TestCase):
    def run_js(self, script):
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_panel_rows_hide_text_sizes_and_offer_individual_deletion(self):
        source = (ROOT / "web/panel.js").read_text(encoding="utf-8")
        start = source.index("  const COPY_TITLE")
        end = source.index("  // Filters in the browser", start)
        self.run_js("const assert = require('assert/strict'); const NO_THUMBNAIL = new Set(); const esc = String;\n" + source[start:end] + """
for (const item of [
  {id:'text', detail:'Texto: hello', size_text:'77 KB'},
  {id:'link', detail:'Link', link:'https://example.invalid', size_text:'77 KB'},
  {id:'file', detail:'report.pdf', path:'report.pdf', size_text:'77 KB'}
]) {
  const row = historyRow(item);
  assert.equal(row.includes('77 KB'), !!item.path);
  assert.ok(row.includes('deleteHistoryItem(this.dataset.id)'));
  assert.ok(row.includes('aria-label="Delete from history"'));
  assert.ok(row.includes('event.stopPropagation(); deleteHistoryItem'));
}
""")

    def test_checkbox_changes_are_unsaved_settings(self):
        source = (ROOT / "web/panel.js").read_text(encoding="utf-8")
        start = source.index("  const SETTINGS_FIELDS")
        end = source.index("  function unsavedSettings", start)
        self.run_js("const assert = require('assert/strict'); const fields = {}; const document = {getElementById: id => fields[id]};\n" + source[start:end] + """
for (const id of SETTINGS_FIELDS) fields[id] = {type:'text', value:'value'};
fields['cfg-clipboard-notices'] = {type:'checkbox', value:'on', checked:false};
const before = settingsValues();
fields['cfg-clipboard-notices'].checked = true;
assert.notEqual(settingsValues(), before);
fields['cfg-clipboard-notices'].checked = false;
assert.equal(settingsValues(), before);
""")

    def test_delete_refreshes_missing_entries_and_reports_failures(self):
        source = (ROOT / "web/panel.js").read_text(encoding="utf-8")
        start = source.index("  async function deleteHistoryItem")
        end = source.index("  async function pinHistoryItem", start)
        self.run_js("""
const assert = require('assert/strict');
let response = {ok:true}; let refreshed = 0; let message = ''; let request;
const fetch = async (path, options) => {request = {path, ...options}; return response;};
const showToast = value => message = value;
const updateHistory = async force => {assert.equal(force, true); refreshed++;};
""" + source[start:end] + """
(async () => {
  await deleteHistoryItem('entry');
  assert.equal(request.path, '/app/delete-history');
  assert.equal(request.method, 'POST');
  assert.deepEqual(JSON.parse(request.body), {id:'entry'});
  assert.equal(refreshed, 1);
  response = {ok:false,status:404};
  await deleteHistoryItem('entry');
  assert.equal(refreshed, 2);
  response = {ok:false,status:500};
  await deleteHistoryItem('entry');
  assert.equal(refreshed, 2);
  assert.equal(message, 'Couldn’t delete the entry. Try again.');
})().catch(error => {console.error(error);process.exitCode=1;});
""")

    def test_preview_rows_hide_text_sizes_and_offer_individual_deletion(self):
        source = (ROOT / "web/preview.js").read_text(encoding="utf-8")
        source = source[:source.rindex("  refresh();\n  scheduleRefresh();")]
        self.run_js("""
const assert = require('assert/strict');
const nodes = {};
const document = {addEventListener(){}, getElementById(id){return nodes[id] ||= {};}};
const window = {addEventListener(){}};
let items = [];
const fetch = async path => ({ok:true,json:async()=>path.startsWith('/app/state') ? {anunciado:true} : items});
""" + source + """
(async () => {
  for (const item of [
    {id:'text', detail:'Texto: hello', size_text:'77 KB'},
    {id:'link', detail:'Link', link:'https://example.invalid', size_text:'77 KB'},
    {id:'file', detail:'report.pdf', path:'report.pdf', size_text:'77 KB'}
  ]) {
    items = [item];
    await refresh();
    assert.equal(nodes.list.innerHTML.includes('77 KB'), !!item.path);
    assert.ok(nodes.list.innerHTML.includes('deleteItem(this.dataset.id)'));
    assert.ok(nodes.list.innerHTML.includes('aria-label="Delete from history"'));
  }
})().catch(error => {console.error(error);process.exitCode=1;});
""")


if __name__ == "__main__":
    unittest.main()
