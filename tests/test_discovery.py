"""mDNS name collisions without sending announcements on the real network."""
import unittest
from unittest.mock import Mock, patch

from zeroconf import NonUniqueNameException

import bridge
import p2p


class DiscoveryTests(unittest.TestCase):
    cfg = {"name": "puente-pc", "port": 8765, "id": "a" * 32}

    def test_pcs_with_default_name_have_distinct_dns_hosts_from_each_other_and_ios(self):
        desktop = p2p.create_service('puente-pc', 8765, 'a' * 32, '192.168.0.10')
        laptop = p2p.create_service('puente-pc', 8765, 'b' * 32, '192.168.0.12')
        self.assertNotEqual(desktop.server, laptop.server)
        self.assertNotEqual(desktop.name, laptop.name)
        self.assertNotEqual(desktop.server, 'puente-pc.local.')
        self.assertNotEqual(laptop.server, 'puente-pc.local.')
        self.assertEqual(desktop.parsed_addresses(), ['192.168.0.10'])
        self.assertEqual(laptop.parsed_addresses(), ['192.168.0.12'])

    def test_unique_service_keeps_friendly_name_in_discovered_list(self):
        info = p2p.create_service('Laptop', 8765, 'b' * 32, '192.168.0.12')
        client = Mock()
        client.get_service_info.return_value = info
        observer = p2p.PcsObserver('a' * 32)
        observer.add_service(client, info.type, info.name)
        self.assertEqual(observer.list_all()[0]['name'], 'Laptop')

    def test_duplicate_service_name_is_renamed_without_changing_ios_hostname(self):
        client = Mock()

        def register(info, allow_name_change=False):
            if not allow_name_change:
                raise NonUniqueNameException
            info.name = "Puente puente-pc-2._http._tcp.local."

        client.register_service.side_effect = register
        with patch("zeroconf.Zeroconf", return_value=client):
            registered, info = bridge.register_announcement(self.cfg, "192.168.0.10")
        self.assertIs(registered, client)
        self.assertEqual(info.server, "puente-pc.local.")
        self.assertEqual(info.port, 8765)
        self.assertEqual(info.properties[b"id"], b"a" * 32)
        self.assertTrue(info.name.startswith("Puente "))
        client.close.assert_not_called()

    def test_registration_failure_closes_client_before_retry(self):
        client = Mock()
        client.register_service.side_effect = OSError("network unavailable")
        with patch("zeroconf.Zeroconf", return_value=client):
            with self.assertRaises(OSError):
                bridge.register_announcement(self.cfg, "192.168.0.10")
        client.close.assert_called_once_with()
