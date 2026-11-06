# PC to PC

[← Back to the README](../README.md)

If you have two or more computers running CopyOver on the same network (Windows or Linux), they find each other over mDNS (`_puente._tcp.local.`) and you can send files directly.

## Send a file

1. **Right-click** one or more files → **Send with CopyOver**. On Windows it’s also under **Send to**; on GNOME under **Scripts**, and in KDE Dolphin under **Actions**.
2. If there are several computers nearby, a window asks which one.
3. The other PC shows: **“Laptop-Windows wants to send you photo.jpg (3.0 MB) — Accept / Decline”**, with an *Automatically accept from this computer next time* checkbox. Unanswered offers expire after 60 seconds.
4. Once accepted, the file is sent in chunks (up to **10 GB** without filling RAM), its **SHA-256** is checked, and it lands in `Documents/From CopyOver/From another PC`.

You can also use the tray’s **Send to another PC…**, or press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>O</kbd> on Windows.

## From the command line

| Command | What it does |
|---|---|
| `python copyover.py --send <files...>` | Sends one or more files to the nearby computer (or opens the picker) |
| `python copyover.py --send --to <computer> <files...>` | Sends straight to the computer given by name or IP |
| `python copyover.py --pair [computer]` | Pairs with someone else’s PC using a 6-digit code |

> [!TIP]
> If your computers share the same key (`key` in `config.json`) but have different network names (`name`), they can already send files to each other. To send to someone else’s PC, run `python copyover.py --pair`.

## Linked computers

From the icon next to the clock, **Linked computers → Manage linked PCs…** (on Linux, **Linked computers…** in the menu) shows the linked PCs, even offline, and lets you rename them locally or unlink them. The alias also shows up when you choose where to send.

Unlinking revokes the key and auto-accept on this PC, and cancels pending sends and receives. Files that already arrived are kept. The other computer may still show up as nearby; to use the link again, connect again from **Link → Another PC…**.

## Resuming after a network drop

Transfers between PCs **resume after a network drop** when both PCs run this version. CopyOver keeps the part already received, finds the computer again by its ID if its IP changed, and keeps trying for five minutes after the drop. The progress shows “Reconnecting” while it waits. When done, it checks the SHA-256 of the whole file before saving it as received.

If the network takes longer to come back, send **the same files, in the same order** again: the pending offer is picked up without asking to accept again. Pending parts expire after 24 hours without activity.

> [!IMPORTANT]
> **Keep CopyOver open on both PCs.** Resuming covers network drops, not program restarts. `.parcial` files left by a session cut short by closing or shutting down can be deleted while CopyOver is closed.

Computers on older versions can still send whole files. Resuming applies to PC-to-PC transfers; sends from Android and from the iPhone shortcuts work as before.

How pairing and TLS between PCs work is in [SECURITY.md](../SECURITY.md).
