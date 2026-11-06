"""Local linking QR and public identity for the mDNS announcement."""
import json
import hashlib
import hmac
import os
import ssl
from pathlib import Path

APP = "puente-portapapeles"


def mdns_properties(cfg):
    return {"app": APP, "id": cfg["id"]}


def key_fingerprint(hash_key):
    """What the phone sends to /identity so the PC knows which device key to sign with, without sending it."""
    return hashlib.sha256(f"puente-huella:{hash_key}".encode()).hexdigest()[:32]


def identity_test(identifier, key, challenge, address):
    message = f"puente-v1:{identifier}:{challenge}:{address}".encode()
    signature = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    return {"id": identifier, "address": address, "proof": signature}


def qr_content(cfg, ip, key=None, ca_pem=None):
    """`clave` is the device's own key; without it the PC's shared key is used.

    `ca_pem` is this PC's CA: with it the phone talks to it over HTTPS and only accepts its certificate. It
    goes apart from "server", which stays on http:// so an old app that doesn't know "ca" keeps working the
    same."""
    name = cfg.get("name", "puente-pc")
    content = {
        "app": APP,
        "version": 1,
        "id": cfg["id"],
        "name": name,
        "server": f"http://{name}.local:{cfg['port']}",
        "fallback": f"http://{ip}:{cfg['port']}",
        "key": key or cfg["key"],
    }
    if ca_pem:
        content["ca"] = ca_pem
        content["ca_sha256"] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca_pem)).hexdigest()
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))


def save_qr(cfg, ip, path):
    import qrcode

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(qr_content(cfg, ip))
    qr.make(fit=True)
    # O_EXCL won't open a file or link that already exists, and 0600 keeps the QR for this user only.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        qr.make_image(fill_color="black", back_color="white").save(output, format="PNG")
    return path
