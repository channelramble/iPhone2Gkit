#!/bin/sh
# Build the restore tools carried by iPhone2Gkit.app. Build tools may come from
# Homebrew; shipped programs link only macOS libraries. Run fetch-deps.sh first
# so the tested iOS 1 USB implementation is available (never rebuild it here).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="$HERE/vendor/build/prefix"
R="$HERE/vendor/restore"
P="$R/prefix"
COMMIT=c25aefd49b3769c2907875e15566d433d59bd979
export MACOSX_DEPLOYMENT_TARGET=13.0
export CFLAGS="-O2 -mmacosx-version-min=13.0"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-mmacosx-version-min=13.0 -framework IOKit -framework CoreFoundation"
[ "$(uname -m)" = arm64 ] || { echo 'Restore tools currently require an Apple Silicon build host.' >&2; exit 1; }
[ -f "$BASE/lib/libirecovery-1.0.a" ] || { echo 'Run macos/fetch-deps.sh first.' >&2; exit 1; }
for tool in pkg-config cmake autoreconf make clang perl; do
    command -v "$tool" >/dev/null || { echo "Missing build tool: $tool (build tools are not app dependencies)." >&2; exit 1; }
done
mkdir -p "$R/src" "$R/bin" "$R/licenses"

fetch() { # URL filename SHA-256
    archive="$R/src/$2"
    if [ ! -f "$archive" ] || [ "$(shasum -a 256 "$archive" | cut -d' ' -f1)" != "$3" ]; then
        echo "Downloading $2"
        curl -fsSL --retry 3 -o "$archive.part" "$1"
        [ "$(shasum -a 256 "$archive.part" | cut -d' ' -f1)" = "$3" ] || { rm -f "$archive.part"; echo "Checksum mismatch: $2" >&2; exit 1; }
        mv "$archive.part" "$archive"
    fi
}
fetch https://github.com/libimobiledevice/libusbmuxd/releases/download/2.1.1/libusbmuxd-2.1.1.tar.bz2 \
    libusbmuxd-2.1.1.tar.bz2 5546f1aba1c3d1812c2b47d976312d00547d1044b84b6a461323c621f396efce
fetch https://github.com/libimobiledevice/libimobiledevice/releases/download/1.4.0/libimobiledevice-1.4.0.tar.bz2 \
    libimobiledevice-1.4.0.tar.bz2 23cc0077e221c7d991bd0eb02150a0d49199bcca1ddf059edccee9ffd914939d
fetch https://github.com/libimobiledevice/libtatsu/releases/download/1.0.5/libtatsu-1.0.5.tar.bz2 \
    libtatsu-1.0.5.tar.bz2 536fa228b14f156258e801a7f4d25a3a9dd91bb936bf6344e23171403c57e440
fetch https://github.com/openssl/openssl/releases/download/openssl-3.5.4/openssl-3.5.4.tar.gz \
    openssl-3.5.4.tar.gz 967311f84955316969bdb1d8d4b983718ef42338639c621ec4c34fddef355e99
fetch https://github.com/nih-at/libzip/releases/download/v1.11.4/libzip-1.11.4.tar.xz \
    libzip-1.11.4.tar.xz 8a247f57d1e3e6f6d11413b12a6f28a9d388de110adc0ec608d893180ed7097b
fetch "https://codeload.github.com/tihmstar/idevicerestore/tar.gz/$COMMIT" \
    idevicerestore-c25aefd.tar.gz 266e1f444d97fcdd78227eba8c4b252803ef3680d3ad6c1a3e5f286fce96ac29

