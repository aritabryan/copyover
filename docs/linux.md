# CopyOver on Linux

[← Back to the README](../README.md)

Works on Fedora, Ubuntu and Debian, with GNOME or KDE, on Wayland or X11, and with dual boot too.

- [One-line install](#one-line-install)
- [While the repository is private](#while-the-repository-is-private)
- [Install by hand](#install-by-hand)
- [Dual boot and two PCs](#dual-boot-and-two-pcs)
- [Everyday commands](#everyday-commands)
- [Tray icon and firewall](#tray-icon-and-firewall)

## One-line install

One line installs everything and starts it on login:

```bash
curl -fsSL https://raw.githubusercontent.com/aritabryan/copyover/main/install.sh | bash
```

The script shows each step with progress bars. It installs what’s missing with `dnf` (Fedora) or `apt` (Ubuntu and Debian): git, wl-clipboard or xclip, libnotify, PyGObject and Tkinter. It asks for your password once, before installing packages.

Then it downloads CopyOver to `~/copyover`, creates a virtual environment with its libraries and registers the `copyover` user service. CopyOver ends up in the **applications menu**, on the **desktop** (Ubuntu, KDE) and **pinned to the GNOME dock**. At the end it shows how to see the address and how to uninstall.

To install somewhere else:

```bash
COPYOVER_DIR=~/apps/copyover bash install.sh
```

## While the repository is private

While the repository is private, the `curl` line can’t download the script. Sign in to GitHub with an account that has access and run the script from a copy:

```bash
sudo dnf install gh && gh auth login   # on Ubuntu: sudo apt install gh
gh repo clone aritabryan/copyover ~/copyover
~/copyover/install.sh
```

## Install by hand

```bash
sudo dnf install git wl-clipboard libnotify python3-tkinter
# on Ubuntu and Debian:
# sudo apt install git wl-clipboard libnotify-bin python3-gi python3-tk python3-venv gir1.2-ayatanaappindicator3-0.1
git clone https://github.com/aritabryan/copyover.git ~/copyover
cd ~/copyover
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python copyover.py --install
```

Fedora and Ubuntu ship no `pip` for the system Python, hence the virtual environment (`.venv`). `--system-site-packages` lets it use the system PyGObject, which the tray icon needs.

To try it without installing the service: `.venv/bin/python copyover.py` (<kbd>Ctrl</kbd>+<kbd>C</kbd> to stop).

## Update

CopyOver checks for new versions and notifies you. To update, run the installer from your copy again. It pulls the new code and restarts the service:

```bash
~/copyover/install.sh
```

## Dual boot and two PCs

> [!IMPORTANT]
> **With dual boot**, use the same `key` and `name` as on Windows, so the shortcuts work on both systems without changes. If you have **two PCs on at the same time**, use the same `key` but **different `name` values** (for example `pc-windows` and `pc-fedora`).

```bash
cat > config.json <<'EOF'
{"port": 8765, "key": "THE_SAME_KEY_AS_WINDOWS", "name": "THE_SAME_NAME_AS_WINDOWS"}
EOF
```


See [Settings](configuration.md) for every field.

## Everyday commands

| Command | What it does |
|---|---|
| `systemctl --user status copyover` | Check whether it’s running |
| `journalctl --user -u copyover -f` | Follow the log |
| `systemctl --user restart copyover` | Restart it |
| `.venv/bin/python copyover.py --info` | Show the address and key |
| `.venv/bin/python copyover.py --uninstall && rm -rf ~/copyover` | Uninstall: removes autostart, the menu, desktop and dock entries and the right-click entry, and deletes the folder with all its data |

On Linux, the tray’s **Restart** restarts the systemd service, and left-click on the tray icon opens the menu (the quick view is Windows only).

Also Windows only: automatic receiving on Android (use **Get from PC** in the app), Wi-Fi Direct and the global shortcut.

## Tray icon and firewall

On GNOME, the tray icon needs the *AppIndicator* extension; without it CopyOver still works.

Fedora Workstation’s firewall already allows the port and mDNS (Ubuntu ships with its firewall off). If you use another zone:

```bash
sudo firewall-cmd --permanent --add-port=8765/tcp --add-service=mdns
sudo firewall-cmd --reload
```

The right-click **Send with CopyOver** entry is under **Scripts** on GNOME and under **Actions** in KDE Dolphin. See [PC to PC](pc-to-pc.md).
