#!/bin/sh
# Explicit, optional administrator setup. Never starts/stops a daemon.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run with sudo: sh setup-usb.sh' >&2; exit 1; }
command -v udevadm >/dev/null || { echo 'This setup requires a Linux desktop with udev.' >&2; exit 1; }
RULE=/etc/udev/rules.d/70-iphone2gkit.rules
TEMP="$(mktemp /etc/udev/rules.d/.iphone2gkit.XXXXXX)"
trap 'rm -f "$TEMP"' EXIT HUP INT TERM
cat > "$TEMP" <<'EOF'
# Apple mobile USB devices only; access granted to the active desktop seat.
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", ATTR{idProduct}=="12[0-9a-f][0-9a-f]", TAG+="uaccess"
EOF
chmod 644 "$TEMP"
mv "$TEMP" "$RULE"
udevadm control --reload-rules
echo "Installed $RULE. Unplug and reconnect the phone. No daemon was started or stopped."
