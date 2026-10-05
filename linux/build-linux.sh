#!/usr/bin/env bash
# Ubuntu 22.04+ x86_64 builder. Runtime software is private to the tar.gz.
# Public builds contain no Apple IPSWs or proprietary legacy app payloads.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
V="$HERE/vendor"
P="$V/prefix"
DIST="$ROOT/dist"
BUNDLE="$DIST/iPhone2Gkit"
PUBLIC=0
KIT=""
for argument in "$@"; do
    case "$argument" in
        --public) PUBLIC=1 ;;
        *) [ -z "$KIT" ] || { echo 'Use: build-linux.sh --public [optional private kit folder]' >&2; exit 1; }; KIT="$argument" ;;
    esac
done
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || { echo 'Build on x86_64 GNU/Linux (Ubuntu 22.04 or newer).' >&2; exit 1; }
for tool in cc c++ make cmake pkg-config autoreconf curl tar patch patchelf sha256sum perl zstd; do
    command -v "$tool" >/dev/null || { echo "Missing build tool: $tool. See linux/README.md." >&2; exit 1; }
done
mkdir -p "$V/src" "$P" "$DIST"

fetch() { # URL filename SHA256; cached data is verified on every run.
    local file="$V/src/$2"
    if [ ! -f "$file" ] || [ "$(sha256sum "$file" | cut -d' ' -f1)" != "$3" ]; then
        echo "Downloading $2"
        curl -fsSL --retry 3 --connect-timeout 30 -o "$file.part" "$1"
        [ "$(sha256sum "$file.part" | cut -d' ' -f1)" = "$3" ] || { rm -f "$file.part"; echo "Checksum mismatch: $2" >&2; exit 1; }
        mv "$file.part" "$file"
    fi
}

PY=cpython-3.12.15+20261003-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
# Digest from the upstream GitHub release asset API, not inferred from its name.
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15%2B20261003-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" \
    "$PY" 731af898886c5f821890dc901eca3c651cca8e51fa7308c159d12a1194aeac91
# The install-only archive omits notices for linked runtime components. Extract
# all licenses + build metadata from the matching full upstream distribution.
PYFULL=cpython-3.12.15+20261003-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15%2B20261003-x86_64-unknown-linux-gnu-pgo%2Blto-full.tar.zst" \
    "$PYFULL" aaeeeeea4f98a41c7acd7379229a374c201aee45c1954be5788c18af83733d2b
mkdir -p "$V/python-notices"
tar --zstd -xf "$V/src/$PYFULL" -C "$V/python-notices" --strip-components=1 python/licenses python/PYTHON.json
fetch https://raw.githubusercontent.com/tcltk/tk/core-9-0-4/license.terms \
    tk-9.0.4-license.terms 2cde822b93ca16ae535c954b7dfe658b4ad10df2a193628d1b358f1765e8b198
fetch https://github.com/libimobiledevice/libplist/releases/download/2.7.0/libplist-2.7.0.tar.bz2 \
    libplist-2.7.0.tar.bz2 7ac42301e896b1ebe3c654634780c82baa7cb70df8554e683ff89f7c2643eb8b
fetch https://github.com/libimobiledevice/libimobiledevice-glue/releases/download/1.3.2/libimobiledevice-glue-1.3.2.tar.bz2 \
    libimobiledevice-glue-1.3.2.tar.bz2 6489a3411b874ecd81c87815d863603f518b264a976319725e0ed59935546774
fetch https://github.com/libimobiledevice/libirecovery/releases/download/1.3.1/libirecovery-1.3.1.tar.bz2 \
    libirecovery-1.3.1.tar.bz2 28a3a521782063c8eb2ee5f4c0f38a517e023853edb55856052cdd7ac400381b
fetch https://github.com/libimobiledevice/libusbmuxd/releases/download/2.1.1/libusbmuxd-2.1.1.tar.bz2 \
    libusbmuxd-2.1.1.tar.bz2 5546f1aba1c3d1812c2b47d976312d00547d1044b84b6a461323c621f396efce
fetch https://github.com/libimobiledevice/libimobiledevice/releases/download/1.4.0/libimobiledevice-1.4.0.tar.bz2 \
    libimobiledevice-1.4.0.tar.bz2 23cc0077e221c7d991bd0eb02150a0d49199bcca1ddf059edccee9ffd914939d
