import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import linked_pcs as catalog
import bridge
import p2p


class PcsTests(unittest.TestCase):
    def test_management_is_not_blocked_by_a_send_in_progress(self):
        import tray_actions as actions
        with patch.object(actions.dialogs, 'manage_pcs', return_value=None) as dialog, patch.object(actions, 'notice'):
            actions._pc_lock.acquire()
            try:
                actions.manage_pcs(lambda: {'pcs': {}}, lambda: [], lambda *a: None)
            finally:
                actions._pc_lock.release()
        dialog.assert_called_once()

    def test_rediscover_pc_by_id_and_detect_revocation(self):
        cfg = {'pcs': {'a': 'token'}, 'port': 8765, 'key': 'local'}
        with patch.object(bridge, 'load_config', return_value=cfg), patch.object(bridge.p2p, 'query_local_pcs', return_value=[{'id': 'a', 'ip': 'new', 'port': 123}]):
            self.assertEqual(bridge.rediscover_pc('a'), ('new', 123))

    def test_listing_includes_disconnected_without_expose_keys(self):
        cfg = {'pcs': {'a': 'secreto', 'b': 'otro'},
               'pcs_info': {'a': {'name': 'Viejo', 'alias': 'Mi laptop'}}}
        result = catalog.list_all(cfg, [{'id': 'a', 'name': 'LAPTOP', 'ip': '10.0.0.2'}])
        self.assertEqual([e['name'] for e in result], ['Mi laptop', 'PC b'])
        self.assertTrue(result[0]['available'])
        self.assertFalse(result[1]['available'])
        self.assertNotIn('secreto', str(result))
        self.assertNotIn('otro', str(result))

    def test_alias_applies_without_change_network_identity(self):
        cfg = {'pcs_info': {'a': {'alias': 'Mi laptop'}}}
        peer = {'id': 'a', 'name': 'LAPTOP', 'ip': '10.0.0.2', 'port': 8765}
        result = catalog.with_alias(cfg, [peer])[0]
        self.assertEqual(result['name'], 'Mi laptop')
        self.assertEqual(result['id'], 'a')
        self.assertEqual(peer['name'], 'LAPTOP')

    def test_unlink_revokes_key_and_trust_in_memory_and_disk(self):
        cfg = {'pcs': {'a': 'token', 'b': 'other'}, 'trusted_pcs': ['a', 'b'],
               'pcs_info': {'a': {'alias': 'Laptop'}}}
        keys = p2p.PairingManager(cfg['pcs'])
        transfers = p2p.TransfersManager(Path(tempfile.gettempdir()), trusted_pcs={'a', 'b'})
        with patch.object(bridge, 'load_config', return_value=cfg), patch.object(bridge, 'save_config') as save, patch.object(bridge.Handler, 'pairing_manager', keys), patch.object(bridge.Handler, 'p2p_manager', transfers):
            bridge.modify_pc('a', 'unpair')
        self.assertFalse(keys.pc_valid_key('token'))
        self.assertTrue(keys.pc_valid_key('other'))
        self.assertNotIn('a', transfers.trusted_pcs)
        self.assertNotIn('a', save.call_args.args[0]['pcs'])
        self.assertNotIn('a', cfg['pcs_info'])

    def test_rename_persists_and_rejects_empty_names(self):
        cfg = {'pcs': {'a': 'token'}}
        with patch.object(bridge, 'load_config', return_value=cfg), patch.object(bridge, 'save_config'):
            bridge.modify_pc('a', 'rename', ' Laptop personal ')
            self.assertEqual(cfg['pcs_info']['a']['alias'], 'Laptop personal')
            with self.assertRaises(ValueError):
                bridge.modify_pc('a', 'rename', '  ')
