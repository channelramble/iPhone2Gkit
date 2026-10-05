"""Offline tests (no phone needed): python3 -m unittest discover -s tests"""
import os
import plistlib
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kit import cli, payloads as P, ramdisk as RD, usb_recovery as U  # noqa: E402

KIT = cli.find_kit()


class FakePayload:
    def __init__(self, key, size, sys_kb=10, var_kb=0, lines=()):
        self.key = self.name = key
        self.pack = key + ".zip"
        self.zip_size = size
        self.sys_kb, self.var_kb = sys_kb, var_kb
        self._lines = list(lines)

    def post_lines(self):
        return self._lines


class TranslateTests(unittest.TestCase):
    WRAPPER = """#!/bin/sh
# Id: x
. $FUNCTIONS
echo "Install X"
PACK=iOS1-X
run unzip -o ${PL_DIR}/${PACK}.zip -d /
rc=$?
if [ $rc -gt 1 ]; then
\techo "Failed (unzip $rc), abort in 10 seconds"
\tsleep 10
\texit 1
fi
rm -f ${PL_DIR}/${PACK}.zip
%s
echo "Done"
"""

    def t(self, body):
        return P.translate_script(self.WRAPPER % body)

    def test_prefixes_paths(self):
        self.assertEqual(self.t('chmod 755 "/Applications/A B.app/A"'), ['chmod 755 "$R/Applications/A B.app/A"'])
        self.assertEqual(self.t("ln -sf /usr/lib/x /usr/local/y"),
                         ['rm -f "$R/usr/local/y"', 'ln -s /usr/lib/x "$R/usr/local/y"'])

    def test_if_block(self):
        out = self.t("if [ ! -f /a ]; then\n\tcp /b /a\nfi")
        self.assertEqual(out, ['if [ ! -f "$R/a" ]; then', 'cp "$R/b" "$R/a"', "fi"])

    def test_rejects_unknown(self):
        for bad in ("rm -rf /", "launchctl load x", "chmod +x /a", "exit 1", "mv /a /b", "chmod 755 rel/path"):
            with self.assertRaises(P.PayloadError, msg=bad):
                self.t(bad)

    def test_quotes_shell_metacharacters(self):
        self.assertEqual(self.t('chmod 644 "/a$b`c"'), ['chmod 644 "$R/a\\$b\\`c"'])


class PlanTests(unittest.TestCase):
    def test_batches_preserve_order_and_budget(self):
        ps = [FakePayload("p%d" % i, 3 * 1024 * 1024) for i in range(5)]
        b = RD.plan_batches(ps, 10 * 1024 * 1024, 760 * 1024)
        self.assertEqual([[p.key for p in x] for x in b], [["p0", "p1", "p2"], ["p3", "p4"]])

    def test_too_big(self):
        with self.assertRaises(RD.BuildError):
            RD.plan_batches([FakePayload("big", 50 << 20)], 10 << 20, 0)

    def test_activation_only_first_batch_when_needed(self):
        b = RD.plan_batches([FakePayload("a", 9 << 20), FakePayload("b", 1 << 20)], 10 << 20, 2 << 20)
        self.assertEqual([[p.key for p in x] for x in b], [[], ["a", "b"]])

    def test_empty_plan_still_one_batch(self):
        self.assertEqual(RD.plan_batches([], 10 << 20, 0), [[]])


