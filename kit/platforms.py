"""Small host-platform adapters; USB enumeration never opens a phone service."""
import os
from pathlib import Path
import re
import subprocess
import sys


class PlatformError(RuntimeError):
    pass


def is_linux():
    return sys.platform.startswith("linux")


def supported():
    return sys.platform == "darwin" or is_linux()


def cache_dir():
    if is_linux():
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        if not base.is_absolute():
            base = Path.home() / ".cache"
        return base / "iPhone2Gkit"
    return Path.home() / "Library/Caches/ios1kit"


def data_dir():
    if is_linux():
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        if not base.is_absolute():
            base = Path.home() / ".local/share"
        return base / "iPhone2Gkit"
    return Path.home() / "Library/Application Support/iPhone2Gkit"


def build_tools():
    if is_linux():
        return ("hfsplus", "ssh-keygen")
    if sys.platform == "darwin":
        return ("hdiutil", "ditto", "ssh-keygen")
    raise PlatformError("Supported hosts are macOS and Linux.")


def parse_ioreg(text):
    devices = []
    for block in re.split(r"\+-o ", text):
        vid = re.search(r'"idVendor" = (\d+)', block)
        pid = re.search(r'"idProduct" = (\d+)', block)
        if not (vid and pid and int(vid.group(1)) == 0x05AC):
            continue
        product = int(pid.group(1))
        if 0x1200 <= product <= 0x12FF:
            serial = re.search(r'"USB Serial Number" = "([^"\n]*)"', block)
            devices.append({"pid": product, "usb_serial": serial.group(1) if serial else None})
    return devices


def linux_usb_devices(root=Path("/sys/bus/usb/devices")):
    """Use kernel sysfs descriptors, including devices without user USB access.

    Interface entries lack idVendor and are ignored. Other Apple mobile devices
    are included so a second, newer phone can never be silently overlooked.
    """
    devices = []
    try:
        entries = list(Path(root).iterdir())
        if len(entries) > 4096:
            raise PlatformError("Unexpectedly large Linux USB device inventory.")
        for device in sorted(entries):
            vendor_path = device / "idVendor"
            if not vendor_path.is_file():
                continue
            try:
                vendor = vendor_path.read_text().strip()
                product = (device / "idProduct").read_text().strip()
            except FileNotFoundError:
                continue  # Unplugged between directory listing and descriptor read.
            if not re.fullmatch(r"[0-9a-fA-F]{4}", vendor) or not re.fullmatch(r"[0-9a-fA-F]{4}", product):
                raise PlatformError("Invalid Linux USB descriptor in " + device.name)
            pid = int(product, 16)
            if int(vendor, 16) != 0x05AC or not 0x1200 <= pid <= 0x12FF:
                continue
            try:
                serial = (device / "serial").read_text().strip() or None
            except FileNotFoundError:
                serial = None
            if serial is not None and (len(serial) > 128 or "\0" in serial):
                raise PlatformError("Invalid Linux USB serial descriptor.")
            devices.append({"pid": pid, "usb_serial": serial})
    except OSError as e:
        raise PlatformError("Cannot enumerate Linux USB devices: " + str(e)) from e
    return devices


def usb_devices():
    if is_linux():
        return linux_usb_devices()
    if sys.platform != "darwin":
        raise PlatformError("USB discovery is supported on macOS and Linux.")
    try:
        result = subprocess.run(["/usr/sbin/ioreg", "-p", "IOUSB", "-l", "-w0"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise PlatformError("Cannot inspect USB devices: " + str(e)) from e
    if result.returncode:
        raise PlatformError("Cannot inspect USB devices with ioreg.")
    return parse_ioreg(result.stdout)
