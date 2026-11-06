# Security

CopyOver moves your clipboard and files between devices on the same local network.
This page explains what it protects, what it doesn’t, and how to report a problem.

## Reporting a vulnerability

Please report it privately, not in a public issue: through [GitHub security advisories](https://github.com/aritabryan/copyover/security/advisories/new) or by email to [copyover28@gmail.com](mailto:copyover28@gmail.com).
Include the version, the system and the steps to reproduce it. It’s a one-person project, so answers may take a few days.
Only the latest release gets security fixes.

## Threat model

**Who CopyOver trusts:** you, the PC it runs on, and the network you’re connected to.
It is built for a home or office Wi-Fi, or your own phone’s hotspot.

**Who it defends against:** other devices on the same network that don’t have a key.
They can see that CopyOver is running, but they can’t read your clipboard, send you files or pick up what you copied.

**Who it does not defend against:** someone who can capture the traffic between your PC and a phone or browser, or someone with access to your PC’s files.
Other accounts on the same PC can’t use the panel or the local helpers (see *The panel and other accounts on this PC* below), but CopyOver doesn’t protect your files from the PC’s administrators.

### Between PCs: TLS with each PC’s own certificate

The server listens on port 8765 and answers HTTP and TLS on that same port: it looks at the first byte of each connection.
Each PC creates its own certificate authority (EC P-256, valid for 10 years) and signs a server certificate with it for its `.local` name and its private IP addresses (renewed every year, and as soon as the name or an address changes).
The authority is limited with NameConstraints to `.local` names and private ranges, so even a stolen key couldn’t sign a certificate for a site on the internet.
Private keys live in the `certificates` folder next to `config.json`, readable only by your user.

**Pairing (protocol 2)** works like Bluetooth’s numeric comparison:

1. The PC that asks sends a commitment (the SHA-256 hash of its X25519 public key).
2. The other PC answers with its own public key, and only then the first one reveals its key. A man in the middle must commit to a key before seeing the other one, so he gets a single guess.
3. Both PCs derive keys with HKDF-SHA256 over a transcript of the session (IDs and both public keys) and show the same 6-digit code, computed from that transcript. You compare it on both screens.
4. Each PC’s certificate authority and the 144-bit token travel encrypted with AES-256-GCM, and each side proves it holds the keys before anything is stored.

The code isn’t a secret that travels, so capturing the traffic doesn’t help to guess it. An attacker in the middle makes the two screens show different codes, and has one chance in a million per attempt of making them match. Each session allows a single attempt. A session nobody has answered yet expires after 15 seconds and doesn’t hold up anyone else; once a person is looking at the code, other requests wait until they answer or the window closes. Each address can start 5 sessions per minute, with a higher overall limit so nobody can flood the PC.

The name the other PC sends is shown on a single line, without control or formatting characters and cut at 40 characters, so it can’t imitate other text in the window.

**Replacing a link.** If a PC asks to pair with an ID that is already linked, the result depends on the link:

- an encrypted link can only be replaced by that same PC, over TLS with its current token. Anyone else is refused before a window opens;
- a link from an earlier version can be replaced, and the window says *This replaces the link with …* with the name saved on this PC, not the one the other PC sends.

After pairing, every connection to that PC uses TLS checked against the authority stored for it, and its token is only accepted over TLS. If the other PC answers with a different certificate, CopyOver sends nothing and says so.

**PCs with an earlier version.** They don’t know protocol 2, so pairing with them falls back to the earlier protocol and the link stays on plain HTTP. The panel shows those links as *Not encrypted: link again*, and the log notes it on every send. Once both PCs are updated, linking them again encrypts the connection.
A PC paired with protocol 2 never goes back to plain HTTP with that other PC, and never accepts the earlier protocol from it.
Every PC with encryption also announces it on the network (`empareja=2` in its mDNS record). If the PC you chose announces it and then answers like an earlier version, the pairing stops with a warning instead of falling back: that only happens when someone is in the middle.
What remains: an attacker who also blocks or rewrites the mDNS announcements can make a first pairing with a PC you found by address fall back to the earlier protocol. Set `"emparejar_sin_cifrar": false` in `config.json` to refuse pairings without encryption.

### Android: TLS pinned to the PC's authority

When an Android phone links, the settings the PC delivers (encrypted, see below) include the PC's certificate authority and its SHA-256 fingerprint. The app checks one against the other and stores the authority next to the key, encrypted with the Android Keystore.
From then on the app talks to that PC over TLS on the same port, and only accepts a certificate chain that ends in that authority. The host name is not checked, because the PC is reached by whatever IP address it has today; the signature, the dates and the authority's name constraints are. If the PC answers with another certificate, nothing is sent (not even the key) and the app says so.
A phone linked with an earlier version has no authority stored, so it keeps using plain HTTP. The app shows that PC as *Not encrypted* until it is linked again. Older apps ignore the new fields and keep working over HTTP.

### iPhone and browsers: no TLS

The iPhone shortcuts, the panel and the guest page still use plain HTTP.
Anyone on the same network who can capture packets (an untrusted Wi-Fi, a compromised router, a café network without client isolation) can see:

- the text, images and files you send and receive with a phone or a browser;
- the `X-Key` header, that is, the shared PC key or the device’s own key;
- the setup page that a phone opens after scanning the QR code, which contains that phone’s key;
- transfers with Android phones and PCs linked with an earlier version (see above).

With a captured key, that person can act as your device until you remove it from the panel or change the shared key (*Settings → Change the shared key*).
**Use CopyOver only on networks you trust.** On public networks, connect the PC to your phone’s hotspot instead.

Linking an Android phone is the exception: its settings are delivered encrypted (X25519, HKDF-SHA256 and AES-256-GCM), and you confirm a 6-digit code on both screens before anything is sent. Everything after that goes over TLS, as described above.

### What the keys protect

- **Shared PC key** (`key` in `config.json`): random, 72 bits. It authorizes the shortcuts and the PCs that share it. It is stored in plain text in `config.json`. *Settings → Change the shared key* replaces it at once, without restarting: shortcuts set up by hand and devices linked with an earlier version stop working until you link them again with the QR code. Phones with their own key and PCs paired with a code are not affected.
- **Per-device keys**: each phone linked by QR code or from the Android app gets its own random 192-bit key. The PC only keeps its SHA-256 hash, so reading `config.json` doesn’t reveal it. Removing the device from the panel revokes it right away, and also ends a connection that is still waiting for the clipboard (Android’s automatic receiving) within about a second. Changing the shared key does the same for the devices that use it.
- **PC-to-PC tokens**: two PCs paired with a 6-digit code get a 144-bit token. It is stored in plain text in `config.json`, next to the other PC’s certificate authority and its SHA-256 fingerprint, and unlinking revokes it.
- Keys are generated with Python’s `secrets` module and compared in constant time.

### Confirmations and checks

- **Sending between PCs** asks the recipient to **Accept / Decline** each offer (it expires after 60 seconds), unless you ticked *Automatically accept from this computer next time*.
- Every PC-to-PC file is checked with **SHA-256** before it is saved. The sender declares that hash, so it detects corruption, not a malicious sender.
- File names from other devices are cleaned: no folders, no `..`, no Windows reserved names.
- **The panel** (`/app`) only answers requests from the PC itself, with a `127.0.0.1` or `localhost` host, to block DNS rebinding and other sites in your browser, and only with the local secret described below.
- The QR code for a phone works once and expires after 5 minutes.

### The panel and other accounts on this PC

Any account on the same computer can reach `127.0.0.1`, so coming from the PC itself isn’t proof that it’s you.
Every time CopyOver starts, once it has the port, it creates a random 256-bit secret. It keeps it in an environment variable that its own windows inherit (the panel and the quick view) and in the `local-secret` file next to `config.json`, readable only by your user: on Linux it is created with `0600` permissions, and on Windows its inherited permissions are removed before the secret is written into it.

- **Every `/app` route** asks for that secret on top of the address and the `Host`/`Origin` checks. The panel window opens `/app/enter` with it once, and CopyOver answers with an `HttpOnly`, `SameSite=Strict` cookie limited to `/app`, so the page’s scripts never see it. If the panel has to open in a browser, it goes through a private local page, so the secret doesn’t appear on the browser’s command line. Typing `http://127.0.0.1:8765/app` by hand gets *Open the panel from the CopyOver icon*.
- **The local helpers** (*Send with CopyOver* from Explorer, the buttons in notifications, opening CopyOver again and the tray’s *Send to phone*) send the secret in an `X-Local-Secret` header. They never send the shared key to `127.0.0.1`: if another program held the port, it would get a secret that stops working on the next start.
- **On Windows** the server opens its port with `SO_EXCLUSIVEADDRUSE`, so no other program can listen on the same port while CopyOver is running.

### Guest sharing

The guest page lets anyone with its link upload files, send text to your clipboard, read the start of your clipboard and download what you prepared or copied, with no key.
Its link carries a random token. A token made from the panel lasts 30 minutes, but **it keeps getting extended while the page stays open**: each time the page checks in, it gets 30 more minutes from then.
Nothing you do on the PC extends a guest link. Sending to your own iPhone or iPad, or starting Wi-Fi Direct, makes a separate link for your device (2 hours) and never reuses one you gave to someone else.
*Link phone → Web app → Cut off guest access* ends every guest link at once, including an upload that is still arriving, and shows a new QR code. Your own device’s links keep working.
Share the link only with people you’d hand your clipboard to, and close the page when you’re done.

## Known limitations

These are known and still open. Fixes are welcome.

- **No TLS for the iPhone and browsers.** See above.
- **Wrong keys only slow an attacker down.** After 10 wrong keys from one IP address within a minute, that address gets `429` until the minute is over, even with the right key. That's plenty against a 72-bit key, but a short key set by hand in `config.json` can still be guessed slowly.
- **Pairing requests with the earlier protocol are not rate-limited.** Each request opens a confirmation window on the desktop. Protocol 2 allows one person deciding at a time and 5 sessions per minute from each address.
- **`/identity` answers anyone.** It returns an HMAC of the challenge the caller chooses, which proves the PC knows the key without revealing it.
- **Disk use:** a send from the iPhone and a guest upload are written to disk as they arrive, up to 10 GB, so they don't fill the memory. Before reading, CopyOver checks that the Downloads folder has room; a send that is cut off is deleted.
- **Files at rest:** on Linux, `config.json`, `history.json`, `copyover.log` and the private keys are readable only by your user (0600), and CopyOver closes its data folder to 0700 when it starts. On Windows they keep the data folder’s permissions: the installer puts it in your profile (`%LOCALAPPDATA%\Programs\CopyOver`), which other users can’t read, but a portable copy unzipped in a shared folder is readable by whoever can read that folder. Only the private keys get their own ACL on Windows. Received files use the Downloads folder’s normal permissions.
- **Tokens in the log:** `copyover.log` writes `***` in place of the values of `t`, `token`, `clave`, `key` and `k` in any URL, so a guest or setup link can’t be taken from it. Logs from earlier versions may still have them.
- **Temporary files:** the clipboard image, the zips for the phone and the files dragged onto the panel go in a folder of your own: `%TEMP%` on Windows, and `copyover-<uid>` (0700, checked before each use) inside the temp folder on Linux. If that name is taken by someone else or is a link, CopyOver uses a new private folder with a random name.

## What CopyOver never does

- It doesn’t use a cloud service or an account. Nothing leaves your network, except the update check, which reads the public GitHub releases API.
- It doesn’t send telemetry.
- Phones get Android updates from the linked PC only, after checking the SHA-256, the package name, a higher version and the same signing certificate.
