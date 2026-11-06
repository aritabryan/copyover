import unittest
import network


class AdapterDescriptionTests(unittest.TestCase):
    def test_generic_alias_does_not_hide_virtualbox_adapter(self):
        adapters = network.json_interfaces([
            {'InterfaceAlias': 'Ethernet 2', 'IPAddress': '192.168.56.1', 'PrefixLength': 24,
             'InterfaceDescription': 'VirtualBox Host-Only Ethernet Adapter'},
            {'InterfaceAlias': 'Ethernet', 'IPAddress': '192.168.0.15', 'PrefixLength': 24,
             'InterfaceDescription': 'Realtek PCIe GbE Family Controller'},
        ])
        self.assertTrue(adapters[0].virtual)
        self.assertFalse(adapters[1].virtual)
        self.assertEqual([a.ip for a in adapters if not a.virtual], ['192.168.0.15'])

    def test_descriptions_filter_virtual_adapters_even_after_alias_rename(self):
        for description in ['VirtualBox Host-Only Ethernet Adapter', 'VMware Virtual Ethernet Adapter',
                            'Microsoft Hyper-V Network Adapter', 'Tailscale Tunnel',
                            'ZeroTier Virtual Port', 'WireGuard Tunnel', 'Npcap Loopback Adapter']:
            with self.subTest(description=description):
                adapter = network.json_interfaces({'InterfaceAlias': 'Ethernet 2', 'IPAddress': '192.168.56.1',
                    'PrefixLength': 24, 'InterfaceDescription': description})[0]
                self.assertTrue(adapter.virtual)
                self.assertEqual(network.own_ips([adapter]), [])

    def test_physical_and_wifi_direct_connections_remain_available(self):
        for description in ['Realtek PCIe GbE Family Controller', 'Intel(R) Wi-Fi 6 AX201',
                            'Realtek USB GbE Family Controller', 'Microsoft Wi-Fi Direct Virtual Adapter']:
            with self.subTest(description=description):
                adapter = network.json_interfaces({'InterfaceAlias': 'Ethernet 2', 'IPAddress': '192.168.137.1',
                    'PrefixLength': 24, 'InterfaceDescription': description})[0]
                self.assertFalse(adapter.virtual)
                self.assertEqual(network.own_ips([adapter]), ['192.168.137.1'])
