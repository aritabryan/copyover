import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class TrayActionTests(unittest.TestCase):
    def test_prepared_files_use_system_notification_without_modal_box(self):
        import tray_actions as actions
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok":true,"mensaje":"Listo"}'
        with patch.object(actions.urllib.request, 'urlopen', return_value=response), \
                patch.object(actions, 'notice') as modal, \
                patch(('windows_system' if sys.platform == 'win32' else 'linux_system') + '.notify') as notification:
            actions.send_phone(8765, lambda: [{'path': 'C:/documento.pdf'}])
        modal.assert_not_called()
        notification.assert_called_once()

    def test_selected_files_are_offered_to_android_without_browser(self):
        import tray_actions as actions
        selector, notice = Mock(return_value=[{'path': 'C:/photo.png'}]), Mock()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok":true,"message":"En Android, abre CopyOver"}'
        with patch.object(actions.urllib.request, 'urlopen', return_value=response) as send:
            actions.send_phone(8765, selector, notice, linked_types=lambda: ['android'])
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8765/app/send')
        self.assertEqual(json.loads(request.data), {'destination': 'android', 'paths': ['C:/photo.png']})
        self.assertIn('Android', notice.call_args.args[1])

    def test_destination_depends_from_linked_phones(self):
        import tray_actions as actions
        self.assertEqual(actions.phone_destination(['android']), 'android')
        self.assertEqual(actions.phone_destination(['android', 'android']), 'android')
        self.assertEqual(actions.phone_destination(['iphone']), 'ios')
        self.assertEqual(actions.phone_destination(['iphone', 'android']), 'ios')
        self.assertEqual(actions.phone_destination([]), 'ios')

    def test_cancel_does_not_send_or_notify(self):
        import tray_actions as actions
        notice = Mock()
        with patch.object(actions.urllib.request, 'urlopen') as send:
            actions.send_phone(8765, Mock(return_value=[]), notice)
        send.assert_not_called()
        notice.assert_not_called()

    def test_android_preparation_has_android_labels_and_no_guest_qr(self):
        import bridge
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'foto.png'
            file.write_bytes(b'photo')
            payload = json.dumps({'destination': 'android', 'paths': [str(file)]}).encode()
            handler = types.SimpleNamespace(path='/app/send', _panel_local=lambda: True,
                headers={'Content-Length': str(len(payload))}, rfile=io.BytesIO(payload), respond=Mock())
            state = {'pcs': [], 'apple_devices': {}}
            with patch.object(bridge, 'STATE', state), patch.object(bridge, 'load_config', return_value={}), patch.object(bridge.so, 'set_files'), patch.object(bridge, 'register_history'), patch.object(bridge, '_get_or_create_mobile_token') as qr:
                bridge.Handler._post_app(handler)
            self.assertEqual(handler.respond.call_args.args[0], 200)
            reply = json.loads(handler.respond.call_args.args[1])
            self.assertTrue(reply['is_android'])
            self.assertIn('Android', reply['message'])
            self.assertNotIn('iPhone', state['last'][1])
            qr.assert_not_called()

    def test_large_android_offer_is_prepared_without_shortcut_limit(self):
        import bridge
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'large.bin'
            file.write_bytes(b'large')
            payload = json.dumps({'destination': 'android', 'paths': [str(file)]}).encode()
            handler = types.SimpleNamespace(path='/app/send', _panel_local=lambda: True,
                headers={'Content-Length': str(len(payload))}, rfile=io.BytesIO(payload), respond=Mock())
            state = {'pcs': [], 'apple_devices': {}, 'history': [], 'phone_offer': {'id': 'keep'}}
            with patch.object(bridge, 'MAX_BYTES', 2), patch.object(bridge, 'STATE', state), patch.object(bridge, 'load_config', return_value={}):
                bridge.Handler._post_app(handler)
            self.assertEqual(handler.respond.call_args.args[0], 200)
            self.assertNotEqual(state['phone_offer']['id'], 'keep')
            self.assertEqual(state['prepared_files'], [file])

if __name__ == '__main__':
    unittest.main()
