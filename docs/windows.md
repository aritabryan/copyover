# CopyOver on Windows

[← Back to the README](../README.md)

Windows 10 and 11.

- [Install](#install)
- [Updates, uninstalling and the portable version](#updates-uninstalling-and-the-portable-version)
- [Running from source](#running-from-source)
- [Tray icon and Start menu](#tray-icon-and-start-menu)
- [Command line](#command-line)

## Install

Download **[CopyOver-Setup.exe](https://github.com/aritabryan/copyover/releases/latest)**, open it and click **Install**. No admin rights, no Python. CopyOver opens next to the clock and walks you through the rest.

> [!NOTE]
> The installer isn’t signed yet. If Windows shows «Windows protected your PC», click **More info → Run anyway**.

The installer registers autostart, adds the **Start menu** entry (search for "CopyOver"), sets up the right-click menu and, if needed, the firewall rule.

## Updates, uninstalling and the portable version

- **Updates:** when a new version is out, the panel says so. One click downloads it, checks its SHA-256 and installs it over the current one; settings and history stay.
- **Uninstall:** Settings → Apps → CopyOver. Everything CopyOver wrote is removed; only the files you received stay, in *Documents/From CopyOver* (or the folder you chose).
- **Portable:** `CopyOver-windows.zip`: unzip it and open `CopyOver.exe`.
- **Firewall:** if the panel says connections are blocked, click **Allow in firewall**.

## Running from source

With Python 3.10 or newer (this also works on Linux; see the [Linux guide](linux.md)):

```powershell
python -m pip install -r requirements.txt
python copyover.py --install
python copyover.py --info
```

To build the installer and the zip yourself, install [Inno Setup 6](https://jrsoftware.org/isinfo.php) and run:

```powershell
.\build.ps1
```

It leaves `dist\CopyOver-Setup-<version>.exe` and `dist\CopyOver-windows.zip`.

## Tray icon and Start menu

- **Windows search:** type `CopyOver` to start it without a console or browser.
- **System tray (next to the clock):**
  - **Left- or right-click:** opens the same compact [quick view](panel.md#quick-view), with the history visible and fixed buttons.
  - **History**, **Linked computers** and **Settings**: saved copies, devices and preferences (Start with Windows, file notifications and language).
  - **Send to phone:** opens the Windows picker. On Android, accept in CopyOver → Files; on iPhone, use Receive PC.
  - **Send to another PC:** pick the recipient and the files. If they aren’t linked yet, confirm the code first. Accept on the other PC.
  - **Link** (under Linked computers): *iPhone or iPad* opens the panel’s QR code, *Android* shows how to find the PC from the app, and *Another PC* connects with the same code on both PCs.
  - **Open panel**, **Open folder** (where received files are saved) and **Quit**.
- <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>O</kbd> opens the picker to send files from anywhere.
- Icon color: **green** means active and discoverable on the local network; **yellow** means no network or no local connection.

## Command line

| Command | What it does |
|---|---|
| `python copyover.py` | Starts with a console window (for testing) |
| `python copyover.py --info` | Shows the address and key for the shortcuts |
| `python copyover.py --install` | Registers autostart, adds the Start menu shortcut and **Send with CopyOver** to the right-click menu |
| `python copyover.py --uninstall` | Removes autostart and the right-click menu |
| `python copyover.py --link` | Explains how to link an Android phone (open CopyOver on Android, pick this PC, accept on the desktop) |
| `python copyover.py --send <files...>` | Sends files to another PC; see [PC to PC](pc-to-pc.md) |
| `python copyover.py --pair [computer]` | Pairs with someone else’s PC using a 6-digit code |

Settings live in `config.json`; see [Settings](configuration.md). The log (`copyover.log`) is capped at 10 MB and keeps two older copies.