PATCH_SHA=none
[ ! -f "$HERE/restore-build.patch" ] || PATCH_SHA="$(shasum -a 256 "$HERE/restore-build.patch" | cut -d' ' -f1)"
BUILD_KEY="$(shasum -a 256 "$0" | cut -d' ' -f1):$PATCH_SHA:$(shasum -a 256 "$BASE/lib/libirecovery-1.0.a" | cut -d' ' -f1)"
if [ "$(cat "$R/build.sha256" 2>/dev/null || true)" != "$BUILD_KEY" ] || [ ! -x "$R/bin/idevicerestore" ] || [ ! -x "$R/bin/ideviceinfo" ] || [ ! -x "$R/bin/idevice_id" ]; then
    rm -rf "$R/build" "$P"
    mkdir -p "$R/build" "$P/lib/pkgconfig"
    for archive in "$R/src/"*.tar.*; do tar -xf "$archive" -C "$R/build"; done
    export PKG_CONFIG_PATH="$P/lib/pkgconfig:$BASE/lib/pkgconfig"
    export PKG_CONFIG_LIBDIR="$PKG_CONFIG_PATH"
    # Static transitive dependencies must be present on every final link line.
    export PKG_CONFIG="$(command -v pkg-config) --static"
    SDK="$(xcrun --show-sdk-path)"
    cat > "$P/lib/pkgconfig/zlib.pc" <<EOF
