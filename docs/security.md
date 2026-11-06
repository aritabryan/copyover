# Security and limits

[← Back to the README](../README.md)

This is the short version. The full threat model, what the key protects and what it doesn’t, and how to report a problem are in [SECURITY.md](../SECURITY.md).

## What CopyOver does

- It only listens on the local network; nothing is exposed to the internet. There’s no account and no cloud.
- Transfers require the `X-Key` header (except `/ping`, the local `/app` and temporary pairing).
- Each iPhone, iPad or Android phone gets its own key when linked, and removing it from the panel revokes it. *Settings → Change the shared key* replaces the PC’s shared key without restarting.
- The local panel (`/app`) and the local helpers also ask for a secret that changes on every start and that only your user can read, so another account on the same PC can’t use the panel.
- `/identity` lets a device check the linked PC with a signed challenge, without revealing the key.
- Pairing requests are local, must be accepted on the desktop and deliver the settings encrypted. Compare the code on both devices before confirming.
- Between paired PCs and with the Android app, everything goes over TLS with each PC’s own certificate.
- PC-to-PC sends ask the recipient to **Accept / Decline** (or expire after 60 s), unless they ticked *Automatically accept from this computer next time* for that PC. Every file is checked with **SHA-256** before it’s saved.
- Guests only get what you send to their own card, and *Cut off guest access* ends every guest link at once.
- On Linux, `config.json`, the history and the log are readable only by your user, and temporary files go in a private folder instead of the shared `/tmp`.

## What it doesn’t protect

> [!WARNING]
> The iPhone shortcuts and the browser pages use plain HTTP. Inside your network, someone capturing traffic can see what’s sent and the key. Use them on networks you trust.

CopyOver doesn’t protect your files from the PC’s administrators, nor from someone with access to your PC’s files.

## Size limits

| Path | Limit |
|---|---|
| Android ↔ PC | No fixed cap, subject to free disk space (update both devices) |
| iPhone → PC, Send PC shortcut | Tested up to 2 GB |
| iPhone → PC, browser page | 10 GB, written to disk as it arrives |
| PC → iPhone, Receive PC | 500 MB |
| PC → iPhone, browser page | 10 GB |
| PC ↔ PC | 10 GB |

Transfers go in 1 MB chunks straight to disk, to keep RAM use low. More in [Large files](iphone-shortcuts.md#large-files).

## Reporting a vulnerability

Don’t open a public issue. Report it privately through [GitHub security advisories](https://github.com/aritabryan/copyover/security/advisories/new), as [SECURITY.md](../SECURITY.md) explains.
