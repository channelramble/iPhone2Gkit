# iPhone2Gkit for Linux

Experimental x86_64 desktop release for Ubuntu 22.04 or newer (glibc 2.35+).
This package includes Python 3.12, Tk, the patched USB/restore tools, a USB
connection daemon, HFS image tools, and SSH host-key generation. No Python,
iTunes, Homebrew, XP machine, or separately installed restore tool is needed.
It uses your Linux kernel, glibc, CA certificates, and normal desktop X11/font
libraries. Wayland works through XWayland.

Extract the complete folder, then run `./iphone2gkit`. To use the command line,
pass a command: `./iphone2gkit restore-info` or `./iphone2gkit --help`.
The app works from another directory and through a symlink.

Click **Download setup files** once. The app downloads and verifies the
historical setup bundle and stock 1.0 resources, then shows the app list.
No kit folder is needed. Downloading setup does not contact or change the phone.
For a phone already running 1.0, choose your apps and click **Install selected
apps**. **Show Launcher** makes installed apps accessible from its single home screen.

The **Restore / downgrade** tab downloads the selected firmware separately,
or accepts your own iPhone1,1 IPSW. Check the restore plan and read its warnings
before erasing. **Advanced** contains manual kit import, USB setup, repair,
recovery exit, ramdisk settings and the detailed log.

Setup/firmware lives in `~/.local/share/iPhone2Gkit` and working files in
`~/.cache/iPhone2Gkit` (standard XDG overrides are supported). Stock IPSWs
are not bundled; downloads are checked against fixed sizes and SHA-256 digests.
Historical setup components retain their original licenses.

## USB access

Read-only device detection uses Linux sysfs even without USB permissions.
Commands/uploads also need access to the phone's USB device. On a normal
desktop, run `sudo sh setup-usb.sh` once, then reconnect the phone. The rule
applies only to Apple mobile USB devices and grants access to the active
desktop session; it grants no global world-writable device access.

Normal/restore-mode communications need a running `usbmuxd`. Keep an existing
system service if present. If none is running, in a separate Terminal run:

```sh
sudo ./resources/bin/usbmuxd --foreground --user root
```

Leave that terminal open while using the app. The executable has a relative
library path and works without a separate install. The app never stops or
replaces an existing daemon. Use **Advanced → Check phone service** to check the connection.

If Tk reports a missing X11/font library on a minimal system, install the
desktop OS packages `libx11-6 libxext6 libxft2 libxrender1 libfontconfig1`.
These are normally installed with a desktop environment. Headless CLI use
does not require an X server.

## Current limitations

Full hardware restore coverage is still being tested. Native iPhone OS 1.x
restore is experimental; neither a Linux package test nor a successful
device query establishes that a destructive restore will complete. The app
requires explicit erase, model, and relevant firmware warnings before running
the backend. Cancellation can leave a partially restored phone.

A serial number does not identify the NAND chip. Automatic selection is
limited to known evidence; unknown hardware requires a manual choice. Stock
1.0 does not support every original iPhone. Activation/app installation is
for a phone already restored to 1.0; later firmware activation is not provided.

Build recipes, test details and technical context are in [AGENTS.md](../AGENTS.md).
