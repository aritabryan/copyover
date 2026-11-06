"""P2P between computers running CopyOver."""

import hashlib
import hmac
import http.client
import json
import logging
import re
import secrets
import shutil
import socket
import ssl
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Callable, Optional
from zeroconf import ServiceInfo, ServiceListener

from configuration import is_default_name
from i18n import t
from wire_keys import from_wire, headers_to_wire, route_to_old, to_wire

SERVICE_TYPE = "_puente._tcp.local."
PROTOCOL_VERSION = "1"
MAX_BYTES_P2P = 10 * 1024 * 1024 * 1024  # 10 GB
BYTES_BLOCK = 1024 * 1024  # 1 MB
OFFER_TIME = 60.0

_HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def chassis_type() -> str:
    """Detects whether this computer is a laptop (has a battery) or a desktop PC."""
    if sys.platform == "win32":
        try:
            import ctypes

            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_byte),
                    ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            s = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
                if s.BatteryFlag == 128:
                    return "desktop"
                return "laptop"
        except Exception:
            pass
    return "laptop"


def create_service(name: str, port: int, my_id: str, ip: str, pairing_level: int = 1) -> ServiceInfo:
    """Builds the ServiceInfo that announces this computer to other CopyOvers.

    `empareja`: 2 if this PC pairs with encryption (protocol 2). Whoever sees it announced that way won't
    accept it answering like an older PC later: that only happens if someone got in the middle."""
    current_so = "windows" if sys.platform == "win32" else "linux"
    # Two PCs with the same name would share the host and their IPs would get mixed. The iPhone host doesn't
    # change.
    host_id = hashlib.sha256(my_id.encode("utf-8")).hexdigest()[:24]
    service_name = f"Puente-{host_id}.{SERVICE_TYPE}"
    service_host = f"puente-p2p-{host_id}.local."
    host = socket.gethostname()
    props = {"v": PROTOCOL_VERSION, "so": current_so, "id": my_id, "host": host,
             "name": host if is_default_name(name, host) else name, "chassis": chassis_type()}
    if pairing_level >= 2:
        props["pairing"] = str(pairing_level)
    return ServiceInfo(
        SERVICE_TYPE,
        service_name,
        addresses=[socket.inet_aton(ip)],
        port=port,
        server=service_host,
        properties=to_wire(props),
    )


def decode_properties(props: dict) -> dict[str, str]:
    res = {}
    for k, v in props.items():
        key = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
        value = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        res[key] = value
    return from_wire(res)


WIN_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}


def is_valid_name(name: object) -> bool:
    """Rejects paths, directory traversal or control characters in file names."""
    if not isinstance(name, str) or not name.strip(" ."):
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    if name in (".", "..") or ".." in Path(name).parts:
        return False
    if Path(name).drive:
        return False
    p = Path(name)
    if p.stem.upper() in WIN_RESERVED_NAMES:
        return False
    return True


def safe_name(name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).name).strip(" .")
    if not clean:
        clean = "file"
    p = Path(clean)
    if p.stem.upper() in WIN_RESERVED_NAMES:
        clean = f"file_{clean}"
    if len(clean) > 200:
        clean = p.stem[:180] + p.suffix[:20]
    return clean