fetch https://github.com/libimobiledevice/libtatsu/releases/download/1.0.5/libtatsu-1.0.5.tar.bz2 \
    libtatsu-1.0.5.tar.bz2 536fa228b14f156258e801a7f4d25a3a9dd91bb936bf6344e23171403c57e440
fetch https://github.com/libimobiledevice/usbmuxd/releases/download/1.1.1/usbmuxd-1.1.1.tar.bz2 \
    usbmuxd-1.1.1.tar.bz2 c0ec9700172bf635ccb5bed98daae607d2925c2bc3597f25706ecd9dfbfd2d9e
fetch https://github.com/libusb/libusb/releases/download/v1.0.29/libusb-1.0.29.tar.bz2 \
    libusb-1.0.29.tar.bz2 5977fc950f8d1395ccea9bd48c06b3f808fd3c2c961b44b0c2e6e29fc3a70a85
fetch https://github.com/openssl/openssl/releases/download/openssl-3.5.4/openssl-3.5.4.tar.gz \
    openssl-3.5.4.tar.gz 967311f84955316969bdb1d8d4b983718ef42338639c621ec4c34fddef355e99
fetch https://curl.se/download/curl-8.10.1.tar.xz \
    curl-8.10.1.tar.xz 73a4b0e99596a09fa5924a4fb7e4b995a85fda0d18a2c02ab9cf134bebce04ee
fetch https://github.com/nih-at/libzip/releases/download/v1.11.4/libzip-1.11.4.tar.xz \
    libzip-1.11.4.tar.xz 8a247f57d1e3e6f6d11413b12a6f28a9d388de110adc0ec608d893180ed7097b
RESTORE_COMMIT=c25aefd49b3769c2907875e15566d433d59bd979
fetch "https://codeload.github.com/tihmstar/idevicerestore/tar.gz/$RESTORE_COMMIT" \
    idevicerestore-c25aefd.tar.gz 266e1f444d97fcdd78227eba8c4b252803ef3680d3ad6c1a3e5f286fce96ac29
XPWN_COMMIT=20c32e5c12d1b22a9d55a59a0ff6267f539b77f4
fetch "https://codeload.github.com/planetbeing/xpwn/tar.gz/$XPWN_COMMIT" \
    xpwn-20c32e5.tar.gz 633a34c602bc95c27342eb63ac2502a15f7394ad8dcc9ace1781ba4bb2fb6e0d
fetch https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/openssh-9.9p2.tar.gz \
    openssh-9.9p2.tar.gz 91aadb603e08cc285eddf965e1199d02585fa94d994d6cae5b41e1721e215673

