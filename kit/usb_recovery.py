"""Talk to the iPhone 2G's iBoot (recovery mode) and watch USB mode changes.

iPhone OS 1.0's iBoot-159 does not speak the usual recovery protocol (control
0x40 commands, bulk EP 0x04 uploads). It uses a legacy one: a 12-byte header on
interrupt EP 0x04 acknowledged on EP 0x83, command text on EP 0x02, and file
data in 16 KB bulk chunks on EP 0x05. The bundled libirecovery 1.3.1
is patched for the legacy IOKit APIs and completion sequence, selected for
CPID 0x8900 without an ECID. All device I/O goes through that `irecovery`.

iOS 1 iBoot cannot answer `getenv`; a separate legacy console reader retrieves
`printenv` after failures. Success is judged by the phone booting iPhone OS.

The phone's mode is read from the USB product ID with macOS's `ioreg`,
which also sees a normally booted phone (irecovery only sees recovery/DFU).
"""
import os
import re
import select
import shutil
import subprocess
import sys
import time

from . import platforms

APPLE_VID = 0x05AC
NORMAL_PIDS = {0x1290}                       # iPhone1,1 booted into iPhone OS
RECOVERY_PIDS = {0x1280, 0x1281, 0x1282, 0x1283}
DFU_PIDS = {0x1222, 0x1227}                  # S5L8900 WTF / DFU
MIN_IRECOVERY = (1, 3, 1)                    # first release with the iOS 1 protocol


class USBError(RuntimeError):
    pass


def mode_for_pid(pid):
    if pid in NORMAL_PIDS:
        return "normal"
    if pid in RECOVERY_PIDS:
        return "recovery"
    if pid in DFU_PIDS:
        return "dfu"
    return None


def parse_ioreg(text):
    """Return [(mode, pid)] for iPhone-2G-family devices in `ioreg -p IOUSB -l -w0` output."""
    found = []
    for block in re.split(r"\+-o ", text):
        vid = re.search(r'"idVendor" = (\d+)', block)
        pid = re.search(r'"idProduct" = (\d+)', block)
        if vid and pid and int(vid.group(1)) == APPLE_VID:
            m = mode_for_pid(int(pid.group(1)))
            if m:
                found.append((m, int(pid.group(1))))
    return found


