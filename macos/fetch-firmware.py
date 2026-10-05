#!/usr/bin/env python3
"""Build-time only: collect the four fixed stock IPSWs into the private bundle."""
import hashlib
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kit import firmware, restore

target = ROOT / "macos/vendor/firmware"
target.mkdir(parents=True, exist_ok=True)
kit = Path(sys.argv[1])
for f in firmware.firmwares():
    dest = target / f["filename"]
    if not dest.exists() or restore.sha256(dest) != f["sha256"]:
        source = kit / "downloads/firmware" / f["filename"]
        part = dest.with_suffix(".ipsw.part")
        if source.exists():
            shutil.copyfile(source, part)
        else:
            print("Downloading stock " + f["filename"], flush=True)
            with urllib.request.urlopen(f["url"], timeout=120) as response, part.open("wb") as out:
                shutil.copyfileobj(response, out)
        if restore.sha256(part) != f["sha256"] or part.stat().st_size != f["size"]:
            part.unlink()
            raise SystemExit("Firmware checksum mismatch: " + f["filename"])
        part.replace(dest)
    info = restore.inspect_ipsw(dest)
    assert info["version"] == f["version"] and info["build"] == f["build"]
    print("Verified " + f["filename"], flush=True)
