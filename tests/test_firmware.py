"""Firmware decisions and offline checks against actual cached stock assets."""

import base64
import hashlib
import json
from pathlib import Path
import plistlib
import re
import struct
import subprocess
import sys
import unittest
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kit import firmware as F, ramdisk, restore  # noqa: E402


def decoded_kernel(container):
    """Decode the stock 8900/LZSS container for independent evidence checks."""
    if container[:4] != b"8900":
        raise ValueError("Not an 8900 kernel")
    size = struct.unpack_from("<I", container, 12)[0]
    data = container[0x800:0x800 + size]
    if container[7] == 3:
        data = subprocess.run(
            ["/usr/bin/openssl", "enc", "-d", "-aes-128-cbc", "-nopad",
             "-K", "188458a6d15034dfe386f23b61d43774", "-iv", "0" * 32],
            input=data, capture_output=True, check=True).stdout
    elif container[7] != 4:
        raise ValueError("Unknown 8900 format")
    if data[:8] != b"complzss":
        raise ValueError("Not a compressed kernel")
    checksum, expected, length = struct.unpack_from(">III", data, 8)
    source = data[0x180:0x180 + length]
    ring, position, flags, cursor = bytearray(b" " * 4096), 4078, 0, 0
    output = bytearray()
    while cursor < len(source):
        flags >>= 1
        if not flags & 0x100:
            flags = source[cursor] | 0xFF00
            cursor += 1
        if flags & 1:
            values = [source[cursor]]
            cursor += 1
        else:
            first, second = source[cursor:cursor + 2]
            cursor += 2
            offset = first | ((second & 0xF0) << 4)
            values = None
        for index in range(1 if values is not None else (second & 15) + 3):
            byte = values[0] if values is not None else ring[(offset + index) & 4095]
            output.append(byte)
            ring[position] = byte
            position = (position + 1) & 4095
    if len(output) != expected or zlib.adler32(output) != checksum:
        raise ValueError("Kernel length/checksum mismatch")
    return bytes(output)


def kernel_tables(kernel):
    result = []
    for match in re.finditer(rb"<key>device-info-list</key>", kernel):
        start = kernel.index(b"<array", match.end())
        end = kernel.index(b"</array>", start)
        entries = re.findall(rb"<data[^>]*>([^<]+)</data>", kernel[start:end])
        result.append((start, ["0x%08X" % struct.unpack_from(">I", base64.b64decode(raw), 8)[0]
                               for raw in entries]))
    return result


