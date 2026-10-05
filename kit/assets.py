"""Download verified stock firmware locally; never contact or reboot a phone."""
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import urllib.request
import zipfile

from . import firmware, platforms


class AssetError(RuntimeError):
    pass


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid(path, record):
    return path.is_file() and path.stat().st_size == record["size"] and _digest(path) == record["sha256"]


def _save_resource(name, data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise AssetError("Resource checksum mismatch: " + name)
    resources = platforms.data_dir() / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".resource-", dir=resources)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, resources / name)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def prepare_kernel(path):
    """Extract just the expected 1.0 kernel from the checksum-verified IPSW."""
    from . import ramdisk
    with zipfile.ZipFile(path) as archive:
        component = "kernelcache.restore.release.s5l8900xrb"
        entry = archive.getinfo(component)
        if not 0 < entry.file_size <= ramdisk.KERNEL_SLOT:
            raise AssetError("Invalid stock 1.0 kernel component size.")
        data = archive.read(component)
    _save_resource("kernelcache-1.0.dat", data, ramdisk.STOCK_KERNEL_SHA)


def import_plutil(kit):
    """Use the existing kit's genuine 2007 converter without redistributing it."""
    from . import ramdisk
    path = Path(kit) / "iLiberty-portable/iLiberty/BasePack.zip"
    try:
        with zipfile.ZipFile(path) as archive:
            member = archive.getinfo("bin/plutil")
            if member.file_size > 1024 * 1024:
                raise AssetError("Unexpected plist converter size.")
            data = archive.read(member)
        _save_resource("plutil-ios1", data, ramdisk.PLUTIL_SHA)
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        raise AssetError("Cannot import the plist converter from the kit: " + str(e)) from e
    return str(platforms.data_dir() / "resources/plutil-ios1")


def fetch_firmware(version, progress=lambda *args: None):
    record = next((r for r in firmware.firmwares() if r["version"] == version), None)
    if record is None:
        raise AssetError("Unknown stock firmware version.")
    target = platforms.data_dir() / "firmware"
    target.mkdir(parents=True, exist_ok=True)
    dest = target / record["filename"]
    # Hold a per-version lock so two GUI/CLI jobs cannot replace one download.
    with (target / (record["filename"] + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise AssetError("This firmware is already being downloaded.") from e
        if not _valid(dest, record):
            if shutil.disk_usage(target).free < record["size"] + 64 * 1024**2:
                raise AssetError("Not enough disk space for the stock firmware download.")
            if not record["url"].startswith("https://"):
                raise AssetError("Firmware downloads require HTTPS.")
            fd, part = tempfile.mkstemp(prefix=".firmware-", suffix=".part", dir=target)
            try:
                started, count, digest = time.monotonic(), 0, hashlib.sha256()
                request = urllib.request.Request(record["url"], headers={"User-Agent": "iPhone2Gkit/2.1.0"})
                with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=30) as response:
                    if not response.geturl().startswith("https://"):
                        raise AssetError("Firmware download redirected to an insecure URL.")
                    while True:
                        if time.monotonic() - started > 3600:
                            raise AssetError("Firmware download timed out.")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > record["size"]:
                            raise AssetError("Downloaded firmware exceeds its expected size.")
                        digest.update(chunk)
                        out.write(chunk)
                        progress(version, count, record["size"])
                if count != record["size"] or digest.hexdigest() != record["sha256"]:
                    raise AssetError("Firmware checksum or size mismatch. The download was discarded.")
                os.chmod(part, 0o644)
                os.replace(part, dest)
            except (OSError, ValueError) as e:
                raise AssetError("Firmware download failed: " + str(e)) from e
            finally:
                if os.path.exists(part):
                    os.unlink(part)
        if version == "1.0":
            prepare_kernel(dest)
    return {"version": version, "path": str(dest), "sha256": record["sha256"], "verified": True}
