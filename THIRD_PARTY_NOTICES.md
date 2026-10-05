# Third-party components

The MIT license applies to original iPhone2Gkit code. It does not relicense the
components below. Native programs run as separate processes. Binary packages
include their license texts, exact upstream source archives, local patches and
build recipes in `ThirdPartyLicenses` and `ThirdPartySources`.

| Component | Source and license |
| --- | --- |
| CPython / python-build-standalone | [CPython](https://github.com/python/cpython), PSF and included notices; [distribution build scripts](https://github.com/astral-sh/python-build-standalone) |
| libplist, libimobiledevice-glue, libirecovery, libusbmuxd, libimobiledevice, libtatsu | [libimobiledevice project](https://github.com/libimobiledevice); retain each archive's COPYING/LICENSE texts; library LGPL notices and program licenses apply separately |
| usbmuxd (Linux) | [libimobiledevice/usbmuxd](https://github.com/libimobiledevice/usbmuxd), GPL-3.0-or-later; separate daemon, sources and license included |
| libusb (Linux) | [libusb](https://github.com/libusb/libusb), LGPL-2.1-or-later |
| idevicerestore | [tihmstar fork](https://github.com/tihmstar/idevicerestore/tree/c25aefd49b3769c2907875e15566d433d59bd979), LGPL-2.1-or-later; `macos/restore-build.patch` is a modification under those terms |
| xpwn hfsplus (Linux) | [planetbeing/xpwn](https://github.com/planetbeing/xpwn/tree/20c32e5c12d1b22a9d55a59a0ff6267f539b77f4), GPL-3.0-or-later; separate rootless image-editing executable |
| Pwnage WTF patch | [Legacy iOS Kit](https://github.com/LukeZGD/Legacy-iOS-Kit/tree/66b137aae91d77b96b14e7052189f824351ce887), GPL-3.0; license retained at `kit/resources/PWNAGE-SOURCE-LICENSE.txt` |
| OpenSSL | [OpenSSL](https://github.com/openssl/openssl), Apache-2.0 |
| libzip | [libzip](https://github.com/nih-at/libzip), BSD-3-Clause |
| curl | [curl](https://github.com/curl/curl), curl license |
| OpenSSH ssh-keygen (Linux) | [OpenSSH portable](https://github.com/openssh/openssh-portable), BSD and included component notices |
| Tcl/Tk (Linux GUI) | [Tcl](https://github.com/tcltk/tcl), [Tk](https://github.com/tcltk/tk), BSD-style notices retained with the private Python runtime |

Linux bundles additionally include the required native runtime libraries with
their package copyright notices. The build recipe records exact downloaded
source versions and SHA-256 digests. macOS system libraries and Linux's kernel,
glibc, display server and desktop infrastructure are supplied by the host OS.

Apple IPSWs and extracted Apple kernels are **not** included in the public Git
repository or public release packages. The integrated downloader obtains a
selected stock IPSW locally and verifies its fixed SHA-256 before use. Those
firmwares remain Apple's software. Historical iLiberty/app packs and the phone
plist converter are imported from the user's existing kit; their presence does
not imply an open-source license or permission to relicense them. ROMs, BIOS
images, pairing records, SSH keys and personal phone data are never release
assets.