Name: zlib
Description: macOS system compression library
Version: 1.2.11
Libs: -lz
Cflags: -I$SDK/usr/include
EOF
    CURL_VERSION="$(sed -n 's/^#define LIBCURL_VERSION "\([^"]*\)"/\1/p' "$SDK/usr/include/curl/curlver.h")"
    cat > "$P/lib/pkgconfig/libcurl.pc" <<EOF
Name: libcurl
Description: macOS system URL transfer library
Version: $CURL_VERSION
Libs: -lcurl
Cflags: -I$SDK/usr/include
EOF
    (
        cd "$R/build/openssl-3.5.4"
        ./Configure darwin64-arm64-cc no-shared no-module no-tests \
            --prefix="$P" --openssldir=/nonexistent/iphone2gkit-openssl "$CFLAGS" || exit 1
        make -j8 || exit 1
        make install_sw || exit 1
    ) > "$R/build/openssl.log" 2>&1 || { tail -40 "$R/build/openssl.log" >&2; exit 1; }
    for pkg in libusbmuxd-2.1.1 libtatsu-1.0.5 libimobiledevice-1.4.0; do
        echo "Building $pkg"
        (
            cd "$R/build/$pkg"
            extra=""
            [ "$pkg" != libimobiledevice-1.4.0 ] || extra="--without-cython --without-readline --without-gnutls --without-mbedtls --with-openssl --disable-wireless-pairing"
            ./configure --prefix="$P" --disable-shared --enable-static $extra || exit 1
            make -j8 || exit 1
            make install || exit 1
        ) > "$R/build/$pkg.log" 2>&1 || { tail -40 "$R/build/$pkg.log" >&2; exit 1; }
    done
    echo 'Building libzip'
    cmake -S "$R/build/libzip-1.11.4" -B "$R/build/libzip-cmake" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$P" \
        -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DBUILD_SHARED_LIBS=OFF -DBUILD_TOOLS=OFF -DBUILD_REGRESS=OFF \
        -DBUILD_OSSFUZZ=OFF -DBUILD_EXAMPLES=OFF -DBUILD_DOC=OFF \
        -DENABLE_COMMONCRYPTO=OFF -DENABLE_GNUTLS=OFF -DENABLE_MBEDTLS=OFF \
        -DENABLE_OPENSSL=OFF -DENABLE_BZIP2=OFF -DENABLE_LZMA=OFF -DENABLE_ZSTD=OFF \
        > "$R/build/libzip.log" 2>&1
    cmake --build "$R/build/libzip-cmake" -j8 >> "$R/build/libzip.log" 2>&1
    cmake --install "$R/build/libzip-cmake" >> "$R/build/libzip.log" 2>&1
    echo "Building idevicerestore $COMMIT"
    (
        cd "$R/build/idevicerestore-$COMMIT"
        printf '%s\n' "iphone2gkit-$COMMIT" > .tarball-version
        if [ -f "$HERE/restore-build.patch" ]; then patch -p1 < "$HERE/restore-build.patch" || exit 1; fi
        autoreconf -fi || exit 1
        ./configure --prefix="$P" --disable-shared --enable-static --with-openssl || exit 1
        make -j8 || exit 1
        make install || exit 1
    ) > "$R/build/idevicerestore.log" 2>&1 || { tail -50 "$R/build/idevicerestore.log" >&2; exit 1; }
    cp "$P/bin/idevicerestore" "$P/bin/ideviceinfo" "$P/bin/idevice_id" "$R/bin/"
    # License texts and exact source archives stay available for redistribution.
    for pkg in libusbmuxd-2.1.1 libimobiledevice-1.4.0 libtatsu-1.0.5 openssl-3.5.4 libzip-1.11.4 "idevicerestore-$COMMIT"; do
        mkdir -p "$R/licenses/$pkg"
        for f in "$R/build/$pkg/"COPYING* "$R/build/$pkg/"LICENSE* "$R/build/$pkg/"AUTHORS*; do
            [ ! -f "$f" ] || cp "$f" "$R/licenses/$pkg/"
        done
    done
    for pkg in libplist-2.7.0 libimobiledevice-glue-1.3.2 libirecovery-1.3.1; do
        mkdir -p "$R/licenses/$pkg"
        for f in "$HERE/vendor/build/$pkg/"COPYING* "$HERE/vendor/build/$pkg/"LICENSE* "$HERE/vendor/build/$pkg/"AUTHORS*; do
            [ ! -f "$f" ] || cp "$f" "$R/licenses/$pkg/"
        done
    done
    cat > "$R/licenses/SOURCES.txt" <<EOF
Restore executable: https://github.com/tihmstar/idevicerestore
Pinned commit: $COMMIT (experimental iPhone OS 1.0 restore protocol 7)
Source archive SHA-256: 266e1f444d97fcdd78227eba8c4b252803ef3680d3ad6c1a3e5f286fce96ac29
Source build recipe and local patches: macos/fetch-restore-deps.sh, macos/restore-build.patch
Libraries: libimobiledevice 1.4.0, libusbmuxd 2.1.1, libtatsu 1.0.5, OpenSSL 3.5.4,
libzip 1.11.4, libplist 2.7.0, libimobiledevice-glue 1.3.2, libirecovery 1.3.1.
The libirecovery implementation includes the iPhone2Gkit iOS 1 USB patches.
Every downloaded source SHA-256 is fixed in the build recipe.
macOS system libcurl, zlib, IOKit, CoreFoundation, and libSystem are dynamically linked.
The app does not use Homebrew libraries at runtime.
For full corresponding source, the distributable source package includes this recipe,
the local patches, and upstream URLs/checksums. Source archives are cached under
macos/vendor/restore/src when building.
EOF
    printf '%s\n' "$BUILD_KEY" > "$R/build.sha256"
fi

for program in idevicerestore ideviceinfo idevice_id; do
    links="$(otool -L "$R/bin/$program" | sed '1d' | awk '{print $1}')"
    if printf '%s\n' "$links" | grep -v -E '^/(usr/lib|System)/' >/dev/null; then
        echo "$program links non-system libraries: $links" >&2; exit 1
    fi
    [ "$(lipo -archs "$R/bin/$program")" = arm64 ] || { echo "Unexpected architecture: $program" >&2; exit 1; }
    minos="$(otool -l "$R/bin/$program" | awk '/LC_BUILD_VERSION/{inside=1} inside && /minos/{print $2;exit}')"
    [ "$minos" = 13.0 ] || { echo "Unexpected minimum macOS: $program $minos" >&2; exit 1; }
    "$R/bin/$program" --help >/dev/null
done
echo "Restore tools ready in $R/bin (arm64, macOS 13, system libraries only)."