def free_path(folder: Path, name: str) -> Path:
    safe = safe_name(name)
    path = folder / safe
    n = 1
    while path.exists() or path.with_name(path.name + ".parcial").exists():
        path = folder / f"{Path(safe).stem} ({n}){Path(safe).suffix}"
        n += 1
    return path


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(BYTES_BLOCK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def format_size(n: int) -> str:
    value = float(n)
    for drive in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or drive == "TB":
            return f"{int(value)} B" if drive == "B" else f"{value:.1f} {drive}"
        value /= 1024
    return f"{n} B"


class PcsObserver(ServiceListener):
    """Listens for mDNS announcements from other CopyOvers and keeps the list in memory."""

    def __init__(self, my_id: str, on_change: Optional[Callable[[list[dict]], None]] = None):
        self.my_id = my_id
        self.on_change = on_change
        self._pcs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _notify(self):
        if self.on_change:
            pcs = self.list_all()
            try:
                self.on_change(pcs)
            except Exception:
                pass

    def _process(self, zc, type_: str, name: str):
        info = zc.get_service_info(type_, name)
        if not info:
            return
        props = decode_properties(info.properties)
        peer_id = props.get("id")
        if not peer_id or peer_id == self.my_id:
            return

        addresses = info.parsed_addresses()
        ip = addresses[0] if addresses else ""
        clean_name = props.get("name") or name.replace(f".{SERVICE_TYPE}", "")

        pc = {
            "id": peer_id,
            "name": clean_name,
            "host": props.get("host", ""),
            "ip": ip,
            "port": info.port,
            "so": props.get("so", "unknown"),
            "chassis": props.get("chassis", "laptop"),
            "v": props.get("v", "1"),
            "pairing": props.get("pairing", "1"),
            "service": name,
        }
        with self._lock:
            self._pcs[name] = pc
        self._notify()

    def add_service(self, zc, type_: str, name: str):
        self._process(zc, type_, name)

    def update_service(self, zc, type_: str, name: str):
        self._process(zc, type_, name)

    def remove_service(self, zc, type_: str, name: str):
        with self._lock:
            if name in self._pcs:
                del self._pcs[name]
        self._notify()

    def list_all(self) -> list[dict]:
        with self._lock:
            return list(self._pcs.values())

    def clean(self):
        with self._lock:
            self._pcs.clear()
        self._notify()


class PairingManager:
    """Pairing with a 6-digit code for other people's computers."""

    def __init__(
        self,
        paired_pcs: Optional[dict[str, str]] = None,
        confirm_code: Optional[Callable[[str, str], bool]] = None,
        on_save: Optional[Callable[..., None]] = None,
        encrypted: Optional[set[str]] = None,
        allow_unencrypted: bool = True,
        link_name: Optional[Callable[[str], str]] = None,
    ):
        """`cifrados`: computers paired with protocol 2, with their stored CA. Their key only works over TLS
        and is no longer accepted with protocol 1 (so nobody can downgrade an encrypted link to a plain
        one). `link_name(id)`: the name this PC stored that link under, to say it's being replaced."""
        from pc_pairing import PairingReceiver

        self._pcs: dict[str, str] = dict(paired_pcs or {})
        self._encrypted: set[str] = set(encrypted or ())
        self.confirm_code = confirm_code
        self.on_save = on_save
        self.allow_unencrypted = allow_unencrypted
        self.link_name = link_name
        self._lock = threading.Lock()
        self.receiver = PairingReceiver(self._confirm, self.register, link=self._link)

    def _confirm(self, remote_name: str, code: str, replaces: Optional[str] = None) -> bool:
        if not self.confirm_code:
            return False
        if replaces:
            return bool(self.confirm_code(remote_name, code, replaces=replaces))
        return bool(self.confirm_code(remote_name, code))

    def _saved_name(self, peer_id: str) -> str:
        name = ""
        if self.link_name:
            try:
                name = self.link_name(peer_id) or ""
            except Exception:
                logging.exception("Couldn't read the name of a link")
        return name or f"PC {peer_id[:8]}"

    def _link(self, peer_id: str) -> Optional[tuple[bool, str]]:
        """(encrypted, name stored here) if this PC is already linked with that one; None otherwise."""
        with self._lock:
            if peer_id not in self._pcs:
                return None
            encrypted = peer_id in self._encrypted
        return encrypted, self._saved_name(peer_id)

    def key_pc(self, received: str, by_tls: bool) -> str:
        """The id of the encrypted linked PC whose token is `recibida`, if it came over TLS; otherwise ""."""
        if not received or not by_tls:
            return ""
        rec_bytes = received.encode("utf-8")
        with self._lock:
            pair_list = [(peer, c) for peer, c in self._pcs.items() if peer in self._encrypted and c]
        for peer, key in pair_list:
            if hmac.compare_digest(rec_bytes, key.encode("utf-8")):
                return peer
        return ""

    def register(self, peer_id: str, token: str, ca_pem: Optional[str] = None):
        """Stores a new link (with the other PC's CA if it paired with protocol 2)."""
        with self._lock:
            self._pcs[peer_id] = token
            if ca_pem:
                self._encrypted.add(peer_id)
            else:
                self._encrypted.discard(peer_id)
        if self.on_save:
            try:
                if ca_pem:
                    self.on_save(peer_id, token, ca_pem)
                else:
                    self.on_save(peer_id, token)
            except Exception:
                logging.exception("Couldn't save the link with another PC")

    def is_encrypted(self, peer_id: str) -> bool:
        with self._lock:
            return peer_id in self._encrypted

    def start_pairing(
        self, remote_name: str, remote_id: str, proposed_code: str = ""
    ) -> tuple[bool, str, str]:
        """Protocol 1 (older CopyOver): the code arrives in plain text and the key goes back in plain text."""
        from pc_pairing import visible_name

        code = proposed_code if (len(proposed_code) == 6 and proposed_code.isdigit()) else f"{secrets.randbelow(1_000_000):06d}"
        if not self.allow_unencrypted or self.is_encrypted(remote_id):
            logging.warning("Rejected an unencrypted pairing from %s", remote_id[:8])
            return False, code, ""
        if not self.confirm_code:
            return False, code, ""
        existing = self._link(remote_id)
        try:
            accepted = self._confirm(visible_name(remote_name), code,
                                       replaces=visible_name(existing[1]) if existing else None)
        except Exception:
            accepted = False
        if not accepted:
            return False, code, ""

        token = secrets.token_urlsafe(18)
        self.register(remote_id, token)
        return True, code, token

    def pc_valid_key(self, received: str, by_tls: bool = True) -> bool:
        """`by_tls`: whether the request came encrypted. An encrypted computer's key isn't valid over plain
        HTTP."""
        if not received:
            return False
        rec_bytes = received.encode("utf-8")
        with self._lock:
            keys = [c for peer, c in self._pcs.items() if by_tls or peer not in self._encrypted]
        return any(hmac.compare_digest(rec_bytes, c.encode("utf-8")) for c in keys if c)

    def remember_key(self, peer_id: str, token: str, encrypted: bool = False):
        """Refresh credentials saved by a local pairing action without invoking its persistence callback."""
        with self._lock:
            self._pcs[peer_id] = token
            if encrypted:
                self._encrypted.add(peer_id)
            else:
                self._encrypted.discard(peer_id)

    def forget_pc(self, peer_id: str):
        with self._lock:
            self._pcs.pop(peer_id, None)
            self._encrypted.discard(peer_id)

    def id_for_key(self, key):
        with self._lock:
            return next((peer for peer, token in self._pcs.items() if hmac.compare_digest(key.encode(), token.encode())), None)


class TransfersManager:
    """Coordinates offers, user confirmations and chunked reception to disk."""

    def __init__(
        self,
        destination: Path | Callable[[], Path],
        ask_user: Optional[Callable[[dict], Optional[str]]] = None,
        trusted_pcs: Optional[set[str]] = None,
        on_trust: Optional[Callable[[str], None]] = None,
        to_progress: Optional[Callable[[Optional[dict]], None]] = None,
        offer_time: float = OFFER_TIME,
        max_bytes: Optional[int] = MAX_BYTES_P2P,
    ):
        self._destination = destination
        self.ask_user = ask_user
        self.trusted_pcs: set[str] = set(trusted_pcs or ())
        self.on_trust = on_trust
        self.to_progress = to_progress
        self.offer_time = offer_time
        self.max_bytes = max_bytes
        self._transfers: dict[str, dict] = {}
        self._revocations = {}
        self._lock = threading.Lock()

    def _notify_progress(self, info: Optional[dict]):
        if self.to_progress:
            try:
                self.to_progress(info)
            except Exception:
                pass

    def forget_pc(self, peer_id):
        with self._lock:
            self._revocations[peer_id] = self._revocations.get(peer_id, 0) + 1
            self.trusted_pcs.discard(peer_id)
            pending = [tid for tid, trans in self._transfers.items() if trans['sender']['id'] == peer_id]
        for tid in pending:
            self.cancel_transfer(tid)

    def validate_offer(self, data: object, check_space=True) -> tuple[bool, int, str]:
        if not isinstance(data, dict):
            return False, 400, t("Invalid transfer request format")
        de = data.get("sender")
        files = data.get("files")
        if not isinstance(de, dict) or not de.get("name") or not de.get("id"):
            return False, 400, t("Missing information about the sending computer")
        if not isinstance(files, list) or not files:
            return False, 400, t("The transfer request contains no files")

        total = 0
        for item in files:
            if not isinstance(item, dict):
                return False, 400, t("Invalid file entry")
            name = item.get("name")
            size = item.get("size")
            sha256 = item.get("sha256")
            if not is_valid_name(name):
                return False, 400, t("File name rejected: {name}", name=repr(name))
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                return False, 400, t("Invalid file size")
            if not isinstance(sha256, str) or not _HEX_SHA256.match(sha256):
                return False, 400, t("Invalid SHA-256 hash")
            total += size

        if self.max_bytes is not None and total > self.max_bytes:
            return False, 413, t("The transfer exceeds the allowed limit (10 GB)")
        if check_space:
            return self._check_space(total)
        return True, 200, 'OK'

    @property
    def destination(self) -> Path:
        """The folder can be changed in the panel while running, so it's looked up every time."""
        return self._destination() if callable(self._destination) else self._destination

    def _check_space(self, total):
        try:
            free = shutil.disk_usage(self.destination).free
            if free < total + 50 * 1024 * 1024:
                return False, 507, t("Not enough disk space (free: {free}, required: {required})",
                                      free=format_size(free), required=format_size(total))
        except OSError:
            pass
        return True, 200, "OK"

    def _decide_offer(self, data: dict) -> Optional[str]:
        peer_id = str(data["sender"]["id"])
        with self._lock:
            if peer_id in self.trusted_pcs:
                return "accept"
        if not self.ask_user:
            return None
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(self.ask_user, data)
            try:
                return fut.result(timeout=self.offer_time)
            except FuturesTimeoutError:
                return None

    @staticmethod
    def _files_signature(files):
        return [(a['name'], a['size'], a['sha256'].lower()) for a in files]

    def _pending_offer(self, data, owner_hash):
        signature = self._files_signature(data['files'])
        for tid, trans in self._transfers.items():
            matches = (trans.get('resumable') and trans.get('owner') == owner_hash
                        and trans['sender']['id'] == data['sender']['id']
                        and self._files_signature(trans['files']) == signature)
            if matches and not all(a['received'] for a in trans['files']):
                trans['activity'] = time.monotonic()
                return {'id': tid, 'resume': True}
        return None

    def process_offer(self, data: object, owner=None, authorize=None) -> tuple[int, dict | str]:
        valid, code, msg = self.validate_offer(data, check_space=False)
        if not valid:
            return code, msg
        self.clean_expired()
        peer_id = str(data['sender']['id'])
        owner_hash = self._owner_hash(owner)
        resumable = data.get('resume') is True
        with self._lock:
            review = self._revocations.get(peer_id, 0)
            if authorize and not authorize():
                return 403, t('The pairing is no longer authorized')
            recovered = self._pending_offer(data, owner_hash) if resumable else None
            if recovered:
                return 200, recovered
        space, code, msg = self._check_space(sum(a['size'] for a in data['files']))
        if not space:
            return code, msg
        decision = self._decide_offer(data)
        if decision is None:
            return 408, t('The transfer request expired without a response')
        if decision not in ('accept', 'always'):
            return 403, t('Transfer declined by the user')
        return self._register_offer(data, owner_hash, resumable, decision, review, authorize)

    def _register_offer(self, data, owner_hash, resumable, decision, review, authorize):
        peer_id = str(data['sender']['id'])
        id_trans = secrets.token_urlsafe(12)
        files = [dict(name=a['name'], size=a['size'], sha256=a['sha256'].lower(),
                         received=False, final_path=None, partial_path=None)
                    for a in data['files']]
        with self._lock:
            if review != self._revocations.get(peer_id, 0):
                return 403, t('The computer was unpaired during confirmation')
            if authorize and not authorize():
                return 403, t('The pairing is no longer authorized')
            if decision == 'always':
                self.trusted_pcs.add(peer_id)
            self._transfers[id_trans] = {
                'id': id_trans, 'sender': {'name': str(data['sender']['name']), 'id': peer_id},
                'files': files, 'created': time.monotonic(), 'cancelled': False,
                'resumable': resumable, 'owner': owner_hash, 'activity': time.monotonic(),
            }
        if decision == 'always' and self.on_trust:
            try:
                self.on_trust(peer_id)
            except Exception:
                pass
        return 200, {'id': id_trans, 'resume': resumable}

    @staticmethod
    def _owner_hash(owner):
        return hashlib.sha256(owner.encode('utf-8')).hexdigest() if owner is not None else None

    def clean_expired(self):
        with self._lock:
            expired = [tid for tid, trans in self._transfers.items()
                        if time.monotonic() - trans.get('activity', trans['created']) > 86400
                        and not any(a.get('busy') for a in trans['files'])]
        for tid in expired:
            self.cancel_transfer(tid)

    def resume_state(self, id_trans, owner=None):
        self.clean_expired()
        with self._lock:
            trans = self._transfers.get(id_trans)
            if not trans or not trans.get('resumable') or trans['owner'] != self._owner_hash(owner):
                return 404, t('Transfer not found')
            if any(a.get('busy') for a in trans['files']):
                return 409, t('The receiver is finishing the previous attempt')
            trans['activity'] = time.monotonic()
            files = []
            for info in trans['files']:
                partial = info.get('partial_path')
                offset = info['size'] if info['received'] else partial.stat().st_size if partial and partial.exists() else 0
                files.append({'offset': offset, 'received': info['received']})
            return 200, {'files': files}

    def get_transfer(self, id_trans: str) -> Optional[dict]:
        with self._lock:
            return self._transfers.get(id_trans)

    def receive_file(self, id_trans, index, stream, length, offset=0, owner=None, authorize=None):
        from p2p_receive import receive
        return receive(self, id_trans, index, stream, length, offset, owner, authorize)

    def cancel_transfer(self, id_trans: str, owner=None) -> bool:
        with self._lock:
            trans = self._transfers.get(id_trans)
            if not trans or (owner is not None and trans.get('owner') != self._owner_hash(owner)):
                return False
            self._transfers.pop(id_trans)
            trans["cancelled"] = True
            partial = [a.get("partial_path") for a in trans["files"] if a.get("partial_path")]

        for p in partial:
            self._delete_safe(p)
        self._notify_progress(None)
        return True

    @staticmethod
    def _delete_safe(path: Optional[Path]):
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


# --- P2P client (sender and pairing) ------------------------------------------


def prepare_offer(my_name: str, my_id: str, paths: list[Path]) -> dict:
    files = []
    for r in paths:
        if not r.is_file():
            raise ValueError(t("Not a valid file: {path}", path=r))
        files.append({
            "name": r.name,
            "size": r.stat().st_size,
            "sha256": compute_sha256(r),
        })
    return {
        "sender": {"name": my_name, "id": my_id},
        "files": files,
    }


def bundle_if_is_directory(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Compresses any directories into temporary ZIP files. Returns (paths_to_send, temporary_files_to_clean).
    """
    import private_files
    final = []
    temp_paths = []
    for r in paths:
        if r.is_dir():
            zip_name = f"{r.name}.zip"
            tmp = private_files.temporary_folder() / f"puente_{secrets.token_hex(4)}_{zip_name}"
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for child in r.rglob("*"):
                    if child.is_file():
                        z.write(child, child.relative_to(r.parent))
            final.append(tmp)
            temp_paths.append(tmp)
        else:
            final.append(r)
    return final, temp_paths


def open_connection(ip: str, port: int, timeout: float, ca_pem: Optional[str] = None) -> http.client.HTTPConnection:
    """HTTPS that trusts only the CA stored when pairing; without a CA (older link), HTTP as before."""
    if not ca_pem:
        return http.client.HTTPConnection(ip, port, timeout=timeout)
    import certificates
    return http.client.HTTPSConnection(ip, port, timeout=timeout, context=certificates.client_context(ca_pem))


def tls_error_message(error: ssl.SSLError, name: str) -> str:
    """Why the encrypted connection failed, in the person's words."""
    if isinstance(error, ssl.SSLCertVerificationError):
        return t("The other PC is not the one you paired: its certificate does not match, so nothing was sent. If "
                 "you reinstalled CopyOver on {name}, unlink it and link it again.", name=name)
    return t("{name} did not answer with an encrypted connection and nothing was sent. If it went back to an older "
             "version of CopyOver, update it.", name=name)


def send_to_pc(
    ip: str,
    port: int,
    key: str,
    my_name: str,
    my_id: str,
    paths: list[Path],
    to_progress: Optional[Callable[[int, int, str], None]] = None,
    offer_timeout: float = 65.0,
    reconnection_wait: float = 300.0,
    retry_interval: float = 2.0,
    resolve_destination=None,
    cancel=None,
    destination_name: str = "",
    connection_timeout: float = 5.0,
    ca_pem: Optional[str] = None,
) -> tuple[bool, str]:
    """Offers and uploads a list of files to another CopyOver in chunks.

    Connecting has its own short timeout: a PC that's no longer on the network doesn't answer at all and, with
    the offer's timeout, Windows took 21 s to give up. Once connected, we wait for the offer's answer (the
    other side may be asking whether to accept).

    With `ca_pem` (the other PC's CA, stored when pairing) everything goes over TLS and, if the other PC
    doesn't present a certificate from that CA, nothing is sent: neither the key nor the files."""
    proc_paths, temp_paths = bundle_if_is_directory(paths)
    try:
        offer = prepare_offer(my_name, my_id, proc_paths)
        offer['resume'] = True
        offer_body = json.dumps(to_wire(offer)).encode("utf-8")
        headers_base = headers_to_wire({"X-Key": key})

        conn = open_connection(ip, port, connection_timeout, ca_pem)
        try:
            conn.connect()
            conn.sock.settimeout(offer_timeout)
        except ssl.SSLError as error:
            conn.close()
            logging.warning("Encrypted connection with %s rejected: %s", ip, error)
            return False, tls_error_message(error, destination_name or ip)
        except OSError:
            conn.close()
            return False, t("{name} wasn’t found on this network", name=destination_name or ip)
        try:
            conn.request(
                "POST",
                route_to_old("/p2p/offer"),
                body=offer_body,
                headers={**headers_base, "Content-Type": "application/json", "Content-Length": str(len(offer_body))},
            )
            resp = conn.getresponse()
            resp_data = resp.read().decode("utf-8", errors="replace")
            if resp.status == 403:
                return False, resp_data or t("Rejected or wrong key")
            if resp.status == 408:
                return False, t("The other computer didn’t respond in time (60 s)")
            if resp.status != 200:
                return False, t("Error {code}: {detail}", code=resp.status, detail=resp_data)
            accepted = from_wire(json.loads(resp_data))
            id_trans = accepted['id']
        except Exception as e:
            return False, t("Couldn’t connect to {ip}:{port}: {error}", ip=ip, port=port, error=e)
        finally:
            conn.close()

        from p2p_send import upload_files
        return upload_files(ip, port, key, id_trans, proc_paths, offer,
                              accepted.get('resume') is True, to_progress,
                              reconnection_wait, retry_interval, resolve_destination, cancel,
                              ca_pem=ca_pem, destination_name=destination_name or ip)
    finally:
        for temporary in temp_paths:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def pair_with_pc(
    ip: str,
    port: int,
    my_name: str,
    my_id: str,
    confirm_code: Optional[Callable[[str], bool]] = None,
    my_ca_pem: str = "",
    expected_id: str = "",
    allow_unencrypted: bool = True,
    announces_v2: bool = False,
    current_ca: Optional[str] = None,
    current_token: str = "",
) -> tuple[bool, str, str, Optional[str]]:
    """Pairs with another PC. Returns (ok, code, token or error, the other PC's CA or None if unencrypted).

    `confirm_code(código)` asks the person at this PC whether they see the same code on the other one; it
    runs while the other PC waits for its own confirmation. Without it, it's accepted automatically (tests and
    scripts). Without `my_ca_pem` or with a PC running an older CopyOver, protocol 1 is used, unencrypted, if
    allowed and the other PC doesn't announce protocol 2 (`announces_v2`, from the mDNS announcement).
    `current_ca` and `current_token`: the encrypted link we already have with it, to replace it."""
    from pc_pairing import OutgoingPairing, PairingError

    output = OutgoingPairing(ip, port, my_name, my_id, my_ca_pem, expected_id, allow_unencrypted,
                                    announces_v2=announces_v2, current_ca=current_ca, current_token=current_token)
    try:
        code = output.prepare()
    except PairingError as error:
        return False, "", str(error), None
    response: dict = {}

    def wait():
        try:
            response["token"] = output.wait_response()
        except PairingError as error:
            response["error"] = str(error)
        except Exception as error:
            response["error"] = t("Connection error: {error}", error=error)

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    accepted = True
    if confirm_code:
        try:
            accepted = bool(confirm_code(code))
        except Exception:
            accepted = False
    # Even if this person rejects, we wait for the other PC: with protocol 2 it stores nothing without the
    # confirmation.
    thread.join(output.response_timeout + 5)
    if not accepted:
        return False, code, t("Rejected"), None
    if "token" not in response:
        return False, code, response.get("error") or t("Rejected"), None
    try:
        output.confirm()
    except PairingError as error:
        return False, code, str(error), None
    return True, code, output.token, output.remote_ca


def _try_cancel(ip: str, port: int, key: str, id_trans: str, ca_pem: Optional[str] = None):
    conn = open_connection(ip, port, 5, ca_pem)
    try:
        conn.request("POST", route_to_old(f"/p2p/cancel/{id_trans}"), headers=headers_to_wire({"X-Key": key, "Content-Length": "0"}))
        conn.getresponse().read()
    except Exception:
        pass
    finally:
        conn.close()


def query_local_pcs(port: int, secret: str) -> Optional[list[dict]]:
    """Asks the local CopyOver (127.0.0.1) for the nearby computers it already found, with the local secret (see
    local_secret.py) and not the shared key, in case another program holds the port."""
    import local_secret
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", route_to_old("/p2p/peers"), headers=headers_to_wire({local_secret.HEADER: secret}))
        resp = conn.getresponse()
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    finally:
        conn.close()
    return None


def discover_network_pcs(my_id: str, wait: float = 2.0) -> list[dict]:
    """Looks for computers over mDNS directly if the local server isn't running."""
    from zeroconf import IPVersion, ServiceBrowser, Zeroconf

    obs = PcsObserver(my_id)
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    try:
        nav = ServiceBrowser(zc, SERVICE_TYPE, obs)
        time.sleep(wait)
        nav.cancel()
        return obs.list_all()
    finally:
        zc.close()
