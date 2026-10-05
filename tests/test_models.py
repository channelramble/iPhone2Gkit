"""Offline tests for kit/models.py (serial decode only; catalog lives in firmware.py)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kit import models as M  # noqa: E402


class SerialTests(unittest.TestCase):
    def test_decode_valid(self):
        d = M.decode_serial("7A1234567CD")
        self.assertTrue(d["valid"])
        self.assertEqual((d["factory"], d["year_digit"], d["week"]), ("7A", "1", "23"))
        self.assertEqual((d["unit"], d["model_code"]), ("456", "7CD"))
        json.dumps(d)

    def test_decode_is_case_insensitive_and_trims(self):
        self.assertEqual(M.decode_serial("  7a1234567cd ")["raw"], "7A1234567CD")

    def test_decode_invalid(self):
        for bad in ("short", "", None, "7A1234567C!", "7A1234567CDE"):
            self.assertFalse(M.decode_serial(bad)["valid"])

    def test_no_catalog_or_eligibility_leaked_here(self):
        # firmware.py is the single source of truth; models must not duplicate it.
        for name in ("firmwares", "recommended_target", "one_zero_warning", "NAND_1_0"):
            self.assertFalse(hasattr(M, name), "%s must live in firmware.py, not models.py" % name)


class AnnotateTests(unittest.TestCase):
    def test_adds_serial_decoded_without_mutating_input(self):
        ident = {"product_type": "iPhone1,1", "serial": "7A1234567CD", "capacity_gb": 8}
        out = M.annotate_identity(ident)
        self.assertEqual(out["serial_decoded"]["week"], "23")
        self.assertEqual(out["product_type"], "iPhone1,1")
        self.assertNotIn("serial_decoded", ident)  # original untouched
        json.dumps(out)

    def test_handles_missing_serial_and_none(self):
        self.assertFalse(M.annotate_identity({})["serial_decoded"]["valid"])
        self.assertFalse(M.annotate_identity(None)["serial_decoded"]["valid"])


if __name__ == "__main__":
    unittest.main()
