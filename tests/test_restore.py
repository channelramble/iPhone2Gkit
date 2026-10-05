"""Restore guards/protocol supervision; never sends commands to a real phone."""
import contextlib
import io
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kit import cli, pwnage, restore as R


IDENTITY = {"mode": "recovery", "product_type": "iPhone1,1", "hardware_model": "m68ap",
            "cpid": "0x8900", "bdid": "0x00", "serial": None, "capacity_gb": None,
            "nand_id": None, "version": None, "multiple": False}


def make_ipsw(path, product="iPhone1,1", **changes):
    data = {"ProductType": product, "ProductVersion": "3.1.3", "ProductBuildVersion": "7E18",
            "DeviceMap": [{"BoardConfig": "m68ap"}], "RestoreRamDisks": {"User": "ram.dmg"},
            "RestoreKernelCaches": {"Release": "kernel"}, "SystemRestoreImages": {"User": "root.dmg"}}
    data.update(changes)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Restore.plist", plistlib.dumps(data))
        z.writestr("Firmware/all_flash/all_flash.m68ap.production/manifest", "LLB.img3\n")
        z.writestr("Firmware/all_flash/all_flash.m68ap.production/LLB.img3", "data")
        for n in ("ram.dmg", "root.dmg", "kernel"):
            z.writestr(n, "data")


