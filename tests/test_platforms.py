import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kit import platforms as P, restore as R, usb_recovery as U


class LinuxDiscoveryTests(unittest.TestCase):
    def device(self, root, name, vendor="05ac", pid="1290", serial="old-phone"):
        d = Path(root) / name
        d.mkdir()
        (d / "idVendor").write_text(vendor)
        (d / "idProduct").write_text(pid)
        if serial is not None:
            (d / "serial").write_text(serial)

    def test_sysfs_reads_old_and_new_phone_without_opening_usb(self):
        with tempfile.TemporaryDirectory() as d:
            self.device(d, "1-2")
            self.device(d, "1-3", pid="12a8", serial="new-phone")
            self.device(d, "2-1", vendor="1234")
            (Path(d) / "1-2:1.0").mkdir()
            self.assertEqual(P.linux_usb_devices(d), [
                {"pid": 0x1290, "usb_serial": "old-phone"},
                {"pid": 0x12a8, "usb_serial": "new-phone"}])

    def test_recovery_and_dfu_can_omit_serial(self):
        with tempfile.TemporaryDirectory() as d:
            self.device(d, "1-1", pid="1280", serial=None)
            self.device(d, "1-2", pid="1227", serial=None)
            self.assertEqual([r["pid"] for r in P.linux_usb_devices(d)], [0x1280, 0x1227])

    def test_unreadable_inventory_is_error_not_no_phone(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(P.PlatformError):
                P.linux_usb_devices(Path(d) / "absent")
            self.device(d, "1-1", pid="not-a-pid")
            with self.assertRaises(P.PlatformError):
                P.linux_usb_devices(d)

    def test_restore_inventory_on_linux_preserves_other_devices(self):
        with patch.object(P, "is_linux", return_value=True), patch.object(P, "usb_devices", return_value=[
                {"pid": 0x1280, "usb_serial": None}, {"pid": 0x12a8, "usb_serial": "new"}]):
            inventory = R.usb_inventory()
        self.assertEqual(len(inventory), 2)
        self.assertEqual(inventory[0]["mode"], "recovery")
        self.assertIsNone(inventory[1]["mode"])

    def test_mode_cannot_ignore_second_newer_phone(self):
        t = U.Irecovery.__new__(U.Irecovery)
        with patch.object(P, "usb_devices", return_value=[{"pid": 0x1290}, {"pid": 0x12a8}]):
            with self.assertRaises(U.USBError):
                t.mode()

    def test_xdg_paths_and_relative_overrides(self):
        with patch.object(P, "is_linux", return_value=True), patch.dict(os.environ, {"XDG_CACHE_HOME": "/tmp/cache", "XDG_DATA_HOME": "relative"}):
            self.assertEqual(P.cache_dir(), Path("/tmp/cache/iPhone2Gkit"))
            self.assertEqual(P.data_dir(), Path.home() / ".local/share/iPhone2Gkit")

    def test_host_specific_build_dependencies(self):
        with patch.object(P, "is_linux", return_value=True):
            self.assertNotIn("hdiutil", P.build_tools())
            self.assertIn("hfsplus", P.build_tools())

