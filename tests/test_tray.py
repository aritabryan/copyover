import types
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import tray


class Item:
    def __init__(self, text, action, **options):
        self.text, self.action, self.options = text, action, options


class Menu:
    SEPARATOR = object()
    def __init__(self, *items):
        self.items = items


class TrayTests(unittest.TestCase):
    def setUp(self):
        import i18n
        i18n.pin("es")

    def test_tray_has_native_phone_actions_without_browser_default(self):
        icon = Mock()
        backend = types.SimpleNamespace(Menu=Menu, MenuItem=Item, Icon=Mock(return_value=icon))
        send, pair, panel, pc_send, pc_pair = Mock(), Mock(), Mock(), Mock(), Mock()
        state = dict(announced=False, ip=None, last=None, pcs=[])
        with patch.dict(sys.modules, {'pystray': backend}), patch.object(tray, 'enable_app_id'), patch.object(tray, '_enable_pystray_double_click'), patch.object(tray, 'ensure_ico', return_value=Path('test.ico')), patch.object(tray, '_draw'):
            self.assertTrue(tray.show(state, 'PC', Mock(), Mock(), open_panel=panel,
                send_phone=send, link_iphone=Mock(), link_android=pair, send_pc=pc_send, connect_pc=pc_pair, restart=Mock(), manage_pcs=Mock()))
        menu = backend.Icon.call_args.args[3]
        items = [item for item in menu.items if isinstance(item, Item)]
        text = lambda item: item.text() if callable(item.text) else item.text
        labels = [text(item) for item in items]
        self.assertIn('Equipos vinculados…', labels)
        # Short menu: links and folders go in submenus.
        self.assertEqual([l for l in labels if l in ('Abrir panel', 'Enviar al celular…', 'Enviar a otra PC…', 'Vincular', 'Abrir carpeta', 'Ver registro', 'Reiniciar', 'Salir')],
                         ['Abrir panel', 'Enviar al celular…', 'Enviar a otra PC…', 'Vincular', 'Abrir carpeta', 'Ver registro', 'Reiniciar', 'Salir'])
        submenus = {text(item): [text(sub) for sub in item.action.items] for item in items if isinstance(item.action, Menu)}
        self.assertEqual(submenus['Vincular'], ['iPhone o iPad…', 'Android…', 'Otra PC…'])
        self.assertEqual(submenus['Abrir carpeta'], ['Recibido del celular'])
        self.assertFalse(any('Desde iPhone' in label for label in labels))
        self.assertFalse(any(item.options.get('default') for item in items))
        self.assertIn('Sin red: el celular no puede encontrar la PC', labels)
        # With no nearby PCs, transfer, new version or autostart, those lines aren't shown.
        hidden = [item for item in items if 'visible' in item.options and not item.options['visible']()]
        self.assertEqual(len(hidden), 4)
        self.assertIn('Iniciar con Windows', [text(item) for item in hidden])
        state['new_version'] = 'v9.0.0'
        version = [text(item) for item in items if 'visible' in item.options and item.options['visible']()]
        # Windows downloads the installer; Linux updates from the repository, so it only says there's one.
        expected = 'Descargar CopyOver v9.0.0…' if sys.platform == 'win32' else 'CopyOver v9.0.0 disponible…'
        self.assertIn(expected, version)
        panel.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Bandeja de Windows')
    def test_left_click_and_keyboard_open_menu_instead_of_default_action(self):
        original = Mock(return_value='menu')
        icon_type = type('FakeIcon', (), {'_on_notify': original})
        fake = types.SimpleNamespace(Icon=icon_type, win32=types.SimpleNamespace(WM_RBUTTONUP=0x205))
        with patch.dict(sys.modules, {'pystray._win32': fake}), patch('pystray._win32', fake, create=True), patch.object(tray, '_to_left_click', None):
            tray._enable_pystray_double_click()
            instance = Mock()
            for event in (0x202, 0x203, 1024, 1025):
                self.assertEqual(icon_type._on_notify(instance, 0, event), 'menu')
                self.assertEqual(original.call_args.args[-1], 0x205)
            instance.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Bandeja de Windows')
    def test_all_clicks_and_keyboard_open_same_menu(self):
        import threading
        original = Mock(return_value='menu')
        icon_type = type('FakeIcon', (), {'_on_notify': original})
        fake = types.SimpleNamespace(Icon=icon_type, win32=types.SimpleNamespace(WM_RBUTTONUP=0x205))
        opened = threading.Event()
        with patch.dict(sys.modules, {'pystray._win32': fake}), patch('pystray._win32', fake, create=True), \
             patch.object(tray, '_to_left_click', opened.set):
            tray._enable_pystray_double_click()
            for event in (0x202, 0x203, 0x205, 1024, 1025):
                opened.clear()
                with self.subTest(event=event):
                    icon_type._on_notify(Mock(), 0, event)
                    self.assertTrue(opened.wait(1))
            original.assert_not_called()

    def test_checkbox_start_with_windows(self):
        backend = types.SimpleNamespace(Menu=Menu, MenuItem=Item, Icon=Mock(return_value=Mock()))
        state = dict(announced=True, ip='192.168.1.5', last=None, pcs=[])
        active = {'value': False}
        change = Mock(side_effect=lambda v: active.__setitem__('value', v))
        with patch.dict(sys.modules, {'pystray': backend}), patch.object(tray, 'enable_app_id'),                 patch.object(tray, '_enable_pystray_double_click'),                 patch.object(tray, 'ensure_ico', return_value=Path('test.ico')), patch.object(tray, '_draw'):
            tray.show(state, 'PC', Mock(), Mock(), automatic_startup=(lambda: active['value'], change))
        menu = backend.Icon.call_args.args[3]
        checkbox = next(item for item in menu.items if isinstance(item, Item) and item.text == 'Iniciar con Windows')
        self.assertTrue(checkbox.options['visible']())
        self.assertFalse(checkbox.options['checked']())
        with patch.object(tray.threading, 'Thread', side_effect=lambda target, args, daemon: Mock(start=lambda: target(*args))):
            checkbox.action()
        change.assert_called_once_with(True)
        self.assertTrue(checkbox.options['checked']())

    def test_ico_has_windows_sizes(self):
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path = tray.ensure_ico(Path(folder) / 'logo.ico', force=True)
            with Image.open(path) as ico:
                sizes = {s[0] for s in ico.info['sizes']}
        self.assertTrue({16, 32, 48, 256} <= sizes, sizes)

    def test_logo_is_two_tables(self):
        logo = tray.draw_logo(size=64)

        def light(x, y):
            return logo.getpixel((x, y))[0] > 200

        # Outline of the back board (left edge), its inside, the gap between the two and the solid front
        # board.
        self.assertTrue(light(10, 30))
        self.assertFalse(light(16, 30))
        self.assertFalse(light(22, 40))
        self.assertTrue(light(39, 45))
        self.assertEqual(logo.getpixel((0, 0))[3], 0)  # transparent rounded corner


if __name__ == '__main__':
    unittest.main()