# Rebuild only when source checksums, patches or this recipe change.
BUILD_KEY="$(sha256sum "$0" "$HERE/usbmuxd-plist.patch" "$ROOT/macos/libirecovery-iokit.patch" "$ROOT/macos/restore-build.patch" "$ROOT/macos/recovery-console.c" | sha256sum | cut -d' ' -f1)"
if [ "$(cat "$V/build.key" 2>/dev/null || true)" != "$BUILD_KEY" ] || [ ! -x "$P/bin/idevicerestore" ] || [ ! -x "$P/bin/hfsplus" ]; then
    rm -rf "$V/build" "$P"
    mkdir -p "$V/build" "$P/lib/pkgconfig"
    for file in "$V/src/"*.tar.*; do
        case "$(basename "$file")" in cpython-*) ;; *) tar -xf "$file" -C "$V/build" ;; esac
    done
    export CFLAGS="-O2 -fPIC"
    export CXXFLAGS="$CFLAGS"
    export CPPFLAGS="-I$P/include"
    export LDFLAGS="-L$P/lib"
    export PKG_CONFIG_PATH="$P/lib/pkgconfig"
    export LD_LIBRARY_PATH="$P/lib"
    JOBS="${IPHONE2GKIT_JOBS:-$(getconf _NPROCESSORS_ONLN)}"
    configure_build() { # archive-directory configure-options
        local package="$1"; shift
        echo "Building $package"
        (
            cd "$V/build/$package" || exit 1
            ./configure --prefix="$P" --enable-shared --disable-static "$@" || exit 1
            make -j"$JOBS" || exit 1
            make install || exit 1
        ) > "$V/build/$package.log" 2>&1 || { tail -60 "$V/build/$package.log" >&2; exit 1; }
    }
    configure_build libusb-1.0.29 --disable-udev
    configure_build libplist-2.7.0 --without-cython --without-tests
    configure_build libimobiledevice-glue-1.3.2
    patch -d "$V/build/libirecovery-1.3.1" -p1 < "$ROOT/macos/libirecovery-iokit.patch"
    configure_build libirecovery-1.3.1 --with-udevrulesdir="$P/share/udev/rules.d"
    (
        cd "$V/build/openssl-3.5.4" || exit 1
        ./Configure linux-x86_64 shared no-module no-tests --prefix="$P" --libdir=lib --openssldir=/etc/ssl || exit 1
        make -j"$JOBS" || exit 1
        make install_sw || exit 1
    ) > "$V/build/openssl.log" 2>&1 || { tail -60 "$V/build/openssl.log" >&2; exit 1; }
    configure_build curl-8.10.1 --with-openssl="$P" --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt \
        --with-zlib --without-libpsl --without-libidn2 --without-librtmp --without-libssh2 \
        --without-libssh --without-gssapi --disable-ldap --disable-ldaps \
        --without-nghttp2 --without-nghttp3 --without-ngtcp2 --without-brotli --without-zstd
    configure_build libusbmuxd-2.1.1
    configure_build libtatsu-1.0.5
    configure_build libimobiledevice-1.4.0 --without-cython --without-readline --without-gnutls --without-mbedtls \
        --with-openssl --disable-wireless-pairing
    # usbmuxd 1.1.1's private enum predates libplist's public format enum. Keep
    # its original 0/1 values and rename only the private constants, not APIs.
    patch -d "$V/build/usbmuxd-1.1.1" -p1 < "$HERE/usbmuxd-plist.patch"
    configure_build usbmuxd-1.1.1 --without-systemd --without-preflight --with-udevrulesdir="$P/share/udev/rules.d"
    cmake -S "$V/build/libzip-1.11.4" -B "$V/build/libzip-cmake" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$P" \
        -DBUILD_SHARED_LIBS=ON -DBUILD_TOOLS=OFF -DBUILD_REGRESS=OFF -DBUILD_OSSFUZZ=OFF -DBUILD_EXAMPLES=OFF \
        -DBUILD_DOC=OFF -DENABLE_COMMONCRYPTO=OFF -DENABLE_GNUTLS=OFF -DENABLE_MBEDTLS=OFF \
        -DENABLE_OPENSSL=OFF -DENABLE_BZIP2=OFF -DENABLE_LZMA=OFF -DENABLE_ZSTD=OFF > "$V/build/libzip.log" 2>&1 || { tail -60 "$V/build/libzip.log" >&2; exit 1; }
    cmake --build "$V/build/libzip-cmake" -j"$JOBS" >> "$V/build/libzip.log" 2>&1 || { tail -60 "$V/build/libzip.log" >&2; exit 1; }
    cmake --install "$V/build/libzip-cmake" >> "$V/build/libzip.log" 2>&1 || { tail -60 "$V/build/libzip.log" >&2; exit 1; }
    (
        cd "$V/build/idevicerestore-$RESTORE_COMMIT" || exit 1
        printf '%s\n' "iphone2gkit-$RESTORE_COMMIT" > .tarball-version || exit 1
        patch -p1 < "$ROOT/macos/restore-build.patch" || exit 1
        autoreconf -fi || exit 1
    ) > "$V/build/idevicerestore-bootstrap.log" 2>&1 || { tail -60 "$V/build/idevicerestore-bootstrap.log" >&2; exit 1; }
    configure_build "idevicerestore-$RESTORE_COMMIT" --with-openssl
    # HFS-only target avoids xpwn's unrelated obsolete OpenSSL / USB code.
    cp "$V/build/xpwn-$XPWN_COMMIT/CMakeLists.txt" "$V/build/xpwn-$XPWN_COMMIT/CMakeLists.upstream.txt"
    cat > "$V/build/xpwn-$XPWN_COMMIT/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.10)
