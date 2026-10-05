#!/bin/sh
# `ios1kit` terminal command shipped inside iOS1Kit.app (Contents/Resources/bin/ios1kit).
# Uses only the app's own Python, engine, irecovery and kit; works through a symlink
# (e.g. /usr/local/bin/ios1kit created by the app's "Install Command-Line Tool…").
SELF="$0"
while [ -L "$SELF" ]; do
	L="$(readlink "$SELF")"
	case "$L" in
		/*) SELF="$L" ;;
		*) SELF="$(dirname "$SELF")/$L" ;;
	esac
done
RES="$(cd "$(dirname "$SELF")/.." && pwd)"
PATH="$RES/bin:/usr/bin:/bin:/usr/sbin:/sbin"
IOS1KIT_BUILD="${IOS1KIT_BUILD:-$HOME/Library/Caches/ios1kit/build}"
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
export PATH IOS1KIT_BUILD PYTHONDONTWRITEBYTECODE PYTHONUNBUFFERED
unset PYTHONHOME PYTHONPATH
exec "$RES/python/bin/python3" "$RES/engine/ios1kit" "$@"
