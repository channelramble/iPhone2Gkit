"""Serial-number reporting for the iPhone 2G (display only).

Single source of truth for the firmware catalog, NAND whitelist and 1.0
eligibility is `kit/firmware.py`. This module deliberately holds none of that:
it only decodes the legacy serial for display and annotates a device-identity
record with it. Compatibility is a NAND-chip question (see firmware.py); the
serial is shown for reference and is never used to decide a restore target.
"""

PRODUCT = "iPhone1,1"


def decode_serial(serial):
    """Best-effort decode of a legacy 11-character Apple serial, for display only.

    Format (pre-2010): FF Y WW SSS CCC = factory(2), year-digit(1), week(2),
    unit(3), model(3). Approximate and NOT a compatibility signal: production
    week does not reliably track which NAND supplier a unit shipped with.
    """
    s = (serial or "").strip().upper()
    if len(s) != 11 or not s.isalnum():
        return {"valid": False, "raw": serial,
                "note": "Not a legacy 11-character serial; shown for reference only."}
    return {
        "valid": True, "raw": s,
        "factory": s[0:2],
        "year_digit": s[2],
        "week": s[3:5],
        "unit": s[5:8],
        "model_code": s[8:11],
        "note": "Approximate; production week does not determine 1.0 (NAND) compatibility.",
    }


def annotate_identity(identity):
    """Return a copy of a restore identity dict with a decoded serial added.

    `identity` is whatever restore.device_identity() produced (product_type,
    serial, capacity_gb, nand_id, ...). Only `serial_decoded` is added, so the
    GUI can show factory/year/week without implying it affects the verdict.
    """
    out = dict(identity or {})
    out["serial_decoded"] = decode_serial(out.get("serial"))
    return out
