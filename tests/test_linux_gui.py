"""Consent, subprocess lifetime and desktop initialization for the Linux app."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import sys
import tempfile
import time
import unittest

MODULE = Path(__file__).resolve().parents[1] / "linux/gui.py"
spec = importlib.util.spec_from_file_location("iphone2gkit_linux_gui", MODULE)
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)


class ConsentTests(unittest.TestCase):
    def plan(self, **changes):
        return dict({"version": "3.1.3", "path": "/tmp/phone.ipsw", "sha256": "a" * 64,
                     "custom": False, "experimental": False, "identity": {"serial": "TEST123"},
                     "recommendation": {"eligibility": "unknown"}}, **changes)

    def test_requires_erase_and_original_model_consent(self):
        for consent in ({}, {"erase": True}, {"model": True}, {"erase": 1, "model": True}):
            with self.assertRaises(ValueError):
                gui.restore_arguments(self.plan(), consent, "/tmp/out")

    def test_legacy_custom_unknown_nand_requires_all_warnings(self):
        plan = self.plan(version="1.0", custom=True, experimental=True)
        consent = dict(erase=True, model=True, experimental=True, one_zero=True, custom=True)
        for key in consent:
            with self.assertRaises(ValueError):
                gui.restore_arguments(plan, dict(consent, **{key: False}), "/tmp/out")
        args = gui.restore_arguments(plan, consent, "/tmp/out")
        for flag in ("-y", "--confirm-original-iphone", "--allow-experimental", "--allow-10", "--allow-custom"):
            self.assertIn(flag, args)

    def test_sha_and_serial_bind_to_inspected_firmware_and_phone(self):
        plan = self.plan(path="/tmp/file with spaces.ipsw")
        args = gui.restore_arguments(plan, dict(erase=True, model=True), "/tmp/out")
        self.assertEqual(args[args.index("--ipsw") + 1], plan["path"])
        self.assertEqual(args[args.index("--expected-sha256") + 1], plan["sha256"])
        self.assertEqual(args[args.index("--expected-serial") + 1], "TEST123")
        with self.assertRaises(ValueError):
            gui.restore_arguments(self.plan(sha256="z" * 64), dict(erase=True, model=True), "/tmp/out")

    def test_even_known_eligible_one_zero_requires_compatibility_ack(self):
        plan = self.plan(version="1.0", experimental=True, recommendation={"eligibility": "eligible"})
        with self.assertRaises(ValueError):
            gui.restore_arguments(plan, dict(erase=True, model=True, experimental=True), "/tmp/out")
        args = gui.restore_arguments(plan, dict(erase=True, model=True, experimental=True, one_zero=True), "/tmp/out")
        self.assertIn("--allow-10", args)

    def test_failed_query_is_never_parsed_as_success(self):
        reply = json.dumps({"ok": True})
        with self.assertRaises(ValueError):
            gui.parse_reply({"code": 2, "output": [reply]})
        self.assertEqual(gui.parse_reply({"code": 0, "output": ["@@{}", reply]}), {"ok": True})


class RunnerTests(unittest.TestCase):
    def wait(self, runner, limit=12):
        deadline = time.monotonic() + limit
        lines = []
        while time.monotonic() < deadline:
            try:
                event, data = runner.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if event == "line":
                lines.append(data)
            if event == "finished":
                self.assertFalse(runner.busy)
                return data, lines
        runner.cancel()
        self.fail("Worker did not finish")

    def test_only_one_job_can_run_and_output_survives(self):
        runner = gui.ProcessRunner([sys.executable, "-u", "-c", "import time;print('hello');time.sleep(.15)"])
        self.assertTrue(runner.start([]))
        self.assertFalse(runner.start([]))
        result, lines = self.wait(runner)
        self.assertEqual(result["code"], 0)
        self.assertIn("hello", lines)
        self.assertTrue(runner.start([]))
        self.wait(runner)

    def test_launch_error_releases_job_and_reports_failure(self):
        runner = gui.ProcessRunner(["/no/such/iphone2gkit-program"])
        runner.start([])
        result, lines = self.wait(runner)
        self.assertNotEqual(result["code"], 0)
        self.assertTrue(lines)

    @unittest.skipIf(os.name != "posix", "Process groups are POSIX")
    def test_silent_job_timeout_is_bounded(self):
        runner = gui.ProcessRunner([sys.executable, "-c", "import time;time.sleep(120)"])
        started = time.monotonic()
        runner.start([], timeout=0.15)
        result, unused = self.wait(runner)
        self.assertTrue(result["cancelled"])
        self.assertNotEqual(result["code"], 0)
        self.assertLess(time.monotonic() - started, 6)

    @unittest.skipIf(os.name != "posix", "Process groups are POSIX")
    def test_cancel_before_launch_finishes_does_not_leave_child(self):
        runner = gui.ProcessRunner([sys.executable, "-c", "import time;time.sleep(120)"])
        runner.start([])
        runner.cancel()
        result, unused = self.wait(runner)
        self.assertTrue(result["cancelled"])
        self.assertNotEqual(result["code"], 0)

    @unittest.skipIf(os.name != "posix", "Process groups are POSIX")
    def test_stop_cleans_nested_upload_before_releasing_job(self):
        child_source = "import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);time.sleep(120)"
        source = ("import subprocess,sys,time;"
                  "p=subprocess.Popen([sys.executable,'-c',%r]);print(p.pid,flush=True);time.sleep(120)" % child_source)
        runner = gui.ProcessRunner([sys.executable, "-u", "-c", source])
        runner.start([])
        event, data = runner.events.get(timeout=5)
        self.assertEqual(event, "line")
        child = int(data)
        runner.cancel()
        result, unused = self.wait(runner)
        self.assertTrue(result["cancelled"])
        # A killed orphan may remain as a zombie briefly in CI. A zombie cannot
        # retain USB handles or execute an upload, and is no longer a live job.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                return
            stat = Path("/proc/%d/stat" % child)
            if stat.exists() and stat.read_text().split()[2] == "Z":
                return
            time.sleep(0.05)
        os.kill(child, signal.SIGKILL)
        self.fail("Nested upload remained live after cancellation")

    @unittest.skipIf(os.name != "posix", "Process groups are POSIX")
    def test_actual_restore_supervisor_reaps_detached_backend_on_stop(self):
        # This runs the real supervisor with a fake backend and no USB inventory.
        # Its backend has start_new_session=True and ignores normal signals.
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "backend.pid"
            log = Path(directory) / "restore.log"
            backend = ("import signal,time,os;from pathlib import Path;"
                       "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                       "signal.signal(signal.SIGINT,signal.SIG_IGN);"
                       "Path(%r).write_text(str(os.getpid()));time.sleep(120)" % str(pid_path))
            source = ("import sys;from kit import restore as R;R.usb_inventory=lambda:[];"
                      "R.supervise([sys.executable,'-c',%r],%r,lambda *a,**kw:None,120)" % (backend, str(log)))
            environment = dict(os.environ, PYTHONPATH=str(MODULE.parent.parent))
            runner = gui.ProcessRunner([sys.executable, "-u", "-c", source], environment)
            runner.start([])
            deadline = time.monotonic() + 5
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_path.exists(), "Fake detached restore backend did not start")
            backend_pid = int(pid_path.read_text())
            runner.cancel()
            result, unused = self.wait(runner, limit=18)
            self.assertTrue(result["cancelled"])
            with self.assertRaises(ProcessLookupError):
                os.kill(backend_pid, 0)

    @unittest.skipUnless(Path("/proc/self/stat").is_file(), "Linux PID/start-time fallback")
    def test_detached_child_is_stopped_even_if_leader_exits_without_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = str(Path(directory) / "ready")
            child = ("import signal,time;from pathlib import Path;"
                     "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                     "signal.signal(signal.SIGINT,signal.SIG_IGN);"
                     "Path(%r).write_text('ready');time.sleep(120)" % ready)
            source = ("import subprocess,sys,time;from pathlib import Path;"
                      "p=subprocess.Popen([sys.executable,'-c',%r],start_new_session=True);\n"
                      "while not Path(%r).exists():time.sleep(.01)\n"
                      "print(p.pid,flush=True);time.sleep(120)" % (child, ready))
            runner = gui.ProcessRunner([sys.executable, "-u", "-c", source])
            runner.start([])
            event, value = runner.events.get(timeout=5)
            self.assertEqual(event, "line")
            child_pid = int(value)
            runner.cancel()
            time.sleep(0.2)
            self.assertTrue(runner.busy, "Detached process must keep the job busy during cleanup")
            result, unused = self.wait(runner, limit=12)
            self.assertTrue(result["cancelled"])
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                return
            # A dead orphan awaiting PID1 is acceptable; it owns no USB handles.
            self.assertEqual(Path("/proc/%d/stat" % child_pid).read_text().rsplit(")", 1)[1].split()[0], "Z")


class FakeRunner:
    def __init__(self):
        self.busy = False
        self.events = queue.Queue()
        self.started = []

    def start(self, args, timeout=None):
        if self.busy:
            return False
        self.busy = True
        self.started.append(list(args))
        return True

    def cancel(self):
        self.busy = False
        self.events.put(("finished", {"code": 130, "cancelled": True, "output": []}))


@unittest.skipUnless(os.environ.get("DISPLAY"), "Run desktop tests under xvfb-run")
class DesktopTests(unittest.TestCase):
    def setUp(self):
        import tkinter
        self.root = tkinter.Tk()
        self.runner = FakeRunner()
        self.app = gui.Application(self.root, "/tmp/resources", self.runner)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_real_window_starts_without_any_phone_action(self):
        self.assertEqual(self.root.title(), "iPhone2Gkit")
        self.assertEqual(self.runner.started, [])
        self.assertIsNone(self.app.plan)
        self.assertEqual(str(self.app.erase_button["state"]), "disabled")

    def test_refresh_only_queries_information_and_does_not_duplicate_job(self):
        self.app.refresh()
        self.app.refresh()
        self.assertEqual(self.runner.started, [["restore-info"]])
        self.assertNotIn("restore", self.runner.started[0])
        self.runner.busy = False
        report = dict(identity={"product_type": "iPhone1,1", "serial": "TEST", "version": "1.0"},
                      transport={"message": "USB ready"}, recommendation={"reason": "Known 1.0"}, firmwares=[])
        self.runner.events.put(("finished", {"code": 0, "cancelled": False, "output": [json.dumps(report)]}))
        self.app.pump()
        self.assertEqual(self.app.status.get(), "USB ready")
        self.assertEqual(str(self.app.erase_button["state"]), "disabled")

    def test_changing_target_invalidates_existing_plan(self):
        self.app.plan = ConsentTests().plan()
        self.app.erase_button.configure(state="normal")
        self.app.target.set("1.0")
        self.assertIsNone(self.app.plan)
        self.assertEqual(str(self.app.erase_button["state"]), "disabled")


if __name__ == "__main__":
    unittest.main()