class FirmwareTests(unittest.TestCase):
    def test_catalog_serializes_and_cannot_be_poisoned(self):
        records = F.firmwares()
        self.assertEqual([r["version"] for r in records], ["1.0", "1.1.1", "1.1.3", "3.1.3"])
        self.assertEqual(json.loads(json.dumps(records)), records)
        for record in records:
            self.assertEqual(record["product_type"], "iPhone1,1")
            self.assertRegex(record["sha256"], r"^[a-f0-9]{64}$")
            self.assertGreater(record["size"], 0)
            self.assertEqual(record["url"].rsplit("/", 1)[1], record["filename"])
        records[0]["sha256"] = "changed"
        self.assertNotEqual(F.firmwares()[0]["sha256"], "changed")

    def test_each_confirmed_legacy_id_selects_one_zero(self):
        for chip in (0x2555D5EC, 0xA585D598, 0x9551D3EC, 0x95C1D3AD, 0x95D1D32C):
            with self.subTest(chip=hex(chip)):
                result = F.recommended_target(nand_id=chip)
                self.assertEqual((result["target"], result["eligibility"]), ("1.0", "eligible"))

    def test_later_tables_choose_distinct_targets(self):
        for chip in (0xB655D7EC, 0xB614D5EC, 0xBA94D598, 0xBA95D798, 0x3ED5D789):
            with self.subTest(chip=hex(chip)):
                result = F.recommended_target(nand_id=chip)
                self.assertEqual((result["target"], result["eligibility"]), ("1.1.1", "ineligible"))
                self.assertIn("1.0 cannot boot", result["reason"])
        for chip in (0x3E94D589, 0x3E94D52C, 0x3ED5D72C):
            with self.subTest(chip=hex(chip)):
                result = F.recommended_target(nand_id=chip)
                self.assertEqual((result["target"], result["eligibility"]), ("1.1.3", "ineligible"))
                self.assertIn("1.0 cannot boot", result["reason"])

    def test_conditional_hynix_table_never_proves_minimum(self):
        for chip in (0xA514D3AD, 0xA555D5AD):
            result = F.recommended_target(nand_id=chip)
            self.assertEqual((result["target"], result["eligibility"]), (None, "unknown"))
            self.assertIn("conditional 1.0", result["reason"])

    def test_nand_input_formats_and_invalid_inputs(self):
        for value in (0x2555D5EC, "0x2555d5ec", "2555D5EC", str(0x2555D5EC), " 0X2555D5EC "):
            self.assertEqual(F.recommended_target(nand_id=value)["target"], "1.0")
        for value in (True, False, 0, -1, 0x100000000, "0x100000000", "-1", "", "bad id", 1.5, [], {}):
            with self.subTest(value=value):
                self.assertEqual(F.recommended_target(nand_id=value)["target"], None)
        self.assertEqual(F.recommended_target(nand_id=0xDEADBEEF)["eligibility"], "unknown")

    def test_serial_and_capacity_alone_never_choose_target(self):
        for serial in (None, "7T725ABCWH8", "5K001ABCWH8", "obviously invalid"):
            for capacity in (None, 4, 8, 16):
                result = F.recommended_target(serial=serial, capacity_gb=capacity)
                self.assertEqual((result["target"], result["eligibility"]), (None, "unknown"))
                confirmed = F.recommended_target(serial=serial, nand_id=0x3E94D52C)
                self.assertEqual(confirmed["target"], "1.1.3")
        self.assertIn("16 GB", F.recommended_target(capacity_gb=16)["reason"])

    def test_conflicting_16gb_legacy_chip_is_unknown(self):
        result = F.recommended_target(capacity_gb=16, nand_id=0x2555D5EC)
        self.assertEqual((result["target"], result["eligibility"]), (None, "unknown"))
        self.assertIn("conflicting", result["reason"])
        self.assertEqual(F.recommended_target(capacity_gb=16, nand_id=0x3E94D52C)["target"], "1.1.3")

    def test_other_products_are_always_ineligible(self):
        for product in ("iPhone1,2", "iPod1,1", "iPhone3,1", None, ""):
            result = F.recommended_target(product_type=product, nand_id=0x2555D5EC)
            self.assertEqual((result["target"], result["eligibility"]), (None, "ineligible"))

    def test_one_zero_warning_explains_unknown_and_capacity(self):
        self.assertIn("Serial numbers", F.one_zero_warning())
        self.assertIn("16 GB", F.one_zero_warning())
        self.assertIn("unknown", F.one_zero_warning())

    def test_evidence_copies_cannot_change_decisions(self):
        tables = F.nand_tables()
        json.dumps(tables)
        tables[0]["ids"].clear()
        self.assertEqual(F.recommended_target(nand_id=0x2555D5EC)["target"], "1.0")
        self.assertEqual(len(F.nand_tables()[0]["ids"]), 5)


class StockEvidenceTests(unittest.TestCase):
    def test_bundled_one_zero_kernel_tables_match_recorded_evidence(self):
        path = Path(ramdisk.STOCK_KERNEL)
        if not path.is_file():
            self.skipTest("Download the verified 1.0 IPSW to prepare its kernel first")
        container = path.read_bytes()
        kernel = decoded_kernel(container)
        evidence = [t for t in F.nand_tables() if t["version"] == "1.0"]
        self.assertEqual(hashlib.sha256(container).hexdigest(), evidence[0]["kernel_sha256"])
        self.assertEqual(hashlib.sha256(kernel).hexdigest(), evidence[0]["macho_sha256"])
        self.assertEqual(kernel_tables(kernel), [(t["array_offset"], t["ids"]) for t in evidence])

    def test_cached_early_ipsws_match_pins_and_nand_tables(self):
        checked = 0
        for record in F.firmwares():
            if record["version"] not in ("1.1.1", "1.1.3"):
                continue
            path = restore.firmware_path(record)
            if path is None:
                continue
            with self.subTest(version=record["version"]):
                self.assertEqual(path.stat().st_size, record["size"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
                evidence = next(t for t in F.nand_tables() if t["version"] == record["version"])
                with zipfile.ZipFile(path) as archive:
                    restore = plistlib.loads(archive.read("Restore.plist"))
                    self.assertEqual(restore["ProductType"], "iPhone1,1")
                    self.assertEqual(restore["ProductVersion"], record["version"])
                    self.assertEqual(restore["ProductBuildVersion"], record["build"])
                    container = archive.read(evidence["component"])
                    self.assertEqual(hashlib.sha256(container).hexdigest(), evidence["kernel_sha256"])
                    kernel = decoded_kernel(container)
                self.assertEqual(hashlib.sha256(kernel).hexdigest(), evidence["macho_sha256"])
                self.assertEqual(kernel_tables(kernel), [(evidence["array_offset"], evidence["ids"])])
                checked += 1
        if not checked:
            self.skipTest("Optional cached 1.1.1/1.1.3 IPSWs are unavailable")


if __name__ == "__main__":
    unittest.main()
