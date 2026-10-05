"""Build the bootable blob: [Apple-signed kernelcache | zero pad to 0x990000 | HFSX ramdisk].

This is the exact layout iLiberty+ 1.3 sends (recovered from iLiberty.exe):
the kernelcache sits at iBoot's loadaddr (0x09000000) so `bootx` boots it, and
the ramdisk lands at 0x09990000, which `pmd0=0x09990000...` maps as md0.

The base ramdisk tools (launchd, bash, mount_hfs, fsck_hfs, unzip, nvram, ...)
come from iLiberty's own ramdisk; ln and df come from the kit's BSD Base pack.
"""
import hashlib
import os
import re
import shutil
import shlex
import subprocess
import tempfile
import zipfile
import uuid

from . import platforms


def _resource_path(name):
    bundled = os.path.join(os.path.dirname(__file__), "resources", name)
    return bundled if os.path.isfile(bundled) else str(platforms.data_dir() / "resources" / name)

KERNEL_SLOT = 0x990000
MAX_UPLOAD = 0x2000000            # largest pmd0 upload known to work on the 2G (ZiPhone)
DEFAULT_RD_SIZE = 0x0E00000       # 14 MB; conservative until larger disks are proven on-device
MIN_INSTALL_RD_SIZE = 13 * 1024 * 1024  # smallest app-install disk verified on this phone
STOCK_KERNEL = _resource_path("kernelcache-1.0.dat")
STOCK_KERNEL_SHA = "7fa800fbf396ba0476a50282928cb66f116f73f023b77c0c7d97db8c1c1eec45"
PLUTIL = _resource_path("plutil-ios1")
PLUTIL_SHA = "4558dd21b575b33a73a0e436b51fe82f5abb90aeeedd2542ab422e1430d9d93e"

def boot_args(rd_size):
    return "rd=md0 -s -x pmd0=0x09990000.0x%08X" % rd_size

BASE_TREE = 9523 * 1024           # measured: base tools + HFS overhead in a 22 MB volume
BASE_SLACK = 1024 * 1024          # per-file rounding + catalog growth headroom

ASSETS = {
    # relative path in the kit folder: sha256
    "iLiberty-portable/iLiberty/iLibertyKC.dat":
        "c34ae4088ad9ffdfe82dce7528f85f3fc476ca5f5a35af647c8255d44968d903",
    "iLiberty-portable/iLiberty/iLibertyRD.zip":
        "89f8761cf0c9fe8461aabf6f125657e52bd5765a866a621f24edd85fc1cf3bdd",
    "iLiberty-portable/optional-payloads/Activate10And101.zip":
        "4345b6366fb6e5bde1ed357b355f1241da446806d719ec146980b753a880bdaf",
}
ASSETS_RD = "iLiberty-portable/iLiberty/iLibertyRD.zip"
ASSETS_KC = "iLiberty-portable/iLiberty/iLibertyKC.dat"
ASSETS_ACT = "iLiberty-portable/optional-payloads/Activate10And101.zip"
RD_DAT_SHA = "e89c1e4e58b9c49ed0ded9d65451bfe00e7ba3da948b22a0f123fa3f4fe65a4f"
RD_EXCLUDE = {"aviegas", ".fseventsd", ".Trashes", ".Spotlight-V100", "etc/profile"}
BSD_TOOLS = {"bin/ln": "bin/ln", "bin/df": "bin/df"}


class BuildError(RuntimeError):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def check_assets(kit):
    problems = []
    if not os.path.isfile(STOCK_KERNEL) or sha256(STOCK_KERNEL) != STOCK_KERNEL_SHA:
        problems.append("iPhone OS 1.0 kernel not prepared; prepare 1.0 resources first")
    if not os.path.isfile(PLUTIL) or sha256(PLUTIL) != PLUTIL_SHA:
        problems.append("iPhone plist converter not prepared; import a kit and prepare 1.0 resources")
    for rel, want in ASSETS.items():
        p = os.path.join(kit, rel)
        if not os.path.isfile(p):
            problems.append("missing %s" % rel)
        elif sha256(p) != want:
            problems.append("checksum mismatch: %s" % rel)
    bsd = os.path.join(kit, "ios1-apps/iliberty-payloads/iOS1-BSDBase.zip")
    if not os.path.isfile(bsd):
        problems.append("missing ios1-apps/iliberty-payloads/iOS1-BSDBase.zip (needed for ln/df)")
    return problems


