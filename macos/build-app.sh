#!/bin/sh
# Build a self-contained iPhone2Gkit.app into ../dist:
#   Contents/MacOS/iPhone2Gkit         SwiftUI front end
#   Contents/Resources/engine/        the ios1kit engine (same code as the CLI)
#   Contents/Resources/python/        private Python 3.12 (stdlib only)
#   Contents/Resources/bin/irecovery  static libirecovery 1.3.1 (iOS 1 recovery protocol)
#   Contents/Resources/kit-assets/    kernel, ramdisk, activation and app packs, checksum-verified
# The only outside programs the app runs are macOS's own hdiutil, ditto, ioreg and ssh-keygen.
#
# Usage: build-app.sh [KIT_FOLDER]   (default: the iphone-2g-ios-1-kit-full the engine finds)
set -eu
PUBLIC=0
if [ "${1:-}" = "--public" ]; then PUBLIC=1; shift; fi
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
DIST="$ROOT/dist"
APP="$DIST/iPhone2Gkit.app"
R="$APP/Contents/Resources"

"$HERE/fetch-deps.sh"
"$HERE/fetch-restore-deps.sh"
V="$HERE/vendor"

KIT="${1:-}"
if [ "$PUBLIC" = 0 ]; then
    KIT="${KIT:-$("$V/python/bin/python3" -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from kit.cli import find_kit; print(find_kit() or "")' "$ROOT")}"
    [ -n "$KIT" ] && [ -f "$KIT/iLiberty-portable/iLiberty/iLibertyKC.dat" ] || {
        echo "Kit folder not found; pass it: $0 /path/to/iphone-2g-ios-1-kit-full" >&2; exit 1; }
    echo "kit: $KIT"
    "$V/python/bin/python3" -B "$HERE/fetch-firmware.py" "$KIT"
fi

cd "$HERE"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/iPhone2Gkit"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$R/engine/kit" "$R/bin"
cp "$BIN" "$APP/Contents/MacOS/iPhone2Gkit"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
[ -f "$HERE/AppIcon.icns" ] && cp "$HERE/AppIcon.icns" "$R/"

