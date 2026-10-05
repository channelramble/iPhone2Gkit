# iPhone2Gkit 2.1.0 — experimental public preview

Tools for restoring the original iPhone and working with iPhone OS 1.0, with
a native macOS app and a Linux desktop app/CLI. macOS requires Apple Silicon
and macOS 13 or later; the Linux package targets x86_64 with glibc 2.35 or later
and an X11 desktop (including XWayland). The previous activation,
app installation, and Launcher repair completed on the owner's phone; **the new
restore paths have been checked offline only. No phone was erased while building
this release. Native 1.x restores are experimental.**

The **Restore** tab includes stock **3.1.3, 1.0, 1.1.1, and 1.1.3**, plus a
custom iPhone1,1 IPSW picker. Both packages include a private Python runtime
and the native USB/restore programs. Public packages download stock firmware
locally with **Download & Verify Selected Firmware**; each download is checked
against a fixed size and SHA-256. Apple firmware and historical application
packs are not published in Git history or public packages. The 1.0 app actions
use **Use Other Kit** to import your existing historical kit, then **Prepare
1.0 Resources** to download its kernel and import the genuine plist converter.
No XP, iTunes, system Python or Homebrew is needed to run the downloads.

1. Connect only the original iPhone and open **Restore**.
2. Choose and download a stock firmware, or select a local custom IPSW.
   **Inspect & Prepare Restore** checks
   the archive, component paths, model, and firmware hash without writing to the phone.
3. Read the warnings, confirm the physical A1203 model and data erasure, and
   click **Erase & Restore** only when ready. Keep USB connected.
4. Confirm boot and version on the phone. Restoration does not activate it.
   The **1.0 Apps** tab's activation and old app packs are specifically for 1.0.

**Compatibility:** every original iPhone reports `iPhone1,1`. Serial week is
informational; it does not identify the NAND chip. **Earliest supported 1.x**
selects a target only when a known NAND ID is supplied. Unknown parts remain
unknown. The app keeps **1.0** selectable behind an explicit warning even if
the NAND is known to require a later version. NAND tables are extracted from
the supplied stock kernels; driver-selection ambiguity is reported conservatively.

**Custom IPSWs:** only iPhone1,1/m68ap firmware is accepted. The app includes
the fixed, hash-checked Pwnage 2.0 WTF preparation used by Legacy iOS Kit.
Enter initial DFU/WTF with the black screen; the app checks for the expected
`iBoot-636.66.3x` DFU state before starting a custom restore. That boot-chain
preparation runs in RAM and does not prove every custom firmware will work.
Custom 1.x remains experimental too.

**Failures:** a restore can leave a partially written phone. The app saves
`~/Library/Caches/ios1kit/build/restore.log`, stops, and never automatically
starts another destructive restore or suppresses a radio failure. In particular,
1.0's full protocol-7 restore requests a baseband/bootloader update; later radio
bootloaders may reject that stage. An error is reported as an error, even if the
OS filesystem was already written. Inspect the log before using Exit Recovery.
Stock 3.1.3 can be downloaded as a recovery option; it is not an automatic substitute
for an unknown earliest supported 1.x version.