class Irecovery:
    name = "irecovery"

    def __init__(self):
        self.exe = shutil.which("irecovery")
        if not self.exe:
            raise USBError("irecovery not found. Use the complete iPhone2Gkit download for your platform.")
        if not platforms.supported():
            raise USBError("iPhone2Gkit supports macOS and Linux.")
        v = subprocess.run([self.exe, "-V"], capture_output=True, text=True).stdout
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", v)
        self.version = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
        if self.version < MIN_IRECOVERY:
            raise USBError("irecovery %s is too old; iOS 1 needs libirecovery >= 1.3.1 "
                           "(brew upgrade libirecovery)" % ".".join(map(str, self.version)))

    def _run(self, *args, timeout=60):
        try:
            return subprocess.run([self.exe, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise USBError("irecovery %s timed out" % " ".join(args))

    def mode(self):
        try:
            devices = platforms.usb_devices()
        except platforms.PlatformError as e:
            raise USBError(str(e)) from e
        if len(devices) > 1:
            raise USBError("more than one Apple mobile device connected; unplug the others")
        found = [(mode_for_pid(d["pid"]), d["pid"]) for d in devices if mode_for_pid(d["pid"])]
        if len(found) > 1:
            raise USBError("more than one iPhone connected; unplug the others")
        return found[0] if found else (None, None)

    def query(self):
        """Device identity as libirecovery sees it (`irecovery -q`).

        libirecovery only uses the iOS 1 legacy protocol when CPID is 0x8900 and
        there is no ECID; with an ECID it falls back to the modern protocol, whose
        command path does not even check for replies."""
        p = self._run("-q", timeout=30)
        info = {}
        for line in p.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
        try:
            cpid = int(info.get("CPID", "0"), 16)
            ecid = int(info.get("ECID", "0"), 16)
        except ValueError:
            cpid, ecid = 0, -1
        info["legacy"] = cpid == 0x8900 and ecid == 0
        info["ok"] = bool(info.get("CPID"))
        return info

    # irecovery exits 0 even when the transfer failed; with -v it prints
    # irecv_strerror() of the result, which is this string on success.
    OK = "Command completed successfully"

    def command(self, cmd, may_disconnect=False):
        """Send one iBoot command. Commands that reboot the phone may drop the USB link mid-way."""
        p = self._run("-v", "-c", cmd)
        if self.OK not in p.stderr and not may_disconnect:
            tail = (p.stderr.strip().splitlines() or [str(p.returncode)])[-1]
            raise USBError("iBoot command %r failed: %s" % (cmd, tail))

    def read_environment(self):
        """Read the legacy printenv console; getenv is unsupported on iBoot-159."""
        helper = os.path.join(os.path.dirname(self.exe), "ios1kit-recovery-console")
        if not os.path.isfile(helper):
            return {}
        try:
            result = subprocess.run([helper], capture_output=True, text=True,
                                    errors="replace", timeout=30)
        except (OSError, subprocess.SubprocessError):
            return {}
        if result.returncode:
            return {}
        return dict(re.findall(r"(?:^|[\r\n])(?:P |  )([\w?-]+) = '([^']*)'", result.stdout.replace("\0", "")))

    def send_file(self, path, log_path=None, timeout=1800, address=0x09000000):
        """Upload a file to iBoot's loadaddr. irecovery's progress bar is passed through to
        stdout; its verbose stderr goes to log_path. On failure the error says how far it got."""
        size = os.path.getsize(path)
        logf = open(log_path, "w") if log_path else subprocess.DEVNULL
        pct = 0.0
        tail = b""
        deadline = time.time() + timeout
        p = None
        try:
            env = os.environ.copy()
            env["LIBIRECOVERY_IOS1_OVERWRITE_LOADADDR"] = "0x%08x" % address
            p = subprocess.Popen([self.exe, "-v", "-f", path], stdout=subprocess.PIPE, stderr=logf, env=env)
            fd = p.stdout.fileno()
            while True:
                left = deadline - time.time()
                if left <= 0:
                    raise UploadError(pct, size, "upload timed out at %.1f%% after %d min" % (pct, timeout // 60))
                # deadline-aware: a stalled upload with no output still hits the timeout
                ready, _, _ = select.select([fd], [], [], min(left, 1.0))
                if not ready:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.flush()
                tail = (tail + chunk)[-200:]
                found = re.findall(rb"([0-9]{1,3}\.[0-9])%", tail)
                if found:
                    pct = float(found[-1])
            p.wait(timeout=30)
        finally:
            # cancellation (KeyboardInterrupt), timeout or any error: never leave irecovery running
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            if p is not None:
                p.stdout.close()
            if log_path:
                logf.close()
        err = ""
        if log_path:
            with open(log_path, errors="replace") as f:
                err = f.read()
        if self.OK not in err:
            lines = err.strip().splitlines() or ["irecovery exit %s" % p.returncode]
            last = lines[-1]
            self.last_log_tail = lines[-15:]
            raise UploadError(pct, size, "upload failed at %.1f%% (~%.1f of %.1f MB): %s%s" % (
                pct, size * pct / 100 / 1048576, size / 1048576.0, last,
                "; full USB log: %s" % log_path if log_path else ""))
        return pct


class UploadError(USBError):
    def __init__(self, pct, size, msg):
        super().__init__(msg)
        self.pct = pct
        self.size = size


def open_transport():
    return Irecovery()


def wait_for_mode(t, wanted, timeout, poll=1.0):
    """Poll until the phone shows up in one of `wanted` modes. Returns the mode or None."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            m, _ = t.mode()
        except USBError:
            m = None
        if m in wanted:
            return m
        time.sleep(poll)
    return None


def wait_until_gone(t, timeout, poll=0.5):
    """True once no iPhone is visible on USB (e.g. after `bootx`)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            m, _ = t.mode()
        except USBError:
            m = None
        if m is None:
            return True
        time.sleep(poll)
    return False
