"""ios1kit command line."""
import argparse
import glob
import json
import os
import platform
import re
import shutil
import sys
import time
import tempfile

from . import payloads as P
from . import ramdisk as RD
from . import usb_recovery as U
from . import restore as R
from . import mux as M
from . import platforms as PL
from . import assets as A

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "installer.sh.in")
ROOT = os.path.dirname(HERE)

RECOVERY_HELP = """\
Put the phone in recovery mode:
  1. Unplug it and hold Sleep/Wake until "slide to power off"; slide it off.
  2. Hold the Home button and plug the USB cable in. Keep holding Home.
  3. Let go when the "Connect to iTunes" picture (cable + iTunes logo) shows.
(If the phone already shows that picture, it is in recovery mode.)"""


def emit(event, **data):
    """Machine-readable progress for the macOS app (enabled by IOS1KIT_EVENTS=1)."""
    if os.environ.get("IOS1KIT_EVENTS") == "1":
        data["event"] = event
        print("@@" + json.dumps(data), flush=True)


def find_kit(explicit=None):
    cands = [explicit, os.environ.get("IOS1KIT_ASSETS"),
             os.path.join(os.path.dirname(ROOT), "kit-assets"),     # inside iOS1Kit.app
             str(PL.data_dir() / "kit-assets"),
             os.path.join(ROOT, "assets"), os.path.join(os.path.dirname(ROOT), "iphone-2g-ios-1-kit-full"),
             os.path.expanduser("~/iphone-2g-ios-1-kit-full")]
    cands += sorted(glob.glob(os.path.join(os.path.dirname(ROOT), ".crosstalk", "uploads", "*", "iphone-2g-ios-1-kit-full")))
    # Where this project keeps it when run from the macOS app. No broad home-folder
    # globs: those would trigger macOS privacy prompts for Desktop/Documents/etc.
    cands += sorted(glob.glob(os.path.expanduser(
        "~/crosstalk-workspaces/*/.crosstalk/uploads/*/iphone-2g-ios-1-kit-full")))
    for c in cands:
        if (c and os.path.isfile(os.path.join(c, RD.ASSETS_RD))
                and os.path.isfile(os.path.join(c, "ios1-apps/catalog.json"))):
            return os.path.abspath(c)
    return None


def need_kit(args):
    kit = find_kit(args.kit)
    if not kit:
        sys.exit("Setup files are missing. Run `iphone2gkit setup` or click Download setup files in the app. "
                 "You can also import an existing kit with --kit PATH.")
    return kit


def template():
    with open(TEMPLATE, encoding="utf-8") as f:
        return f.read()


