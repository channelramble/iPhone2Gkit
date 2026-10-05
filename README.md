# iPhone2Gkit

A native **macOS app** and a **Linux desktop app/CLI** for the original iPhone
(iPhone1,1): restore or downgrade its firmware and set up iPhone OS 1.0 —
activation, the 2007‑era apps, and a Launcher home screen — with no Windows XP,
iTunes, or iLiberty.

> **Status — experimental preview (v2.2.0‑beta.2).** Activation and app install
> are proven on a real phone. The **restore/downgrade feature has only been
> tested offline**; it erases the phone and can leave it stuck in recovery/DFU,
> so treat it as experimental. How it works and exactly what's verified:
> **[AGENTS.md](AGENTS.md)**.

## Download

Grab the latest build from **[Releases »](https://github.com/channelramble/iPhone2Gkit/releases/latest)**

| Platform | File | Requirements |
|---|---|---|
| macOS | `iPhone2Gkit-macos-universal.zip` | Intel or Apple Silicon, macOS 12 Monterey+ |
| Linux | `iPhone2Gkit-linux-x86_64.tar.gz` | x86_64, Ubuntu 22.04+ / glibc 2.35+, X11 or XWayland |

No Homebrew, system Python, iTunes, or XP needed — each package carries its own
runtime and USB/restore tools. Apple firmware is **not** bundled; the app
downloads and checksum‑verifies it on first use.

## Run it

**macOS** — unzip and open `iPhone2Gkit.app`. It's ad‑hoc signed (not notarized),
so the first launch may be blocked: allow it in **System Settings → Privacy &
Security → Open Anyway** ([Apple guidance](https://support.apple.com/en-us/102445)).

**Linux** — extract and run `./iPhone2Gkit/iphone2gkit` for the app. Pass
arguments to use the CLI, e.g. `./iPhone2Gkit/iphone2gkit restore-info`. USB
permissions/setup: [linux/README.md](linux/README.md).

Click **Download Setup Files** once. The app downloads and checks everything
needed for 1.0 app installation (about 115 MB including stock firmware), then
shows the app list. You don't need to find a kit folder. For a phone already
running 1.0, start with **Check phone**, select your apps, then **Install**.

## What it does

- **Restore / downgrade** (*Restore* tab) — stock **3.1.3, 1.0, 1.1.1, 1.1.3**,
  or a custom iPhone1,1 IPSW. Download & verify firmware in‑app, inspect the
  plan, then erase & restore behind explicit confirmations. *Experimental.*
- **iPhone OS 1.0 setup** — activate 1.0 and install the historical app packs.
  Every app shows whether its bundled build is verified as a 2007 original.
- **Launcher** — 1.0 has a single home screen, so **Show Launcher** makes
  Launcher + Finder reachable to open everything you installed.

## Important caveats

- **Restore is experimental and not hardware‑verified.** It can leave a phone
  partially written or stuck in recovery/DFU; native 1.x is not a proven
  restore. The log is saved and nothing auto‑retries. The *proven* workflow is
  activation + app install on a phone already running 1.0.
- **Compatibility is decided by the NAND flash chip, not the serial.** Every
  original iPhone reports `iPhone1,1`; the serial can't identify the chip.
  "Earliest supported 1.x" only resolves when a known NAND ID is supplied —
  automatic NAND discovery isn't implemented yet — and 1.0 stays selectable
  behind a warning.
- Stock IPSWs download separately. Historical components retain their original
  licenses; the project's MIT license applies to its original code.

## Troubleshooting

- **Phone came back in recovery after an install** → the run failed and showed
  `FAILED at step …`. Re‑run (steps are safe to repeat), or **Exit recovery
  mode** to boot as‑is.
- **Stuck on the Apple logo after a failed boot** → it's still carrying ramdisk
  boot‑args; enter recovery and use **Exit recovery mode** (`iphone2gkit kick`).
- **SpringBoard won't start after SummerBoard** → install the `undosummerboard` pack.
- **SSH after installing OpenSSH** → `root` / `dottie` over Wi‑Fi.

## More

- **[AGENTS.md](AGENTS.md)** — how it works; the NAND, boot and USB internals;
  the restore engine; build‑from‑source; and what's verified vs not.
- **[LICENSE](LICENSE)** — MIT (original code) · **[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)**
