"""The panel's /app/* routes: they only answer requests from this same PC.

It's a mixin of bridge.py's Manejador. Shared state (ESTADO, the config, the managers) is always read through
`bridge`, which is the same module that started (also when it runs as __main__) and the one the tests patch.
"""
import hmac
import json
import logging
import mimetypes
import re
import secrets
import socket
import threading
import time
from datetime import datetime
from functools import cache
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import updates
import dialogs
import devices
import links
import linked_pcs
import p2p
import i18n
import local_secret
from i18n import t

bridge = None  # set by bridge.py on import

# Routes that something outside the panel calls (a second CopyOver handing off to the running one, the tray,
# the preview window, the panel window's first URL). The Spanish names of 1.0.x keep working, so a running
# older or newer copy still understands them.
LEGACY_APP_ROUTES = {
    "/app/accion": "/app/action",
    "/app/enviar": "/app/send",
    "/app/entrar": "/app/enter",
    "/app/vista-previa": "/app/preview",
}


@cache
def _html_app() -> str:
    """The panel with the shortcut QRs drawn in: the links don't change, so they're drawn once."""
    import web_ui
    return web_ui.with_shortcuts_qr(web_ui.HTML_APP, bridge.qr_as_svg)


def devices_for_tray(cfg, nearby):
    """Linked phones and PCs for the quick view, connected or not: name and type only, never their keys."""
    phones = [{"name": d.get("name") or t("Mobile device"), "type": d.get("type", "mobile")}
              for d in cfg.get("devices", {}).values()]
    pcs = [{"name": pc["name"], "type": "pc"} for pc in linked_pcs.list_all(cfg, nearby)]
    return phones + pcs


def _with_exists(entry: dict) -> dict:
    """History images carry «existe»: false if the file is gone (moved or deleted), so the panel doesn't ask
    for a thumbnail that won't come."""
    path = entry.get("path")
    if not path or not (mimetypes.guess_type(str(path))[0] or "").startswith("image/"):
        return entry
    try:
        exists = Path(path).is_file()
    except OSError:
        exists = False
    return entry if exists else {**entry, "exists": False}


def crop(text: str, length: int) -> str:
    """The text up to `largo` characters, with «…» only if it was cut."""
    return text if len(text) <= length else text[:length].rstrip() + "…"


THEMES = ("system", "dark", "light")


def panel_theme(cfg: dict) -> str:
    """The panel was always dark, so that stays the default; «system» follows Windows."""
    theme = cfg.get("theme")
    return theme if theme in THEMES else "dark"


def _visible_origin(origin: str) -> str:
    """The origin stored in the history, in the interface language. The Spanish ones come from older versions."""
    if origin in ("Sent to phone", "Enviado al celular", "Portapapeles / iOS"):
        return t("Sent to phone")
    if origin in ("Sent to iPhone or iPad", "Enviado al iPhone o iPad", "Para iPhone / iPad"):
        return t("Sent to iPhone or iPad")
    if origin in ("Sent to Android", "Enviado a Android", "Para Android"):
        return t("Sent to Android")
    if origin == "Invitado Web":
        return t("Guest (web page)")
    guest = re.match(r"(?:Guest|Invitado) (\d+)(?!\d)", origin)
    if guest:  # guests are numbered; the name follows the language even after it changes
        return t("Guest {number}", number=guest.group(1)) + origin[guest.end():]
    for generic in ("Mobile device", "Dispositivo Móvil"):  # a phone that didn't give its name; the second is the old one
        if origin.startswith(generic):
            return t("Mobile device") + origin[len(generic):]
    return origin


def history_to_show(entries: list) -> list:
    """Copy of the history for the panel: origin and «N files» in the interface language."""
    visible_items = []
    for entry in entries:
        visible = {**entry, "origin": _visible_origin(str(entry.get("origin") or ""))}
        # Earlier versions added «...» to every text or note, even when it wasn't cut.
        old = re.fullmatch(r"(Texto|Nota): (.*)\.\.\.", str(entry.get("detail") or ""), flags=re.S)
        if old and entry.get("origin") in ("Portapapeles / iOS", "Invitado Web"):
            body = old[2]
            visible["detail"] = visible["name"] = f"{old[1]}: {body}" + ("…" if len(body) >= 40 else "")
        count = re.fullmatch(r"(\d+) (?:files|archivos)", str(entry.get("detail") or ""))
        if count:
            visible["detail"] = visible["name"] = t("{count} files", count=int(count[1]))
        visible_items.append(visible)
    return visible_items


# Image types the history can show as a thumbnail.
THUMBNAILS = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