def _sh(*args, check=True):
    p = subprocess.run(list(args), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise BuildError("%s failed: %s" % (" ".join(args[:3]), (p.stderr or p.stdout).strip()))
    return p


def _attach(image, mountpoint, readonly=False, raw=False):
    args = ["hdiutil", "attach", "-nobrowse", "-noautofsck", "-mountpoint", mountpoint]
    if readonly:
        args.append("-readonly")
    if raw:
        args += ["-imagekey", "diskimage-class=CRawDiskImage"]
    _sh(*args, image)


def _detach(mountpoint):
    if _sh("hdiutil", "detach", mountpoint, check=False).returncode != 0:
        _sh("hdiutil", "detach", "-force", mountpoint)


def payload_budget(rd_size):
    if rd_size < MIN_INSTALL_RD_SIZE:
        raise BuildError("app installs need a ramdisk of at least 13 MB; use --ramdisk-mb 14 "
                         "(10 MB is only for probe/repair)")
    budget = rd_size - BASE_TREE - BASE_SLACK
    if budget < 1024 * 1024:
        raise BuildError("a %d MB ramdisk has no room for apps; use --ramdisk-mb 14 or more "
                         "(10 MB is only enough for probe/repair)" % (rd_size >> 20))
    return budget


def _round4k(n):
    return (n + 4095) // 4096 * 4096


def fit_size(payloads, activation_bytes, cap):
    """Smallest ramdisk (whole MB) that holds this batch, never above cap.
    Smaller ramdisks mean smaller uploads; iLiberty's proven upload was ~20 MB."""
    if not payloads and not activation_bytes:
        return min(cap, 10 * 1024 * 1024)      # probe/repair: exactly iLiberty's 10 MB ramdisk
    need = BASE_TREE + BASE_SLACK + _round4k(activation_bytes) + sum(_round4k(p.zip_size) for p in payloads)
    if cap < MIN_INSTALL_RD_SIZE:
        raise BuildError("app installs need a ramdisk of at least 13 MB")
    mb = max(MIN_INSTALL_RD_SIZE // (1024 * 1024), -(-need // (1024 * 1024)))
    return min(cap, mb * 1024 * 1024)


def plan_batches(payloads, budget, activation_bytes):
    """Greedy, order-preserving split of payloads into ramdisk-sized batches.

    Activation rides in batch 1. If activation plus the first pack would not
    fit, batch 1 carries activation alone."""
    act = _round4k(activation_bytes)
    if act > budget:
        raise BuildError("activation files do not fit in the ramdisk")
    batches, cur, used = [], [], act
    for p in payloads:
        need = _round4k(p.zip_size)
        if need > budget:
            raise BuildError("%s (%d KB) does not fit in one ramdisk" % (p.pack, need // 1024))
        if used + need > budget:
            batches.append(cur)          # may be [] = activation-only first batch
            cur, used = [], 0
        cur.append(p)
        used += need
    if cur or not batches:
        batches.append(cur)
    return batches


def render_installer(template, mode, batch, activate, payloads, hold=8):
    steps = []
    for k, p in enumerate(payloads, 1):
        steps.append('say "Installing %s (%d/%d)..."' % (p.name.replace('"', "'").replace("$", ""), k, len(payloads)))
        steps.append('unzip -o /payloads/%s -d "$R" >> "$LOG" 2>&1' % p.pack)
        steps.append('rc=$?; if [ $rc -gt 1 ]; then fail "unzip-%s"; fi' % p.key)
        post = []
        if p.key == "openssh":
            # The kit ships sshd without a host key; install the Mac-generated one once.
            post += ['if [ ! -f "$R/private/etc/ssh_host_rsa_key" ]; then',
                     'cp /payloads/ssh/ssh_host_rsa_key /payloads/ssh/ssh_host_rsa_key.pub "$R/private/etc/"',
                     'chown 0:0 "$R/private/etc/ssh_host_rsa_key" "$R/private/etc/ssh_host_rsa_key.pub"',
                     'chmod 644 "$R/private/etc/ssh_host_rsa_key.pub"',
                     'fi']
        post += p.post_lines()
        for line in post:
            # every command is checked; if/fi lines are structure only
            if line.startswith("if ") or line == "fi":
                steps.append(line)
            else:
                steps.append('%s >> "$LOG" 2>&1 || fail "post-%s"' % (line, p.key))
    archives = ["/payloads/" + p.pack for p in payloads]
    if activate:
        archives += ["/payloads/activate/Lockdownd10.zip", "/payloads/activate/Lockdownd101.zip"]
    checks = ["check_archive " + shlex.quote(path) for path in archives]
    need_sys = sum(p.sys_kb for p in payloads) + (900 if activate else 0) + 1024
    need_var = sum(p.var_kb for p in payloads) + 512
    s = template
    for k, v in {
        "@MODE@": mode, "@BATCH@": batch, "@RUN_ID@": uuid.uuid4().hex,
        "@ACTIVATE@": "1" if activate else "0", "@NEED_SYS_KB@": str(need_sys),
        "@NEED_VAR_KB@": str(need_var), "@HOLD@": str(hold),
        "@ARCHIVE_CHECKS@": "\n".join(checks) if checks else ": # no archives",
        "@PAYLOAD_STEPS@": "\n".join(steps) if steps else ": # no apps in this batch",
    }.items():
        s = s.replace(k, v)
    left = re.findall(r"@[A-Z_]+@", s)
    if left:
        raise BuildError("unfilled installer placeholders: %s" % ", ".join(sorted(set(left))))
    return s


def assemble_blob(kernel_bytes, rd_bytes):
    if len(kernel_bytes) > KERNEL_SLOT:
        raise BuildError("kernelcache larger than its 0x990000 slot")
    if rd_bytes[0x400:0x402] not in (b"H+", b"HX"):
        raise BuildError("ramdisk image has no HFS+ volume header at 0x400")
    if int.from_bytes(rd_bytes[0x404:0x408], "big") & 0x2000:
        raise BuildError("ramdisk volume is journaled; the 2007 kernel must get a plain HFS+ volume")
    blob = kernel_bytes + b"\0" * (KERNEL_SLOT - len(kernel_bytes)) + rd_bytes
    if len(blob) > MAX_UPLOAD:
        raise BuildError("upload is %d bytes, over the 0x2000000 limit" % len(blob))
    return blob


def ssh_host_key():
    """Per-Mac SSH host key (PEM RSA, readable by the phone's OpenSSH 4.6), kept outside the repo."""
    d = os.path.join(os.path.expanduser("~"), ".ios1kit")
    key = os.path.join(d, "ssh_host_rsa_key")
    if not (os.path.isfile(key) and os.path.isfile(key + ".pub")):
        os.makedirs(d, mode=0o700, exist_ok=True)
        _sh("ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-m", "PEM", "-N", "", "-C", "iphone2g", "-f", key)
    return key, key + ".pub"


def build_blob(kit, template, out_path, mode, batch="1/1", activate=False,
               payloads=(), rd_size=DEFAULT_RD_SIZE, hold=8, log=print):
    if rd_size + KERNEL_SLOT > MAX_UPLOAD:
        raise BuildError("ramdisk size too large")
    work = tempfile.mkdtemp(prefix="ios1kit-")
    src_mnt, dst_mnt = os.path.join(work, "src"), os.path.join(work, "dst")
    os.makedirs(src_mnt)
    os.makedirs(dst_mnt)
    attached = []
    try:
        with zipfile.ZipFile(os.path.join(kit, ASSETS_RD)) as z:
            z.extract("iLibertyRD.dat", work)
        rd_src = os.path.join(work, "iLibertyRD.dat")
        if sha256(rd_src) != RD_DAT_SHA:
            raise BuildError("iLibertyRD.dat checksum mismatch")
        img = os.path.join(work, "rd.dmg")
        if platforms.is_linux():
            # Keep the verified base's Unix metadata and symlinks in place.
            # Stage additions on the host; the rootless HFS backend grows and
            # updates the image directly, without mounts or elevated privileges.
            os.makedirs(os.path.join(dst_mnt, "etc"))
            os.makedirs(os.path.join(dst_mnt, "bin"))
        else:
            _attach(rd_src, src_mnt, readonly=True, raw=True)
            attached.append(src_mnt)
            _sh("hdiutil", "create", "-size", "%dk" % (rd_size // 1024), "-fs", "HFSX", "-volname", "ios1kit",
                "-layout", "NONE", "-type", "UDIF", img)
            _attach(img, dst_mnt)
            attached.append(dst_mnt)
            for name in sorted(os.listdir(src_mnt)):
                if name in RD_EXCLUDE:
                    continue
                _sh("ditto", "--norsrc", "--noextattr", "--noacl",
                    os.path.join(src_mnt, name), os.path.join(dst_mnt, name))
        prof = os.path.join(dst_mnt, "etc", "profile")
        if os.path.exists(prof):
            os.remove(prof)

        with zipfile.ZipFile(os.path.join(kit, "ios1-apps/iliberty-payloads/iOS1-BSDBase.zip")) as z:
            for src, dst in BSD_TOOLS.items():
                target = os.path.join(dst_mnt, dst)
                with z.open(src) as fi, open(target, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                os.chmod(target, 0o755)

        script = render_installer(template, mode, batch, activate, payloads, hold)
        with open(prof, "w", newline="\n") as f:
            f.write(script)
        os.chmod(prof, 0o644)
        shutil.copyfile(os.path.join(os.path.dirname(__file__), "homescreen.awk"),
                        os.path.join(dst_mnt, "etc", "ios1kit-homescreen.awk"))
        shutil.copyfile(PLUTIL, os.path.join(dst_mnt, "bin", "ios1kit-plutil"))
        os.chmod(os.path.join(dst_mnt, "bin", "ios1kit-plutil"), 0o755)

        pdir = os.path.join(dst_mnt, "payloads")
        os.makedirs(os.path.join(pdir, "activate"))
        if activate:
            with zipfile.ZipFile(os.path.join(kit, ASSETS_ACT)) as z:
                for member in ("Activate10And101/Lockdownd10.zip", "Activate10And101/Lockdownd101.zip"):
                    with z.open(member) as fi, open(os.path.join(pdir, "activate", os.path.basename(member)), "wb") as fo:
                        shutil.copyfileobj(fi, fo)
        for p in payloads:
            shutil.copyfile(p.zip_path, os.path.join(pdir, p.pack))
            if p.key == "openssh":
                os.makedirs(os.path.join(pdir, "ssh"))
                for src in ssh_host_key():
                    shutil.copyfile(src, os.path.join(pdir, "ssh", os.path.basename(src)))

        for junk in (".fseventsd", ".Trashes", ".Spotlight-V100"):
            shutil.rmtree(os.path.join(dst_mnt, junk), ignore_errors=True)
        if platforms.is_linux():
            from . import hfs_linux
            raw_image = os.path.join(work, "rd.img")
            try:
                hfs_linux.build_image(rd_src, dst_mnt, raw_image, rd_size, excludes=RD_EXCLUDE)
            except hfs_linux.HFSError as e:
                raise BuildError(str(e)) from e
        else:
            _detach(dst_mnt)
            attached.remove(dst_mnt)
            _detach(src_mnt)
            attached.remove(src_mnt)
            raw = os.path.join(work, "rd")
            _sh("hdiutil", "convert", img, "-format", "UDTO", "-o", raw)
            raw_image = raw + ".cdr"
        with open(raw_image, "rb") as f:
            rd_bytes = f.read()
        if len(rd_bytes) != rd_size:
            raise BuildError("raw ramdisk is %d bytes, expected %d" % (len(rd_bytes), rd_size))
        with open(STOCK_KERNEL, "rb") as f:
            kernel = f.read()
        blob = assemble_blob(kernel, rd_bytes)
        with open(out_path, "wb") as f:
            f.write(blob)
        with open(out_path + ".profile.sh", "w") as f:
            f.write(script)
        log("  built %s (%.1f MB, %d app packs%s)" % (
            os.path.basename(out_path), len(blob) / 1048576.0, len(payloads), ", activation" if activate else ""))
        return out_path
    finally:
        for m in attached:
            _detach(m)
        shutil.rmtree(work, ignore_errors=True)
