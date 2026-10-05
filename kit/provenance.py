"""App-era disclosures, separate from compatibility and installation metadata.

A catalog date is not proof of an original build. Evidence-backed labels are
bound to the exact payload hash, so another kit cannot inherit them by name.
The payload wrappers and iPhone2Gkit itself are modern software.
"""
from datetime import date
import hashlib
from pathlib import Path


_RECORDS = {
    "iOS1-Launcher.zip": {
        "sha256": "51f59353ad8e11df89a5eb65bcaab6688c3a05460455aa2f6d1f32e4b78eaff9",
        "status": "period2007", "label": "2007 archived app · repackaged",
        "date": "2007-08-20", "version": "0.2",
        "detail": "Nullriver Launcher 0.2. All four app files match the preserved "
                  "Launcher-0.2.pxl archive, whose package dates are August 2007. "
                  "The original app is unchanged; the installation wrapper and bundled "
                  "compatibility libraries were packaged for this kit.",
        "sources": ["https://www.pxl.nerdvittles.com/",
                    "https://www.pxl.nerdvittles.com/Launcher-0.2.pxl"],
    },
    "iOS1-UndoSummerBoard.zip": {
        "sha256": "49099862c17ae231755b5574085755db045d7763f9a0dd5b064896e9a4f78ab3",
        "status": "modern", "label": "Modern helper · not a 2007 app",
        "date": None, "version": None,
        "detail": "This kit-generated repair pack restores a stock SpringBoard launch file. "
                  "It is a modern installation helper, not an original 2007 application.",
        "sources": ["Kit-generated Undo SummerBoard payload; catalog has no historical source package."],
    },
}


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def describe(entry, payload_path=None):
    record = _RECORDS.get(entry.get("pack"))
    if record and payload_path is not None:
        try:
            matches = _digest(payload_path) == record["sha256"]
        except OSError:
            matches = False
        if matches:
            return {key: (list(value) if isinstance(value, list) else value)
                    for key, value in record.items() if key != "sha256"}
    raw_date = entry.get("date")
    try:
        parsed = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
    except ValueError:
        parsed = None
    version = entry.get("ver") if isinstance(entry.get("ver"), str) else None
    source = entry.get("src") if isinstance(entry.get("src"), str) else None
    sources = ["Catalog source: " + source] if source else []
    if parsed:
        after = parsed.year > 2007
        label = "Catalog: %s · %s" % (parsed.year,
                                     "after 2007" if after else "build unverified")
        detail = ("The kit catalog dates %s to %s. This exact packaged build has not been "
                  "independently authenticated as a 2007 release. The installation wrapper "
                  "is modern." % (version or "this version", parsed.isoformat()))
        return {"status": "later" if after else "unverified", "label": label,
                "date": parsed.isoformat(), "version": version, "detail": detail, "sources": sources}
    return {"status": "unverified", "label": "Era unverified", "date": None,
            "version": version, "detail": "No verified historical date for this packaged build. "
            "It is not presented as an authenticated 2007 app; the installation wrapper is modern.",
            "sources": sources}
