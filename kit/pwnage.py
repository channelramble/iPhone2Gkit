"""Documented S5L8900 Pwnage 2.0 preparation for custom IPSWs.

The patch is the unchanged Legacy iOS Kit WTF patch. It runs in RAM; it does
not flash a radio/bootloader. This is not evidence that custom 1.x restores work.
"""
import bz2
import hashlib
from pathlib import Path
import time
import zipfile

PATCH = Path(__file__).resolve().parent / "resources/pwnage2-wtf.patch"
PATCH_SHA = "d12486bc101ec5a2df8dd4267b4f2c00cd4c8e2416b565c11af5051cb9f14d12"
ORIGINAL_SHA = "38ea64c64881e882ca8c729405cb35d7bd857183892beb1a41f5207abd0816d2"
OUTPUT_SHA = "46208102527414183a8f307cbc17ad20893028267c753f3e528de8450d599605"


def _offset(b):
    if len(b) != 8:
        raise ValueError("Invalid BSDIFF integer")
    n = int.from_bytes(b, "little") & ((1 << 63) - 1)
    return -n if b[7] & 128 else n


def patch_wtf(original):
    """Apply only the fixed 262-byte patch to the fixed stock 3.1.3 bootstrap."""
    patch = PATCH.read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA or hashlib.sha256(patch).hexdigest() != PATCH_SHA:
        raise ValueError("Pwnage bootstrap/patch integrity check failed")
    if patch[:8] != b"BSDIFF40":
        raise ValueError("Invalid patch header")
    ctrl_len, diff_len, size = (_offset(patch[i:i+8]) for i in (8, 16, 24))
    if ctrl_len < 0 or diff_len < 0 or not 0 < size < 1024 * 1024:
        raise ValueError("Invalid patch lengths")
    ctrl = bz2.decompress(patch[32:32+ctrl_len])
    diff = bz2.decompress(patch[32+ctrl_len:32+ctrl_len+diff_len])
    extra = bz2.decompress(patch[32+ctrl_len+diff_len:])
    old_pos = diff_pos = extra_pos = 0
    result = bytearray()
    if len(ctrl) % 24:
        raise ValueError("Invalid patch control block")
    for i in range(0, len(ctrl), 24):
        x, y, jump = (_offset(ctrl[i+j:i+j+8]) for j in (0, 8, 16))
        if x < 0 or y < 0 or len(result) + x + y > size or diff_pos + x > len(diff) or extra_pos + y > len(extra):
            raise ValueError("Invalid patch control lengths")
        for j in range(x):
            old = original[old_pos+j] if 0 <= old_pos+j < len(original) else 0
            result.append((old + diff[diff_pos+j]) & 255)
        result.extend(extra[extra_pos:extra_pos+y])
        old_pos += x + jump
        diff_pos += x
        extra_pos += y
    if len(result) != size or hashlib.sha256(result).hexdigest() != OUTPUT_SHA:
        raise ValueError("Patched bootstrap integrity check failed")
    return bytes(result)


def prepare_custom(t, stock_ipsw, work, inventory, emit, timeout=60):
    """Caller has already checked singleton USB and confirmed A1203/data erasure."""
    q = t.query()
    if "iBoot-636.66.3x" in q.get("SRTG", ""):
        return
    devices = inventory()
    if len(devices) != 1 or devices[0]["pid"] != 0x1222:
        raise ValueError("For custom firmware, enter the 2G's initial DFU/WTF mode (black screen) first. "
                         "Pwnage preparation cannot run from recovery mode.")
    with zipfile.ZipFile(stock_ipsw) as z:
        original = z.read("Firmware/dfu/WTF.s5l8900xall.RELEASE.dfu")
    path = Path(work) / "pwnage2-wtf.dfu"
    path.write_bytes(patch_wtf(original))
    emit("restore_phase", step="preparing", message="Preparing 2G custom-firmware DFU (Pwnage 2.0)…")
    print("Sending the fixed Pwnage 2.0 bootstrap to RAM; no flash write yet.", flush=True)
    t.send_file(str(path), str(Path(work) / "pwnage-upload.log"), timeout=60)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        devices = inventory()
        if len(devices) > 1:
            raise ValueError("Another mobile device connected during DFU preparation.")
        if len(devices) == 1 and devices[0]["pid"] == 0x1227:
            q = t.query()
            if q.get("PRODUCT") != "iPhone1,1" or q.get("MODEL") != "m68ap":
                raise ValueError("The reconnected device is not an iPhone 2G.")
            if "iBoot-636.66.3x" in q.get("SRTG", ""):
                return
            raise ValueError("Custom DFU preparation did not produce the expected Pwnage 2.0 boot chain.")
        time.sleep(1)
    raise ValueError("Timed out waiting for the 2G's custom-firmware DFU state. Re-enter DFU and inspect again.")
