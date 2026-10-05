# AGENTS.md — iPhone2Gkit internals & development notes

Everything here is the technical, research, and contributor context. End users
only need the [README](README.md); this file is for developers and agents
working on the project. Status of the whole project: **experimental preview** —
activation and app install are proven on a real iPhone 2G; restore/downgrade is
offline‑tested only.

---

## 1. How it works: one‑pass ramdisk vs iLiberty

iLiberty+ worked in two passes: it uploaded `payload.zip` over AFC (the step that
stalls at "Sending payload.zip"), then booted a ramdisk that parked SpringBoard's
launch plist in `/` and installed a pass‑2 script. The phone then rebooted from
its own flash and ran payloads using its own `/bin/sh`, `/bin/cp`, etc. The ALL
master payload unzipped new `/bin/sh`, `/bin/cp`, `/bin/chmod`, `/sbin/reboot`
**over the tools pass 2 was running from** — if pass 2 died, SpringBoard's plist
stayed parked and the phone booted to a black screen.

iPhone2Gkit does everything **inside the ramdisk, in one pass**:

- Activation and apps travel inside the ramdisk over recovery‑mode USB — no AFC,
  pairing, or iTunes.
- The ramdisk mounts the phone's flash and unzips each app pack directly; nothing
  runs from files being replaced.
- Free space is checked before anything is written; every command is checked and
  logged.
- On failure, the failed step stays on the phone's screen for a minute, then the
  phone restarts into recovery; steps after the failure don't run.
- iOS 1's iBoot can't report over USB, so **the result is where the phone boots**.
  The Mac saves `auto-boot=false` before booting the ramdisk; only the installer's
  success path sets it back to `true`. Boots iPhone OS → success; lands in
  recovery → failed/crashed/panicked.
- Apps that don't fit one ramdisk are split into batches; the phone boots
  normally between batches and you re‑enter recovery for the next.
- Every run first undoes a half‑finished iLiberty pass 2 (restores SpringBoard /
  CommCenter plists; removes `/bin2` and leftover payload files).

## 2. Boot sequence & USB protocol (iBoot‑159)

The bootloader receives the HFSX ramdisk at `0x09990000`, then the signed stock
1.0 kernel at `0x09000000`. The engine sends quoted `boot-args` with the actual
ramdisk size, saves them, and calls `bootx`. The old iLiberty kernel does **not**
load on this bootloader; the stock kernel is extracted locally from the verified
1.0 IPSW and checked against a pinned SHA‑256.

The bundled **libirecovery 1.3.1 is patched** for iBoot‑159 (an unmodified
Homebrew build is insufficient on macOS):

- Correct IOKit APIs for interrupt endpoints; bulk endpoint `0x05` with 16 KB
  file chunks; the legacy file‑completion sequence.
- Interrupt commands with ACKs and **hexadecimal `filesize`**.
- Command newline + NUL are included when rounding packets to 16 bytes —
  otherwise a command exactly on the boundary joins the next one.
- The legacy console reader drains the full bounded response, including zero
  padding between buffers.

The ramdisk records its run ID, result, and failed step in **NVRAM**. On recovery
failures the bundled legacy console reader retrieves those via `printenv`
(this reader reads **iBoot** output, not the kernel console). Recovery idle
shutdown is disabled during a run and restored on success or Exit Recovery.

## 3. NAND compatibility (the "every 2G" problem)

Every original iPhone reports `iPhone1,1`; serial week is informational and does
**not** identify the NAND chip. 1.0 eligibility is a hardware fact decided by the
NAND chip ID, which the flash driver logs on the kernel console
(`NAND device ID 0x%08x not supported` for a rejected part).

- NAND tables are extracted from the supplied stock kernels (1.0 / 1.1.1 / 1.1.3);
  driver‑selection ambiguity is reported conservatively.
- **Earliest supported 1.x** resolves a target only when a known NAND ID is
  supplied (`--nand-id`, never inferred from the serial). Unknown or conflicting
  IDs stay **unknown**; 1.0 is kept selectable behind an explicit warning even
  when the NAND is known to require a later version.
- `kit/nandlog.py` parses a chip ID out of captured device text and feeds it to
  `kit/firmware.recommended_target`. A "not supported" line is a *rejection*, not
  proof of 1.0 compatibility; the firmware tables decide the earliest supporting
  version from the chip ID alone.
- **Pending:** automatic NAND discovery. The kernel‑console capture path (a
  NAND‑probe ramdisk, or a kernel serial reader) is not implemented — the bundled
  console reader only talks to iBoot. Until then, NAND ID is manual.

## 4. Restore engine (experimental)