class BlobTests(unittest.TestCase):
    def rd(self, sig=b"HX", attrs=0x100):
        b = bytearray(0x1000)
        b[0x400:0x402] = sig
        b[0x404:0x408] = attrs.to_bytes(4, "big")
        return bytes(b)

    def test_layout(self):
        blob = RD.assemble_blob(b"8900KERNEL", self.rd())
        self.assertEqual(blob[:10], b"8900KERNEL")
        self.assertEqual(blob[RD.KERNEL_SLOT + 0x400:RD.KERNEL_SLOT + 0x402], b"HX")
        self.assertFalse(any(blob[10:RD.KERNEL_SLOT]))

    def test_rejects_journaled_and_non_hfs(self):
        with self.assertRaises(RD.BuildError):
            RD.assemble_blob(b"k", self.rd(attrs=0x2000))
        with self.assertRaises(RD.BuildError):
            RD.assemble_blob(b"k", self.rd(sig=b"\0\0"))

    def test_fit_size(self):
        mb = 1024 * 1024
        self.assertEqual(RD.fit_size([], 0, 22 * mb), 10 * mb)            # probe/repair = iLiberty's size
        small = RD.fit_size([FakePayload("a", mb)], 0, 22 * mb)
        self.assertEqual(small, 13 * mb)
        with self.assertRaises(RD.BuildError):
            RD.fit_size([FakePayload("a", 1000)], 0, 12 * mb)
        with self.assertRaises(RD.BuildError):
            RD.payload_budget(12 * mb)
        self.assertEqual(RD.fit_size([FakePayload("a", 30 * mb)], 0, 22 * mb), 22 * mb)  # capped

    def test_default_size_fits_upload_limit(self):
        self.assertLessEqual(RD.KERNEL_SLOT + RD.DEFAULT_RD_SIZE, RD.MAX_UPLOAD)


class InstallerTests(unittest.TestCase):
    def render(self, mode="install", payloads=()):
        return RD.render_installer(cli.template(), mode, "1/1", True, list(payloads))

    def test_no_placeholders_and_valid_syntax(self):
        ps = [FakePayload("openssh", 1000, lines=['chmod 600 "$R/private/etc/ssh_host_rsa_key"'])]
        for mode in ("install", "repair", "probe"):
            s = self.render(mode, ps)
            self.assertNotRegex(s, r"@[A-Z_]+@")
            r = subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_post_step_is_checked(self):
        s = self.render(payloads=[FakePayload("x", 1, lines=['chmod 755 "$R/a"', 'if [ ! -f "$R/b" ]; then',
                                                               'cp "$R/c" "$R/b"', "fi"])])
        self.assertIn('chmod 755 "$R/a" >> "$LOG" 2>&1 || fail "post-x"', s)
        self.assertIn('cp "$R/c" "$R/b" >> "$LOG" 2>&1 || fail "post-x"', s)

    def test_space_check_precedes_any_write(self):
        s = self.render()
        self.assertLess(s.index('fail "system-partition-full"'), s.index("Repairing iLiberty"))
        self.assertLess(s.index('fail "system-partition-full"'), s.index('/private/etc/fstab"'))

    def test_only_success_path_sets_auto_boot_true(self):
        s = self.render()
        self.assertEqual(s.count("nvram auto-boot=true"), 1)
        self.assertIn("nvram auto-boot=false", s[s.index("fail() {"):s.index("succeed() {")])

    def test_clears_boot_args_before_touching_nand(self):
        s = self.render()
        self.assertLess(s.index('nvram boot-args="" >/dev/null 2>&1 || fail clear-boot-args'), s.index("mount_hfs"))
        succ = s[s.index("succeed() {"):]
        self.assertLess(succ.index('nvram boot-args=""'), succ.index("nvram auto-boot=true"))


class IoregTests(unittest.TestCase):
    SAMPLE = """+-o Root  <class IORegistryEntry>
  +-o iPhone@01100000  <class IOUSBHostDevice>
      "idProduct" = 4737
      "idVendor" = 1452
  +-o USB2 Hub@01200000  <class IOUSBHostDevice>
      "idProduct" = 32779
      "idVendor" = 1452
"""

    def test_parse(self):
        self.assertEqual(U.parse_ioreg(self.SAMPLE), [("recovery", 0x1281)])
        self.assertEqual(U.parse_ioreg(self.SAMPLE.replace("4737", "4752")), [("normal", 0x1290)])
        self.assertEqual(U.parse_ioreg(""), [])


