import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import tray_actions as actions
import bridge


class TrayPcTests(unittest.TestCase):
    pc = dict(id='peer-id', name='Laptop', host='LAPTOP', ip='192.168.0.12', port=9876)

    def test_second_pc_action_is_rejected_while_pairing_is_in_progress(self):
        with patch.object(actions.dialogs, 'choose_pc') as choose, patch.object(actions, 'notice') as notice:
            self.assertTrue(actions._pc_lock.acquire(blocking=False))
            try:
                actions.connect_pc({'pcs': {}}, [self.pc], Mock())
            finally:
                actions._pc_lock.release()
        choose.assert_not_called()
        notice.assert_called_once()

    def test_cancel_keeps_guard_until_remote_request_finishes(self):
        entered, finish = threading.Event(), threading.Event()
        def pending(**_):
            entered.set()
            finish.wait(2)
            return False, '123456', 'Rechazado', None
        cfg = dict(id='own', key='local', name='Desktop', pcs={})
        with patch.object(actions.p2p, 'pair_with_pc', side_effect=pending), patch.object(actions.dialogs, 'confirm_pairing', return_value=False), patch.object(actions.dialogs, 'choose_pc', return_value=self.pc) as choose, patch.object(actions, 'notice'):
            worker = threading.Thread(target=actions.connect_pc, args=(cfg, [self.pc], Mock(), Mock()))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                worker.join(0.1)
                actions.connect_pc(cfg, [self.pc], Mock(), Mock())
                self.assertEqual(choose.call_count, 1)
            finally:
                finish.set()
                worker.join(3)

    def test_selection_by_id_or_address_preserves_peer_id_port_and_pairing(self):
        for target in ('peer-id', '192.168.0.12', '192.168.0.12:9876', 'Laptop'):
            with patch.object(bridge.p2p, 'query_local_pcs', return_value=[self.pc]):
                result = bridge._resolve_send_destination({'port': 8765, 'key': 'local', 'id': 'own'}, target)
            self.assertEqual(result, self.pc)

    def test_pairing_requires_both_confirmation_and_success_before_saving_key(self):
        cfg = dict(id='own', key='local', name='Desktop', pcs={})
        save = Mock()
        with patch.object(actions.p2p, 'pair_with_pc', side_effect=self._pairing('PEM')) as pair, patch.object(actions.dialogs, 'confirm_pairing', return_value=True) as dialog:
            self.assertTrue(actions._connect_selected(cfg, self.pc, save, Mock(), own_ca='OWN'))
        dialog.assert_called_once_with('Laptop', '123456')
        self.assertEqual(pair.call_args.kwargs['my_ca_pem'], 'OWN')
        self.assertEqual(pair.call_args.kwargs['expected_id'], 'peer-id')
        save.assert_called_once_with('peer-id', 'peer-key', 'PEM')
        self.assertEqual(cfg['pcs']['peer-id'], 'peer-key')

    @staticmethod
    def _pairing(ca):
        def pair(confirm_code=None, **_):
            if not confirm_code('123456'):
                return False, '123456', 'Rechazado', None
            return True, '123456', 'peer-key', ca
        return pair

    def test_pairing_with_an_older_pc_warns_that_it_is_unencrypted(self):
        cfg = dict(id='own', key='local', name='Desktop', pcs={})
        save, notify = Mock(), Mock()
        with patch.object(actions.p2p, 'pair_with_pc', side_effect=self._pairing(None)), patch.object(actions.dialogs, 'confirm_pairing', return_value=True):
            self.assertTrue(actions._connect_selected(cfg, self.pc, save, notify))
        save.assert_called_once_with('peer-id', 'peer-key', None)
        self.assertEqual(notify.call_args.args[0], 'Conexión sin cifrar')

    def test_connect_upgrades_an_unencrypted_link_but_send_reuses_it(self):
        cfg = dict(id='own', key='local', name='Desktop', pcs={'peer-id': 'old'})
        with patch.object(actions.p2p, 'pair_with_pc', side_effect=self._pairing('PEM')) as pair, patch.object(actions.dialogs, 'confirm_pairing', return_value=True):
            self.assertTrue(actions._connect_selected(cfg, self.pc, Mock(), Mock()))
            pair.assert_not_called()
            self.assertTrue(actions._connect_selected(cfg, self.pc, Mock(), Mock(), improve=True))
            pair.assert_called_once()
            self.assertTrue(pair.call_args.kwargs['allow_unencrypted'])
            self.assertTrue(actions._connect_selected(cfg, self.pc, Mock(), Mock(), improve=True))
            pair.assert_called_once()  # already encrypted: not paired again

    def test_rejected_pairing_never_saves_credentials(self):
        cfg = dict(id='own', key='local', name='Desktop', pcs={})
        save = Mock()
        with patch.object(actions.p2p, 'pair_with_pc', side_effect=self._pairing('PEM')), patch.object(actions.dialogs, 'confirm_pairing', return_value=False):
            self.assertFalse(actions._connect_selected(cfg, self.pc, save, Mock()))
        save.assert_not_called()

    def test_send_selects_peer_and_uses_paired_identity_without_browser(self):
        cfg = dict(id='own', key='local', name='Desktop', pcs={'peer-id': 'paired-key'})
        send = Mock(return_value=0)
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'test.txt'
            file.write_text('hello')
            with patch.object(actions.dialogs, 'choose_pc', return_value=self.pc), patch.object(actions.dialogs, 'select_native_files', return_value=[{'path': str(file)}]):
                actions.send_pc(cfg, [self.pc], send, Mock(), Mock())
            send.assert_called_once_with(cfg, ['--to', 'peer-id', str(file)])

    def test_cancel_selection_does_not_pair_or_send(self):
        send, save = Mock(), Mock()
        with patch.object(actions.dialogs, 'choose_pc', return_value=None):
            actions.send_pc(dict(pcs={}), [self.pc], send, save, Mock())
        send.assert_not_called()
        save.assert_not_called()

    def test_saving_pair_key_updates_running_receiver_for_reverse_transfers(self):
        manager = actions.p2p.PairingManager()
        with patch.object(bridge, 'load_config', return_value={'pcs': {}}), patch.object(bridge, 'save_config'), patch.object(bridge.Handler, 'pairing_manager', manager):
            bridge.save_pc_key('peer-id', 'pair-key')
        self.assertTrue(manager.pc_valid_key('pair-key'))