The restore engine pins
[tihmstar's protocol‑7 idevicerestore](https://github.com/tihmstar/idevicerestore/tree/c25aefd49b3769c2907875e15566d433d59bd979)
and fixes its startup handshake, ECID handling, ASR error propagation, and device
guards. The
[current Legacy iOS Kit support notes](https://gist.github.com/LukeZGD/9d781f1b03a69fa46869384a9407a41a)
still list **2.0 as the lowest working native target** — this release adds an
experimental 1.x implementation, not a verified native 1.0 restore.

- **Targets:** stock 3.1.3 / 1.0 / 1.1.1 / 1.1.3 plus a custom iPhone1,1/m68ap
  IPSW. Firmware downloads in‑app, each checked against a pinned size + SHA‑256.
- **Custom IPSWs:** only iPhone1,1/m68ap is accepted. The app includes the fixed,
  hash‑checked **Pwnage 2.0 WTF** preparation from
  [Legacy iOS Kit](https://github.com/LukeZGD/Legacy-iOS-Kit/tree/66b137aae91d77b96b14e7052189f824351ce887);
  it checks for the expected `iBoot-636.66.3x` DFU state before starting. Boot‑chain
  prep runs in RAM and doesn't prove every custom firmware will work.
- **Failure handling:** a restore can leave a partially written phone. The app
  saves `~/Library/Caches/ios1kit/build/restore.log`, stops, and never
  auto‑starts another destructive restore or suppresses a radio failure. 1.0's
  full protocol‑7 restore requests a baseband/bootloader update; later radio
  bootloaders may reject that stage. An error is reported as an error even if the
  OS filesystem was already written — inspect the log before Exit Recovery. Stock
  3.1.3 can be downloaded as a recovery option; it is **not** an automatic
  substitute for an unknown earliest‑supported 1.x.
- Corresponding sources, build recipes, patches, and license texts ship in the
  app's `Contents/Resources/ThirdPartySources` and `ThirdPartyLicenses`.

## 5. USB restore readiness (transport preflight)

The Restore tab separately reports **physical USB detection** and whether the
host's **usbmuxd connection service** can see the phone. Linux enumerates USB
descriptors through sysfs instead of macOS's ioreg. **Check phone service** makes
a bounded, read‑only `QueryType` request — it does not pair, reboot, boot a
ramdisk, or restore. It uses the bundled Python runtime and the host's existing
usbmuxd; the Linux package also carries a usbmuxd for hosts without one, and the
setup helper never replaces a running daemon.

```sh
iphone2gkit transport-info --json
iphone2gkit transport-info --json --probe-service
```

An early phone may be physically visible yet absent from the USB service (then
normal‑mode identity/SSH can't use it). Recovery/DFU is reported **pending**
because the restore‑mode service can't be checked until its ramdisk boots.
Neither detection nor a service reply proves a restore will finish; restoring is
blocked before the backend starts if the service is unavailable or there isn't a
single connected phone.

## 6. App era & authenticity

**Launcher is Nullriver Launcher 0.2, an original 2007 app** — its executable,
Info.plist, icon, and launch image match the preserved
[Launcher 0.2 PXL archive](https://www.pxl.nerdvittles.com/Launcher-0.2.pxl)
byte for byte (August 2007 dates). The app checklist and `iphone2gkit list`
disclose every pack's era:

- **2007 archived app · repackaged** — evidence‑backed original files (Launcher);
  its install wrapper and compat libraries are modern.
- **Modern helper · not a 2007 app** — e.g. the kit‑generated Undo SummerBoard.
- **Catalog: YEAR · build unverified / after 2007**, or **Era unverified** — the
  exact build isn't matched to a historical release.

Known labels are pinned to the payload SHA‑256, so another kit can't inherit them
by app name. iPhone2Gkit itself and all install wrappers are modern.

## 7. Launcher & the home screen

iPhone OS 1.0 has a single home screen, so 42 installed apps don't give 42 icons.
**Show Launcher** installs the small Launcher + Finder packs (no activation
change) and puts them at the start of the icon list; the same layout fix runs
automatically during installs when Launcher is present. It preserves the dock and
the relative order of other icons; on a full custom layout the last third‑party
icons move to the hidden list (still reachable via Launcher), Apple icons kept.
The original is saved beside `DisplayOrder.plist` as `…ios1kit-orig`. Binary
layouts are converted on a copy using the historical ARM `plutil` from iLiberty's
BasePack (the only helper that loads the phone's Foundation libraries); the XML
editor validates the copy, leaving the original intact on an unsupported layout.
Terminal: `iphone2gkit launcher`.

Ramdisk sizing: app‑install images are ≥ 13 MB (the size that completed on‑device);
Check/Repair use a 10 MB image; the default cap is 14 MB, splitting larger
selections into batches. Every embedded archive is tested before mounting/writing.
Larger ramdisks remain experimental — a successful USB upload doesn't prove the
payload files stay readable after boot.

## 8. On‑device validation history

- **iBoot‑159 phone (Oct 4, 2026):** a read‑only 10 MB probe completed. Activation
  and the first 39 app packs installed; the run stopped at 40/42 because the phone
  couldn't read BSD Base's ZIP directory. A retry with BSD Base, OpenSSH, Installer
  in a 13 MB ramdisk passed the new ZIP‑integrity checks, finished, and returned
  in normal USB mode. Owner confirmed boot to the home screen with no activation
  prompt.
- **Launcher repair (v1.2.1):** the corrected Launcher/Finder repair completed and
  the phone rebooted to normal USB mode (the original 738‑byte binary layout
  needed conversion). Icons and individual app launches after the repair still
  need visual confirmation.

### What's verified and what isn't

| | Status |
|---|---|
| Boot command sequence / blob layout | Recovered from iLiberty.exe 1.3.0.113 (unpacked); iBoot‑159 has `bootx`/`setenv`/`saveenv` |
| Kernelcache | Signed stock 1.0 restore kernel, SHA‑256 pinned; boots on iBoot‑159 |
| Ramdisk build | Plain HFSX; 10 MB probe and 13 MB app install booted on‑device; default cap 14 MB |
| Payload translation | All 48 kit scripts translate; anything unknown aborts the build |
| Offline tests | `python3 -m unittest discover -s tests` |
| USB protocol | libirecovery 1.3.1 legacy iOS 1 path (CPID 0x8900, no ECID) |
| On‑device run | Probe, remaining‑app install, Launcher/Finder repair completed; all 42 selected packs processed across run + retry. Icons/app launches after the layout repair still need visual confirmation |
| Native restore | **Offline only. Experimental. No successful hardware restore recorded.** |

The 21 MB image used for the initial 42‑pack run booted but its BSD archive was
unreadable (an on‑device archive problem, distinct from the original USB upload
failure); it is not a supported default.

## 9. Command line

```sh
iphone2gkit probe                 # read-only: firmware, free space, iLiberty leftovers
iphone2gkit install               # activate + every app (preset "apps")
iphone2gkit install --no-activate # phone already activated
iphone2gkit install --apps launcher,lightsoff,mobileterminal
iphone2gkit install --apps all    # apps + tweaks; SummerBoard only if named
iphone2gkit repair                # undo iLiberty leftovers and boot
iphone2gkit launcher              # show Launcher + Finder; keep activation
iphone2gkit list                  # app keys (+ era labels)
iphone2gkit status / kick         # phone mode / leave recovery and boot
iphone2gkit restore-info|restore-plan|restore   # read-only inspect, then confirmed erase+restore
```

`restore-info` / `restore-plan --target VERSION` never write to the phone. The
`restore` command requires explicit erasure/model confirmations plus the relevant
experimental/custom/1.0 warning flags. On‑device installs tell you how to enter
recovery (power off, hold Home, plug in, release at "Connect to iTunes"); the disk
and kernel upload separately in 16 KB chunks. `iphone2gkit build [--apps …]`
builds blobs (with a `.profile.sh` of the exact script) without a phone. The
`ios1kit` command name is kept as a backward‑compatible alias.

Troubleshooting the CLI: a failed install leaves the phone in recovery showing
`FAILED at step …`; the on‑phone log is `/var/root/Media/ios1kit/install.log`.
`df-system-unreadable`/`fsck-…` → run `probe`; `system-partition-full` → fewer
apps; stuck on the Apple logo → `kick` from recovery.

## 10. Build from source

**macOS:** `macos/build-app.sh --public` (needs Xcode/CLT plus pkgconf, autoconf,
automake, libtool, cmake, perl as build tools; fetch scripts use
pinned‑checksum sources for the private native tools). The signed public bundle
passes clean‑PATH checks and contains no Apple firmware. `macos/build-app.sh
KIT_FOLDER` is the private mode for local historical‑kit testing, not the public
recipe.

**Linux:** `bash linux/build-linux.sh --public` on Ubuntu 22.04+; `bash
linux/test-linux.sh` exercises the packaged CLI and GUI under Xvfb. The GitHub
Actions workflow builds and tests Linux before publishing. CI does **not** perform
a physical restore. Both platforms require explicit erasure/model acknowledgments,
preserve logs, and avoid automatic retry erasures.

Bundle layout (`…app/Contents/Resources`, mirrored on Linux): `engine/` (shared
Python engine + install scripts), `python/` (private Python 3.12, stdlib only,
SHA‑256 pinned), `bin/` (patched static libirecovery, legacy console reader, the
restore tools, Terminal launcher — linked only to system libraries),
`ThirdPartySources/` + `ThirdPartyLicenses/`. The app otherwise runs only the
OS's own `hdiutil`/`ditto`/`ioreg`/`ssh-keygen` (macOS) equivalents; PATH is
limited to its own `bin/` and system folders. Build artifacts go in
`~/Library/Caches/ios1kit/build`; the OpenSSH host key in `~/.ios1kit/`.

## 11. Restoring 1.0 the historical way

The historical **iTunes 7 / Windows XP** route remains the kit's previously
demonstrated 1.0 restoration method. The native 1.x path in this release is
experimental and has not replaced that observed validation.
