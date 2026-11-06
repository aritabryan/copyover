"""config.json: read it, save it and change it under a lock."""
import json
import logging
import os
import re
import secrets
import socket
import threading
import unicodedata
from pathlib import Path

from i18n import OLD_EVENT_ACTIONS
import private_files
import bundle

CONFIG = bundle.data_folder() / "config.json"
LOCK = threading.RLock()

# Configs with no «name» keep puente-pc so shortcuts that already point to puente-pc.local don't break, and
# the ones created before the computer name was used keep copyover-pc.
NEW_NETWORK_NAME = "copyover-pc"
_DNS_LABEL_LENGTH = 63


def _sanitize_for_dns(text: str) -> str:
    """Lowercase, a-z, 0-9 and single hyphens: what a DNS label allows (no accents or spaces)."""
    without_accents = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", without_accents.lower())).strip("-")


def default_network_name(pc: str | None = None) -> str:
    """The network name of a new install: copyover-<computer name>, so two new PCs on the same network don't
    both announce themselves as copyover-pc.local."""
    clean = _sanitize_for_dns(socket.gethostname() if pc is None else pc) or secrets.token_hex(2)
    return f"copyover-{clean}"[:_DNS_LABEL_LENGTH].rstrip("-")


def is_default_name(name: str, pc: str) -> bool:
    """Whether the network name is one set automatically (not one the person chose)."""
    return name in ("puente-pc", NEW_NETWORK_NAME) or (
        bool(_sanitize_for_dns(pc)) and name == default_network_name(pc))


def write_json(path: Path, data, indent=None):
    """Writes to a .tmp and swaps it in one go: a power cut or a crash halfway leaves the old file whole,
    never a truncated one. Readable only by this user on Linux (0600): config.json holds the keys and
    history.json what was copied."""
    private_files.write_private(Path(path), json.dumps(data, indent=indent).encode("utf-8"))


def back_up_corrupt(path: Path, error) -> Path | None:
    """Keeps a copy of the file that couldn't be read (ruta.bak) and notes it in the log."""
    path = Path(path)
    copy = path.with_name(path.name + ".bak")
    try:
        os.replace(path, copy)
    except OSError:
        logging.exception("%s is damaged and couldn't be moved aside", path.name)
        return None
    logging.error("%s was damaged (%s): saved it as %s and starting over", path.name, error, copy.name)
    return copy


# Keys written by versions before 1.0.7, when config.json used Spanish names. They're renamed on load.
OLD_KEYS = {
    "puerto": "port", "clave": "key", "nombre": "name", "nombre_celular": "phone_name", "nombre_ipad": "ipad_name",
    "avisos_portapapeles": "clipboard_notifications",
    "avisos": "notifications", "carpeta_descargas": "download_folder", "dispositivos_apple": "apple_devices",
    "wifi_direct_clave": "wifi_direct_password", "dispositivos": "devices", "portapapeles_celular": "phone_clipboard",
    "conservar_dias": "keep_days", "idioma": "language", "tema": "theme", "bienvenida_vista": "welcome_seen",
    "icono": "tray_icon", "equipos": "pcs", "equipos_info": "pcs_info", "equipos_confiables": "trusted_pcs",
    "emparejar_sin_cifrar": "allow_unencrypted_pairing", "modo_seguridad": "security_mode",
    "confirmar_celular": "confirm_phone", "avisos_envio": "send_notifications", "al_cerrar": "on_close",
    "auto_aceptar_p2p": "auto_accept_p2p", "equipos_revocados": "revoked_pcs",
}
DEVICE_OLD_KEYS = {
    "nombre": "name", "tipo": "type", "clave_hash": "key_hash", "origen": "origin", "vinculado": "linked_at",
    "nombre_fijado": "name_locked", "ultima_ip": "last_ip", "usado": "used", "nombre_equipo": "device_name",
    "ultimo_uso": "last_use", "repite": "repeats", "distinto_de": "distinct_from", "momento": "timestamp",
    "invitado": "guest", "pendiente": "pending",
}
OLD_USE_KEYS = {"accion": "action", "marca": "time"}
APPLE_OLD_KEYS = {"nombre": "name", "tipo": "type", "vinculado": "linked", "momento": "timestamp", "repite": "repeats",
                  "pendiente": "pending", "invitado": "guest"}