class PanelPaths:
    def _respond_json_state(self):
        last = bridge.STATE.get("last")
        bridge.STATE["window_alive"] = time.time()
        show = bool(bridge.STATE.get("show_window"))
        bridge.STATE["show_window"] = False
        current_cfg = bridge.load_config()
        body = json.dumps({
            "pc_name": bridge.STATE.get("pc_name") or socket.gethostname(),
            "network_name": current_cfg.get("name", "puente-pc"),
            "ip": bridge.STATE.get("ip") or bridge.ip_local(),
            "port": current_cfg.get("port", 8765),
            "key": current_cfg.get("key", "") if self._is_localhost() else "",
            "announced": bool(bridge.STATE.get("announced")),
            "phone_device": bridge.STATE.get("phone_device"),
            "phone_ip": bridge.STATE.get("phone_ip"),
            "phone_name": devices.configured_name(current_cfg, "iphone"),
            "ipad_name": devices.configured_name(current_cfg, "ipad"),
            "download_folder": current_cfg.get("download_folder") or str(bridge.so.DOCUMENTS),
            # Only what's in memory: the config was loaded at startup, and reading it again here brought back
            # the device that was just removed (the X had to be clicked twice). «network»: same | other |
            # unknown (see bridge.device_network); «pc_network» is internal only.
            "apple_devices": [{**{k: v for k, v in d.items() if k != "pc_network"},
                                    "name": _visible_origin(d.get("name") or "") if d.get("guest") else d.get("name"),
                                    "guest": bool(d.get("guest")),
                                    "last_event": i18n.translate_event(d.get("last_event") or ""),
                                    **({"repeats": {**d["repeats"], "linked": i18n.translate_event(d["repeats"].get("linked") or "")}}
                                       if d.get("repeats") else {}),
                                    "network": bridge.device_network(d)}
                                   for d in bridge._dedupe_apple_devices(list(bridge.STATE.get("apple_devices", {}).values()))],
            "guests": [{**i, "name": _visible_origin(i["name"])} for i in bridge.active_guests()],
            "last": f"{last[0]:%H:%M} -> {last[1]}" if last else None,
            "pcs": bridge._dedupe_p2p_pcs(bridge.STATE.get("pcs", [])),
            "show_window": show,
            "web_app": True,
            "app_version": 2026110505,
            "version_app": 2026110505,  # a panel left open from before this name reads it and reloads
            "pc_chassis": p2p.chassis_type(),
            "transfer": bridge.STATE.get("transfer"),
            "phone_offer": bridge.STATE.get("phone_offer"),
            "wifi_direct": bridge.STATE.get("wifi_direct"),
            "welcome": bridge.open_panel_on_start(current_cfg),
            "new_version": bridge.STATE.get("new_version"),
            # Installed copy: the panel installs it with one click. Portable zip: it links to the releases page.
            "self_update": updates.self_updates(),
            "releases_page": updates.update_page(),
            # Only the panel (?panel=1) takes it: when the quick view read it, the panel never opened the
            # modal.
            "open_modal": bridge.STATE.pop("open_modal", None) if self._query("panel") == "1" else None,
        })
        return self.respond(200, body, "application/json; charset=utf-8")

    def _query(self, key: str) -> str:
        return parse_qs(urlsplit(self.path).query).get(key, [""])[0]

    def _respond_json_profile(self):
        current_cfg = bridge.load_config()
        body = json.dumps({
            "name": current_cfg.get("name", "puente-pc"),
            "pc_name": bridge.STATE.get("pc_name") or socket.gethostname(),
            "phone_name": devices.configured_name(current_cfg, "iphone"),
            "ipad_name": devices.configured_name(current_cfg, "ipad"),
            "phone_ip": bridge.STATE.get("phone_ip"),
            "notifications": bool(current_cfg.get("notifications", True)),
            "clipboard_notifications": bool(current_cfg.get("clipboard_notifications", False)),
            "theme": panel_theme(current_cfg),
            "autostart": bool(bridge.so.installed()) if bridge.sys.platform == "win32" else None,
            "linked_pcs": devices_for_tray(current_cfg, bridge.STATE.get("pcs", [])),
            "download_folder": current_cfg.get("download_folder") or str(bridge.so.DOCUMENTS),
            "download_folder_is_default": not bool(current_cfg.get("download_folder")),
            "keep_days": bridge.days_to_keep(current_cfg),
            "language": i18n.detect(current_cfg),
            "key": current_cfg.get("key", ""),
            "port": current_cfg.get("port", 8765),
            "trusted_count": len(current_cfg.get("trusted_pcs", [])),
            "linked_count": len(current_cfg.get("pcs", {})),
        })
        return self.respond(200, body, "application/json; charset=utf-8")

    def _respond_qr(self):
        token = secrets.token_urlsafe(8)
        if "qr_tokens" not in bridge.STATE:
            bridge.STATE["qr_tokens"] = {}
        bridge.STATE["qr_tokens"][token] = time.time() + 300
        server_ip = bridge.STATE.get("ip") or bridge.ip_local()
        server_port = self.server.server_address[1]
        url = f"http://{server_ip}:{server_port}/configure?t={token}"
        qr_svg = bridge.qr_as_svg(url)
        body = json.dumps({"token": token, "url": url, "svg": qr_svg, "validity": 300})
        return self.respond(200, body, "application/json; charset=utf-8")

    def _apply_profile_changes(self, changes: dict):
        current_cfg = bridge.load_config()
        if "name" in changes:
            # A DNS name: a-z, 0-9 and hyphens, 63 characters at most.
            clean = re.sub(r"[^a-zA-Z0-9-]", "-", str(changes["name"]).strip()).strip("-")[:63].rstrip("-")
            if clean:
                current_cfg["name"] = clean
        if "phone_name" in changes:
            phone_name = str(changes["phone_name"]).strip()
            if phone_name:
                current_cfg["phone_name"] = phone_name
                phone_ip = bridge.STATE.get("phone_ip")
                if phone_ip:
                    bridge.STATE["phone_device"] = f"{phone_name} ({phone_ip})"
        if "ipad_name" in changes:
            ipd_name = str(changes["ipad_name"]).strip()
            if ipd_name:
                current_cfg["ipad_name"] = ipd_name
        if changes.get("theme") in THEMES:
            current_cfg["theme"] = changes["theme"]
        if "notifications" in changes:
            current_cfg["notifications"] = bool(changes["notifications"])
            bridge.Handler.notices = current_cfg["notifications"]
        if "clipboard_notifications" in changes:
            current_cfg["clipboard_notifications"] = bool(changes["clipboard_notifications"])
        if "keep_days" in changes:
            try:
                days = int(changes["keep_days"])
            except (TypeError, ValueError):
                days = 0
            if days in bridge.KEEP_OPTIONS:
                current_cfg["keep_days"] = days
        if changes.get("language") in i18n.LANGUAGES:
            current_cfg["language"] = changes["language"]
            i18n.pin(changes["language"])
        if changes.get("revoke_trust"):
            current_cfg["trusted_pcs"] = []
            current_cfg["pcs"] = {}
            if bridge.Handler.p2p_manager:
                bridge.Handler.p2p_manager.trusted_pcs.clear()
            bridge.Handler.pairing_manager = bridge._create_pairing_manager(current_cfg)
        bridge.save_config(current_cfg)
        if "name" in changes:
            bridge.change_announced_name(current_cfg.get("name", "puente-pc"))
        return self.respond(200, json.dumps({"ok": True, "profile": current_cfg}), "application/json; charset=utf-8")

    def _post_app(self):
        path = self.path.split("?")[0]
        path = LEGACY_APP_ROUTES.get(path, path)
        if not self._panel_local():
            return self.respond(403, t("Only available locally"))
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            data = {}

        if path == "/app/profile":
            return self._apply_profile_changes(data)

        if path == "/app/autostart":
            # «Start with Windows» from the quick view; «ok» only if Windows really applied it.
            enable = data.get("enable")
            if bridge.sys.platform != "win32" or not isinstance(enable, bool):
                return self.respond(400, json.dumps({"ok": False}), "application/json; charset=utf-8")
            bridge._automatic_startup()[1](enable)
            applied = bool(bridge.so.installed())
            return self.respond(200, json.dumps({"ok": applied == enable, "active": applied}),
                                "application/json; charset=utf-8")

        if path == "/app/change-device-type":
            device_ip = str(data.get("ip", "")).strip()
            device_id = str(data.get("id", "")).strip()
            new_type = str(data.get("type", "")).strip().lower()
            new_name = str(data.get("name", "")).strip()
            current_cfg = bridge.load_config()
            if device_id and device_id in current_cfg.get("devices", {}):
                # Renaming a linked device doesn't change its type unless asked.
                def rename(registry):
                    linked = registry.get(device_id)
                    if linked and new_type in devices.TYPES:
                        linked["type"] = new_type
                    if linked and new_name:
                        linked["name"] = devices.clean_name(new_name)
                        linked["name_locked"] = True
                    return linked

                linked = bridge.modify_devices(rename)
                if not linked:
                    return self.respond(404, json.dumps({"ok": False, "message": t("That device no longer exists")}), "application/json; charset=utf-8")
                live = bridge.STATE["apple_devices"].get(device_id)
                if live:
                    live.update(type=linked["type"], name=linked["name"])
                return self.respond(200, json.dumps({"ok": True, "device": {"name": linked["name"], "type": linked["type"]}}), "application/json; charset=utf-8")
            if device_ip:
                current_cfg = bridge.load_config()
                if "apple_devices" not in current_cfg:
                    current_cfg["apple_devices"] = {}
                current_device = current_cfg["apple_devices"].get(device_ip, {})
                final_name = new_name or current_device.get("name") or devices.default_name("ipad" if new_type == "ipad" else "iphone")
                current_cfg["apple_devices"][device_ip] = {"name": final_name, "type": new_type}
                bridge.save_config(current_cfg)
                if device_ip in bridge.STATE["apple_devices"]:
                    bridge.STATE["apple_devices"][device_ip]["type"] = new_type
                    bridge.STATE["apple_devices"][device_ip]["name"] = final_name
                else:
                    bridge.STATE["apple_devices"][device_ip] = {"ip": device_ip, "name": final_name, "type": new_type, "last_seen": datetime.now().strftime("%H:%M"), "last_event": "Saved"}
                return self.respond(200, json.dumps({"ok": True, "device": {"name": final_name, "type": new_type}}), "application/json; charset=utf-8")
            return self.respond(400, t("No IP specified"))

        if path == "/app/scan":
            bridge.find_pcs_now()
            eqs = bridge._dedupe_p2p_pcs(bridge.STATE.get("pcs", []))
            return self.respond(200, json.dumps({
                "ok": True,
                "pcs": eqs,
                "apple_devices": bridge._dedupe_apple_devices(list(bridge.STATE.get("apple_devices", {}).values()))
            }), "application/json; charset=utf-8")

        if path == "/app/ping":
            ip_obj = str(data.get("ip", "")).strip()
            obj_port = int(data.get("port", 8765))
            if not ip_obj:
                return self.respond(400, json.dumps({"ok": False, "message": t("IP required")}), "application/json; charset=utf-8")
            t0 = time.perf_counter()
            active = False
            lat_ms = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.7)
                sock.connect((ip_obj, obj_port))
                sock.close()
                lat_ms = round((time.perf_counter() - t0) * 1000, 1)
                active = True
            except Exception:
                try:
                    import subprocess
                    output = subprocess.run(["ping", "-n", "1", "-w", "500", ip_obj], capture_output=True, text=True, timeout=1.2)
                    if "TTL=" in output.stdout or "ttl=" in output.stdout:
                        active = True
                        lat_ms = round((time.perf_counter() - t0) * 1000, 1)
                except Exception:
                    pass
            return self.respond(200, json.dumps({
                "ok": True,
                "ip": ip_obj,
                "active": active,
                "latency_ms": lat_ms or 1.8,
                "status_text": t("Online") if active else t("Idle / Waiting for a request")
            }), "application/json; charset=utf-8")

        if path == "/app/send-clipboard-to-pc":
            current_text = bridge.so.read_text() or ""
            return self.respond(200, json.dumps({
                "ok": True,
                "text": current_text,
                "message": t("Clipboard synced and ready to paste")
            }), "application/json; charset=utf-8")

        if path == "/app/choose-files":
            if not bridge._LOCK_SELECTOR.acquire(blocking=False):
                dialogs.focus_existing_window(dialogs.files_selector_title())
                return self.respond(200, json.dumps({"files": [], "busy": True}), "application/json; charset=utf-8")
            try:
                files = dialogs.select_native_files()
            finally:
                bridge._LOCK_SELECTOR.release()
            total = sum(a["size"] for a in files)
            return self.respond(
                200,
                json.dumps({"files": files, "total_text": p2p.format_size(total)}),
                "application/json; charset=utf-8",
            )

        if path == "/app/choose-folder":
            if not bridge._LOCK_SELECTOR.acquire(blocking=False):
                dialogs.focus_existing_window(dialogs.folder_selector_title())
                return self.respond(200, json.dumps({"folder": None, "busy": True}), "application/json; charset=utf-8")
            try:
                selected_folder = dialogs.select_native_folder()
            finally:
                bridge._LOCK_SELECTOR.release()
            if selected_folder and Path(selected_folder).is_dir():
                p_folder = Path(selected_folder)
                weight = sum(f.stat().st_size for f in p_folder.rglob("*") if f.is_file())
                return self.respond(
                    200,
                    json.dumps({
                        "folder": {
                            "path": str(p_folder),
                            "name": p_folder.name,
                            "size": weight,
                            "size_text": p2p.format_size(weight),
                            "is_directory": True,
                        }
                    }),
                    "application/json; charset=utf-8",
                )
            return self.respond(200, json.dumps({"folder": None}), "application/json; charset=utf-8")

        if path == "/app/upload-dragged-files":
            import private_files
            parts = bridge.request_parts(self.headers.get("Content-Type", ""), body)
            tmp_folder = private_files.temporary_subfolder("puente_arrastrados")
            res_files = []
            for orig_name, _, bin_data in parts:
                if not bin_data:
                    continue
                name = p2p.safe_name(orig_name or "file")
                f_dest = bridge.free_path(name, tmp_folder)
                f_dest.write_bytes(bin_data)
                res_files.append({
                    "path": str(f_dest),
                    "name": f_dest.name,
                    "size": len(bin_data),
                })
            total = sum(a["size"] for a in res_files)
            return self.respond(
                200,
                json.dumps({"files": res_files, "total_text": p2p.format_size(total)}),
                "application/json; charset=utf-8",
            )

        if path == "/app/local-paths":
            # Paths from the Linux panel window (its picker or a drop): used in place, nothing is uploaded.
            res_files = []
            for r in data.get("paths", []):
                p = Path(str(r))
                if p.is_file():
                    res_files.append({"path": str(p), "name": p.name, "size": p.stat().st_size})
                elif p.is_dir():
                    weight = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                    res_files.append({"path": str(p), "name": p.name, "size": weight, "is_directory": True})
            total = sum(a["size"] for a in res_files)
            return self.respond(
                200,
                json.dumps({"files": res_files, "total_text": p2p.format_size(total)}),
                "application/json; charset=utf-8",
            )

        if path == "/app/wifi-direct":
            action = str(data.get("action", "start")).strip().lower()
            if action in ("stop", "detener"):  # a panel left open from before this name
                res = bridge.stop_wifi_direct()
            else:
                res = bridge.start_wifi_direct()
            return self.respond(200, json.dumps(res), "application/json; charset=utf-8")

        if path == "/app/wifi-direct-network":
            # No fields: only reports the current name. «suffix» renames it (the «CopyOver-» prefix is fixed) and
            # «new_key» generates another password.
            suffix = data.get("suffix")
            if suffix is not None and not isinstance(suffix, str):
                return self.respond(400, json.dumps({"ok": False, "message": t("Invalid network name")}),
                                      "application/json; charset=utf-8")
            res = bridge.change_wifi_direct_network(suffix, bool(data.get("new_key")))
            return self.respond(400 if res.get("invalid") else 200, json.dumps(res),
                                  "application/json; charset=utf-8")

        if path == "/app/send":
            destination = str(data.get("destination", "ios")).strip()
            str_paths = [str(r) for r in data.get("paths", []) if r]
            if not str_paths:
                return self.respond(400, json.dumps({"message": t("No files")}), "application/json; charset=utf-8")
            current_cfg = bridge.load_config()
            paths_p = [Path(r) for r in str_paths if Path(r).exists()]
            if not paths_p:
                return self.respond(400, json.dumps({"ok": False, "message": t("The selected files don’t exist")}), "application/json; charset=utf-8")

            ips_pc = {eq.get("ip") for eq in bridge.STATE.get("pcs", []) if eq.get("ip")}
            mobile_ips = {d.get("ip") for d in bridge.STATE.get("apple_devices", {}).values() if d.get("ip")}
            is_pc_destination = destination in ips_pc and destination not in mobile_ips

            if not is_pc_destination:
                processed_paths, _ = p2p.bundle_if_is_directory(paths_p)
                total_weight = sum(rp.stat().st_size for rp in processed_paths)
                exceeds_500 = total_weight > bridge.MAX_BYTES
                bridge.STATE["prepared_files"] = processed_paths
                # Only once they're on the clipboard: until then, nothing to compare against (see
                # bridge.files_for_phone).
                bridge.STATE["prepared_on_clipboard"] = None
                if not exceeds_500:
                    def copy():
                        bridge.so.set_files(paths_p)
                        bridge.STATE["prepared_on_clipboard"] = paths_p
                    threading.Thread(target=copy, daemon=True).start()
                display_name = paths_p[0].name if len(paths_p) == 1 else f"{len(paths_p)} files"
                # What's only displayed gets translated; the history keeps the English one.
                visible_name = paths_p[0].name if len(paths_p) == 1 else t("{count} files", count=len(paths_p))
                bridge.STATE["phone_offer"] = {
                    "id": secrets.token_hex(6),
                    "ts": time.time(),
                    "type": "file",
                    "name": visible_name,
                    "count": len(paths_p),
                    "size": total_weight,
                    "size_text": p2p.format_size(total_weight),
                    "exceeds_shortcut": exceeds_500,
                    "destination": destination,
                    "delivered": False,
                }
                if destination == 'android':
                    bridge.register_history('sent', 'Sent to Android', display_name, size=total_weight, local_path=str(paths_p[0]))
                    bridge.STATE['last'] = (datetime.now(), t('Ready for Android: {name}', name=visible_name))
                    return self.respond(200, json.dumps({'ok': True, 'is_android': True, 'is_ios': False,
                        'message': t('On Android, open CopyOver → Files and tap Receive.')}), 'application/json; charset=utf-8')
                mobile_data = bridge._get_or_create_mobile_token(port=int(current_cfg.get("port", 8765)))
                bridge.register_history("sent", "Sent to iPhone or iPad", display_name, size=total_weight, local_path=str(paths_p[0]))
                bridge.STATE["last"] = (datetime.now(), t("Ready for iPhone/iPad: {name}", name=visible_name))
                # The destination says whether it's the iPhone or the iPad; if unknown, «the phone».
                device = {"iphone": "iPhone", "ipad": "iPad"}.get(destination.lower()) or t("phone")
                msg = (
                    t("Over the shortcut’s 500 MB limit ({size}): get it in the {device} web app", size=p2p.format_size(total_weight), device=device)
                    if exceeds_500
                    else t("Ready for the {device} ({size})", size=p2p.format_size(total_weight), device=device)
                )
                return self.respond(
                    200,
                    json.dumps({
                        "ok": True,
                        "is_ios": True,
                        "exceeds_shortcut": exceeds_500,
                        "name": visible_name,
                        "size": total_weight,
                        "size_text": p2p.format_size(total_weight),
                        "phone_url": mobile_data["url"],
                        "qr_svg": mobile_data["qr_svg"],
                        "message": msg,
                    }),
                    "application/json; charset=utf-8",
                )

            threading.Thread(
                target=lambda: bridge.send_command(current_cfg, ["--to", destination, *str_paths]),
                daemon=True,
            ).start()
            return self.respond(
                200,
                json.dumps({"ok": True, "is_ios": False, "message": t("Sending {count} file(s) to {destination}...", count=len(str_paths), destination=destination)}),
                "application/json; charset=utf-8",
            )

        if path == "/app/send-text":
            text = str(data.get("text", "")).strip()
            text_destination = str(data.get("destination", "")).strip()
            if not text:
                return self.respond(400, json.dumps({"ok": False, "message": t("Empty text")}), "application/json; charset=utf-8")
            bridge.so.set_text(text)
            bridge.STATE["prepared_files"] = []
            bridge.STATE["prepared_on_clipboard"] = None
            bridge.STATE["phone_offer"] = {
                "id": secrets.token_hex(6),
                "ts": time.time(),
                "type": "text",
                "name": crop(text, 60),
                "text": text,
                "size": len(text.encode("utf-8")),
                "size_text": t("{count} characters", count=len(text)),
                "exceeds_shortcut": False,
                "destination": text_destination,
                "delivered": False,
            }
            bridge.register_history("sent", "Sent to phone", f"Texto: {crop(text, 40)}", size=len(text.encode("utf-8")))
            bridge.STATE["last"] = (datetime.now(), t("Text ready for iPhone/iPad"))
            if bridge.load_config().get("notifications", True):
                bridge.so.notify(t("Text ready for the iPhone or iPad"), text[:80],
                                 note=t("Get it on the phone with the CopyOver shortcut"))
            return self.respond(200, json.dumps({"ok": True, "size": len(text)}), "application/json; charset=utf-8")

        if path == "/app/choose-downloads-folder":
            if not bridge._LOCK_SELECTOR.acquire(blocking=False):
                dialogs.focus_existing_window(dialogs.folder_selector_title())
                return self.respond(200, json.dumps({"ok": False, "busy": True}), "application/json; charset=utf-8")
            try:
                new_value = dialogs.select_native_folder()
            finally:
                bridge._LOCK_SELECTOR.release()
            if new_value and Path(new_value).is_dir():
                current_cfg = bridge.load_config()
                current_cfg["download_folder"] = new_value
                bridge.save_config(current_cfg)
                return self.respond(200, json.dumps({"ok": True, "folder": new_value}), "application/json; charset=utf-8")
            return self.respond(200, json.dumps({"ok": False, "cancelled": True}), "application/json; charset=utf-8")

        if path == "/app/reset-downloads-folder":
            current_cfg = bridge.load_config()
            current_cfg["download_folder"] = ""
            bridge.save_config(current_cfg)
            return self.respond(200, json.dumps({"ok": True, "folder": str(bridge.so.DOCUMENTS)}), "application/json; charset=utf-8")

        if path == "/app/action":
            # The same actions as the tray menu, for the quick view.
            action = self.actions.get(str(data.get("action", "")))
            if not action:
                return self.respond(404, json.dumps({"ok": False, "message": t("Unknown action")}), "application/json; charset=utf-8")
            threading.Thread(target=action, daemon=True).start()
            return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")

        if path == "/app/open-folder":
            kind = str(data.get("type", "base"))
            folder = bridge.get_downloads_folder(kind)
            folder.mkdir(parents=True, exist_ok=True)
            bridge.so.open_stream(folder)
            return self.respond(200, json.dumps({"ok": True, "path": str(folder)}), "application/json; charset=utf-8")

        if path == "/app/resolve-repeated":
            device_id = str(data.get("id", "")).strip()
            replace = data.get("replace") is True

            def resolve(registry):
                if not registry.get(device_id, {}).get("repeats"):
                    return False, None
                removed = devices.resolve_repeated({"devices": registry}, device_id, replace)
                return True, (removed, dict(registry[device_id]))

            done, result = bridge.modify_devices(resolve)
            if not done:
                return self.respond(404, json.dumps({"ok": False, "message": t("That device no longer exists")}), "application/json; charset=utf-8")
            removed, new_item = result
            state_map = bridge.STATE.get("apple_devices", {})
            if removed:
                state_map.pop(removed, None)
            if device_id in state_map:
                state_map[device_id].update(name=new_item["name"], repeats=None)
            return self.respond(200, json.dumps({"ok": True, "removed": bool(removed)}), "application/json; charset=utf-8")

        if path == "/app/remove-device":
            device_ip = str(data.get("ip", "")).strip()
            device_id = str(data.get("id", "")).strip()
            current_cfg = bridge.load_config()
            if device_id and device_id in current_cfg.get("devices", {}):
                state_map = bridge.STATE.get("apple_devices", {})
                removed = state_map.pop(device_id, None) or {}
                removed_ip = removed.get("ip") or current_cfg["devices"][device_id].get("last_ip")
                for key_v, info_v in list(state_map.items()):
                    if not info_v.get("id") and removed_ip and info_v.get("ip") == removed_ip:
                        state_map.pop(key_v, None)  # the «not linked» shadow entry with the same IP goes with it

                def remove(cfg):
                    devices.remove(cfg, device_id)
                    if removed_ip:
                        cfg.get("apple_devices", {}).pop(removed_ip, None)

                bridge.modify_config(remove)
                return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
            dict_device = current_cfg.get("apple_devices", {})
            key_delete = None
            for k, v in list(dict_device.items()):
                if k == device_ip or (device_id and v.get("type") == device_id):
                    key_delete = k
                    break
            if key_delete and key_delete in dict_device:
                del dict_device[key_delete]
                current_cfg["apple_devices"] = dict_device
                bridge.save_config(current_cfg)
            if device_ip in bridge.STATE.get("apple_devices", {}):
                del bridge.STATE["apple_devices"][device_ip]
            return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")

        if path == "/app/open-history":
            id_item = str(data.get("id", ""))
            in_folder = bool(data.get("folder", False))
            with bridge._HISTORY_LOCK:
                found = next((h for h in bridge.STATE["history"] if h["id"] == id_item), None)
            if found and found.get("link") and not found.get("path"):
                # A received http(s) link: opened in the browser (validated again before opening).
                if links.open_in_browser(found["link"]):
                    return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
                return self.respond(400, json.dumps({"ok": False, "message": t("Couldn’t open the link")}), "application/json; charset=utf-8")
            if found and found.get("path"):
                file_path = Path(found["path"])
                if file_path.exists():
                    if in_folder:
                        bridge.so.highlight_in_explorer(file_path)
                    else:
                        bridge.so.open_stream(file_path)
                    return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
            return self.respond(404, json.dumps({"ok": False, "message": t("File not found")}), "application/json; charset=utf-8")

        if path == "/app/copy-history":
            id_item = str(data.get("id", ""))
            with bridge._HISTORY_LOCK:
                found = next((h for h in bridge.STATE["history"] if h["id"] == id_item), None)
            if found:
                str_path = found.get("path")
                if str_path and Path(str_path).exists():
                    bridge.so.set_files([Path(str_path)])
                    return self.respond(200, json.dumps({"ok": True, "type": "file", "message": t("File copied to the clipboard")}), "application/json; charset=utf-8")
                detail = found.get("link") or found.get("detail", "")
                for prefix in ("Texto: ", "Nota: "):
                    if detail.startswith(prefix):
                        detail = detail[len(prefix):]
                bridge.so.set_text(detail)
                return self.respond(200, json.dumps({"ok": True, "type": "text", "message": t("Text copied to the clipboard")}), "application/json; charset=utf-8")
            return self.respond(404, json.dumps({"ok": False, "message": t("Item not found")}), "application/json; charset=utf-8")

        if path == "/app/delete-history":
            identifier = data.get("id")
            if not isinstance(identifier, str) or not identifier.strip():
                return self.respond(400, json.dumps({"ok": False, "message": t("Invalid request")}), "application/json; charset=utf-8")
            if not bridge.delete_history(identifier):
                return self.respond(404, json.dumps({"ok": False, "message": t("Item not found")}), "application/json; charset=utf-8")
            return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")

        if path == "/app/clear-history":
            # Pinned items stay: the user marked them so they wouldn't be lost.
            remain = bridge.empty_history()
            return self.respond(200, json.dumps({"ok": True, "remaining": remain}), "application/json; charset=utf-8")

        if path == "/app/pin-history":
            if not bridge.pin_history(str(data.get("id", "")), bool(data.get("pinned", True))):
                return self.respond(404, json.dumps({"ok": False, "message": t("Item not found")}), "application/json; charset=utf-8")
            return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")

        if path == "/app/cut-guests":
            cut = bridge.cut_guests()
            return self.respond(200, json.dumps({"ok": True, "cut": cut}), "application/json; charset=utf-8")

        if path == "/app/change-common-key":
            # Breaks the devices that use the shared key: only when the panel confirmed it.
            if data.get("confirm") is not True:
                return self.respond(400, json.dumps({"ok": False, "message": t("Invalid request")}), "application/json; charset=utf-8")
            new_value = bridge.rotate_common_key()
            return self.respond(200, json.dumps({"ok": True, "key": new_value}), "application/json; charset=utf-8")

        if path == "/app/fix":
            import diagnostics
            ok = diagnostics.fix(str(data.get("key", "")), port=bridge.load_config().get("port", 8765))
            return self.respond(200, json.dumps({"ok": ok, "problems": bridge._diagnose()}),
                                  "application/json; charset=utf-8")

        if path == "/app/update":
            # Answers once the installer is running; it then closes CopyOver and opens the new version.
            try:
                ok = bridge.Handler.updates.install_new_version()
            except (OSError, ValueError, KeyError, TypeError) as error:
                logging.warning("Couldn't install the new version: %s", error)
                ok = False
            return self.respond(200, json.dumps({"ok": ok}), "application/json; charset=utf-8")

        if path == "/app/welcome-seen":
            bridge.modify_config(lambda cfg: cfg.__setitem__("welcome_seen", True))
            return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")

        if path == "/app/background":
            try:
                bridge.so.notify(
                    t("CopyOver is running in the background"),
                    t("Find it in the hidden icons of the taskbar. Your devices stay connected."),
                )
            except Exception:
                pass
            return self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")

        if path == "/app/quit":
            self.respond(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
            import os
            threading.Timer(0.25, lambda: os._exit(0)).start()
            return

        return self.respond(404, t("Not found"))

    def _enter_to_panel(self):
        """The panel window (or the browser, through a private local page) arrives here once with the local
        secret; the answer leaves it in a cookie that only /app sees and moves on to the panel.

        It moves on with a page and not a redirect: after a navigation that started in a local file, the
        browser wouldn't send a SameSite=Strict cookie on the redirect, but it does once the page is ours."""
        query = parse_qs(urlsplit(self.path).query)
        received = query.get("s", [""])[0]
        destination = query.get("a", ["/app"])[0]
        destination = LEGACY_APP_ROUTES.get(destination, destination)
        expected = getattr(self.server, "local_secret", None) or ""
        if (destination not in local_secret.DESTINATIONS or not self._local_origin() or not expected
                or not hmac.compare_digest(received.encode(), expected.encode())):
            return self.respond(403, t("Open the panel from the CopyOver icon."))
        page = f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={destination}">'
        return self.respond(200, page, "text/html; charset=utf-8", {
            "Set-Cookie": f"{local_secret.COOKIE}={expected}; Path=/app; HttpOnly; SameSite=Strict",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        })

    def _get_app(self, path):
        path = LEGACY_APP_ROUTES.get(path, path)
        if path == "/app/enter":
            return self._enter_to_panel()
        if not self._panel_local():
            if self._local_origin():
                # This PC, but without the secret: opened by hand or left over from before a restart.
                return self.respond(403, t("Open the panel from the CopyOver icon."))
            return self.respond(403, t("Only available locally"))
        if path == "/app":
            html = i18n.translate_html(_html_app()).replace("{{THEME}}", panel_theme(bridge.load_config()))
            return self.respond(200, html, "text/html; charset=utf-8")
        if path == "/app/state":
            return self._respond_json_state()
        if path == "/app/diagnostics":
            return self.respond(200, json.dumps(bridge._diagnose()), "application/json; charset=utf-8")
        if path == "/app/preview":
            import web_ui
            return self.respond(200, i18n.translate_html(web_ui.PREVIEW_PAGE), "text/html; charset=utf-8")
        if path == "/app/profile":
            return self._respond_json_profile()
        if path == "/app/history":
            with bridge._HISTORY_LOCK:
                entries = bridge.sorted_history(bridge.STATE["history"])
            body = json.dumps([_with_exists(h) for h in history_to_show(entries)])
            return self.respond(200, body, "application/json; charset=utf-8")
        if path == "/app/history/image":
            # Thumbnail of a received image, for the panel's history.
            id_item = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            with bridge._HISTORY_LOCK:
                entry = next((h for h in bridge.STATE["history"] if h["id"] == id_item), None)
            img_path = Path(entry["path"]) if entry and entry.get("path") else None
            img_type = mimetypes.guess_type(img_path.name)[0] if img_path else None
            # Only plain pictures: an SVG (or anything else) someone sent could run scripts on the panel's address.
            if not img_path or not img_path.is_file() or img_type not in THUMBNAILS or img_path.stat().st_size > 25 * 1024 * 1024:
                return self.respond(404, "No image")
            return self.respond(200, img_path.read_bytes(), img_type, {
                "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox"})
        if path == "/app/qr":
            return self._respond_qr()
        if path == "/app/android-apk":
            # QR of the Android tab: from this PC if it already has the verified APK, otherwise from GitHub.
            cache = bridge.Handler.updates
            if cache is not None and cache.current_apk() is not None:
                token = secrets.token_urlsafe(12)
                expires = time.time() + bridge.APK_VALIDITY
                bridge._clean_tokens("tokens_apk")[token] = expires
                server_ip = bridge.STATE.get("ip") or bridge.ip_local()
                url = f"http://{server_ip}:{self.server.server_address[1]}/app-android?t={token}"
                data = {"from_pc": True, "validity": bridge.APK_VALIDITY, "version": cache.version_apk()}
            else:
                url = f"https://github.com/{updates.REPOSITORY}/releases/latest/download/Puente.apk"
                data = {"from_pc": False}
            body = json.dumps({"ok": True, "url": url, "qr_svg": bridge.qr_as_svg(url), **data})
            return self.respond(200, body, "application/json; charset=utf-8")
        if path == "/app/guest-token":
            token = secrets.token_urlsafe(12)
            tokens = bridge.clean_guest_tokens()
            tokens[token] = time.time() + bridge.GUEST_VALIDITY
            # From when this guest may receive offers: not the ones made before it scanned.
            since = bridge.STATE.setdefault("guests_since", {})
            for old in [k for k in since if k not in tokens]:
                since.pop(old)
            since[token] = time.time()
            server_ip = bridge.STATE.get("ip") or bridge.ip_local()
            server_port = self.server.server_address[1]
            url = f"http://{server_ip}:{server_port}/guest?t={token}"
            qr_svg = bridge.qr_as_svg(url)
            body = json.dumps({"ok": True, "token": token, "url": url, "qr_svg": qr_svg, "expires": tokens[token]})
            return self.respond(200, body, "application/json; charset=utf-8")
        return self.respond(404, t("Not found"))