def ask(q):
    try:
        return input(q + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --------------------------------------------------------------------------- device steps
def wait_recovery(t, timeout=900):
    m, _ = t.mode()
    if m == "recovery":
        return
    if m == "dfu":
        sys.exit("The phone is in DFU mode. Restore iPhone OS 1.0 first (see README).")
    print(RECOVERY_HELP)
    print("Waiting for recovery mode (Ctrl-C to stop)...")
    emit("need_recovery")
    if U.wait_for_mode(t, {"recovery"}, timeout) != "recovery":
        emit("recovery_timeout")
        sys.exit("Timed out waiting for recovery mode.")
    emit("recovery_ok")
    print("  phone is in recovery mode.")
    time.sleep(3)


def describe_device(t):
    """Print what libirecovery sees and which protocol it will use. Returns the info dict."""
    info = t.query()
    if not info["ok"]:
        print("  ! irecovery could not read the device identity (`irecovery -q` failed)")
        return info
    print("  iBoot: CPID %s  ECID %s  SRTG %s  MODE %s" % (
        info.get("CPID"), info.get("ECID"), info.get("SRTG", "N/A"), info.get("MODE", "?")))
    print("  protocol: %s" % ("iOS 1 legacy (interrupt commands, 16 KB bulk file chunks)" if info["legacy"]
                              else "MODERN (iBoot reported an ECID or another CPID)"))
    emit("device", cpid=info.get("CPID"), ecid=info.get("ECID"), srtg=info.get("SRTG"), legacy=info["legacy"])
    if not info["legacy"]:
        print("  ! libirecovery will not use the iOS 1 protocol for this phone. If it is on")
        print("    iPhone OS 1.x, uploads will fail; please send this output to the developers.")
    return info


def print_log_tail(t):
    tail = getattr(t, "last_log_tail", None)
    if tail:
        print("  --- last lines of the USB log ---")
        for line in tail:
            print("  | " + line)


def kick(t, quiet=False):
    """Clear the ramdisk boot-args and boot iPhone OS from flash."""
    t.command('setenv boot-args ""')
    t.command("setenv idle-off true")
    t.command("setenv auto-boot true")
    t.command("saveenv")
    t.command("reboot", may_disconnect=True)
    if not quiet:
        print("Sent reboot. iPhone OS should start in ~40 s.")


def run_ramdisk(t, blob, label, timeout=1800, settle=45):
    """Boot one ramdisk and return True only if the phone then boots iPhone OS.

    auto-boot=false is saved before `bootx`, and only the installer's success
    path sets it back to true. So the phone comes back in normal mode only
    after a full success. A failure, crash or panic brings it back in
    recovery mode instead."""
    print("[%s]" % label)
    emit("phase", label=label, step="waiting")
    wait_recovery(t)
    t.command("setenv idle-off false")
    t.command("setenv auto-boot false")
    with open(blob + ".profile.sh") as profile:
        run_id = re.search(r'RUN_ID="([a-f0-9]+)"', profile.read())
    if run_id:
        # Predeclare persistent strings in iBoot before the ramdisk updates them.
        t.command("setenv ios1kit-run-id " + run_id.group(1))
        t.command("setenv ios1kit-result pending")
        t.command("setenv ios1kit-step pending")
    t.command("saveenv")
    t.command("bgcolor 64 0 128", may_disconnect=True)   # purple screen = USB link works (cosmetic)
    print("  sending ramdisk %.1f MB + stock kernel %.1f MB (16 KB bulk chunks)..." % (
        (os.path.getsize(blob) - RD.KERNEL_SLOT) / 1048576.0,
        os.path.getsize(RD.STOCK_KERNEL) / 1048576.0))
    describe_device(t)
    emit("phase", label=label, step="uploading")
    log_path = os.path.join(os.path.dirname(os.path.abspath(blob)), "irecovery-upload.log")
    try:
        # Upload the disk first, then the stock kernel: this exact sequence
        # was verified on iBoot-159. Kernel filesize must describe the kernel.
        with tempfile.TemporaryDirectory(prefix="ios1kit-upload-") as work:
            disk = os.path.join(work, "ramdisk.img")
            with open(blob, "rb") as source, open(disk, "wb") as target:
                source.seek(RD.KERNEL_SLOT)
                shutil.copyfileobj(source, target)
            print("  uploading ramdisk...")
            t.send_file(disk, log_path, address=0x09990000)
            print("  uploading stock 1.0 kernel...")
            t.send_file(RD.STOCK_KERNEL, log_path + ".kernel", address=0x09000000)
    except U.UploadError as e:
        emit("upload_failed", pct=e.pct, mb=round(e.size / 1048576.0, 1))
        print_log_tail(t)
        # Nothing was booted. Put auto-boot back so a restart boots iPhone OS normally
        # (best effort: after a failed upload iBoot may not answer until replugged).
        try:
            t.command("setenv auto-boot true")
            t.command("saveenv")
        except U.USBError:
            pass
        raise U.USBError("%s\nNothing was changed on the phone. Run `ios1kit usbtest` (app: Test USB upload) "
                         "to measure the largest upload this phone accepts." % e)
    emit("phase", label=label, step="booting")
    t.command("setenv boot-args " + '"' + RD.boot_args(os.path.getsize(blob) - RD.KERNEL_SLOT) + '"')
    t.command("saveenv")
    t.command("bootx", may_disconnect=True)
    if not U.wait_until_gone(t, 90):
        print("  the phone did not start the ramdisk; clearing boot-args and booting it unchanged.")
        kick(t, quiet=True)
        return False
    print("  ramdisk is running; watch the phone's screen. This can take several minutes.")
    emit("phase", label=label, step="running")
    end = time.time() + timeout
    while time.time() < end:
        m = U.wait_for_mode(t, {"normal", "recovery"}, max(1, end - time.time()))
        if m == "normal":
            return True
        if m is None:
            break
        # Recovery mode: either the failure signal, or iBoot passing through on
        # its way to booting. Only a recovery mode that persists counts as failure.
        if U.wait_for_mode(t, {"normal"}, settle) == "normal":
            return True
        if t.mode()[0] == "recovery":
            print("  the phone came back in recovery mode: the run failed.")
            environment = t.read_environment()
            with open(blob + ".profile.sh") as profile:
                run_id = re.search(r'RUN_ID="([a-f0-9]+)"', profile.read())
            if run_id and environment.get("ios1kit-run-id") == run_id.group(1):
                print("  installer result: %s; step: %s" % (
                    environment.get("ios1kit-result", "unknown"), environment.get("ios1kit-step", "unknown")))
            else:
                print("  no result for this run; check the phone's screen.")
            return False
    print("  no answer from the phone after %d min." % (timeout // 60))
    return False


# --------------------------------------------------------------------------- commands
def cmd_doctor(args):
    if args.json:
        return _doctor_json(args)
    ok = True
    print("System: %s %s, Python %s" % (platform.system(), platform.mac_ver()[0] or platform.release(),
                                       platform.python_version()))
    for tool in PL.build_tools():
        if not shutil.which(tool):
            print("  ! %s missing" % tool)
            ok = False
    try:
        t = U.open_transport()
        print("USB: irecovery %s (iOS 1 legacy protocol)" % ".".join(map(str, t.version)))
        m, pid = t.mode()
        print("Phone: %s%s" % (m or "not connected", " (pid 0x%04x)" % pid if pid else ""))
    except U.USBError as e:
        print("  ! USB: %s" % e)
        ok = False
    kit = find_kit(args.kit)
    if not kit:
        print("  ! setup files are missing (run `iphone2gkit setup`)")
        return 1
    print("Kit: %s" % kit)
    for prob in RD.check_assets(kit):
        print("  ! " + prob)
        ok = False
    try:
        cat = P.load_catalog(os.path.join(kit, "ios1-apps"))
        for p in cat:
            p.check()
        print("Payloads: %d packs verified (zip CRCs, safe paths, scripts translate)" % len(cat))
    except (P.PayloadError, OSError) as e:
        print("  ! payloads: %s" % e)
        ok = False
    print("All good." if ok else "Problems found (see ! lines).")
    return 0 if ok else 1


def _doctor_json(args):
    r = {"ok": True, "problems": [], "kit": None, "irecovery": None, "phone": None}
    for tool in PL.build_tools():
        if not shutil.which(tool):
            r["problems"].append("%s missing from this platform's tools" % tool)
    try:
        t = U.open_transport()
        r["irecovery"] = ".".join(map(str, t.version))
        r["phone"] = t.mode()[0]
    except U.USBError as e:
        r["problems"].append(str(e))
    kit = find_kit(args.kit)
    r["kit"] = kit
    kit_problems = []
    if not kit:
        kit_problems.append("Setup files are missing. Click Download setup files.")
    else:
        kit_problems += RD.check_assets(kit)
        try:
            for p in P.load_catalog(os.path.join(kit, "ios1-apps")):
                p.check()
        except (P.PayloadError, OSError) as e:
            kit_problems.append("payloads: %s" % e)
    r["problems"] += kit_problems
    r["ok"] = not r["problems"]
    from . import legacy_assets
    setup = legacy_assets.manifest()
    r["setup"] = {"ready": kit is not None and not kit_problems,
                  "download_url": setup["url"], "size": setup["size"],
                  "resource_version": setup["version"]}
    print(json.dumps(r))
    return 0 if r["ok"] else 1


def cmd_list(args):
    kit = need_kit(args)
    cat = P.load_catalog(os.path.join(kit, "ios1-apps"))
    if args.json:
        print(json.dumps([{"key": p.key, "name": p.name, "cat": p.cat, "kb": p.sys_kb + p.var_kb,
                           "note": p.entry.get("note", ""), "desc": p.entry.get("desc", ""),
                           "in_apps": p.cat in P.APP_CATS, "era": p.era()} for p in cat]))
        return 0
    print("%-18s %-10s %6s  %s" % ("KEY", "CATEGORY", "KB", "NAME"))
    for p in cat:
        print("%-18s %-10s %6d  %s — %s" % (p.key, p.cat, p.sys_kb + p.var_kb, p.name, p.era()["label"]))
    print("\nPresets: --apps apps (every app, no tweaks) | all (apps + tweaks, no SummerBoard) | none")
    return 0


def cmd_status(args):
    t = U.open_transport()
    m, pid = t.mode()
    if args.json:
        print(json.dumps({"mode": m, "pid": pid}))
        return 0
    print("Phone: %s%s" % (m or "not connected", " (pid 0x%04x)" % pid if pid else ""))
    if m == "recovery":
        print("  If an ios1kit run just ended here, it failed: the phone shows the failed step")
        print("  for a minute and /var/root/Media/ios1kit/install.log has details.")
        print("  `ios1kit kick` boots iPhone OS as it is.")
    return 0


USBTEST_MB = (2, 8, 14, 18, 20, 22, 24, 26, 28, 30, 32)
MIN_INSTALL_RD_MB = RD.MIN_INSTALL_RD_SIZE // 1048576


def cmd_usbtest(args):
    """Upload zero-filled files of growing size to iBoot's load buffer. Nothing is booted
    or written to flash; it measures the largest upload this phone/cable/port accepts."""
    t = U.open_transport()
    wait_recovery(t)
    describe_device(t)
    out = _outdir(args)
    ok_mb, failed = 0, None
    for mb in USBTEST_MB:
        path = os.path.join(out, "usbtest.bin")
        with open(path, "wb") as f:
            f.truncate(mb * 1024 * 1024)
        print("Uploading %d MB test file..." % mb, flush=True)
        emit("phase", label="USB test %d MB" % mb, step="uploading")
        try:
            t.send_file(path, os.path.join(out, "irecovery-usbtest.log"))
            ok_mb = mb
            print("  %d MB: OK" % mb)
        except U.UploadError as e:
            failed = (mb, e.pct)
            print_log_tail(t)
            print("  %d MB: FAILED at %.1f%% (~%.1f MB)" % (mb, e.pct, mb * e.pct / 100))
            break
        finally:
            os.remove(path)
    # Disk and kernel are uploaded separately; allow 0.5 MB margin for the disk.
    rd_mb = min(14, int(ok_mb - 0.5)) if ok_mb else 0
    emit("usbtest_result", ok_mb=ok_mb, failed_mb=failed[0] if failed else None,
         failed_pct=failed[1] if failed else None, ramdisk_mb=rd_mb, min_ramdisk_mb=MIN_INSTALL_RD_MB)
    print("")
    if not failed:
        print("All sizes up to %d MB uploaded fine. The default 14 MB ramdisk fits the upload limit; boot compatibility is checked separately." % ok_mb)
    elif rd_mb < MIN_INSTALL_RD_MB:
        print("Largest good upload: %d MB. Check/Repair need a 10 MB disk upload;" % ok_mb)
        print("installing needs at least %d MB. Try another USB port or cable (no hub)," % MIN_INSTALL_RD_MB)
        print("replug the phone in recovery mode, then run this test again.")
        return 1
    else:
        print("Largest good upload: %d MB. Use --ramdisk-mb %d (app: Advanced > Ramdisk size)." % (ok_mb, rd_mb))
        if rd_mb < 14:
            print("That leaves little room for apps per boot; they will be split into several batches.")
    print("If the phone stopped answering, unplug it and put it back in recovery mode.")
    return 0


def cmd_usbinfo(args):
    t = U.open_transport()
    m, pid = t.mode()
    print("Phone: %s%s" % (m or "not connected", " (pid 0x%04x)" % pid if pid else ""))
    if m != "recovery":
        print("Put the phone in recovery mode to read the iBoot identity.")
        return 1
    print(t._run("-q", timeout=30).stdout.rstrip())
    describe_device(t)
    return 0


def cmd_kick(args):
    t = U.open_transport()
    m, _ = t.mode()
    if m != "recovery":
        print("Phone is not in recovery mode (mode: %s); nothing to do." % m)
        return 0
    kick(t)
    return 0


def _outdir(args):
    d = os.path.abspath(args.out or os.environ.get("IOS1KIT_BUILD") or str(PL.cache_dir() / "build"))
    os.makedirs(d, exist_ok=True)
    return d


def _rd_size(args):
    return int(args.ramdisk_mb * 1024 * 1024) // 4096 * 4096


def _plan(args, kit):
    cat = P.load_catalog(os.path.join(kit, "ios1-apps"))
    chosen = P.select(cat, args.apps)
    for p in chosen:
        p.check()
    rd_size = _rd_size(args)
    act_bytes = 760 * 1024 if args.activate else 0
    batches = RD.plan_batches(chosen, RD.payload_budget(rd_size), act_bytes)
    return chosen, batches, rd_size


def _build_all(args, kit, mode):
    out = _outdir(args)
    for old in glob.glob(os.path.join(out, "ios1kit-*.bin*")):
        os.remove(old)
    tpl = template()
    if mode != "install":
        path = os.path.join(out, "ios1kit-%s.bin" % mode)
        RD.build_blob(kit, tpl, path, mode, rd_size=RD.fit_size([], 0, _rd_size(args)), hold=args.hold)
        return [(path, mode)]
    chosen, batches, rd_size = _plan(args, kit)
    total_kb = sum(p.sys_kb for p in chosen)
    print("Plan: %s%d app pack(s), ~%.1f MB on the system partition, %d ramdisk boot(s)" % (
        "activation + " if args.activate else "", len(chosen), total_kb / 1024.0, len(batches)))
    if total_kb > 22 * 1024:
        print("  ! that is close to 1.0's ~24 MB free system space; the phone checks before writing.")
    blobs = []
    for i, b in enumerate(batches, 1):
        label = "%d/%d" % (i, len(batches))
        path = os.path.join(out, "ios1kit-install-%d.bin" % i)
        act = args.activate and i == 1
        RD.build_blob(kit, tpl, path, "install", batch=label, activate=act, payloads=b,
                      rd_size=RD.fit_size(b, 760 * 1024 if act else 0, rd_size), hold=args.hold)
        blobs.append((path, label))
    return blobs


def cmd_build(args):
    kit = need_kit(args)
    probs = RD.check_assets(kit)
    if probs:
        sys.exit("Kit problems: " + "; ".join(probs))
    _build_all(args, kit, args.mode)
    return 0


def _device_run(args, mode):
    kit = need_kit(args)
    probs = RD.check_assets(kit)
    if probs:
        sys.exit("Kit problems: " + "; ".join(probs))
    t = U.open_transport()
    print("Building ramdisk(s)...")
    emit("phase", label=mode, step="building")
    blobs = _build_all(args, kit, mode)
    emit("plan", batches=len(blobs))
    if len(blobs) > 1:
        print("Between batches the phone boots iPhone OS; you'll be asked to put it back in recovery mode.")
    if not args.yes and not ask("Ready to send to the phone. Continue?"):
        return 1
    for n, (path, label) in enumerate(blobs, 1):
        if not run_ramdisk(t, path, "%s %s" % (mode, label)):
            emit("done", ok=False, mode=mode, label=label)
            print("\nStopped at %s %s. Steps after the failed one were not run." % (mode, label))
            if n > 1:
                print("Batches before this one are installed.")
            print("Read the step name on the phone's screen, then see README > Troubleshooting.")
            print("`ios1kit kick` boots the phone as it is.")
            return 1
        print("  %s %s succeeded." % (mode, label))
        emit("batch_ok", label=label, index=n, total=len(blobs))
    emit("done", ok=True, mode=mode)
    if mode == "probe":
        print("\nProbe finished: the ramdisk booted, mounted both partitions read-only and showed")
        print("the results on the phone's screen. The phone is booting iPhone OS unchanged.")
    else:
        print("\nDone. The phone is booting iPhone OS.")
        print("Log on the phone: /var/root/Media/ios1kit/install.log")
    return 0


def cmd_install(args):
    return _device_run(args, "install")


def cmd_launcher(args):
    # A small repair for phones whose four extra icons omit Launcher. Reinstall
    # only the two launch tools; the installer updates DisplayOrder afterwards.
    args.apps, args.activate = "launcher,mobilefinder", False
    return _device_run(args, "install")


def cmd_repair(args):
    return _device_run(args, "repair")


def cmd_probe(args):
    return _device_run(args, "probe")


def cmd_restore_info(args):
    report = R.info(find_kit(args.kit))
    report["transport"] = M.check(R.usb_inventory())
    print(json.dumps(report))
    return 0


def cmd_fetch_firmware(args):
    def progress(version, count, total):
        emit("download", version=version, pct=round(count * 100 / total, 1))
    versions = [f["version"] for f in R.catalog()] if args.target == "all" else [args.target]
    # Experimental 1.x requires a verified local recovery IPSW before erasure.
    # Downloads are host-only and never launch a restore.
    if args.target.startswith("1."):
        A.fetch_firmware("3.1.3", progress)
    results = [A.fetch_firmware(version, progress) for version in versions]
    if args.import_kit:
        A.import_plutil(args.import_kit)
    print(json.dumps(results if args.target == "all" else results[0]))
    return 0


def cmd_setup(args):
    from . import legacy_assets
    def progress(version, count, total):
        emit("download", version=version, pct=round(count * 100 / total, 1))
    print(json.dumps(legacy_assets.setup(progress)))
    return 0


def cmd_transport_info(args):
    report = M.check(R.usb_inventory(), probe_service=args.probe_service)
    if args.json:
        print(json.dumps(report))
    else:
        print(report["message"])
        print(report["detail"])
        if report["phone_query_error"]:
            print(report["phone_query_error"])
    incomplete_probe = args.probe_service and report["service_type"] is None
    return 2 if report["blocking"] or report["phone_query_error"] or incomplete_probe else 0


def cmd_restore_plan(args):
    plan = R.prepare(args.target, find_kit(args.kit), args.ipsw, args.nand_id)
    plan["transport"] = M.check(R.usb_inventory())
    print(json.dumps(plan))
    return 0


def cmd_restore(args):
    plan = R.prepare(args.target, find_kit(args.kit), args.ipsw, args.nand_id)
    if args.expected_sha256 and args.expected_sha256 != plan["sha256"]:
        raise R.RestoreError("Firmware changed after confirmation. Inspect and confirm it again.")
    if args.expected_serial and args.expected_serial != plan["identity"].get("serial"):
        raise R.RestoreError("Phone changed after confirmation. Inspect it again.")
    for warning in plan["warnings"]:
        print(warning, flush=True)
    print(R.DFU_HELP, flush=True)
    confirmed = args.yes or ask("Erase the iPhone 2G and restore %s (%s)?" % (plan["version"], plan["build"]))
    if confirmed:
        transport = M.check(R.usb_inventory())
        if transport["blocking"]:
            raise R.RestoreError(transport["message"] + " No restore was started.")
        print("USB preflight: " + transport["message"], flush=True)
    return R.execute(plan, confirmed=confirmed, allow_experimental=args.allow_experimental,
                     allow_incompatible=args.allow_10, allow_custom=args.allow_custom,
                     confirm_model=args.confirm_original_iphone, out=args.out, emit=emit)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="iphone2gkit", description="iPhone2Gkit — restore and iPhone OS 1.0 tools for macOS and Linux")
    ap.add_argument("--kit", help="path to iphone-2g-ios-1-kit-full (auto-detected)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common_build(sp, with_apps=True):
        sp.add_argument("--out", help="where to write ramdisk blobs (default: platform cache directory)")
        sp.add_argument("--hold", type=int, default=None,
                        help="seconds the final message stays on screen (default 10; probe 60)")
        sp.add_argument("--ramdisk-mb", type=float, default=RD.DEFAULT_RD_SIZE / 1048576.0,
                        help="ramdisk size in MB (max 22; 10 = iLiberty's size)")
        if with_apps:
            sp.add_argument("--apps", default="apps",
                            help="apps | all | none | comma list of keys from `ios1kit list` (default: apps)")
            sp.add_argument("--no-activate", dest="activate", action="store_false",
                            help="skip the patched lockdownd (phone already activated)")

    for name, fn, hlp in (("doctor", cmd_doctor, "check this computer, the kit files and the phone"),
                          ("list", cmd_list, "list installable apps"),
                          ("status", cmd_status, "show which mode the phone is in")):
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(fn=fn)
    sub.add_parser("kick", help="leave recovery mode and boot iPhone OS").set_defaults(fn=cmd_kick)
    sp = sub.add_parser("install", help="activate and install apps (one pass per ramdisk)")
    common_build(sp)
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(fn=cmd_install)
    sp = sub.add_parser("launcher", help="make Launcher and Finder visible; keep activation and existing apps")
    common_build(sp, with_apps=False)
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(fn=cmd_launcher)
    sp = sub.add_parser("repair", help="undo a half-finished iLiberty run, make / writable")
    common_build(sp, with_apps=False)
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(fn=cmd_repair)
    sp = sub.add_parser("probe", help="read-only check: firmware, free space, leftovers")
    common_build(sp, with_apps=False)
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(fn=cmd_probe)
    sub.add_parser("usbinfo", help="show the iBoot identity and USB protocol irecovery will use").set_defaults(fn=cmd_usbinfo)
    sp = sub.add_parser("usbtest", help="measure the largest upload the phone accepts (boots nothing)")
    sp.add_argument("--out", help="scratch folder (default: ios1kit/build)")
    sp.set_defaults(fn=cmd_usbtest)
    sp = sub.add_parser("build", help="only build the ramdisk blob(s), no phone needed")
    common_build(sp)
    sp.add_argument("--mode", choices=("install", "repair", "probe"), default="install")
    sp.set_defaults(fn=cmd_build)

    sub.add_parser("restore-info", help="read-only firmware catalog, USB identity and compatibility (JSON)").set_defaults(fn=cmd_restore_info)
    sp = sub.add_parser("fetch-firmware", help="download and verify stock firmware locally; never connects to a phone")
    sp.add_argument("--target", choices=("1.0", "1.1.1", "1.1.3", "3.1.3", "all"), default="3.1.3")
    sp.add_argument("--import-kit", help="also import the matching 2007 plist converter from an existing kit folder")
    sp.set_defaults(fn=cmd_fetch_firmware)
    sub.add_parser("setup", help="download and prepare 1.0 app setup files; never contacts the phone").set_defaults(fn=cmd_setup)
    sp = sub.add_parser("transport-info", help="read-only physical USB / macOS connection-service readiness")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.add_argument("--probe-service", action="store_true", help="also send only QueryType to the visible phone's service")
    sp.set_defaults(fn=cmd_transport_info)
    for name, fn in (("restore-plan", cmd_restore_plan), ("restore", cmd_restore)):
        sp = sub.add_parser(name, help="inspect restore plan (JSON)" if name == "restore-plan" else "erase and restore iPhone1,1")
        sp.add_argument("--target", default="3.1.3", choices=("auto", "1.0", "1.1.1", "1.1.3", "3.1.3"))
        sp.add_argument("--ipsw", help="a local stock/custom iPhone1,1 IPSW; overrides --target")
        sp.add_argument("--nand-id", help="known NAND chip ID; never inferred from the serial")
        if name == "restore":
            sp.add_argument("-y", "--yes", action="store_true", help="confirm irreversible data erasure")
            sp.add_argument("--allow-experimental", action="store_true", help="acknowledge untested native 1.x restore")
            sp.add_argument("--allow-10", action="store_true", help="acknowledge that 1.0 may not support this NAND")
            sp.add_argument("--allow-custom", action="store_true", help="acknowledge custom firmware/pwnDFU requirements")
            sp.add_argument("--confirm-original-iphone", action="store_true", help="confirm A1203 hardware when old USB identity is ambiguous")
            sp.add_argument("--expected-sha256", help=argparse.SUPPRESS)
            sp.add_argument("--expected-serial", help=argparse.SUPPRESS)
            sp.add_argument("--out", help="restore workspace/log folder")
        sp.set_defaults(fn=fn)

    args = ap.parse_args(argv)
    if getattr(args, "hold", 0) is None:
        args.hold = 60 if "probe" in (args.cmd, getattr(args, "mode", None)) else 10
    if args.cmd in ("repair", "probe") or (args.cmd == "build" and args.mode != "install"):
        args.apps, args.activate = "none", False
    try:
        return args.fn(args)
    except (U.USBError, RD.BuildError, P.PayloadError, R.RestoreError, A.AssetError, PL.PlatformError) as e:
        emit("error", message=str(e))
        print("error: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. If restoring, the phone may be partially restored; keep it connected and inspect the log. "
              "If an app-install ramdisk was already booting, let it finish on its own.")
        return 130