The restore engine pins [tihmstar's protocol-7 implementation](https://github.com/tihmstar/idevicerestore/tree/c25aefd49b3769c2907875e15566d433d59bd979)
and fixes its startup handshake, ECID handling, ASR error propagation, and device
guards. The [current Legacy iOS Kit support notes](https://gist.github.com/LukeZGD/9d781f1b03a69fa46869384a9407a41a)
still list 2.0 as the lowest working native target. This release adds an
experimental implementation, not a claim of a verified native 1.0 restore.
Pwnage patch source: [Legacy iOS Kit](https://github.com/LukeZGD/Legacy-iOS-Kit/tree/66b137aae91d77b96b14e7052189f824351ce887).
Corresponding restore sources, build recipes, patches, and license texts are in
the app's `Contents/Resources/ThirdPartySources` and `ThirdPartyLicenses`.

## USB restore readiness (2.0.1)

The Restore tab separately reports physical USB detection and whether macOS's
USB connection service can see the phone. Linux enumerates USB descriptors
through sysfs instead of macOS's ioreg. **Check phone service** makes a
bounded, read-only `QueryType` request to the selected phone. It does not pair,
reboot, boot a ramdisk, or start a restore. All of this uses the bundled Python
runtime and the host's existing usbmuxd service. The Linux package also carries
a usbmuxd executable for hosts without that service; USB permissions/service
setup are described in `linux/README.md`. The app never replaces a running daemon.

Terminal equivalents:

```sh
iphone2gkit transport-info --json
iphone2gkit transport-info --json --probe-service
```

An early phone may be physically visible while absent from macOS's USB service;
in that case normal-mode USB identity/SSH cannot use that service. Recovery/DFU
is reported as **pending**, because the restore-mode service cannot be checked
until its ramdisk boots. Neither USB detection nor a service reply establishes
that restoration will finish. Restoring is blocked before starting the backend
if the system service is unavailable or there is no single connected phone.
The preflight never starts a replacement daemon or modifies Apple's service.

Automatic NAND discovery and full restores across early/later 4/8/16 GB units
remain work in progress. This public preview is still an experimental build, with the same
firmware catalog and on-device validation limits stated above.

## App era and authenticity

**Launcher is Nullriver Launcher 0.2, an original 2007-era app.** The executable,
Info.plist, icon, and launch image match the preserved
[Launcher 0.2 PXL archive](https://www.pxl.nerdvittles.com/Launcher-0.2.pxl)
byte for byte. The [preserved package repository](https://www.pxl.nerdvittles.com/)
identifies Nullriver and version 0.2; the archive records August 2007 dates.
The independently downloaded PXL matches the supplied source archive too.
ZIP dates corroborate provenance; they are not signed historical certificates.

The app checklist and `iphone2gkit list` disclose the era of every pack:

- **2007 archived app · repackaged:** evidence-backed original app files;
  Launcher currently has this label. Its installation wrapper and compatibility
  libraries are packaged for the current kit.
- **Modern helper · not a 2007 app:** the kit-generated Undo SummerBoard repair.
- **Catalog: YEAR · build unverified / after 2007**, or **Era unverified:**
  the exact build has not been independently matched to a historical release.
  A 2007 catalog date alone does not authenticate a binary.

Hover over an era label for its evidence and source. Known labels are pinned
to the payload SHA-256; another kit cannot inherit them merely by app name.
iPhone2Gkit itself and all installation wrappers are modern. The current
catalog has no confirmed post-2007 application build; unknown builds are shown
as unverified rather than assumed to be 2007 originals.

## On-device transport update (October 4, 2026)

On an iPhone 2G running iBoot-159, a read-only 10 MB probe completed.
Activation and the first 39 app packs installed in the first run. That run
stopped at 40/42 because the phone could not read BSD Base's ZIP directory.
A retry with BSD Base, OpenSSH and Installer in a 13 MB ramdisk passed the
new ZIP integrity checks, completed all installation steps and returned in
normal USB mode. The owner confirmed that the phone boots to the home
screen without an activation prompt. App launches still need checking.

## Launcher and the home screen (version 1.2.1)

The corrected Launcher/Finder repair completed on the connected iPhone 2G
and it rebooted into normal USB mode. Its original 738-byte binary layout
needed conversion; the XML editor now handles that path. The home-screen
icons and individual app launches still need visual confirmation.

iPhone OS 1.0 has a single home screen, so installing 42 apps does not give
42 icons. **Show Launcher** installs the small Launcher and Finder packs
without changing activation, then puts them at the start of the icon list.
Open **Launcher** on the phone to choose another installed app. **Finder**
provides a fallback: browse `/Applications` and open an app there.

The same layout fix runs automatically during installs when Launcher is
present. It preserves the dock and the relative order of other icons.
For an already full custom layout, the last third-party icons move into
the hidden list and remain accessible through Launcher. Apple icons are
kept. The original layout is saved beside `DisplayOrder.plist` as
`DisplayOrder.plist.ios1kit-orig`. Binary layouts are converted on a copy,
using the historical ARM `plutil` from iLiberty's BasePack. Only that helper
loads the phone's Foundation libraries. The XML editor then validates the
converted copy; an unsupported or damaged layout leaves the original intact.
The replacement is staged beside the original on the mounted system
partition; the ramdisk's `/tmp` is read-only.

Terminal equivalent: `ios1kit launcher`. It asks for recovery mode and
uses one small ramdisk; no full reinstall is needed.

App-install disks are at least 13 MB, the size that completed the previous
on-device app install. The first Launcher trial stopped because its temporary
output targeted the read-only ramdisk. Check/Repair continue to use the verified
10 MB disk; the default maximum is 14 MB.

Version 1.1 defaults to a 14 MB maximum ramdisk, splitting larger selections
into batches. Every embedded archive is tested before mounting or writing
the flash. Larger ramdisks remain experimental; a successful USB upload
alone does not prove that their payload files remain readable after boot.

The bundled libirecovery patch includes the command newline and NUL when
rounding packets to 16 bytes. Without that fix, commands exactly on the
boundary can join the following command. The legacy console reader drains
the full bounded response, including zero padding between buffers.

The bundled libirecovery patch uses the correct IOKit APIs for interrupt
endpoints, bulk endpoint 0x05 with 16 KB file chunks, and the legacy file
completion sequence. The engine uploads the ramdisk at 0x09990000 first,
then the signed stock 1.0 kernel at 0x09000000. Boot arguments specify the
actual disk size. The old iLiberty kernel does not load on this bootloader.
The stock kernel is extracted locally from the verified 1.0 IPSW and checked
against a pinned SHA-256.

## Activation and apps (the existing ios1kit engine)

Activation, apps and repair for an original iPhone (iPhone1,1) running iPhone OS 1.0. Public packages run on Apple Silicon macOS and x86_64 Linux with no Windows XP, iTunes or iLiberty application.

## Why iLiberty failed, and what this does differently

iLiberty+ works in two passes. First it uploads `payload.zip` to the phone over AFC (this is where "Sending payload.zip" stalls). Then it boots a ramdisk that parks SpringBoard's launch plist in `/` and installs a pass-2 script. The phone then reboots from its own flash and runs your payloads with its own `/bin/sh`, `/bin/cp` and so on.

The ALL master payload unzips new `/bin/sh`, `/bin/cp`, `/bin/chmod` and `/sbin/reboot` **over the tools pass 2 is running from**. If pass 2 dies, SpringBoard's plist stays parked and the phone boots to a black screen.

ios1kit does all the work **inside the ramdisk**, in one pass:

- Activation and the apps travel inside the ramdisk, sent over recovery-mode USB. There is no AFC, pairing or iTunes.
- The ramdisk mounts the phone's flash and unzips each app pack directly. Nothing runs from the files being replaced.
- Free space is checked before anything on the flash is changed. Every command is checked and logged.
- If something fails, the failed step stays on the phone's screen for a minute and the phone restarts into recovery mode. Steps after the failure don't run.
- iOS 1's iBoot can't report anything back over USB, so the result is where the phone boots. The Mac saves `auto-boot=false` before booting the ramdisk, and only the installer's success path sets it back to true. If the phone boots iPhone OS, the run succeeded. If it lands in recovery mode, it failed, crashed or panicked.
- If the apps don't fit in one ramdisk, they are split into batches. After each batch the phone boots normally, and you put it back in recovery mode for the next one.
- Every run first undoes a half-finished iLiberty pass 2: SpringBoard and CommCenter plists are restored, and `/bin2` and the leftover payload files are removed.

The bootloader receives the HFSX ramdisk at `0x09990000`, then the signed stock
1.0 kernel at `0x09000000`. The engine sends quoted `boot-args` with the actual
ramdisk size, saves them and calls `bootx`. The bundled libirecovery is patched
for iBoot-159: interrupt commands with ACKs, bulk file chunks of 16 KB, and
hexadecimal `filesize`. An unmodified Homebrew libirecovery 1.3.1 is insufficient
on macOS. The base ramdisk tools come from iLiberty's ramdisk; its `/etc/profile`
is replaced with ios1kit's installer.

The ramdisk records its run ID, result and failed step in NVRAM. On recovery
failures, the bundled legacy console reader retrieves those values using
`printenv`, so the Mac can show the current run's failed step. Recovery idle
shutdown is disabled during a run and restored on success or Exit recovery.

## The macOS app

`dist/iPhone2Gkit.app` (download: `iPhone2Gkit-macos-arm64.zip`) carries its own
Python and native programs. Unzip it and open it. It is ad-hoc signed, without
Developer ID notarization. If macOS blocks the first launch, review the app in
**System Settings → Privacy & Security → Open Anyway**. On macOS 15 or later,
Control-click no longer overrides this check; see [Apple's opening guidance](https://support.apple.com/en-us/102445).
It needs an Apple Silicon Mac running macOS 13 or later. Firmware is downloaded
in the app and historical app packs are imported from your existing kit.

| Inside `iPhone2Gkit.app/Contents/Resources` | |
|---|---|
| `engine/` | the shared Python engine and installation scripts |
| `python/` | private Python 3.12 (python-build-standalone 20261003, stdlib only, SHA-256 pinned) |
| `bin/` | patched static libirecovery 1.3.1, a legacy console reader and the Terminal launcher; linked only to macOS system libraries |
| `ThirdPartySources/`, `ThirdPartyLicenses/` | Corresponding native restore sources, build recipes, patches, license texts |

Besides these, the app runs only macOS's own `hdiutil`, `ditto`, `ioreg` and `ssh-keygen`. Its PATH is limited to its own `bin/` and the system folders, so nothing installed elsewhere is used by accident. Ramdisks are built in `~/Library/Caches/ios1kit/build`; the OpenSSH host key lives in `~/.ios1kit/`.

**Terminal command:** click **Terminal Command…** in Setup, or choose **iPhone2Gkit → Install Command-Line Tool…**. This installs `iphone2gkit` plus the backward-compatible `ios1kit` alias. Without installing it, run `/path/to/iPhone2Gkit.app/Contents/Resources/bin/iphone2gkit`. `restore-info` and `restore-plan --target VERSION` inspect without writing to the phone. The `restore` command requires explicit erasure/model confirmations plus the relevant experimental/custom/1.0 warning flags.

**The window:**
- **iPhone panel.** A live status light: not connected, recovery mode, or iPhone OS running.
- **Setup panel.** Shows the imported kit and built-in USB tool. **Use Other Kit…** points it at your historical kit folder.
- **Actions:**
  - **1. Check phone** runs `probe`.
  - **2. Install** installs activation plus the apps ticked on the right.
  - **Show Launcher** makes Launcher and Finder visible for opening other apps.
  - **Repair iLiberty leftovers** runs `repair`.
  - **Exit recovery mode** runs `kick`.
- **App list.** Every app with its size, plus **All apps** / **None** buttons.
- **Bottom pane.** What's happening now, recovery-mode instructions when they're needed, an upload progress bar and the full log.

**Rebuilding the public app:** `macos/build-app.sh --public` needs Xcode/CLT plus
pkgconf, autoconf, automake, libtool, cmake and perl as build tools. The fetch
scripts use pinned-checksum sources to build the private native tools. Build
tools are not runtime dependencies. The signed public bundle passes clean-PATH
checks for the engine and restore programs, and contains no Apple firmware.
The private build mode `macos/build-app.sh KIT_FOLDER` is for local historical-kit
testing and is not the public release recipe.

## Linux app and CLI

Extract `iPhone2Gkit-linux-x86_64.tar.gz`, then run `./iPhone2Gkit/iphone2gkit`.
With arguments, the same launcher runs the CLI, for example
`./iPhone2Gkit/iphone2gkit restore-info`. It uses its private Python/Tk runtime,
patched USB/restore tools and rootless HFS+ image editor. USB permissions and
service setup are documented in [linux/README.md](linux/README.md). The setup
helper does not disable or replace an installed daemon.

Build on Ubuntu 22.04+ with `bash linux/build-linux.sh --public`; run
`bash linux/test-linux.sh` to exercise the packaged CLI and GUI under Xvfb.
The GitHub Actions workflow builds and tests Linux before publishing artifacts.
Tests do not perform a physical phone restore. Both platforms require explicit
erasure/model acknowledgments, preserve logs and avoid automatic retry erasures.

## Command line (alternative)

### Setup (once)

```sh
sh macos/fetch-deps.sh          # builds the patched USB tools (needs pkgconf + CLT)
export PATH="$PWD/macos/vendor:$PATH"
cd ios1kit
./ios1kit doctor               # checks the Mac, the kit files (sha256) and the phone
```

The kit folder (`iphone-2g-ios-1-kit-full`, with `iLiberty-portable/` and `ios1-apps/`) is found automatically. Otherwise pass `--kit PATH` or set `IOS1KIT_ASSETS`. No Python packages are needed; the system `python3` is enough.

## Use

```sh
./ios1kit probe                 # read-only: firmware, free space, iLiberty leftovers
./ios1kit install               # activate + every app (default preset "apps")
./ios1kit install --no-activate # phone is already activated
./ios1kit install --apps launcher,lightsoff,mobileterminal
./ios1kit install --apps all    # apps + tweaks (Customize, DockSwap, Dock); SummerBoard only if named
./ios1kit repair                # just undo iLiberty leftovers and boot
./ios1kit launcher              # show Launcher and Finder; keep activation
./ios1kit list                  # app keys
./ios1kit status                # which mode the phone is in
./ios1kit kick                  # leave recovery mode and boot iPhone OS
```

`install` tells you how to put the phone in recovery mode when needed: power off, hold Home, plug in, and let go at "Connect to iTunes". The screen may turn purple when the Mac has control. The disk and kernel upload separately in 16 KB chunks. The phone then shows the installer's progress, and reboots into iPhone OS when it's done.

`./ios1kit build [--apps ...]` builds the blobs into `build/` without a phone. Each blob also gets a `.profile.sh` copy of the exact script it will run.

## What's verified and what isn't

| | Status |
|---|---|
| Boot command sequence and blob layout | Recovered from iLiberty.exe 1.3.0.113 (unpacked); iBoot-159 (1.0) has `bootx`/`setenv`/`saveenv` |
| Kernelcache | Signed stock iPhone OS 1.0 restore kernel, SHA-256 pinned; boots on iBoot-159 |
| Ramdisk build | Plain HFSX; 10 MB probe and 13 MB remaining-app install booted on-device. Default cap is 14 MB. |
| Payload translation | All 48 kit scripts translate; anything unknown aborts the build |
| Offline tests | `python3 -m unittest discover -s tests` |
| USB protocol | libirecovery 1.3.1 legacy iOS 1 path (`legacy_recovery_mode` for CPID 0x8900 without ECID); `irecovery` is called with `-v` because it exits 0 even when a transfer fails |
| **On-device run** | Probe, remaining-app install and the corrected Launcher/Finder repair completed. All 42 selected packs were processed across the initial run and retry. Owner confirmed home-screen boot without activation prompt before the layout repair. Icons and app launches after that repair still need visual confirmation. |

Use the default 14 MB cap. The engine uses a 10 MB image for probe/repair and
sizes each install batch to its contents. Put the phone back into recovery
when prompted between batches. The 21 MB image used for the initial
42-pack run booted, but its BSD archive was unreadable; it is not a supported
default. This was an on-device archive problem, distinct from the original
USB upload failure.

## Restoring 1.0 itself

Use the Restore tab described above. The historical iTunes 7/XP route remains
the kit's previously demonstrated 1.0 restoration method. Native 1.x in this
release is experimental and has not replaced that observed validation.

## Troubleshooting

- **"came back in recovery mode".** The run failed. The phone showed `FAILED at step: ...` for a minute, and the log is at `/var/root/Media/ios1kit/install.log` (from step 1 onward). Fix the cause and rerun; every step is safe to repeat. `./ios1kit kick` boots the phone as it is.
- **`df-system-unreadable` or `fsck-...`.** Run `probe` and note what it shows. Install refuses to write when it can't measure free space or the file system isn't clean.
- **`system-partition-full`.** Install fewer apps (`--apps` list).
- **Stuck on the Apple logo after a failed boot.** The phone is probably still carrying `rd=md0` boot-args. Enter recovery mode and run `./ios1kit kick`.
- **SpringBoard won't start after installing SummerBoard.** Run `./ios1kit install --no-activate --apps undosummerboard`.
- **SSH:** the OpenSSH pack gets a host key generated once on this Mac (`~/.ios1kit/`). Log in as root with password `dottie`; see `ios1-apps/README.txt` for the client flags.
