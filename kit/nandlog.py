"""Read an iPhone 2G's NAND chip ID from device console/kernel output.

This is the piece that lets iPhone2Gkit decide 1.0 eligibility from hardware
instead of asking the user, without ever guessing from a serial number.

iPhone OS's flash driver (AppleS5L8900XADMFMC) logs the 32-bit NAND read ID on
the device's **kernel** console; a unit whose chip is not in the running OS's
whitelist prints, verbatim (see research/FINDINGS-2026-10.md):

    NAND device ID 0x%08x not supported

That line is a *rejection by the currently running OS*, not proof of 1.0
compatibility. This module only extracts the hardware chip ID from the text; the
earliest firmware that actually supports that chip is decided separately by
`kit.firmware.recommended_target`, from the verified per-kernel NAND tables.

NOTE ON CAPTURE: the bundled `ios1kit-recovery-console` reads **iBoot** output
(it issues `printenv`), not the kernel console, so it does NOT capture the line
above. A kernel-console capture path (e.g. a NAND-probe ramdisk that prints the
chip ID, or a serial/console reader) is still to be built; this parser is the
reusable core for whatever produces that text (probe output, a restore log, or a
pasted console).
"""
import re

# The flash driver's own message (primary). Exactly 0x + up to 8 hex, and the
# token must not run into more hex digits (so it can't grab a prefix of a longer
# address). Case-insensitive.
_NOT_SUPPORTED = re.compile(r"\bNAND\s+device\s+ID\s*[:=]?\s*(0x[0-9A-Fa-f]{1,8})(?![0-9A-Za-z])", re.IGNORECASE)
# Require an explicit field directly before the token. "NAND device mapped at
# 0x..." and region addresses are not NAND-ID evidence, even if their numeric
# value happens to be in a known firmware's table.
_EXPLICIT_ID = re.compile(
    r"\bnand(?:\d+)?\s*:?\s*(?:found\s+)?(?:chip\s+id|chipid|id)\s*[:=]?\s*"
    r"(0x[0-9A-Fa-f]{1,8})(?![0-9A-Za-z])", re.IGNORECASE)


def _norm(hexstr):
    try:
        value = int(hexstr, 16)
    except (TypeError, ValueError):
        return None
    if not 0 < value <= 0xFFFFFFFF:
        return None
    return "0x%08X" % value


def parse_nand_id(text):
    """Return the NAND chip ID as '0x%08X' found in console/kernel text, or None.

    Only the flash driver's message or NAND-tagged id lines are considered, so
    unrelated hex (addresses, ECIDs) is ignored. If the text contains two or more
    *different* chip IDs, the result is ambiguous and None is returned rather than
    a guess.
    """
    if not text:
        return None
    found = set()
    for pattern in (_NOT_SUPPORTED, _EXPLICIT_ID):
        for m in pattern.finditer(text):
            n = _norm(m.group(1))
            if n:
                found.add(n)
    if len(found) == 1:
        return next(iter(found))
    return None  # nothing found, or conflicting IDs


def recommend_from_console(text, product_type="iPhone1,1", capacity_gb=None):
    """Turn captured device output into a firmware verdict.

    Extracts the hardware chip ID (if unambiguous) and asks
    `firmware.recommended_target` which firmware supports it. Returns
    {target, eligibility, reason, nand_id}; with no usable id the verdict is
    firmware's `unknown` (target None) — never a guess.
    """
    from . import firmware
    nand_id = parse_nand_id(text)
    verdict = firmware.recommended_target(product_type=product_type,
                                          capacity_gb=capacity_gb, nand_id=nand_id)
    return dict(verdict, nand_id=nand_id)
