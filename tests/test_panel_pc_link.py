"""Linking from a discovered PC card uses the existing native confirmation flow."""
import http.client
import json
import re
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch

import bridge
import i18n
import tray_actions
import web_ui


class PanelPcLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, data=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, json.dumps(data) if data is not None else None,
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            body = response.read().decode('utf-8')
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
            return response.status, body
        finally:
            connection.close()

    def test_link_rejects_a_pc_that_was_not_discovered(self):
        with patch.dict(bridge.STATE, pcs=[]), patch.object(tray_actions, 'connect_pc') as connect:
            status, data = self.request('POST', '/app/link-pc', {'id': 'unknown', 'ip': '192.168.1.20'})
        self.assertEqual(status, 404)
        self.assertFalse(data['ok'])
        connect.assert_not_called()

    def test_link_requires_the_local_panel_secret(self):
        with patch.object(self.server, 'local_secret', 'test-local-secret', create=True), \
                patch.object(tray_actions, 'connect_pc') as connect:
            status, _ = self.request('POST', '/app/link-pc', {'id': 'peer'})
        self.assertEqual(status, 403)
        connect.assert_not_called()

    def test_link_uses_discovered_address_and_existing_confirmation_flow(self):
        pc = {'id': 'peer', 'name': 'Nearby PC', 'ip': '192.168.1.20', 'port': 8765, 'pairing': '2'}
        cfg = {'id': 'local', 'pcs': {}}
        called = threading.Event()
        with patch.dict(bridge.STATE, pcs=[{**pc, 'ip': '192.168.1.21'}, pc]), patch.object(bridge, 'load_config', return_value=cfg), \
                patch.object(bridge, 'own_ca', return_value='local-ca'), \
                patch.object(tray_actions, 'connect_pc', side_effect=lambda *a, **kw: called.set()) as connect:
            status, data = self.request('POST', '/app/link-pc', {'id': 'peer', 'ip': '192.168.1.99', 'port': 1})
            self.assertTrue(called.wait(2))
        self.assertEqual(status, 202)
        self.assertTrue(data['ok'])
        connect.assert_called_once_with(cfg, [pc], bridge.save_pc_key, own_ca='local-ca', selected_pc=pc)

    def test_selected_card_skips_picker_and_keeps_existing_pairing_confirmation(self):
        pc = {'id': 'peer', 'name': 'Nearby PC'}
        cfg = {'pcs': {}}
        with patch.object(tray_actions, '_choose_pc') as choose, \
                patch.object(tray_actions, '_connect_selected', return_value=True) as connect:
            tray_actions.connect_pc(cfg, [pc], bridge.save_pc_key, notify=lambda *args: None,
                                    own_ca='local-ca', selected_pc=pc)
        choose.assert_not_called()
        self.assertEqual(connect.call_args.args[:3], (cfg, pc, bridge.save_pc_key))
        self.assertEqual(connect.call_args.kwargs, {'improve': True})

    def test_tray_link_keeps_picker_without_selected_card(self):
        pc = {'id': 'peer', 'name': 'Nearby PC'}
        with patch.object(tray_actions, '_choose_pc', return_value=pc) as choose, \
                patch.object(tray_actions, '_connect_selected', return_value=True):
            tray_actions.connect_pc({}, [pc], bridge.save_pc_key, notify=lambda *args: None)
        choose.assert_called_once()

    def test_link_from_card_still_requires_native_code_confirmation(self):
        pc = {'id': 'peer', 'name': 'Nearby PC', 'ip': '192.168.1.20', 'port': 8765, 'pairing': '2'}

        def pair(**kwargs):
            self.assertFalse(kwargs['confirm_code']('123456'))
            return False, '', '', ''

        with patch.object(tray_actions, '_choose_pc') as choose, \
                patch.object(tray_actions.p2p, 'pair_with_pc', side_effect=pair), \
                patch.object(tray_actions.dialogs, 'confirm_pairing', return_value=False) as confirm, \
                patch.object(bridge, 'save_pc_key') as save:
            tray_actions.connect_pc({'id': 'local'}, [pc], save, notify=lambda *args: None, selected_pc=pc)
        choose.assert_not_called()
        confirm.assert_called_once_with('Nearby PC', '123456')
        save.assert_not_called()

    def test_state_exposes_link_status_without_pc_credentials(self):
        pc = {'id': 'peer', 'name': 'Nearby PC', 'ip': '192.168.1.20', 'port': 8765}
        cfg = {'pcs': {'peer': 'private-token'}, 'pcs_info': {'peer': {'ca': 'private-ca'}}}
        with patch.dict(bridge.STATE, pcs=[pc]), patch.object(bridge, 'load_config', return_value=cfg):
            status, data = self.request('GET', '/app/state')
        self.assertEqual(status, 200)
        self.assertTrue(data['pcs'][0]['linked'])
        self.assertNotIn('private-token', json.dumps(data))
        self.assertNotIn('private-ca', json.dumps(data))

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute the panel renderer')
    def test_rendered_card_offers_link_or_linked_in_both_languages(self):
        for language, link, linked in [('en', 'Link', 'Linked'), ('es', 'Vincular', 'Vinculado')]:
            page = i18n.translate_html(web_ui.HTML_APP, language)
            script = re.search(r'<script>(.*?)</script>', page, re.S)[1]
            renderer = script[script.index('  function renderDevices()'):script.index('  async function sendToDevice(')]
            harness = """
const assert = require('assert');
const container = {innerHTML:''};
const document = {getElementById: () => container};
const window = {};
const ICONS = {laptop:'icon',desktop:'icon'};
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let currentState = {pcs:[{id:'peer',ip:'192.168.1.20',name:'Nearby PC',linked:false}]};
let selectedDestination = {};
function renderRadar(){} function paintDestination(){}
""" + renderer + "\nrenderDevices();\n"
            harness += f"assert(container.innerHTML.includes('>{link}</button>'));\n"
            harness += "assert(container.innerHTML.includes('linkPc(this)'));\n"
            harness += "assert(container.innerHTML.includes('data-id=\"peer\"'));\n"
            harness += "currentState.pcs[0].linked=true;renderDevices();\n"
            harness += f"assert(container.innerHTML.includes('{linked}'));\n"
            harness += "assert(!container.innerHTML.includes('linkPc(this)'));\n"
            result = subprocess.run(['node', '-e', harness], capture_output=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
