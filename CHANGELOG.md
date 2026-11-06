# Changelog

All notable changes to CopyOver. Versions follow [Semantic Versioning](https://semver.org/).

## 1.0.0 — 2026-11-05

First public release.

### PC (Windows and Linux)

- **Tray icon** next to the clock, with a quick view (left click) and a panel in its own window (double click or **Ctrl+Alt+O**).
- **Panel** with your devices, the history of what was sent and received, drag and drop of files and folders, a text box to leave something ready for the phone, and light, dark or system appearance.
- **Everything you receive** goes to *Documents/From CopyOver*; what comes from another PC or a guest gets its own folder inside. Pick another folder in *Settings → Change folder*.
- **English and Spanish** everywhere, following the system language or the one chosen in Settings.
- **PC to PC**: nearby PCs are found on the network, linked with a matching code, and transfers of up to 10 GB resume after a cut.
- **Diagnostics** for the firewall, the network type, cloud-only folders and `.local` names, with a button to allow CopyOver in the firewall.
- **Updates in one click** from the public GitHub releases, with a notice in the tray and the panel.
- **Installer for Windows** (per user, no admin, *Start with Windows*, *Send with CopyOver* in the right-click menu) and a one-line installer for Linux.

### iPhone and iPad

- **Shortcuts** *Send PC*, *Receive PC* and *Link PC*, set up by scanning a QR code. Each device gets its own key, and removing it from the panel revokes it.
- Text, links, photos (HEIC saved as JPG, rotation fixed) and files of up to 2 GB.
- *Receive PC* copies what the PC has to the clipboard; a PDF or any other file that isn’t a photo or video also opens the share sheet.

### Android

- **CopyOver app** that finds the PC on the network and links with a matching code or a QR code.
- Send and receive the clipboard and files from the app, the share menu, the notification, a home screen widget and a Quick Settings tile, with optional automatic sending through Shizuku.
- Clipboard history, transfers that retry on their own, and several linked PCs.
- Updates come from the linked PC, and the app checks for them in the background.

### Guests

- **A QR code for a guest’s phone**, valid for a few minutes, with no app or shortcut to install. The guest gets a page to send to the PC and only receives what you send to their card.

### Security

- Everything stays on the local network: no cloud and no account.
- Each linked device has its own key, PCs talk over TLS checked against each other’s certificate, and the panel only answers this PC. See [SECURITY.md](SECURITY.md).
