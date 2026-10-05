"""Native iPhone1,1 restore planning and supervised execution.

Nothing in inspect/info/prepare talks to a writable phone service. Only execute
starts idevicerestore, after the caller has confirmed data erasure. OS 1.x uses
tihmstar's protocol-7/legacy branch and is explicitly experimental.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import zipfile

from . import usb_recovery as U
from . import platforms as PL

ROOT = Path(__file__).resolve().parent.parent
DFU_HELP = """Connect only the iPhone 2G. Close Finder/iTunes device windows.
For DFU: hold Power + Home for 10 seconds, release Power, then keep holding
Home for another 10–15 seconds. The screen stays black. Recovery mode shows
the cable/iTunes picture. The app must identify iPhone1,1 before proceeding."""
EXPERIMENTAL_WARNING = (
    "Native iPhone OS 1.x restores are experimental and have not been tested "
    "on a phone with this build. The bundled backend implements the legacy "
    "protocol, but a failure may leave a partially restored phone in DFU/recovery. "
    "Keep verified stock 3.1.3 downloaded locally for recovery; it is not "
    "bundled in the public package."
)
CUSTOM_WARNING = (
    "A custom IPSW can change the bootloader, radio firmware, and activation. "
    "Use a trusted iPhone1,1 IPSW. The app includes the documented Pwnage 2.0 "
    "DFU preparation for the 2G; start in initial DFU/WTF (black screen). "
    "That preparation does not establish compatibility of every custom IPSW."
)


class RestoreError(RuntimeError):
    pass


def cache_dir():
    return Path(os.environ.get("IPHONE2GKIT_BUILD", os.environ.get(
        "IOS1KIT_BUILD", str(PL.cache_dir() / "build"))))


def binary(name):
    # Prefer app-owned programs; development builds use the isolated vendor tree.
    for p in (ROOT.parent / "bin" / name,
              ROOT / "linux/vendor/bin" / name,
              ROOT / "macos/vendor/restore/bin" / name):
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    raise RestoreError("Bundled %s is missing. Re-download iPhone2Gkit.app or rebuild it." % name)


def run_query(argv, timeout=15):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, env=clean_environment())
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return -1, b""


def clean_environment():
    env = os.environ.copy()
    for name in ("USBMUXD_SOCKET_ADDRESS", "USBMUXD_PORT", "LIBIRECOVERY_IOS1_OVERWRITE_LOADADDR",
                 "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD"):
        env.pop(name, None)
    return env


def catalog():
    from . import firmware
    return firmware.firmwares()


def firmware_path(record, kit=None):
    candidates = [ROOT.parent / "firmware" / record["filename"],
                  PL.data_dir() / "firmware" / record["filename"],
                  ROOT / "linux/vendor/firmware" / record["filename"],
                  ROOT / "macos/vendor/firmware" / record["filename"]]
    if kit:
        candidates += [Path(kit) / "downloads/firmware" / record["filename"],
                       Path(kit) / "firmware" / record["filename"]]
    return next((p for p in candidates if p.is_file()), None)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_path(value):
    if not isinstance(value, str) or not value or len(value) > 240:
        raise RestoreError("Invalid component path in IPSW.")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or "\\" in value or "\0" in value:
        raise RestoreError("Unsafe component path in IPSW: %r" % value)
    return value


def inspect_ipsw(path, check_crc=True):
    """Validate device identity and referenced components before any phone action."""
    path = Path(path)
    if not path.is_file():
        raise RestoreError("IPSW not found: %s" % path)
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            names = {i.filename for i in entries}
            if len(names) != len(entries):
                raise RestoreError("IPSW contains duplicate file names.")
            if len(entries) > 4096 or sum(i.file_size for i in entries) > 8 * 1024**3:
                raise RestoreError("IPSW is too large or has too many entries.")
            for i in entries:
                _safe_path(i.filename)
                if stat.S_ISLNK(i.external_attr >> 16):
                    raise RestoreError("IPSW contains a symbolic link.")
            if "Restore.plist" not in names:
                raise RestoreError("A 2G IPSW must contain Restore.plist.")
            if z.getinfo("Restore.plist").file_size > 4 * 1024**2:
                raise RestoreError("Restore.plist is too large.")
            restore = plistlib.loads(z.read("Restore.plist"))
            if not isinstance(restore, dict) or restore.get("ProductType") != "iPhone1,1":
                raise RestoreError("This IPSW is not for the original iPhone (iPhone1,1).")
            supported = restore.get("SupportedProductTypes", ["iPhone1,1"])
            if supported != ["iPhone1,1"]:
                raise RestoreError("Only an iPhone1,1-only IPSW is accepted.")
            maps = restore.get("DeviceMap", [])
            if not isinstance(maps, list) or not any(
                    isinstance(m, dict) and m.get("BoardConfig") == "m68ap" for m in maps):
                raise RestoreError("IPSW has no m68ap board configuration.")
            if any(not isinstance(m, dict) or m.get("BoardConfig") not in
                   ("m68ap", "s5l8900xall") for m in maps):
                raise RestoreError("IPSW contains a different board configuration.")
            version, build = restore.get("ProductVersion"), restore.get("ProductBuildVersion")
            if not isinstance(version, str) or not re.fullmatch(r"[123]\.\d+(?:\.\d+)?", version):
                raise RestoreError("Unsupported or missing 2G firmware version.")
            if not isinstance(build, str) or not re.fullmatch(r"[A-Za-z0-9]{1,24}", build):
                raise RestoreError("Invalid firmware build identifier.")
            required = set()
            for key in ("RestoreKernelCaches", "RestoreRamDisks", "SystemRestoreImages"):
                group = restore.get(key)
                if not isinstance(group, dict) or not group:
                    raise RestoreError("IPSW is missing %s." % key)
                required.update(_safe_path(v) for v in group.values())
            manifest_name = "Firmware/all_flash/all_flash.m68ap.production/manifest"
            required.add(manifest_name)
            if manifest_name not in names or z.getinfo(manifest_name).file_size > 4096:
                raise RestoreError("IPSW has no valid m68ap NOR manifest.")
            parts = z.read(manifest_name).decode("ascii").split()
            if not parts or len(parts) > 16:
                raise RestoreError("Invalid NOR component list.")
            for part in parts:
                if "/" in part or len(part) > 100:
                    raise RestoreError("Invalid NOR component name.")
                required.add(_safe_path(str(PurePosixPath(manifest_name).parent / part)))
            for m in ("BuildManifest.plist", "BuildManifesto.plist"):
                if m in names:
                    if z.getinfo(m).file_size > 4 * 1024**2:
                        raise RestoreError("Build manifest is too large.")
                    bm = plistlib.loads(z.read(m))
                    if not isinstance(bm, dict) or bm.get("SupportedProductTypes") != ["iPhone1,1"]:
                        raise RestoreError("Build manifest does not exclusively target iPhone1,1.")
                    for identity in bm.get("BuildIdentities", []):
                        if identity.get("Info", {}).get("DeviceClass") != "m68ap":
                            raise RestoreError("Build manifest targets a different device.")
                        for component in identity.get("Manifest", {}).values():
                            v = component.get("Info", {}).get("Path")
                            if v:
                                required.add(_safe_path(v))
            missing = sorted(required - names)
            if missing:
                raise RestoreError("IPSW is missing components: " + ", ".join(missing[:4]))
            if check_crc:
                bad = z.testzip()
                if bad:
                    raise RestoreError("IPSW checksum error in " + bad)
    except (OSError, zipfile.BadZipFile, ValueError, TypeError, AttributeError,
            KeyError, UnicodeError, RuntimeError) as e:
        if isinstance(e, RestoreError):
            raise
        raise RestoreError("Cannot read IPSW: %s" % e) from e
    return {"path": str(path.resolve()), "version": version, "build": build,
            "product_type": "iPhone1,1", "experimental": version.startswith("1."),
            "sha256": sha256(path), "size": path.stat().st_size}


def mobile_usb_devices(text):
    """All Apple mobile USB devices, including newer phones ignored by the old UI."""
    out = []
    for block in re.split(r"\+-o ", text):
        vid = re.search(r'"idVendor" = (\d+)', block)
        pid = re.search(r'"idProduct" = (\d+)', block)
        if vid and pid and int(vid.group(1)) == 0x05ac:
            product = int(pid.group(1))
            if 0x1200 <= product <= 0x12ff:
                sn = re.search(r'"USB Serial Number" = "([^"\n]*)"', block)
                out.append({"pid": product, "mode": U.mode_for_pid(product),
                            "usb_serial": sn.group(1) if sn else None})
    return out


def usb_inventory():
    if PL.is_linux():
        try:
            return [dict(d, mode=U.mode_for_pid(d["pid"])) for d in PL.usb_devices()]
        except PL.PlatformError as e:
            raise RestoreError(str(e)) from e
    rc, data = run_query(["/usr/sbin/ioreg", "-p", "IOUSB", "-l", "-w0"])
    if rc:
        raise RestoreError("Could not inspect USB devices.")
    return mobile_usb_devices(data.decode("utf-8", "replace"))


def device_identity():
    """Best-effort read-only identity. A serial is informational, never a NAND test."""
    devices = usb_inventory()
    identity = {"mode": None, "product_type": None, "serial": None,
                "capacity_gb": None, "nand_id": None, "version": None,
                "multiple": len(devices) > 1}
    if len(devices) != 1:
        return identity
    identity.update(devices[0])
    if identity["mode"] in ("dfu", "recovery"):
        t = U.open_transport()
        q = t.query()
        identity["product_type"] = q.get("PRODUCT")
        identity["serial"] = q.get("SRNM") or None
        identity["cpid"] = q.get("CPID")
        identity["bdid"] = q.get("BDID")
        identity["hardware_model"] = q.get("MODEL")
    elif identity["mode"] == "normal":
        try:
            rc, data = run_query([binary("idevice_id"), "-l"])
            ids = data.decode().split() if rc == 0 else []
            if len(ids) == 1:
                rc, data = run_query([binary("ideviceinfo"), "-u", ids[0], "-s", "-x"])
                values = plistlib.loads(data) if rc == 0 else {}
                if values.get("ProductType") == "iPhone1,1":
                    identity.update(product_type="iPhone1,1", udid=ids[0],
                                    serial=values.get("SerialNumber"), version=values.get("ProductVersion"))
                    rc, data = run_query([binary("ideviceinfo"), "-u", ids[0], "-s", "-q",
                                          "com.apple.disk_usage", "-x"])
                    disk = plistlib.loads(data) if rc == 0 else {}
                    size = disk.get("TotalDiskCapacity")
                    if isinstance(size, (int, float)) and size > 0:
                        identity["capacity_gb"] = min((4, 8, 16), key=lambda c: abs(c - size / 10**9))
        except (RestoreError, ValueError, TypeError, UnicodeError):
            pass
    return identity


def recommendation(identity, nand_id=None):
    from . import firmware
    if identity.get("product_type") == "iPhone1,1" and identity.get("version") == "1.0" and not nand_id:
        return {"target": "1.0", "eligibility": "eligible", "reason":
                "This phone reports that it is currently running 1.0. Serial numbers alone cannot prove compatibility."}
    return firmware.recommended_target(product_type=identity.get("product_type") or "iPhone1,1",
                                     serial=identity.get("serial"), capacity_gb=identity.get("capacity_gb"),
                                     nand_id=nand_id or identity.get("nand_id"))


def info(kit=None):
    records = []
    for f in catalog():
        p = firmware_path(f, kit)
        records.append(dict(f, available=bool(p), experimental=f["version"].startswith("1.")))
    try:
        rc, output = run_query([binary("idevicerestore"), "-v"])
        backend = output.decode("utf-8", "replace").strip() if rc == 0 else None
    except RestoreError:
        backend = None
    from . import models
    identity = models.annotate_identity(device_identity())
    return {"backend": backend, "firmwares": records, "identity": identity,
            "recommendation": recommendation(identity),
            "experimental_warning": EXPERIMENTAL_WARNING, "custom_warning": CUSTOM_WARNING}


def prepare(target, kit=None, custom_path=None, nand_id=None):
    identity = device_identity()
    if identity["multiple"]:
        raise RestoreError("Disconnect all other iPhones/iPods before planning a restore.")
    verdict = recommendation(identity, nand_id)
    if target == "auto" and custom_path is None:
        target = verdict.get("target")
        if target not in {f["version"] for f in catalog()}:
            raise RestoreError("NAND compatibility is unknown. Serial numbers cannot decide the earliest "
                               "supported version. Choose a firmware explicitly and read its warning.")
    custom = custom_path is not None
    if custom:
        path = Path(custom_path)
        record = None
    else:
        record = next((f for f in catalog() if f["version"] == target), None)
        if record is None:
            raise RestoreError("Unknown firmware target: %s" % target)
        path = firmware_path(record, kit)
        if path is None:
            raise RestoreError("Bundled firmware is missing: " + record["filename"])
    metadata = inspect_ipsw(path)
    if record and (metadata["sha256"] != record["sha256"] or metadata["size"] != record["size"]
                   or metadata["version"] != record["version"] or metadata["build"] != record["build"]):
        raise RestoreError("Bundled firmware integrity check failed: " + record["filename"])
    if metadata["experimental"]:
        recovery = next(f for f in catalog() if f["version"] == "3.1.3")
        recovery_path = firmware_path(recovery, kit)
        if recovery_path is None or recovery_path.stat().st_size != recovery["size"] or sha256(recovery_path) != recovery["sha256"]:
            raise RestoreError("Download and verify stock 3.1.3 before an experimental 1.x restore, "
                               "so a recovery IPSW is available locally if restoration fails.")
    # Selecting an unchanged stock IPSW through the picker does not need -c/pwnDFU.
    if custom:
        custom = not any(f["sha256"] == metadata["sha256"] for f in catalog())
    warnings = ["Restoring erases all photos, apps, activation, and settings on the iPhone 2G. "
                "Radio firmware may change. Back up anything you need first."]
    if metadata["experimental"]:
        warnings.append(EXPERIMENTAL_WARNING)
    if metadata["version"] == "1.0":
        from . import firmware
        warnings.append(firmware.one_zero_warning())
        warnings.append("The full 1.0 restore also requests a radio firmware and radio bootloader update. "
                        "A later baseband bootloader may reject that stage after the OS is already written; "
                        "the app will report the failure and will not automatically erase again.")
        if verdict.get("eligibility") == "ineligible":
            warnings.append(verdict["reason"])
    if custom:
        warnings.append(CUSTOM_WARNING)
    bootstrap = None
    if custom:
        stock = next(f for f in catalog() if f["version"] == "3.1.3")
        bootstrap = firmware_path(stock, kit)
        if bootstrap is None or sha256(bootstrap) != stock["sha256"]:
            raise RestoreError("Verified stock 3.1.3 is required for custom DFU preparation.")
    return dict(metadata, custom=custom, identity=identity,
                recommendation=verdict, warnings=warnings,
                bootstrap_path=str(bootstrap) if bootstrap else None)


def assert_restore_device(identity, inventory=None, confirm_model=False):
    devices = usb_inventory() if inventory is None else inventory
    if len(devices) != 1:
        raise RestoreError("Connect only the iPhone 2G; disconnect every other iPhone/iPod.")
    if identity.get("mode") not in ("recovery", "dfu"):
        raise RestoreError("Put the iPhone 2G in recovery or DFU mode first. " + DFU_HELP)
    if identity.get("product_type") != "iPhone1,1" or identity.get("hardware_model") != "m68ap":
        raise RestoreError("USB identity is not confirmed as iPhone1,1 / m68ap. "
                           "WTF mode alone cannot distinguish an iPod touch from an iPhone. "
                           "Enter full DFU/recovery and check again.")
    try:
        valid = int(identity.get("cpid", "0"), 16) == 0x8900 and int(identity.get("bdid", "-1"), 16) == 0
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise RestoreError("USB chip/board identity does not match the original iPhone.")
    # Legacy USB descriptors can omit chip/board data, which libirecovery fills
    # with defaults. A model inferred this way is not independent hardware proof.
    if not confirm_model:
        raise RestoreError("Confirm this is the aluminum-back A1203 iPhone. Legacy USB identifiers "
                           "alone can be ambiguous; they cannot identify the NAND chip.")


def backend_arguments(plan, snapshot, out):
    args = [binary("idevicerestore"), "-e", "-y", "-P", "-C", str(out)]
    if plan["custom"]:
        args.append("-c")
    args.append(str(snapshot))
    return args


def execute(plan, confirmed=False, allow_experimental=False, allow_incompatible=False,
            allow_custom=False, confirm_model=False, out=None, emit=lambda *a, **kw: None, timeout=7200):
    """The only entry point that can erase a phone. Consent is checked in CLI and GUI."""
    if not confirmed:
        raise RestoreError("Restore not started: confirmation of data erasure is required.")
    if plan["experimental"] and not allow_experimental:
        raise RestoreError("Read and acknowledge the experimental 1.x restore warning first.")
    if plan["version"] == "1.0" and not allow_incompatible:
        raise RestoreError("Read and acknowledge the 1.0 NAND compatibility warning first.")
    if plan["custom"] and not allow_custom:
        raise RestoreError("Read and acknowledge the custom firmware warning first.")
    out = Path(out) if out else cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "restore.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RestoreError("Another restore is already running.")
        if shutil.disk_usage(out).free < 10 * 1024**3:
            raise RestoreError("At least 10 GB free disk space is required to restore.")
        # Freeze the selected IPSW outside the signed app and recheck its hash.
        emit("restore_phase", step="preparing", message="Checking firmware and device identity…")
        with tempfile.TemporaryDirectory(prefix="restore-", dir=out) as work:
            snapshot = Path(work) / "firmware.ipsw"
            shutil.copyfile(plan["path"], snapshot)
            snapshot.chmod(0o400)
            if sha256(snapshot) != plan["sha256"]:
                raise RestoreError("IPSW changed after confirmation. Inspect and confirm it again.")
            current = device_identity()
            assert_restore_device(current, confirm_model=confirm_model)
            planned_serial = plan["identity"].get("serial")
            if planned_serial and current.get("serial") != planned_serial:
                raise RestoreError("The connected device changed after confirmation. Inspect it again.")
            if plan["custom"]:
                from . import pwnage
                stock = plan.get("bootstrap_path")
                if not stock:
                    raise RestoreError("Stock 3.1.3 firmware is required for built-in custom DFU preparation.")
                try:
                    pwnage.prepare_custom(U.open_transport(), stock, work, usb_inventory, emit)
                except (ValueError, OSError, zipfile.BadZipFile) as e:
                    raise RestoreError(str(e)) from e
                assert_restore_device(device_identity(), confirm_model=confirm_model)
            args = backend_arguments(plan, snapshot, work)
            log_path = out / "restore.log"
            emit("restore_phase", step="restoring", message="Restoring %s; keep USB connected." % plan["version"])
            print("Restoring iPhone1,1 to %s (%s). Full log: %s" %
                  (plan["version"], plan["build"], log_path), flush=True)
            rc, tail = supervise(args, log_path, emit, timeout)
            # If the device reported its NAND chip ID anywhere in the output (e.g. a
            # 1.x kernel rejecting an unsupported flash part), surface the real
            # compatibility verdict for this exact unit.
            from . import nandlog
            nand_hint = ""
            detected = nandlog.parse_nand_id(tail)
            if detected:
                # bind the detected chip id to the verified connected device (current)
                v = recommendation(dict(current, nand_id=detected))
                emit("nand_detected", nand_id=detected, target=v.get("target"),
                     eligibility=v.get("eligibility"), reason=v.get("reason"))
                nand_hint = (" This unit's NAND is %s: %s" % (detected, v.get("reason", "")))
            completed = "Status: Restore Finished" in tail or "Done restoring" in tail
            if rc or not completed or not re.search(r"(?m)^DONE\s*$", tail):
                emit("restore_failed", message="Restore failed; the phone may be partially restored.")
                raise RestoreError("Restore did not complete (exit %s). Read %s. "
                                   "The phone may be partially restored. No fallback was flashed automatically; "
                                   "select stock 3.1.3 to recover.%s" % (rc, log_path, nand_hint))
            message = ("Restore backend completed %s (%s). Check the phone boots and confirm the version "
                       "in Settings → General → About. Activation is a separate step; the 1.0 "
                       "activation/apps actions apply only to 1.0." % (plan["version"], plan["build"]))
            emit("restore_done", ok=True, message=message)
            print(message, flush=True)
            return 0


def supervise(args, log_path, emit, timeout):
    """Deadline-aware output reader, with process-group cleanup on cancel/error."""
    p = None
    tail, pending = "", b""
    deadline = time.monotonic() + timeout
    next_usb_check = 0
    with open(log_path, "wb") as log:
        try:
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True, env=clean_environment())
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise RestoreError("Restore timed out. The phone may be partially restored; read %s." % log_path)
                if time.monotonic() >= next_usb_check:
                    if len(usb_inventory()) > 1:
                        raise RestoreError("Another mobile device connected during the restore. Stopped to avoid "
                                           "selecting it. The phone may be partially restored; read %s." % log_path)
                    next_usb_check = time.monotonic() + 1
                ready, _, _ = select.select([p.stdout], [], [], min(left, 1))
                if not ready:
                    if p.poll() is not None:
                        break
                    continue
                chunk = os.read(p.stdout.fileno(), 8192)
                if not chunk:
                    break
                log.write(chunk)
                log.flush()
                pending += chunk
                while b"\n" in pending or b"\r" in pending:
                    match = re.search(rb"[\r\n]", pending)
                    line = pending[:match.start()].decode("utf-8", "replace")
                    pending = pending[match.end():]
                    tail = (tail + line + "\n")[-200000:]
                    print(line, flush=True)
                    progress = re.fullmatch(r"progress:\s*(\d+)\s+([0-9.]+)", line.strip())
                    if progress:
                        emit("restore_progress", step=progress[1], pct=float(progress[2]) * 100)
                if len(pending) > 65536:
                    text = pending.decode("utf-8", "replace")
                    print(text, flush=True)
                    tail = (tail + text)[-200000:]
                    pending = b""
            if pending:
                tail = (tail + pending.decode("utf-8", "replace"))[-200000:]
                print(pending.decode("utf-8", "replace"), flush=True)
            return p.wait(timeout=30), tail
        finally:
            if p is not None and p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
            if p is not None and p.stdout:
                p.stdout.close()