# engine
cp "$ROOT/ios1kit" "$ROOT/README.md" "$R/engine/"
cp "$ROOT"/kit/*.py "$ROOT/kit/installer.sh.in" "$ROOT/kit/homescreen.awk" "$R/engine/kit/"

# runtime
ditto "$V/python" "$R/python"
find "$R/python" -name '__pycache__' -prune -exec rm -rf {} +
cp "$V/irecovery" "$R/bin/irecovery"
cp "$HERE/vendor/ios1kit-recovery-console" "$R/bin/ios1kit-recovery-console"
cp "$HERE/ios1kit-cli.sh" "$R/bin/ios1kit"
chmod 755 "$R/bin/ios1kit"
cp "$HERE/ios1kit-cli.sh" "$R/bin/iphone2gkit"
chmod 755 "$R/bin/iphone2gkit"
cp "$V/restore/bin/idevicerestore" "$V/restore/bin/ideviceinfo" "$V/restore/bin/idevice_id" "$R/bin/"
ditto "$V/restore/licenses" "$R/ThirdPartyLicenses"
mkdir -p "$R/ThirdPartySources"
cp "$V/restore/src/"*.tar.* "$R/ThirdPartySources/"
cp "$V/src/libplist-2.7.0.tar.bz2" "$V/src/libimobiledevice-glue-1.3.2.tar.bz2" \
    "$V/src/libirecovery-1.3.1.tar.bz2" "$R/ThirdPartySources/"
cp "$HERE/fetch-deps.sh" "$HERE/fetch-restore-deps.sh" "$HERE/restore-build.patch" \
    "$HERE/libirecovery-iokit.patch" "$HERE/recovery-console.c" "$R/ThirdPartySources/"
mkdir -p "$R/firmware"
if [ "$PUBLIC" = 0 ]; then cp "$V/firmware/"*.ipsw "$R/firmware/"; fi

mkdir -p "$R/engine/kit/resources"
cp "$ROOT/kit/resources/PWNAGE-SOURCE-LICENSE.txt" "$ROOT/kit/resources/pwnage2-wtf.patch" "$R/engine/kit/resources/"
if [ "$PUBLIC" = 0 ]; then
    cp "$ROOT/kit/resources/kernelcache-1.0.dat" "$ROOT/kit/resources/plutil-ios1" "$R/engine/kit/resources/"
    chmod 644 "$R/engine/kit/resources/plutil-ios1"
fi

# kit assets: only what the engine reads (the iLiberty "master" packs are never used)
A="$R/kit-assets"
if [ "$PUBLIC" = 0 ]; then
mkdir -p "$A/iLiberty-portable/iLiberty" "$A/iLiberty-portable/optional-payloads" "$A/ios1-apps/iliberty-payloads"
cp "$KIT/iLiberty-portable/iLiberty/iLibertyKC.dat" "$KIT/iLiberty-portable/iLiberty/iLibertyRD.zip" "$A/iLiberty-portable/iLiberty/"
cp "$KIT/iLiberty-portable/optional-payloads/Activate10And101.zip" "$A/iLiberty-portable/optional-payloads/"
cp "$KIT/ios1-apps/catalog.json" "$A/ios1-apps/"
for f in "$KIT"/ios1-apps/iliberty-payloads/*; do
	case "$(basename "$f")" in
		iOS1-AllApps.zip|iOS1-AppsOnly.zip|*iOS1AllApps.sh|*iOS1AppsOnly.sh) ;;
		*) cp "$f" "$A/ios1-apps/iliberty-payloads/" ;;
	esac
done
fi

# the terminal command must work through a symlink, from anywhere
ln -sf "$R/bin/ios1kit" "$DIST/.ios1kit-link"
(cd / && "$DIST/.ios1kit-link" list --json >/dev/null) || { echo "bundled ios1kit command failed" >&2; exit 1; }
rm -f "$DIST/.ios1kit-link"

# sign every Mach-O inside (arm64 refuses unsigned code), then the app itself
find "$R" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' \) -print0 |
	while IFS= read -r -d '' f; do
		if file -b "$f" | grep -q 'Mach-O'; then codesign --force --sign - "$f"; fi
	done
codesign --force --sign - "$APP"
codesign --verify --deep --strict "$APP"

# prove the bundle works on its own: bundled python + engine + irecovery + assets, clean PATH
env -i HOME="$HOME" PATH="$R/bin:/usr/bin:/bin:/usr/sbin:/sbin" PYTHONDONTWRITEBYTECODE=1 \
	"$R/python/bin/python3" "$R/engine/ios1kit" doctor --json >"$DIST/selfcheck.json" || true
"$R/python/bin/python3" -B -c '
import json, sys
r = json.load(open(sys.argv[1]))
assert r["irecovery"], "bundled irecovery not usable"
if sys.argv[2] == "0":
    assert "kit-assets" in (r["kit"] or ""), "bundle did not use its own kit: %r" % r["kit"]
    assert not r["problems"], r["problems"]
print("self-check ok: bundled engine and irecovery", r["irecovery"])
' "$DIST/selfcheck.json" "$PUBLIC"
rm -f "$DIST/selfcheck.json"

# Firmware stays inside the bundle and every restore program is self-contained.
env -i HOME="$HOME" PATH="$R/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
    "$R/python/bin/python3" -B -c '
import sys, subprocess
sys.path.insert(0, sys.argv[1])
from kit import restore
if sys.argv[2] == "0":
    for f in restore.catalog():
        p = restore.firmware_path(f)
        assert p and restore.sha256(p) == f["sha256"] and p.stat().st_size == f["size"], f["filename"]
for name in ("idevicerestore", "ideviceinfo", "idevice_id"):
    assert subprocess.run([restore.binary(name), "--help"], stdout=subprocess.DEVNULL).returncode == 0
print("restore self-check ok: private restore tools")
' "$R/engine" "$PUBLIC"


(cd "$DIST" && rm -f iPhone2Gkit.zip && ditto -c -k --keepParent iPhone2Gkit.app iPhone2Gkit.zip)
echo "Built $APP ($(du -sh "$APP" | cut -f1))"
echo "      $DIST/iPhone2Gkit.zip ($(du -sh "$DIST/iPhone2Gkit.zip" | cut -f1))"
