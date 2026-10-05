#!/usr/bin/env bash
# No phone is opened or changed. Optional private assets stay outside the archive.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
DIST="$ROOT/dist"
APP="$DIST/iPhone2Gkit"
R="$APP/resources"
PY="$R/python/bin/python3"
[ -x "$PY" ] || { echo 'Build the Linux package first.' >&2; exit 1; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export HOME="$WORK/home"
export XDG_CACHE_HOME="$HOME/.cache"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_DATA_HOME="$HOME/.local/share"
export PATH="$R/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LD_LIBRARY_PATH="$R/lib:$R/python/lib"
export PYTHONDONTWRITEBYTECODE=1
export HFSPLUS="$R/bin/hfsplus"
mkdir -p "$HOME" "$XDG_DATA_HOME/iPhone2Gkit/resources"
FIXTURES="${IPHONE2GKIT_FIXTURE_ROOT:-}"
if [ -n "$FIXTURES" ]; then
    [ -d "$FIXTURES/kit-assets" ] && [ -f "$FIXTURES/resources/kernelcache-1.0.dat" ] && \
        [ -f "$FIXTURES/resources/plutil-ios1" ] || { echo 'Incomplete private Linux test fixtures.' >&2; exit 1; }
    cp "$FIXTURES/resources/kernelcache-1.0.dat" "$FIXTURES/resources/plutil-ios1" "$XDG_DATA_HOME/iPhone2Gkit/resources/"
    export IOS1KIT_ASSETS="$FIXTURES/kit-assets"
    "$PY" -B -c 'import os, zipfile; from pathlib import Path; f=Path(os.environ["IOS1KIT_ASSETS"])/"iLiberty-portable/iLiberty/iLibertyRD.zip"; z=zipfile.ZipFile(f); Path(os.environ["HOME"]+"/base-hfs.img").write_bytes(z.read("iLibertyRD.dat"))'
    export IOS1KIT_HFS_BASE="$HOME/base-hfs.img"
fi
cd "$ROOT"
{
    echo 'Linux package validation (offline; no phone actions)'
    "$PY" -B -c 'import sys, tkinter, ssl; print(sys.version); print("Tk", tkinter.TkVersion, "TLS", ssl.OPENSSL_VERSION)'
    for binary in irecovery idevicerestore ideviceinfo idevice_id usbmuxd hfsplus ssh-keygen; do
        echo "== $binary dynamic dependencies =="
        ldd "$R/bin/$binary"
    done
    # Test the same modules as source on a real Linux host, plus actual window init.
    xvfb-run -a "$PY" -B -m unittest discover -s tests -v
    xvfb-run -a "$APP/iphone2gkit" --gui-smoke-test --screenshot "$DIST/linux-gui.png"
    # Headless CLI portability: no external Python and no reliance on cwd.
    ln -s "$APP/iphone2gkit" "$WORK/iphone2gkit-link"
    (cd /; "$WORK/iphone2gkit-link" --help >/dev/null; "$WORK/iphone2gkit-link" restore-info)
    if [ -n "$FIXTURES" ]; then
        "$APP/iphone2gkit" doctor --json
        "$APP/iphone2gkit" build --mode probe --out "$WORK/probe" --ramdisk-mb 14
        "$APP/iphone2gkit" build --out "$WORK/apps" --apps apps --ramdisk-mb 22
        "$PY" -B -c 'from pathlib import Path; import sys; paths=list(Path(sys.argv[1]).glob("*.bin")); assert paths; print("Offline app blobs:", [(p.name, p.stat().st_size) for p in paths])' "$WORK/apps"
    fi
    echo 'Linux package validation passed.'
} 2>&1 | tee "$DIST/linux-test-report.txt"
