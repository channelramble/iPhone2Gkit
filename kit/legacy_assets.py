"""Verified, host-only installation of the historical 1.0 setup bundle."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import urllib.request
import uuid
import zipfile

from . import assets, platforms

MANIFEST = Path(__file__).with_name("resources") / "legacy-kit-manifest.json"
ARCHIVE_ROOT = "iPhone2Gkit-setup/"


def manifest():
    try:
        record = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if (record["schema"] != 1 or not isinstance(record["files"], dict)
                or not 0 < len(record["files"]) < 512
                or not 0 < record["size"] < 64 * 1024**2
                or not 0 < record["uncompressed_size"] < 128 * 1024**2
                or not re.fullmatch(r"[a-zA-Z0-9._-]+", record["filename"])
                or not re.fullmatch(r"[a-zA-Z0-9._-]+", record["version"])
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
            raise ValueError("Invalid setup manifest header")
        for name, entry in record["files"].items():
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or ".." in parts
                    or "\\" in name or ":" in name or "\0" in name
                    or str(PurePosixPath(name)) != name
                    or not 0 <= entry["size"] < 32 * 1024**2
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                raise ValueError("Invalid setup file record")
        if record["uncompressed_size"] != sum(e["size"] for e in record["files"].values()):
            raise ValueError("Invalid setup expanded size")
        return record
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise assets.AssetError("The app's setup manifest is damaged. Download the app again.") from exc


def _verified_tree(root, record):
    root = Path(root)
    for name, entry in record["files"].items():
        path = root / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
            return False
        if not assets._valid(path, entry):
            return False
    return True


def _download(record, progress):
    cache = platforms.cache_dir() / "setup"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / record["filename"]
    if assets._valid(destination, record):
        progress("kit", record["size"], record["size"])
        return destination
    if shutil.disk_usage(cache).free < record["size"] + record["uncompressed_size"] + 64 * 1024**2:
        raise assets.AssetError("Not enough disk space for setup files.")
    if not record["url"].startswith("https://"):
        raise assets.AssetError("Setup downloads require HTTPS.")
    fd, temporary = tempfile.mkstemp(prefix=".setup-", suffix=".part", dir=cache)
    try:
        digest, count, started = hashlib.sha256(), 0, time.monotonic()
        request = urllib.request.Request(record["url"], headers={"User-Agent": "iPhone2Gkit/2.2"})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=30) as response:
            if not response.geturl().startswith("https://"):
                raise assets.AssetError("Setup download redirected to an insecure URL.")
            while True:
                if time.monotonic() - started > 1800:
                    raise assets.AssetError("Setup download timed out.")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > record["size"]:
                    raise assets.AssetError("Setup download exceeded its expected size.")
                digest.update(chunk)
                out.write(chunk)
                progress("kit", count, record["size"])
        if count != record["size"] or digest.hexdigest() != record["sha256"]:
            raise assets.AssetError("Setup checksum or size mismatch. The download was discarded.")
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        return destination
    except (OSError, ValueError) as exc:
        raise assets.AssetError("Could not download setup files: " + str(exc)) from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _unpack(archive, record, destination):
    """Only extract the exact manifest members; never trust ZIP paths or modes."""
    expected = {ARCHIVE_ROOT + name: entry for name, entry in record["files"].items()}
    try:
        with zipfile.ZipFile(archive) as source:
            infos = source.infolist()
            if len(infos) != len(expected) or {e.filename for e in infos} != set(expected):
                raise assets.AssetError("The setup archive contains unexpected or duplicate files.")
            for entry in infos:
                spec = expected[entry.filename]
                mode = entry.external_attr >> 16
                if (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or entry.file_size != spec["size"]:
                    raise assets.AssetError("The setup archive contains an invalid file: " + entry.filename)
                path = destination / entry.filename[len(ARCHIVE_ROOT):]
                path.parent.mkdir(parents=True, exist_ok=True)
                count, digest = 0, hashlib.sha256()
                with source.open(entry) as incoming, path.open("xb") as out:
                    while True:
                        chunk = incoming.read(1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > spec["size"]:
                            raise assets.AssetError("A setup file exceeded its expected size.")
                        digest.update(chunk)
                        out.write(chunk)
                if count != spec["size"] or digest.hexdigest() != spec["sha256"]:
                    raise assets.AssetError("Setup file checksum mismatch: " + entry.filename)
                path.chmod(0o644)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise assets.AssetError("Could not unpack setup files: " + str(exc)) from exc


def fetch_kit(progress=lambda *args: None):
    """Atomically publish a verified kit; keep any installed version on failure."""
    record = manifest()
    data = platforms.data_dir()
    data.mkdir(parents=True, exist_ok=True)
    active = data / "kit-assets"
    with (data / "setup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise assets.AssetError("Setup is already running. Let it finish first.") from exc
        if not _verified_tree(active, record):
            if active.exists() and not active.is_symlink():
                raise assets.AssetError("An existing kit occupies the setup folder. Move it aside before downloading a replacement.")
            archive = _download(record, progress)
            versions = data / "legacy-kits"
            versions.mkdir(exist_ok=True)
            if shutil.disk_usage(versions).free < record["uncompressed_size"] + 32 * 1024**2:
                raise assets.AssetError("Not enough disk space to unpack setup files.")
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=versions))
            link = data / (".kit-link-" + uuid.uuid4().hex)
            published = None
            try:
                _unpack(archive, record, staging)
                # Payload CRCs, paths and translated post-steps are checked before publication.
                from . import payloads
                for payload in payloads.load_catalog(staging / "ios1-apps"):
                    payload.check()
                published = versions / (record["version"] + "-" + uuid.uuid4().hex)
                os.rename(staging, published)
                os.symlink(os.path.relpath(published, data), link)
                os.replace(link, active)
                published = None  # The active pointer now owns this verified version.
            finally:
                if link.is_symlink():
                    link.unlink()
                if staging.exists():
                    shutil.rmtree(staging)
                if published is not None:
                    shutil.rmtree(published)
        progress("kit", record["size"], record["size"])
        assets.import_plutil(active)
    from . import payloads
    return {"kit": str(active), "resource_version": record["version"],
            "apps": len(payloads.load_catalog(active / "ios1-apps")), "verified": True}


def setup(progress=lambda *args: None):
    """Prepare 1.0 app actions on the host. This does not start a restore."""
    result = fetch_kit(progress)
    assets.fetch_firmware("1.0", progress)
    # A kernel may have been downloaded after ramdisk.py was first imported.
    from . import ramdisk
    problems = ramdisk.check_assets(result["kit"])
    if problems:
        raise assets.AssetError("Setup is incomplete: " + "; ".join(problems))
    result["ready"] = True
    return result
