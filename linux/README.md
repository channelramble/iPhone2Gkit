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

The open-source download contains no Apple IPSWs or legacy proprietary app
payloads. Download stock firmware inside the app (checked against a pinned
size and SHA-256), or choose your own iPhone1,1 IPSW. Import an existing
`iphone-2g-ios-1-kit-full` folder to enable its 1.0 activation and app actions.
Firmware is cached in `~/.local/share/iPhone2Gkit/firmware` and working files
in `~/.cache/iPhone2Gkit` (standard XDG overrides are supported).

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
replaces an existing daemon. Use **Check phone service** to check the connection.

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

## Build and test

On Ubuntu 22.04 x86_64:

```sh
sudo apt-get install build-essential pkg-config autoconf automake libtool cmake \
  curl patchelf zlib1g-dev libbz2-dev libssl-dev perl xvfb xauth \
  libx11-6 libxext6 libxft2 libxrender1 libfontconfig1
./linux/build-linux.sh --public
```

Every downloaded source/runtime archive has a fixed SHA-256 in the script.
LGPL libraries stay replaceable in `resources/lib`; sources, local patches,
build recipe and license texts are included. The HFS build uses only xpwn's
`common` and `hfs` targets. GUI smoke testing uses Xvfb and never talks to a phone.
Portable package and checksum are produced in `dist/`.
