"""Linked devices: each phone or tablet has its own key.

The PC recognizes the device by the key it handed over when linking, not by the IP (the router changes it) or
by the name it claims (anyone can send one). Only the key's hash is stored; the device sees the plain key
once, when linking.
"""
import hashlib
import hmac
import secrets
from datetime import datetime

from i18n import t

TYPES = ("iphone", "ipad", "android", "mobile")
NAMES = {"iphone": "My iPhone", "ipad": "My iPad", "android": "Android phone"}


def clean_name(name, default=None):
    clean = "".join(c for c in str(name or "") if c.isprintable()).strip()[:60]
    return clean or default or t("Mobile device")


def default_name(kind):
    return t(NAMES.get(kind, "Mobile device"))


def configured_name(cfg, kind):
    """The name set for the phone or the iPad. One nobody changed follows the interface language."""
    field, untouched = ("ipad_name", ("My iPad", "Mi iPad")) if kind == "ipad" else ("phone_name", ("My iPhone", "Mi iPhone"))
    name = str(cfg.get(field) or "").strip()
    return default_name(kind) if not name or name in untouched else name


def type_from_agent(agent, hint=""):
    """Works out iphone/ipad/android from the User-Agent or the name the client sent."""
    ua = (agent or "").lower()
    hint = (hint or "").lower()
    if "android" in ua or "android" in hint:
        return "android"
    if "ipad" in ua or "ipad" in hint or ("macintosh" in ua and "mobile" in ua):
        return "ipad"
    if "iphone" in ua or "iphone" in hint:
        return "iphone"
    return "mobile"


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def register(cfg, name, kind="mobile", origin="qr", pinned_name=False):
    """Registers a device and returns (id, key). Only the hash is kept afterwards."""
    key = secrets.token_urlsafe(24)
    identifier = secrets.token_hex(8)
    kind = kind if kind in TYPES else "mobile"
    cfg.setdefault("devices", {})[identifier] = {
        "name": clean_name(name, default_name(kind)),
        "type": kind,
        "key_hash": _hash(key),
        "origin": origin,
        "linked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # If it wasn't set, the first X-Device that arrives names it.
        "name_locked": bool(pinned_name),
        "last_ip": "",
        "used": False,
    }
    return identifier, key


def find_by_key(cfg, key):
    """Returns the id of the device that owns the key, or None."""
    if not key:
        return None
    searched = _hash(key)
    found = None
    # All of them are checked so the response time doesn't reveal which one matched.
    for identifier, data in cfg.get("devices", {}).items():
        if hmac.compare_digest(data.get("key_hash", ""), searched):
            found = identifier
    return found


def remove(cfg, identifier):
    """Removes the device; its key stops working on the next request."""
    return cfg.get("devices", {}).pop(identifier, None) is not None


def unused(data):
    """Registered by scanning a QR but its key never reached the PC: not linked yet."""
    return data.get("origin") == "qr" and data.get("used") is False  # links from before the field count as used


def remove_unused(registry, hours=1):
    """Drops the QR links whose key wasn't used within an hour: scanning alone doesn't link a device."""
    limit = datetime.now().timestamp() - hours * 3600
    old = []
    for identifier, data in list(registry.items()):
        try:
            mark = datetime.strptime(data.get("linked_at", ""), "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            mark = 0
        if unused(data) and mark < limit:
            registry.pop(identifier)
            old.append(identifier)
    return old


def find_repeated(cfg, identifier):
    """The most recent earlier link with the same type and the same computer name: probably the same device
    linked again. It's only a guess (two phones can share a name): the panel decides."""
    registry = cfg.get("devices", {})
    data = registry.get(identifier) or {}
    name = (data.get("device_name") or "").casefold()
    if not name:
        return None
    candidates = [
        (other.get("linked_at", ""), other_id) for other_id, other in registry.items()
        if other_id != identifier
        and other_id not in data.get("distinct_from", [])
        and other.get("type") == data.get("type")
        and (other.get("device_name") or "").casefold() == name
        and other.get("linked_at", "") <= data.get("linked_at", "")
    ]
    return max(candidates)[1] if candidates else None


def resolve_repeated(cfg, identifier, replace):
    """Replace: the earlier link is removed (its key stops working) and the new one takes its name. Otherwise
    both stay and the question isn't asked again. Returns the removed id, or None."""
    registry = cfg.get("devices", {})
    data = registry.get(identifier)
    if not data or not data.get("repeats"):
        return None
    old = data.pop("repeats")
    if not replace or old not in registry:
        data.setdefault("distinct_from", []).append(old)
        return None
    previous = registry.pop(old)
    if previous.get("name_locked"):
        data["name"] = previous.get("name") or data.get("name")
        data["name_locked"] = True
    return old
