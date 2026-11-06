"""A PC or phone not yet updated still uses the old field names: both directions must keep working."""
import unittest

import configuration
import p2p
from wire_keys import (OLD_ROUTES, OLD_WIRE_NAMES, from_wire, headers_to_wire, query_from_old, route_from_old,
                       route_to_old, to_wire)


class WireNames(unittest.TestCase):
    def test_incoming_offer_from_an_old_pc(self):
        offer = {
            "de": {"id": "abc", "nombre": "Old PC"},
            "archivos": [{"nombre": "a.txt", "tamano": 4, "sha256": "x"}],
            "reanudar": True,
        }
        self.assertEqual(from_wire(offer), {
            "sender": {"id": "abc", "name": "Old PC"},
            "files": [{"name": "a.txt", "size": 4, "sha256": "x"}],
            "resume": True,
        })

    def test_outgoing_data_carries_both_names(self):
        sent = to_wire({"files": [{"name": "a.txt", "size": 4}], "resume": True})
        self.assertEqual(sent["archivos"][0]["nombre"], "a.txt")
        self.assertEqual(sent["archivos"][0]["tamano"], 4)
        self.assertEqual(sent["files"][0]["name"], "a.txt")
        self.assertIs(sent["reanudar"], True)

    def test_the_current_name_wins(self):
        self.assertEqual(from_wire({"nombre": "old", "name": "new"}), {"name": "new"})

    def test_values_are_not_renamed(self):
        self.assertEqual(from_wire({"name": "nombre"}), {"name": "nombre"})
        self.assertEqual(to_wire(["nombre"]), ["nombre"])

    def test_round_trip_for_every_name(self):
        for new_name in OLD_WIRE_NAMES.values():
            self.assertEqual(from_wire(to_wire({new_name: 1})), {new_name: 1})

    def test_announcement_of_an_old_pc(self):
        decoded = p2p.decode_properties({b"id": b"abc", b"nombre": b"Old PC", b"chasis": b"desktop", b"empareja": b"2"})
        self.assertEqual(decoded, {"id": "abc", "name": "Old PC", "chassis": "desktop", "pairing": "2"})


class OldRoutes(unittest.TestCase):
    def test_every_old_route_goes_there_and_back(self):
        for old, new in OLD_ROUTES.items():
            self.assertEqual(route_from_old(old), new)
            self.assertEqual(route_to_old(new), old)

    def test_routes_with_an_id(self):
        for old, new in (("/p2p/subir/t1/3", "/p2p/upload/t1/3"),
                         ("/android/transferencias/t1/estado", "/android/transfers/t1/status"),
                         ("/android/transferencias/t1/cancelar", "/android/transfers/t1/cancel"),
                         ("/android/transferencias/t1/0", "/android/transfers/t1/0"),
                         ("/android/ofertas/o1/archivo", "/android/offers/o1/file"),
                         ("/android/ofertas/o1/confirmar", "/android/offers/o1/confirm")):
            self.assertEqual(route_from_old(old), new)
            self.assertEqual(route_to_old(new), old)

    def test_other_routes_are_left_alone(self):
        for path in ("/invitadox", "/app/state", "/ping", "/android/apk/abc"):
            self.assertEqual(route_from_old(path), path)
            self.assertEqual(route_to_old(path), path)

    def test_query_names_change_and_values_dont(self):
        self.assertEqual(query_from_old("t=a&tipo=reto&disp=b&reto=1"), "t=a&type=reto&device=b&challenge=1")
        self.assertEqual(query_from_old("t=tipo=1"), "t=tipo=1")

    def test_requests_carry_both_header_names(self):
        self.assertEqual(headers_to_wire({"X-Key": "k", "Content-Length": "0"}),
                         {"X-Key": "k", "X-Clave": "k", "Content-Length": "0"})


class ConfigFromOlderVersions(unittest.TestCase):
    def test_panel_theme_value(self):
        for old, new in (("sistema", "system"), ("oscuro", "dark"), ("claro", "light")):
            cfg = {"tema": old}
            self.assertTrue(configuration.migrate_config(cfg))
            self.assertEqual(cfg["theme"], new)
        self.assertFalse(configuration.migrate_config({"theme": "dark"}))

    def test_phone_clipboard_value(self):
        cfg = {"phone_clipboard": "imagenes_videos"}
        self.assertTrue(configuration.migrate_config(cfg))
        self.assertEqual(cfg["phone_clipboard"], "images_videos")

    def test_device_type_value(self):
        cfg = {"devices": {"a": {"tipo": "movil"}}, "apple_devices": {"1.2.3.4": {"type": "movil"}}}
        self.assertTrue(configuration.migrate_config(cfg))
        self.assertEqual(cfg["devices"]["a"]["type"], "mobile")
        self.assertEqual(cfg["apple_devices"]["1.2.3.4"]["type"], "mobile")

    def test_last_action_of_a_device(self):
        cfg = {"devices": {"a": {"last_use": {"action": "Traido", "time": "2026-10-02 10:00"}}}}
        self.assertTrue(configuration.migrate_config(cfg))
        self.assertEqual(cfg["devices"]["a"]["last_use"]["action"], "Got something")

    def test_apple_device_fields(self):
        cfg = {"apple_devices": {"1.2.3.4": {"nombre": "iPhone", "vinculado": True, "momento": 5.0}}}
        configuration.migrate_config(cfg)
        self.assertEqual(cfg["apple_devices"]["1.2.3.4"], {"name": "iPhone", "linked": True, "timestamp": 5.0})


if __name__ == "__main__":
    unittest.main()
