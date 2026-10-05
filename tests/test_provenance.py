import json
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from kit import provenance
from kit.cli import find_kit


class ProvenanceTests(unittest.TestCase):
    def test_catalog_2007_date_is_not_authentication(self):
        era = provenance.describe({"pack": "other.zip", "date": "2007-09-01", "ver": "1.0"})
        self.assertEqual(era["status"], "unverified")
        self.assertIn("build unverified", era["label"])

    def test_later_catalog_date_is_visible(self):
        era = provenance.describe({"date": "2008-02-01"})
        self.assertEqual(era["status"], "later")
        self.assertIn("after 2007", era["label"])
        self.assertIn("2008", era["label"])

    def test_missing_invalid_and_non_string_dates_remain_unknown(self):
        for value in (None, "", "not a date", "2007-99-99", 2007, True):
            with self.subTest(value=value):
                era = provenance.describe({"date": value})
                self.assertEqual(era["status"], "unverified")
                self.assertEqual(era["label"], "Era unverified")
                json.dumps(era)

    def test_same_name_and_date_cannot_authenticate_a_different_build(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "iOS1-Launcher.zip"
            path.write_bytes(b"another build")
            era = provenance.describe({"pack": path.name, "date": "2007-08-20"}, path)
            self.assertEqual(era["status"], "unverified")

    def test_missing_helper_file_cannot_inherit_a_known_label(self):
        with tempfile.TemporaryDirectory() as d:
            era = provenance.describe({"pack": "iOS1-UndoSummerBoard.zip"}, Path(d) / "absent.zip")
            self.assertEqual(era["status"], "unverified")

    def test_launcher_label_is_bound_to_the_original_archived_app(self):
        kit = find_kit()
        if not kit:
            self.skipTest("Original kit assets are needed for archived-app verification")
        kit = Path(kit)
        pxl = kit / "pxl-archive/Launcher-0.2.pxl"
        if not pxl.is_file():
            self.skipTest("The source PXL archive is needed for byte-for-byte verification")
        pack = kit / "ios1-apps/iliberty-payloads/iOS1-Launcher.zip"
        self.assertEqual(hashlib.sha256(pxl.read_bytes()).hexdigest(),
                         "7cd503c753beeb2e33abdea11d122b5898d2b40140a607fd2248ea7130448783")
        with zipfile.ZipFile(pxl) as original, zipfile.ZipFile(pack) as current:
            for name in ("Default.png", "Info.plist", "Launcher", "icon.png"):
                self.assertEqual(original.read("app/" + name),
                                 current.read("Applications/Launcher.app/" + name))
            self.assertEqual(hashlib.sha256(original.read("app/Launcher")).hexdigest(),
                             "9bca3c148f40c8d207474de67f51f5de15be64d8020881297c3e32bb64585569")
        era = provenance.describe({"pack": pack.name}, pack)
        self.assertEqual(era["status"], "period2007")
        self.assertEqual(era["version"], "0.2")
        self.assertIn("repackaged", era["label"])

    def test_exact_modern_helper_is_not_presented_as_a_2007_app(self):
        kit = find_kit()
        if not kit:
            self.skipTest("Original kit assets are needed for payload fingerprint verification")
        folder = Path(kit) / "ios1-apps/iliberty-payloads"
        era = provenance.describe({"pack": "iOS1-UndoSummerBoard.zip"}, folder / "iOS1-UndoSummerBoard.zip")
        self.assertEqual(era["status"], "modern")
        self.assertIn("not a 2007 app", era["label"])
        era["sources"].clear()
        self.assertTrue(provenance.describe({"pack": "iOS1-UndoSummerBoard.zip"},
                                          folder / "iOS1-UndoSummerBoard.zip")["sources"])


if __name__ == "__main__":
    unittest.main()