class InspectTests(unittest.TestCase):
    def test_only_original_iphone_ipsw(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.ipsw"
            make_ipsw(p)
            self.assertEqual(R.inspect_ipsw(p)["version"], "3.1.3")
            make_ipsw(p, "iPhone2,1")
            with self.assertRaisesRegex(R.RestoreError, "not for"):
                R.inspect_ipsw(p)

    def test_incorrect_board_even_with_correct_product_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.ipsw"
            make_ipsw(p, DeviceMap=[{"BoardConfig": "n82ap"}])
            with self.assertRaises(R.RestoreError):
                R.inspect_ipsw(p)

    def test_missing_component_and_traversal_rejected(self):
        for name in ("missing.dmg", "../root.dmg", "/root.dmg"):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "a.ipsw"
                make_ipsw(p, SystemRestoreImages={"User": name})
                with self.assertRaises(R.RestoreError):
                    R.inspect_ipsw(p)

    def test_stock_firmware_actual_hashes_and_components(self):
        kit = cli.find_kit()
        for f in R.catalog():
            path = R.firmware_path(f, kit)
            if path is None:
                self.skipTest("Stock IPSW assets not present on this build host")
            with self.subTest(version=f["version"]):
                metadata = R.inspect_ipsw(path)
                self.assertEqual(metadata["sha256"], f["sha256"])
                self.assertEqual(metadata["size"], f["size"])


class RestoreGuardTests(unittest.TestCase):
    def test_mobile_inventory_includes_other_modern_phone(self):
        text = '+-o old\n "idVendor" = 1452\n "idProduct" = 4736\n+-o newer\n "idVendor" = 1452\n "idProduct" = 4776'
        self.assertEqual(len(R.mobile_usb_devices(text)), 2)

    def test_single_device_identity_and_explicit_model_confirmation(self):
        with self.assertRaises(R.RestoreError):
            R.assert_restore_device(IDENTITY, [{}], confirm_model=False)
        R.assert_restore_device(IDENTITY, [{}], confirm_model=True)
        for changed in ({"product_type": "iPod1,1"}, {"cpid": "0x8010"}, {"bdid": "0x02"}, {"mode": "normal"}):
            with self.assertRaises(R.RestoreError):
                R.assert_restore_device(dict(IDENTITY, **changed), [{}], confirm_model=True)
        with self.assertRaises(R.RestoreError):
            R.assert_restore_device(IDENTITY, [{}, {}], confirm_model=True)

    def test_no_erase_without_all_required_acknowledgments(self):
        p = {"version": "1.0", "experimental": True, "custom": True}
        for kwargs in ({}, {"confirmed": True}, {"confirmed": True, "allow_experimental": True},
                       {"confirmed": True, "allow_experimental": True, "allow_incompatible": True}):
            with patch.object(R, "device_identity") as device, patch.object(R, "supervise") as process:
                with self.assertRaises(R.RestoreError):
                    R.execute(p, **kwargs)
                device.assert_not_called()
                process.assert_not_called()

    def test_backend_argv_full_erase_no_ignore_errors(self):
        with patch.object(R, "binary", return_value="/app/idevicerestore"):
            args = R.backend_arguments({"custom": True}, "/tmp/a b.ipsw", "/tmp/work")
        self.assertIn("-e", args)
        self.assertIn("-c", args)
        self.assertNotIn("--ignore-errors", args)
        self.assertEqual(args[-1], "/tmp/a b.ipsw")

    def test_ambiguous_serial_cannot_select_auto_target(self):
        with patch.object(R, "device_identity", return_value=IDENTITY):
            with self.assertRaisesRegex(R.RestoreError, "unknown"):
                R.prepare("auto")

    def test_readonly_identity_uses_simple_lockdown_for_every_query(self):
        normal = dict(IDENTITY, mode="normal")
        seen = []
        def query(args, **kwargs):
            seen.append(args)
            if "-l" in args:
                return 0, b"abc\n"
            return 0, plistlib.dumps({"ProductType": "iPhone1,1", "TotalDiskCapacity": 8000000000})
        with patch.object(R, "usb_inventory", return_value=[{"mode": "normal", "pid": 0x1290}]), \
             patch.object(R, "binary", side_effect=lambda x: x), patch.object(R, "run_query", side_effect=query):
            R.device_identity()
        self.assertEqual(len(seen), 3)
        self.assertTrue(all("-s" in args for args in seen if args[0] == "ideviceinfo"))

    def test_subprocess_environment_cannot_redirect_usb_or_loadaddr(self):
        with patch.dict(os.environ, {"USBMUXD_SOCKET_ADDRESS": "bad:123", "LIBIRECOVERY_IOS1_OVERWRITE_LOADADDR": "0x123"}):
            env = R.clean_environment()
        self.assertNotIn("USBMUXD_SOCKET_ADDRESS", env)
        self.assertNotIn("LIBIRECOVERY_IOS1_OVERWRITE_LOADADDR", env)


class SupervisionTests(unittest.TestCase):
    def test_silent_timeout_cleans_up_process(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "pid"
            script = 'import os,time,pathlib; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)' % str(pidfile)
            with patch.object(R, "usb_inventory", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(R.RestoreError, "timed out"):
                    R.supervise([sys.executable, "-c", script], Path(d) / "log", lambda *a, **kw: None, .5)
            pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_hotplug_second_phone_aborts_before_any_more_output(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(R, "usb_inventory", return_value=[{}, {}]):
                with self.assertRaisesRegex(R.RestoreError, "Another mobile"):
                    R.supervise([sys.executable, "-c", "import time; time.sleep(60)"], Path(d) / "log", lambda *a, **kw: None, 5)

    def test_progress_and_full_log_retained(self):
        events = []
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log"
            with patch.object(R, "usb_inventory", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
                rc, tail = R.supervise([sys.executable, "-c", 'print("progress: 3 0.5");print("Status: Restore Finished");print("DONE")'],
                                       p, lambda event, **data: events.append((event, data)), 10)
            self.assertEqual(rc, 0)
            self.assertIn("Status: Restore Finished", tail)
            self.assertEqual(events[0][1]["pct"], 50)
            self.assertIn("DONE", p.read_text())


class PwnageTests(unittest.TestCase):
    def test_exact_upstream_patch_on_exact_stock_bootstrap(self):
        f = next(f for f in R.catalog() if f["version"] == "3.1.3")
        path = R.firmware_path(f, cli.find_kit())
        if path is None:
            self.skipTest("Stock 3.1.3 IPSW absent")
        with zipfile.ZipFile(path) as z:
            data = z.read("Firmware/dfu/WTF.s5l8900xall.RELEASE.dfu")
        output = pwnage.patch_wtf(data)
        self.assertEqual(len(output), 62686)
        with self.assertRaises(ValueError):
            pwnage.patch_wtf(data[:-1])


if __name__ == "__main__":
    unittest.main()
