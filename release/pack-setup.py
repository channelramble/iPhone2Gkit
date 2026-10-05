#!/usr/bin/env python3
"""Build the minimal setup ZIP and the app's pinned manifest from a local kit."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kit import payloads, ramdisk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kit", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    entries = {ramdisk.ASSETS_RD: (args.kit / ramdisk.ASSETS_RD).read_bytes(),
               ramdisk.ASSETS_ACT: (args.kit / ramdisk.ASSETS_ACT).read_bytes(),
               "ios1-apps/catalog.json": (args.kit / "ios1-apps/catalog.json").read_bytes()}
    for name in (ramdisk.ASSETS_RD, ramdisk.ASSETS_ACT):
        assert hashlib.sha256(entries[name]).hexdigest() == ramdisk.ASSETS[name]
    for payload in payloads.load_catalog(args.kit / "ios1-apps"):
        payload.check()
        for path in (payload.zip_path, payload.script_path):
            path = Path(path)
            entries[path.relative_to(args.kit).as_posix()] = path.read_bytes()
        with zipfile.ZipFile(payload.zip_path) as archive:
            assert not any(n.lower().endswith((".nes", ".gba", ".ipsw", ".pem", ".key"))
                           for n in archive.namelist()), "ROM/firmware/key in app pack"
    # Only the real converter is needed, not the rest of the old BasePack.
    with zipfile.ZipFile(args.kit / "iLiberty-portable/iLiberty/BasePack.zip") as source:
        converter = source.read("bin/plutil")
    assert hashlib.sha256(converter).hexdigest() == ramdisk.PLUTIL_SHA
    base = io.BytesIO()
    with zipfile.ZipFile(base, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        info = zipfile.ZipInfo("bin/plutil", (2007, 8, 20, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, converter)
    entries["iLiberty-portable/iLiberty/BasePack.zip"] = base.getvalue()
    args.output.mkdir(parents=True, exist_ok=True)
    filename = "iPhone2Gkit-setup-v1.zip"
    target = args.output / filename
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo("iPhone2Gkit-setup/" + name, (2007, 8, 20, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    record = {"schema": 1, "version": "1", "filename": filename,
              "url": "https://github.com/channelramble/iPhone2Gkit/releases/download/kit-v1/" + filename,
              "size": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              "uncompressed_size": sum(map(len, entries.values())),
              "files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                        for name, data in sorted(entries.items())}}
    (ROOT / "kit/resources/legacy-kit-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    (args.output / "SHA256SUMS").write_text(record["sha256"] + "  " + filename + "\n")
    print(json.dumps({k: v for k, v in record.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    main()