project(XPwnHFS C)
include_directories(${PROJECT_SOURCE_DIR}/includes)
add_subdirectory(common)
add_subdirectory(hfs)
EOF
    cmake -S "$V/build/xpwn-$XPWN_COMMIT" -B "$V/build/xpwn-cmake" -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS="-O2 -fcommon" > "$V/build/hfsplus.log" 2>&1 || { tail -60 "$V/build/hfsplus.log" >&2; exit 1; }
    cmake --build "$V/build/xpwn-cmake" --target hfsplus -j"$JOBS" >> "$V/build/hfsplus.log" 2>&1 || { tail -60 "$V/build/hfsplus.log" >&2; exit 1; }
    cp "$V/build/xpwn-cmake/hfs/hfsplus" "$P/bin/hfsplus"
    # Host-key generation for the optional phone OpenSSH pack; no host OpenSSH needed.
    (
        cd "$V/build/openssh-9.9p2" || exit 1
        ./configure --prefix="$P" --with-ssl-dir="$P" --without-pam --without-selinux || exit 1
        make -j"$JOBS" ssh-keygen || exit 1
        cp ssh-keygen "$P/bin/ssh-keygen" || exit 1
    ) > "$V/build/ssh-keygen.log" 2>&1 || { tail -60 "$V/build/ssh-keygen.log" >&2; exit 1; }
    cc -O2 $(pkg-config --cflags libirecovery-1.0) "$ROOT/macos/recovery-console.c" \
        $(pkg-config --libs libirecovery-1.0) -o "$P/bin/ios1kit-recovery-console"
    printf '%s\n' "$BUILD_KEY" > "$V/build.key"
fi

rm -rf "$BUNDLE"
R="$BUNDLE/resources"
mkdir -p "$R/engine/kit/resources" "$R/bin" "$R/lib" "$R/ThirdPartySources" "$R/ThirdPartyLicenses"
tar -xzf "$V/src/$PY" -C "$R"
# Keep Tk and its Tcl scripts. No pip, IDE, package manager or build headers.
rm -rf "$R/python/include" "$R/python/lib/python3.12/site-packages" "$R/python/lib/python3.12/test" \
    "$R/python/lib/python3.12/idlelib" "$R/python/lib/python3.12/ensurepip" "$R/python/lib/python3.12/turtledemo" \
    "$R/python/lib/itcl4.3.8" "$R/python/lib/thread3.0.6"
rm -f "$R/python/bin/pip"* "$R/python/bin/idle"* "$R/python/bin/"*config
find "$R/python" -name '__pycache__' -prune -exec rm -rf {} +
cp "$ROOT/ios1kit" "$R/engine/ios1kit"
cp "$ROOT"/kit/*.py "$ROOT/kit/installer.sh.in" "$ROOT/kit/homescreen.awk" "$R/engine/kit/"
cp "$ROOT/kit/resources/pwnage2-wtf.patch" "$ROOT/kit/resources/PWNAGE-SOURCE-LICENSE.txt" "$R/engine/kit/resources/"
if [ "$PUBLIC" = 0 ]; then
    cp "$ROOT/kit/resources/kernelcache-1.0.dat" "$ROOT/kit/resources/plutil-ios1" "$R/engine/kit/resources/"
    if [ -n "$KIT" ]; then
        cp -a "$KIT" "$R/kit-assets"
    fi
fi
for binary in irecovery idevicerestore ideviceinfo idevice_id ios1kit-recovery-console hfsplus ssh-keygen; do
    cp "$P/bin/$binary" "$R/bin/"
done
cp "$P/sbin/usbmuxd" "$R/bin/"
cp -a "$P/lib/"*.so* "$R/lib/"
# System zlib is the only non-glibc native dependency not built above. Copy it
# as well; the package does not depend on distro developer libraries.
for name in libz.so.1; do
    library="$(ldconfig -p | awk -v name="$name" '$1 == name && /x86-64/ && !seen {print $NF; seen=1}')"
    [ -z "$library" ] || cp -L "$library" "$R/lib/$name"
done
for binary in "$R/bin/"*; do patchelf --set-rpath '$ORIGIN/../lib' "$binary"; done
find "$R/lib" -type f -name '*.so*' -print0 | while IFS= read -r -d '' library; do
    patchelf --set-rpath '$ORIGIN' "$library"
done
cp "$HERE/gui.py" "$HERE/iphone2gkit" "$HERE/setup-usb.sh" "$HERE/README.md" "$BUNDLE/"
cp "$ROOT/README.md" "$BUNDLE/ENGINE-README.md"
cp "$ROOT/LICENSE" "$ROOT/THIRD_PARTY_NOTICES.md" "$BUNDLE/"
chmod 755 "$BUNDLE/iphone2gkit" "$BUNDLE/setup-usb.sh"
# Corresponding sources and patches for the bundled LGPL/GPL programs.
for source in "$V/src/"*.tar.*; do
    [ "$(basename "$source")" = "$PYFULL" ] || cp "$source" "$R/ThirdPartySources/"
done
cp -a "$V/python-notices/licenses" "$R/ThirdPartyLicenses/python-standalone"
cp "$V/python-notices/PYTHON.json" "$R/ThirdPartyLicenses/python-standalone/BUILD-RECORD.json"
cp "$V/src/tk-9.0.4-license.terms" "$R/ThirdPartyLicenses/python-standalone/LICENSE.tk.txt"
cp "$ROOT/macos/libirecovery-iokit.patch" "$ROOT/macos/restore-build.patch" \
    "$ROOT/macos/recovery-console.c" "$HERE/build-linux.sh" "$HERE/usbmuxd-plist.patch" "$R/ThirdPartySources/"
for package in "$V/build/"*/; do
    directory="$R/ThirdPartyLicenses/$(basename "$package")"
    for license in "$package"COPYING* "$package"LICENSE* "$package"LICENCE* "$package"AUTHORS*; do
        if [ -f "$license" ]; then mkdir -p "$directory"; cp "$license" "$directory/"; fi
    done
