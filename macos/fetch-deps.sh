#!/bin/sh
# Fetch/build everything the core app bundles, UNIVERSAL (arm64 + x86_64):
#   python/     private CPython 3.12 (python-build-standalone, stdlib only), lipo'd universal
#   irecovery   static build of libirecovery 1.3.1 (+ libplist, libimobiledevice-glue),
#               the first release that speaks iPhone OS 1's legacy recovery protocol
# Every download is pinned by SHA-256. Needs Xcode or the Command Line Tools,
# and pkg-config for the irecovery build (brew install pkgconf). No Rosetta needed:
# configure runs the arm64 slice of the fat test binaries.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
V="$HERE/vendor"
mkdir -p "$V/src"
export MACOSX_DEPLOYMENT_TARGET=12.0
ARCHES="-arch arm64 -arch x86_64"
# universal? (order-independent)
is_uni() { a="$(lipo -archs "$1" 2>/dev/null)" || return 1; echo "$a" | grep -qw arm64 && echo "$a" | grep -qw x86_64; }

fetch() { # url file sha256
	if [ ! -f "$V/src/$2" ] || [ "$(shasum -a 256 "$V/src/$2" | cut -d' ' -f1)" != "$3" ]; then
		echo "downloading $2"
		curl -fsSL -o "$V/src/$2" "$1"
	fi
	got="$(shasum -a 256 "$V/src/$2" | cut -d' ' -f1)"
	[ "$got" = "$3" ] || { echo "checksum mismatch for $2: $got" >&2; exit 1; }
}

PY=cpython-3.12.15+20261003-aarch64-apple-darwin-install_only_stripped.tar.gz
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15%2B20261003-aarch64-apple-darwin-install_only_stripped.tar.gz" \
	"$PY" ad8d0c637c0a36b967b310e2c07254f4d2ca8cabaa7699e55ed6290aceb481a2
PYX=cpython-3.12.15+20261003-x86_64-apple-darwin-install_only_stripped.tar.gz
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15%2B20261003-x86_64-apple-darwin-install_only_stripped.tar.gz" \
	"$PYX" 562c30864ece2cb1d3e0ad66a1acd498611a47e5a10ce81b99158bef1ccbd355
# Install-only archives omit notices for several linked components. Preserve
# the upstream license collection and build record (arch-independent).
PYFULL=cpython-3.12.15+20261003-aarch64-apple-darwin-pgo+lto-full.tar.zst
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15%2B20261003-aarch64-apple-darwin-pgo%2Blto-full.tar.zst" \
	"$PYFULL" a23a0baff73a5f10c820841cc1889a5b7fc12048fe8c0622f6a481ab82ea367b
mkdir -p "$V/python-notices"
tar -xf "$V/src/$PYFULL" -C "$V/python-notices" --strip-components=1 python/licenses python/PYTHON.json
fetch https://github.com/libimobiledevice/libplist/releases/download/2.7.0/libplist-2.7.0.tar.bz2 \
	libplist-2.7.0.tar.bz2 7ac42301e896b1ebe3c654634780c82baa7cb70df8554e683ff89f7c2643eb8b
fetch https://github.com/libimobiledevice/libimobiledevice-glue/releases/download/1.3.2/libimobiledevice-glue-1.3.2.tar.bz2 \
	libimobiledevice-glue-1.3.2.tar.bz2 6489a3411b874ecd81c87815d863603f518b264a976319725e0ed59935546774
fetch https://github.com/libimobiledevice/libirecovery/releases/download/1.3.1/libirecovery-1.3.1.tar.bz2 \
	libirecovery-1.3.1.tar.bz2 28a3a521782063c8eb2ee5f4c0f38a517e023853edb55856052cdd7ac400381b

# --- Python: build a universal tree. Start from the trimmed arm64 stdlib, then
# lipo every Mach-O with its x86_64 counterpart (pure-Python files are identical).
if [ ! -x "$V/python/bin/python3" ] || ! is_uni "$V/python/lib/libpython3.12.dylib"; then
	rm -rf "$V/python" "$V/python-x86"
	tar -xzf "$V/src/$PY" -C "$V"
	mkdir -p "$V/python-x86"
	tar -xzf "$V/src/$PYX" -C "$V/python-x86"
	L="$V/python/lib/python3.12"
	rm -rf "$L/test" "$L/idlelib" "$L/tkinter" "$L/turtledemo" "$L/ensurepip" "$L/lib2to3" \
		"$L/site-packages" "$V/python/lib/tcl"* "$V/python/lib/tk"* "$V/python/lib/itcl"* \
		"$V/python/lib/thread"* "$V/python/lib/libtcl"* "$V/python/lib/libtk"* \
		"$V/python/include" "$V/python/share"
	find "$L/lib-dynload" -name '_tkinter*' -delete
	find "$V/python" -name '__pycache__' -prune -exec rm -rf {} +
	# merge x86_64 slices into every Mach-O; fail loudly on a missing slice or lipo error
	rm -f "$V/.merge-fail"
	( cd "$V/python" && find . -type f -print0 ) | while IFS= read -r -d '' rel; do
		armf="$V/python/$rel"; xf="$V/python-x86/python/$rel"
		case "$(file -b "$armf" 2>/dev/null)" in
			*Mach-O*)
				if [ ! -f "$xf" ]; then echo "universal: no x86_64 counterpart for $rel" >&2; echo 1 > "$V/.merge-fail"; break; fi
				mode="$(stat -f %OLp "$armf")"
				if ! lipo -create "$armf" "$xf" -output "$armf.uni"; then echo "universal: lipo failed for $rel" >&2; echo 1 > "$V/.merge-fail"; break; fi
				mv -f "$armf.uni" "$armf"; chmod "$mode" "$armf" ;;
		esac
	done
	[ ! -f "$V/.merge-fail" ] || { rm -f "$V/.merge-fail"; echo "universal Python assembly failed" >&2; exit 1; }
	rm -rf "$V/python-x86"
