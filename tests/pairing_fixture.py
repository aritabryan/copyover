"""Local server for the Rust/Python cross tests; never opens dialogs."""
import sys
import tempfile
import hashlib
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge
from pairing import PairingManager

cfg = {"id": "a" * 32, "key": "fixture-only-key", "port": 8765, "name": "fixture-pc"}
certs = None
if '--tls' in sys.argv:
    # TLS and HTTP on the same port, with its own CA in a temporary folder, like the real PC.
    import certificates
    certificates_folder = tempfile.TemporaryDirectory()
    certs = certificates.Certificates(certificates_folder.name, "fixture-pc", ips=lambda: ["127.0.0.1"])
    certs.ensure()
    server = bridge.BridgeServer(("127.0.0.1", 0), bridge.Handler)
    server.certificates = certs
else:
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
cfg["port"] = server.server_address[1]
bridge.Handler.key = cfg["key"]
bridge.Handler.identifier = cfg["id"]
issue_key = None
if certs:
    # Like the real PC: the Android device gets its own key, kept as a hash in a temporary config.
    import configuration
    config_folder = tempfile.TemporaryDirectory()
    configuration.CONFIG = Path(config_folder.name) / "config.json"
    configuration.CONFIG.write_text(json.dumps(cfg), encoding="utf-8")
    issue_key = bridge.issue_android_key
bridge.Handler.links = PairingManager(cfg, lambda *_: "--reject" not in sys.argv, issue_key=issue_key,
                                                ca_pem=certs.ca_pem if certs else None)
if '--updates' in sys.argv:
    from updates import UpdateCache
    update_directory = tempfile.TemporaryDirectory()
    directory = Path(update_directory.name)
    apk = b'PK-update-fixture'
    manifest = dict(versionCode=4, versionName='0.4.0', size=len(apk), sha256=hashlib.sha256(apk).hexdigest())
    (directory / (manifest['sha256'] + '.apk')).write_bytes(apk)
    (directory / 'latest.json').write_text(json.dumps(manifest))
    bridge.Handler.updates = UpdateCache(directory)
if '--sync' in sys.argv:
    from sync import ClipboardChanges
    bridge.Handler.changes = ClipboardChanges(lambda: 5)
    bridge.so.read_files = lambda: []
    bridge.so.read_text = lambda: 'Windows → Android 👋'
print(server.server_address[1], flush=True)
server.serve_forever()