PC_OLD_KEYS = {"nombre": "name", "huella_ca": "ca_fingerprint"}
OLD_THEMES = {"sistema": "system", "oscuro": "dark", "claro": "light"}
OLD_DEVICE_TYPE = "movil"
OLD_PHONE_CLIPBOARD = {"imagenes": "images", "imagenes_videos": "images_videos", "archivos": "files"}


def rename_keys(data: dict, mapping: dict) -> bool:
    """Renames the old keys of `datos` in place (a new key already there wins). Returns whether it changed."""
    change = False
    for old_value, new_value in mapping.items():
        if old_value in data:
            value = data.pop(old_value)
            data.setdefault(new_value, value)
            change = True
    return change


def _new_device_type(data: dict) -> bool:
    if data.get("type") != OLD_DEVICE_TYPE:
        return False
    data["type"] = "mobile"
    return True


def migrate_config(cfg: dict) -> bool:
    """Brings a config.json written by an older version to the current key names. Returns whether it changed."""
    change = rename_keys(cfg, OLD_KEYS)
    if cfg.get("phone_clipboard") in OLD_PHONE_CLIPBOARD:
        cfg["phone_clipboard"] = OLD_PHONE_CLIPBOARD[cfg["phone_clipboard"]]
        change = True
    if cfg.get("theme") in OLD_THEMES:
        cfg["theme"] = OLD_THEMES[cfg["theme"]]
        change = True
    for data in (cfg.get("devices") or {}).values():
        if isinstance(data, dict):
            change |= rename_keys(data, DEVICE_OLD_KEYS)
            change |= _new_device_type(data)
            if isinstance(data.get("last_use"), dict):
                change |= rename_keys(data["last_use"], OLD_USE_KEYS)
                if data["last_use"].get("action") in OLD_EVENT_ACTIONS:
                    data["last_use"]["action"] = OLD_EVENT_ACTIONS[data["last_use"]["action"]]
                    change = True
    for group, mapping in (("apple_devices", APPLE_OLD_KEYS), ("pcs_info", PC_OLD_KEYS)):
        for data in (cfg.get(group) or {}).values():
            if isinstance(data, dict):
                change |= rename_keys(data, mapping)
                change |= _new_device_type(data)
    return change


def move_if_missing(old_value: Path, new_value: Path) -> Path:
    """Renames a file or folder left by an older version to its new name, if the new one doesn't exist yet.
    Returns the path to use: the new one, or the old one if it couldn't be renamed."""
    old_value, new_value = Path(old_value), Path(new_value)
    if old_value.exists() and not new_value.exists():
        try:
            os.replace(old_value, new_value)
            logging.info("Renamed %s to %s", old_value, new_value)
        except OSError:
            logging.warning("Couldn't rename %s to %s", old_value, new_value, exc_info=True)
            return old_value
    return new_value


def load_config():
    cfg = None
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                raise ValueError("not a JSON object")
        except ValueError as error:
            # Without the copy, the key and the linked devices would be lost silently.
            back_up_corrupt(CONFIG, error)
            cfg = None
    if cfg is None:
        cfg = {"port": 8765, "key": secrets.token_urlsafe(9), "name": default_network_name()}
    migrated = migrate_config(cfg)
    if not cfg.get("id"):
        cfg["id"] = secrets.token_hex(16)
        migrated = True
    if migrated:
        save_config(cfg)
    return cfg


def save_config(cfg):
    write_json(CONFIG, cfg, indent=2)


def modify_config(change):
    """Reads, changes and saves the config under a lock, so a change made at the same time isn't overwritten."""
    with LOCK:
        cfg = load_config()
        result = change(cfg)
        save_config(cfg)
        return result


def modify_devices(change):
    """Same, but only on `cfg["devices"]` (adds, removals and renames)."""
    return modify_config(lambda cfg: change(cfg.setdefault("devices", {})))