fi

# --- irecovery + static libs, universal.
PATCH_SHA="$(shasum -a 256 "$HERE/libirecovery-iokit.patch" | cut -d' ' -f1)"
UNI_OK=0; [ -x "$V/irecovery" ] && is_uni "$V/irecovery" && UNI_OK=1
if [ "$UNI_OK" = 0 ] || [ ! -f "$V/build/prefix/lib/libirecovery-1.0.a" ] || [ "$(cat "$V/irecovery-patch.sha256" 2>/dev/null || true)" != "$PATCH_SHA" ]; then
	P="$V/build/prefix"
	rm -rf "$V/build"
	mkdir -p "$V/build"
	export PKG_CONFIG_PATH="$P/lib/pkgconfig"
	export PKG_CONFIG_LIBDIR="$P/lib/pkgconfig"
	export CFLAGS="-O2 -mmacosx-version-min=12.0 $ARCHES"
	export LDFLAGS="-mmacosx-version-min=12.0 $ARCHES"
	for pkg in libplist-2.7.0 libimobiledevice-glue-1.3.2 libirecovery-1.3.1; do
		tar -xjf "$V/src/$pkg.tar.bz2" -C "$V/build"
		if [ "$pkg" = libirecovery-1.3.1 ]; then
			patch -d "$V/build/$pkg" -p1 < "$HERE/libirecovery-iokit.patch"
		fi
		(
			cd "$V/build/$pkg"
			extra=""
			[ "$pkg" = libplist-2.7.0 ] && extra="--without-cython --without-tests"
			./configure --prefix="$P" --disable-shared --enable-static $extra >"$V/build/$pkg.log" 2>&1
			make -j8 >>"$V/build/$pkg.log" 2>&1
			make install >>"$V/build/$pkg.log" 2>&1
		) || { echo "build of $pkg failed; see $V/build/$pkg.log" >&2; exit 1; }
	done
	cp "$P/bin/irecovery" "$V/irecovery"
	printf '%s\n' "$PATCH_SHA" > "$V/irecovery-patch.sha256"
fi

# Read the legacy console so installer failures are visible on the Mac (universal).
CONSOLE_SHA="$(shasum -a 256 "$HERE/recovery-console.c" | cut -d' ' -f1):$PATCH_SHA:uni"
if [ ! -x "$V/ios1kit-recovery-console" ] || ! is_uni "$V/ios1kit-recovery-console" || [ "$(cat "$V/console.sha256" 2>/dev/null || true)" != "$CONSOLE_SHA" ]; then
    P="$V/build/prefix"
    clang -O2 -mmacosx-version-min=12.0 $ARCHES -I"$P/include" "$HERE/recovery-console.c" \
        "$P/lib/libirecovery-1.0.a" "$P/lib/libimobiledevice-glue-1.0.a" "$P/lib/libplist-2.0.a" \
        -framework IOKit -framework CoreFoundation -o "$V/ios1kit-recovery-console"
    printf '%s\n' "$CONSOLE_SHA" > "$V/console.sha256"
fi

echo "vendor ready (universal):"
"$V/python/bin/python3" -B -c 'import sys, zipfile, hashlib, json; print("  python", sys.version.split()[0])'
echo "  python arch: $(lipo -archs "$V/python/lib/libpython3.12.dylib")"
"$V/irecovery" -V | sed 's/^/  /'
echo "  irecovery arch: $(lipo -archs "$V/irecovery")"
for f in "$V/python/bin/python3.12" "$V/python/lib/libpython3.12.dylib" "$V/irecovery" "$V/ios1kit-recovery-console"; do
	is_uni "$f" || { echo "NOT universal: $f ($(lipo -archs "$f" 2>/dev/null))" >&2; exit 1; }
done
otool -L "$V/irecovery" | grep -v -E '^\s*/(usr/lib|System)/|:$' && { echo "irecovery links a non-system library" >&2; exit 1; } || true