done
[ ! -f /usr/share/doc/zlib1g/copyright ] || cp /usr/share/doc/zlib1g/copyright "$R/ThirdPartyLicenses/zlib-copyright"
cat > "$R/ThirdPartyLicenses/SOURCES.txt" <<'EOF'
The bundled native sources, build recipe, patches and fixed SHA-256 checksums
are provided under ../ThirdPartySources. Python is python-build-standalone
3.12.15 (20261003), including Tcl/Tk 9.0.4; runtime component licenses and
build metadata are in ThirdPartyLicenses/python-standalone/.
libusb 1.0.29, libplist 2.7.0, libimobiledevice-glue 1.3.2, libirecovery 1.3.1,
libusbmuxd 2.1.1, libimobiledevice 1.4.0, libtatsu 1.0.5, usbmuxd 1.1.1,
OpenSSL 3.5.4, curl 8.10.1, libzip 1.11.4, OpenSSH 9.9p2, xpwn 20c32e5,
idevicerestore c25aefd (experimental legacy/protocol7 branch with local patches).
The package uses the host GNU C library, kernel, desktop X11/font libraries
and CA trust store. It never replaces or stops an installed USB daemon.
EOF

export LD_LIBRARY_PATH="$R/lib:$R/python/lib"
"$R/python/bin/python3" -B -c 'import tkinter, hashlib, ssl, zipfile; print("private Python/Tk imports OK")'
for binary in irecovery idevicerestore ideviceinfo idevice_id usbmuxd ssh-keygen; do
    LD_LIBRARY_PATH="$R/lib" "$R/bin/$binary" --help > /dev/null 2>&1 || {
        # ssh-keygen uses -? and exits 1 when showing usage.
        [ "$binary" = ssh-keygen ] || { echo "Bundled $binary cannot run." >&2; exit 1; }
    }
done
if command -v xvfb-run >/dev/null; then
    xvfb-run -a "$BUNDLE/iphone2gkit" --gui-smoke-test
else
    echo 'xvfb-run required to test the GUI before packaging.' >&2; exit 1
fi
# Reject any remaining missing dynamic dependency. X11/font libraries are OS
# desktop dependencies, documented in README; native binaries need no packages.
for binary in "$R/bin/"*; do
    if ldd "$binary" | grep -q 'not found'; then ldd "$binary" >&2; exit 1; fi
done
ARCHIVE="$DIST/iPhone2Gkit-linux-x86_64.tar.gz"
tar -C "$DIST" -czf "$ARCHIVE.part" iPhone2Gkit
mv "$ARCHIVE.part" "$ARCHIVE"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
echo "Built $ARCHIVE"
