"""Offline tests for kit/nandlog.py (NAND chip ID parsing + console->verdict)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kit import nandlog as N  # noqa: E402
from kit import firmware as F  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_driver_not_supported_message(self):
        txt = "AppleS5L8900XADMFMC: NAND device ID 0x3E94D589 not supported\n"
        self.assertEqual(N.parse_nand_id(txt), "0x3E94D589")

    def test_whitelisted_id_in_message(self):
        self.assertEqual(N.parse_nand_id("NAND device ID 0x2555d5ec not supported"), "0x2555D5EC")

    def test_zero_pads_short_hex(self):
        self.assertEqual(N.parse_nand_id("NAND device ID 0x95D1D32C not supported"), "0x95D1D32C")

    def test_ignores_unrelated_hex(self):
        # ECID / load addresses must not be mistaken for a chip id.
        txt = "ECID: 0x000001234567890A\nloadaddr 0x09000000\nBooting...\n"
        self.assertIsNone(N.parse_nand_id(txt))

    def test_nand_tagged_fallback_line(self):
        self.assertEqual(N.parse_nand_id("nand0: found chip id 0xB655D7EC geometry ok"), "0xB655D7EC")

    def test_nand_line_without_id_hint_is_ignored(self):
        self.assertIsNone(N.parse_nand_id("nand driver started at 0x80001000"))

    def test_none_and_empty(self):
        self.assertIsNone(N.parse_nand_id(""))
        self.assertIsNone(N.parse_nand_id(None))
        self.assertIsNone(N.parse_nand_id("Status: Restore Finished\nDONE\n"))

    def test_same_id_repeated_is_fine(self):
        txt = ("NAND device ID 0x2555D5EC not supported\n"
               "NAND device ID 0x2555D5EC not supported\n")
        self.assertEqual(N.parse_nand_id(txt), "0x2555D5EC")

    def test_conflicting_ids_return_none(self):
        txt = ("NAND device ID 0x2555D5EC not supported\n"
               "NAND device ID 0x3E94D589 not supported\n")
        self.assertIsNone(N.parse_nand_id(txt))

    def test_longer_hex_is_not_truncated_to_a_chip_id(self):
        # a 10-hex address must not be read as its first 8 hex
        self.assertIsNone(N.parse_nand_id("nand region id 0x09ABCDEF12 mapped"))
        self.assertIsNone(N.parse_nand_id("NAND device ID 0x2555D5ECAB not supported"))


class RecommendTests(unittest.TestCase):
    def test_whitelisted_is_eligible_for_1_0(self):
        v = N.recommend_from_console("NAND device ID 0x2555D5EC not supported")
        self.assertEqual(v["nand_id"], "0x2555D5EC")
        self.assertEqual(v["eligibility"], "eligible")
        self.assertEqual(v["target"], "1.0")

    def test_later_chip_is_ineligible_with_real_target(self):
        v = N.recommend_from_console("NAND device ID 0x3E94D589 not supported")
        self.assertEqual(v["eligibility"], "ineligible")
        self.assertEqual(v["target"], "1.1.3")

    def test_matches_firmware_directly(self):
        txt = "nand0: chip id 0xB655D7EC"
        self.assertEqual(N.recommend_from_console(txt),
                         dict(F.recommended_target(nand_id="0xB655D7EC"), nand_id="0xB655D7EC"))

    def test_unknown_when_no_id(self):
        v = N.recommend_from_console("Restore Finished\nDONE")
        self.assertIsNone(v["nand_id"])
        self.assertEqual(v["eligibility"], "unknown")
        self.assertIsNone(v["target"])

    def test_capacity_passes_through_when_no_id(self):
        v = N.recommend_from_console("booting", capacity_gb=16)
        self.assertIsNone(v["nand_id"])
        self.assertEqual(v["eligibility"], "unknown")  # 16GB w/o id stays unknown per firmware rules


if __name__ == "__main__":
    unittest.main()