class UploadCleanupTests(unittest.TestCase):
    """send_file must time out on a silent stall and never leave irecovery running."""

    def fake(self, body):
        import tempfile
        d = tempfile.mkdtemp()
        exe = os.path.join(d, "irecovery")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\necho $$ > %s/pid\n%s\n" % (d, body))
        os.chmod(exe, 0o755)
        t = U.Irecovery.__new__(U.Irecovery)
        t.exe = exe
        blob = os.path.join(d, "blob")
        open(blob, "wb").write(b"x" * 1024)
        return t, d, blob

    def alive(self, d):
        pid = int(open(os.path.join(d, "pid")).read())
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_silent_stall_times_out_and_child_is_reaped(self):
        t, d, blob = self.fake("exec sleep 60")
        with self.assertRaises(U.UploadError):
            t.send_file(blob, os.path.join(d, "log"), timeout=2)
        self.assertFalse(self.alive(d))

    def test_query_detects_legacy_protocol(self):
        t, d, _ = self.fake("printf 'CPID: 0x8900\\nECID: 0x0000000000000000\\nSRTG: N/A\\nMODE: Recovery\\n'")
        self.assertTrue(t.query()["legacy"])
        t, d, _ = self.fake("printf 'CPID: 0x8900\\nECID: 0x000001234567890a\\n'")
        self.assertFalse(t.query()["legacy"])

    def test_reports_progress_reached(self):
        t, d, blob = self.fake("printf '\\r[==   ] 41.5%%'; echo 'Broken pipe' >&2")
        with self.assertRaises(U.UploadError) as cm:
            t.send_file(blob, os.path.join(d, "log"), timeout=10)
        self.assertEqual(cm.exception.pct, 41.5)
        self.assertIn("Broken pipe", str(cm.exception))


@unittest.skipUnless(KIT, "kit folder not found")
class KitTests(unittest.TestCase):
    def test_assets_and_all_payloads(self):
        self.assertEqual(RD.check_assets(KIT), [])
        for p in P.load_catalog(os.path.join(KIT, "ios1-apps")):
            p.check()

    def test_presets(self):
        cat = P.load_catalog(os.path.join(KIT, "ios1-apps"))
        apps = P.select(cat, "apps")
        self.assertEqual(apps[0].key, "launcher")
        self.assertFalse({"summerboard", "undosummerboard", "customize"} & {p.key for p in apps})
        self.assertNotIn("summerboard", {p.key for p in P.select(cat, "all")})
        with self.assertRaises(P.PayloadError):
            P.select(cat, "summerboard,undosummerboard")
        with self.assertRaises(P.PayloadError):
            P.select(cat, "nosuchapp")


class StockKernelTests(unittest.TestCase):
    def test_stock_kernel_checksum(self):
        if not os.path.isfile(RD.STOCK_KERNEL):
            self.skipTest("Download the verified 1.0 IPSW to prepare its kernel first")
        self.assertEqual(RD.sha256(RD.STOCK_KERNEL), RD.STOCK_KERNEL_SHA)

    def test_boot_args_use_actual_ramdisk_size(self):
        self.assertEqual(RD.boot_args(10 * 1048576), "rd=md0 -s -x pmd0=0x09990000.0x00A00000")
        self.assertTrue(RD.boot_args(22 * 1048576).endswith("0x01600000"))


