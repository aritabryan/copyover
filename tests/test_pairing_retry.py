import unittest
from unittest.mock import Mock
from pairing import PairingManager


class PairingRetryTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.approve = Mock(return_value=False)
        self.manager = PairingManager({'id': 'a' * 32, 'key': 'private-key'},
                                      self.approve, clock=lambda: self.now[0])

    def test_lost_initial_reply_can_be_retried_without_changing_session(self):
        first = self.manager.request('Phone', 'b' * 64, '192.0.2.10')
        again = self.manager.request('Phone', 'b' * 64, '192.0.2.10')
        self.assertEqual(first, again)
        self.assertEqual(len(self.manager.sessions), 1)
        self.approve.assert_not_called()
        self.assertNotIn('private-key', str(again))

    def test_same_phone_can_restart_an_unrevealed_attempt_after_cooldown(self):
        first = self.manager.request('Phone', 'b' * 64, '192.0.2.10')
        with self.assertRaises(ValueError):
            self.manager.request('Phone', 'c' * 64, '192.0.2.10')
        self.now[0] += 11
        second = self.manager.request('Phone', 'c' * 64, '192.0.2.10')
        self.assertNotEqual(first['session'], second['session'])
        self.assertNotIn(first['session'], self.manager.sessions)
        self.approve.assert_not_called()

    def test_another_phone_cannot_replace_an_active_attempt(self):
        first = self.manager.request('Phone', 'b' * 64, '192.0.2.10')
        self.now[0] += 11
        with self.assertRaises(ValueError):
            self.manager.request('Other', 'b' * 64, '192.0.2.11')
        self.assertEqual(self.manager.active_session, first['session'])

    def test_an_attempt_already_showing_a_code_cannot_be_replaced(self):
        first = self.manager.request('Phone', 'b' * 64, '192.0.2.10')
        self.manager.sessions[first['session']]['status'] = 'pending'
        self.now[0] += 11
        with self.assertRaises(ValueError):
            self.manager.request('Phone', 'c' * 64, '192.0.2.10')
        self.assertEqual(self.manager.active_session, first['session'])