class RecoveryReadbackTests(unittest.TestCase):
    def test_failure_records_step_and_success_is_checked(self):
        script = RD.render_installer(cli.template(), "install", "1/1", True, [])
        self.assertIn('nvram ios1kit-step="$1"', script)
        self.assertIn('nvram ios1kit-result=success >/dev/null 2>&1 || fail record-success', script)
        self.assertLess(script.index('nvram ios1kit-result=success'), script.index('nvram auto-boot=true'))
        self.assertRegex(script, r'RUN_ID="[a-f0-9]{32}"')

    def test_printenv_is_parsed_without_modern_getenv(self):
        from unittest.mock import patch
        t = U.Irecovery.__new__(U.Irecovery)
        t.exe = "/bundle/bin/irecovery"
        output = "boot log\r\nP ios1kit-run-id = 'abc'\r\n\0P ios1kit-step = 'unzip-bsdbase'\r\nP auto-boot = 'false'\r\n"
        with patch.object(U.os.path, "isfile", return_value=True), patch.object(U.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            self.assertEqual(t.read_environment()["ios1kit-step"], "unzip-bsdbase")



class SplitUploadTests(unittest.TestCase):
    def test_disk_then_kernel_and_actual_size_in_boot_args(self):
        import tempfile
        from unittest.mock import patch
        calls = []
        class Transport:
            def command(self, command, **kwargs):
                calls.append(("command", command))
            def send_file(self, path, log_path, address):
                with open(path, "rb") as stream:
                    data = stream.read()
                calls.append(("upload", address, data))
        disk = b"disk-data" * 512
        kernel = b"mock-signed-kernel"
        with tempfile.TemporaryDirectory() as directory:
            blob = os.path.join(directory, "probe.bin")
            kernel_path = os.path.join(directory, "kernel.dat")
            with open(kernel_path, "wb") as stream:
                stream.write(kernel)
            with open(blob, "wb") as stream:
                stream.seek(RD.KERNEL_SLOT)
                stream.write(disk)
            with open(blob + ".profile.sh", "w") as stream:
                stream.write('RUN_ID="' + "a" * 32 + '"\n')
            with patch.object(RD, "STOCK_KERNEL", kernel_path), patch.object(cli, "wait_recovery"), patch.object(cli, "describe_device"), patch.object(U, "wait_until_gone", return_value=True), patch.object(U, "wait_for_mode", return_value="normal"):
                self.assertTrue(cli.run_ramdisk(Transport(), blob, "probe"))
        uploads = [call for call in calls if call[0] == "upload"]
        self.assertEqual([call[1] for call in uploads], [0x09990000, 0x09000000])
        self.assertEqual(uploads[0][2], disk)
        self.assertEqual(uploads[1][2], kernel)
        self.assertIn(("command", 'setenv boot-args "' + RD.boot_args(len(disk)) + '"'), calls)



class ArchivePreflightTests(unittest.TestCase):
    def test_embedded_archives_checked_before_flash_mount(self):
        script = RD.render_installer(cli.template(), "install", "1/1", True, [FakePayload("bsdbase", 100)])
        start = script.index("# ---------------------------------------------------------------------------")
        main = script[start:]
        self.assertLess(main.index("check_archive /payloads/bsdbase.zip"), main.index("fsck_hfs -fy"))
        self.assertIn("check_archive /payloads/activate/Lockdownd10.zip", main)
        self.assertIn("check_archive /payloads/activate/Lockdownd101.zip", main)

    def test_bad_archive_stops_in_phone_shell(self):
        script = cli.template()
        check = script[script.index("check_archive() {"):script.index("# ---------------------------------------------------------------------------")]
        harness = "say() { :; }; unzip() { return 9; }; fail() { printf '%s\\n' \"$1\"; exit 7; };\n" + check + "\ncheck_archive /payloads/BSD.zip\necho SHOULD_NOT_RUN\n"
        result = subprocess.run(["zsh", "--emulate", "sh", "-c", harness], text=True, capture_output=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("archive-BSD.zip", result.stdout)
        self.assertNotIn("SHOULD_NOT_RUN", result.stdout)


class HomeScreenTests(unittest.TestCase):
    PINS = "com.nullriver.iphone.Launcher,com.googlecode.MobileFinder"

    def layout(self, icons=(), dock=(), hidden=()):
        return {key: [{"displayIdentifier": i} for i in values] for key, values in
                (("iconList", icons), ("buttonBar", dock), ("special", hidden))}

    def edit(self, data):
        return subprocess.run(["awk", "-v", "pins=" + self.PINS, "-f",
                               os.path.join(os.path.dirname(cli.TEMPLATE), "homescreen.awk")],
                              input=data, capture_output=True)

    def test_launcher_visible_and_dock_and_hidden_preserved(self):
        original = self.layout(["com.apple.Preferences", "com.example.Game"],
                               ["com.apple.mobilephone"], ["com.apple.DemoApp", self.PINS.split(",")[0]])
        result = self.edit(plistlib.dumps(original))
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = plistlib.loads(result.stdout)
        self.assertEqual(updated["buttonBar"], original["buttonBar"])
        self.assertEqual([d["displayIdentifier"] for d in updated["iconList"]],
                         self.PINS.split(",") + ["com.apple.Preferences", "com.example.Game"])
        self.assertEqual(updated["special"], self.layout(hidden=["com.apple.DemoApp"])["special"])
        self.assertEqual(self.edit(result.stdout).stdout, result.stdout)

    def test_existing_docked_launcher_stays_in_dock(self):
        original = self.layout(["com.apple.Preferences"], [self.PINS.split(",")[0]])
        result = self.edit(plistlib.dumps(original))
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = plistlib.loads(result.stdout)
        self.assertEqual(updated["buttonBar"], original["buttonBar"])
        self.assertNotIn(original["buttonBar"][0], updated["iconList"])

    def test_full_home_screen_moves_only_last_third_party_icons(self):
        apple = ["com.apple.App%d" % i for i in range(12)]
        extra = ["com.example.App%d" % i for i in range(4)]
        result = self.edit(plistlib.dumps(self.layout(apple + extra)))
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = plistlib.loads(result.stdout)
        self.assertEqual([d["displayIdentifier"] for d in updated["iconList"]],
                         self.PINS.split(",") + apple + extra[:2])
        self.assertEqual({d["displayIdentifier"] for d in updated["special"]}, set(extra[2:]))

    def test_rejects_bad_or_unknown_input_without_output(self):
        good = plistlib.dumps(self.layout())
        bad = [good[:-15], good.rstrip()[:-1], b"bplist00bad", good.replace(b"</dict>", b"<key>unknown</key><string>x</string></dict>", 1),
               plistlib.dumps(self.layout(["same"], ["same"])),
               plistlib.dumps(self.layout(["com.apple.App%d" % i for i in range(17)]))]
        for data in bad:
            result = self.edit(data)
            self.assertNotEqual(result.returncode, 0, data)
            self.assertEqual(result.stdout, b"")

    def test_home_screen_failure_keeps_original_in_phone_shell(self):
        import tempfile
        source = cli.template()
        patch = source[source.index('if [ "$MODE" = "install" ] && [ -x "$R/Applications/Launcher.app/Launcher" ]; then'):
                       source.index("# --- 6. Shared fix")]
        with tempfile.TemporaryDirectory() as root:
            app = os.path.join(root, "Applications/Launcher.app")
            os.makedirs(app)
            exe = os.path.join(app, "Launcher")
            with open(exe, "w") as f:
                f.write("launcher")
            os.chmod(exe, 0o755)
            directory = os.path.join(root, "System/Library/CoreServices/SpringBoard.app")
            os.makedirs(directory)
            order = os.path.join(directory, "DisplayOrder.plist")
            with open(order, "w") as f:
                f.write("original")
            harness = ('R="$1"; MODE=install; say() { :; }; '
                       'awk() { case "$1" in NR*) printf original;; *) return 2;; esac; }; '
                       'fail() { echo "$1"; exit 7; };\n' + patch)
            result = subprocess.run(["zsh", "--emulate", "sh", "-c", harness, "test", root], capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn("home-screen-format", result.stdout)
            with open(order) as f:
                self.assertEqual(f.read(), "original")
            self.assertFalse(os.path.exists(order + ".ios1kit-orig"))
            self.assertFalse(os.path.exists(order + ".ios1kit-new"))

    def test_staged_output_write_failure_is_reported(self):
        import resource
        import signal
        import tempfile
        def limit_output():
            resource.setrlimit(resource.RLIMIT_FSIZE, (128, 128))
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        with tempfile.TemporaryFile() as stage:
            result = subprocess.run(["awk", "-v", "pins=" + self.PINS, "-f",
                                     os.path.join(os.path.dirname(cli.TEMPLATE), "homescreen.awk")],
                                    input=plistlib.dumps(self.layout()), stdout=stage,
                                    stderr=subprocess.PIPE, preexec_fn=limit_output)
            self.assertNotEqual(result.returncode, 0)

    def test_binary_conversion_is_validated_before_replacement(self):
        import tempfile
        source = cli.template()
        patch = source[source.index('if [ "$MODE" = "install" ] && [ -x "$R/Applications/Launcher.app/Launcher" ]; then'):
                       source.index("# --- 6. Shared fix")]
        for convert in (True, False):
            with self.subTest(convert=convert), tempfile.TemporaryDirectory() as root:
                app = os.path.join(root, "Applications/Launcher.app")
                os.makedirs(app)
                exe = os.path.join(app, "Launcher")
                with open(exe, "w") as f:
                    f.write("launcher")
                os.chmod(exe, 0o755)
                directory = os.path.join(root, "System/Library/CoreServices/SpringBoard.app")
                os.makedirs(directory)
                order = os.path.join(directory, "DisplayOrder.plist")
                original = plistlib.dumps(self.layout(["com.apple.Preferences"]), fmt=plistlib.FMT_BINARY)
                with open(order, "wb") as f:
                    f.write(original)
                converter = os.path.join(root, "convert.py")
                with open(converter, "w") as f:
                    f.write("#!/usr/bin/env python3\nimport sys,plistlib\np=sys.argv[-1]\n")
                    if convert:
                        f.write("with open(p,'rb') as f: d=plistlib.load(f)\nwith open(p,'wb') as f: plistlib.dump(d,f)\n")
                os.chmod(converter, 0o755)
                editor = os.path.join(os.path.dirname(cli.TEMPLATE), "homescreen.awk")
                body = patch.replace("/etc/ios1kit-homescreen.awk", editor).replace("/bin/ios1kit-plutil", converter)
                harness = ('R="$1"; MODE=install; say() { :; }; fail() { echo "$1"; exit 7; }; '
                           'run() { step="$1"; shift; "$@" || fail "$step"; }; chown() { :; };\n' + body)
                result = subprocess.run(["zsh", "--emulate", "sh", "-c", harness, "test", root], capture_output=True, text=True, errors="replace")
                with open(order, "rb") as f:
                    data = f.read()
                if convert:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(plistlib.loads(data)["iconList"][0]["displayIdentifier"], self.PINS.split(",")[0])
                    with open(order + ".ios1kit-orig", "rb") as f:
                        self.assertEqual(f.read(), original)
                else:
                    # Historical plutil can return zero even if writing failed.
                    self.assertEqual(result.returncode, 7, result.stderr)
                    self.assertIn("home-screen-format", result.stdout)
                    self.assertEqual(data, original)
                    self.assertFalse(os.path.exists(order + ".ios1kit-orig"))
                self.assertFalse(os.path.exists(order + ".ios1kit-new"))
                self.assertFalse(os.path.exists(order + ".ios1kit-xml"))

    def test_home_screen_stages_on_phone_partition(self):
        import tempfile
        source = cli.template()
        patch = source[source.index('if [ "$MODE" = "install" ] && [ -x "$R/Applications/Launcher.app/Launcher" ]; then'):
                       source.index("# --- 6. Shared fix")]
        self.assertNotIn("/tmp/", patch)
        with tempfile.TemporaryDirectory() as root:
            app = os.path.join(root, "Applications/Launcher.app")
            os.makedirs(app)
            exe = os.path.join(app, "Launcher")
            with open(exe, "w") as f:
                f.write("launcher")
            os.chmod(exe, 0o755)
            directory = os.path.join(root, "System/Library/CoreServices/SpringBoard.app")
            os.makedirs(directory)
            order = os.path.join(directory, "DisplayOrder.plist")
            original = plistlib.dumps(self.layout(["com.apple.Preferences"], ["com.apple.mobilephone"]))
            with open(order, "wb") as f:
                f.write(original)
            # Real awk and phone shell, replacing only root ownership operations.
            editor = os.path.join(os.path.dirname(cli.TEMPLATE), "homescreen.awk")
            harness = ('R="$1"; MODE=install; say() { :; }; fail() { echo "$1"; exit 7; }; '
                       'run() { shift; "$@"; }; chown() { :; };\n' + patch.replace("/etc/ios1kit-homescreen.awk", editor))
            result = subprocess.run(["zsh", "--emulate", "sh", "-c", harness, "test", root], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(order, "rb") as f:
                updated = plistlib.load(f)
            self.assertEqual(updated["iconList"][0]["displayIdentifier"], self.PINS.split(",")[0])
            self.assertEqual(updated["buttonBar"], plistlib.loads(original)["buttonBar"])
            with open(order + ".ios1kit-orig", "rb") as f:
                self.assertEqual(f.read(), original)
            self.assertFalse(os.path.exists(order + ".ios1kit-new"))


if __name__ == "__main__":
    unittest.main()
