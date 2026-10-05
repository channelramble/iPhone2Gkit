#!/usr/bin/env python3
"""Linux desktop front end. Phone actions always use the reviewed CLI engine."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time


VERSIONS = ("auto", "3.1.3", "1.0", "1.1.1", "1.1.3")
RELEASE_NOTICE = (
    "Experimental release: hardware restore coverage is still being tested. "
    "Serial numbers do not determine the earliest supported firmware."
)


def config_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        base = Path.home() / ".config"
    return base / "iPhone2Gkit/settings.json"


def restore_arguments(plan, acknowledgements, out):
    """One consent gate for both the GUI and tests. No command exists before OK."""
    required = ["erase", "model"]
    if plan.get("experimental"):
        required.append("experimental")
    if plan.get("version") == "1.0":
        required.append("one_zero")
    if plan.get("custom"):
        required.append("custom")
    if not all(acknowledgements.get(key) is True for key in required):
        raise ValueError("Acknowledge every applicable warning before erasing.")
    if not isinstance(plan.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", plan["sha256"]):
        raise ValueError("Inspect the IPSW before erasing.")
    args = ["restore", "-y", "--out", str(out), "--expected-sha256", plan["sha256"],
            "--confirm-original-iphone"]
    # Preserve the exact inspected input, including a stock file picked manually.
    args += ["--ipsw", str(plan["path"])]
    serial = plan.get("identity", {}).get("serial")
    if serial:
        args += ["--expected-serial", serial]
    if acknowledgements.get("experimental"):
        args.append("--allow-experimental")
    if acknowledgements.get("one_zero"):
        args.append("--allow-10")
    if acknowledgements.get("custom"):
        args.append("--allow-custom")
    return args


class ProcessRunner:
    """One process group at a time; events are consumed only on the UI thread.

    Stop is asynchronous. The job remains busy until its process has exited and
    its output reader has finished. A launch/cancel race cannot leave a child.
    """
    def __init__(self, command, environment=None):
        self.command = list(command)
        self.environment = dict(os.environ if environment is None else environment)
        self.events = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None
        self._process = None
        self._cleanup_thread = None
        self._cancel = threading.Event()

    @property
    def busy(self):
        with self._lock:
            return self._thread is not None

    def start(self, args, timeout=None):
        with self._lock:
            if self._thread is not None:
                return False
            self._cancel = threading.Event()
            self._cleanup_thread = None
            self._thread = threading.Thread(target=self._work, args=(list(args), timeout, self._cancel), daemon=True)
            self._thread.start()
        return True

    @staticmethod
    def _signal_group(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    @staticmethod
    def _descendants(parent_pid, known, parent_started):
        """Track Linux descendants by PID AND kernel start time, across setsid.

        The restore backend starts a separate session. Its PID must never be
        confused with a later unrelated process when escalation is necessary.
        """
        process_root = Path("/proc")
        if not process_root.is_dir():
            return known
        processes = {}
        for directory in process_root.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
                if fields[0] != "Z":
                    processes[int(directory.name)] = (int(fields[1]), fields[19])
            except (OSError, IndexError, ValueError):
                continue
        tracked = {pid: started for pid, started in known.items()
                   if pid in processes and processes[pid][1] == started}
        parents = set(tracked)
        if parent_pid in processes and processes[parent_pid][1] == parent_started:
            parents.add(parent_pid)
        while True:
            added = {pid: fields[1] for pid, fields in processes.items()
                     if pid != parent_pid and pid not in parents and fields[0] in parents}
            if not added:
                break
            tracked.update(added)
            parents.update(added)
        return tracked

    @staticmethod
    def _signal_tracked(tracked, sig):
        for pid, started in tracked.items():
            try:
                fields = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()
                if fields[0] != "Z" and fields[19] == started:
                    os.kill(pid, sig)
            except (OSError, IndexError):
                pass

    def _stop(self, process):
        # Escalation happens off the GUI thread. Waiting on the leader also reaps it.
        parent_started = getattr(process, "_iphone2gkit_started", None)
        tracked = self._descendants(process.pid, {}, parent_started)
        # The CLI supervisor spends up to 5 seconds terminating its separate-
        # session backend. Give that finally block time before killing the CLI.
        for sig, delay in ((signal.SIGINT, 15), (signal.SIGTERM, 5), (signal.SIGKILL, 2)):
            self._signal_group(process, sig)
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                tracked = self._descendants(process.pid, tracked, parent_started)
                try:
                    process.wait(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.poll() is not None:
                break
        if process.poll() is None:
            self._signal_group(process, signal.SIGKILL)
            process.wait()
        # Same-session children can still hold stdout after their leader exits.
        self._signal_group(process, signal.SIGKILL)
        tracked = self._descendants(process.pid, tracked, parent_started)
        self._signal_tracked(tracked, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while tracked and time.monotonic() < deadline:
            time.sleep(0.05)
            tracked = self._descendants(process.pid, tracked, parent_started)
        self._signal_tracked(tracked, signal.SIGKILL)
        # SIGKILL cannot immediately reap a process blocked inside the kernel.
        # Keep the job busy until even that process has released its handles.
        while tracked:
            time.sleep(0.05)
            tracked = self._descendants(process.pid, tracked, parent_started)

    def _begin_stop(self, process):
        with self._lock:
            if self._cleanup_thread is not None:
                return
            self._cleanup_thread = threading.Thread(target=self._stop, args=(process,), daemon=True)
            self._cleanup_thread.start()

    def cancel(self):
        with self._lock:
            self._cancel.set()
            process = self._process
            if process is not None and self._cleanup_thread is None:
                self._cleanup_thread = threading.Thread(target=self._stop, args=(process,), daemon=True)
                self._cleanup_thread.start()

    def _work(self, args, timeout, cancelled):
        output = []
        code = 2
        timer = None
        try:
            process = subprocess.Popen(self.command + args, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       env=self.environment, start_new_session=True, bufsize=1)
            try:
                process._iphone2gkit_started = Path("/proc/%d/stat" % process.pid).read_text().rsplit(")", 1)[1].split()[19]
            except (OSError, IndexError):
                process._iphone2gkit_started = None
            with self._lock:
                self._process = process
            if timeout:
                def expire():
                    self.cancel()
                timer = threading.Timer(timeout, expire)
                timer.daemon = True
                timer.start()
            if cancelled.is_set():
                self._begin_stop(process)
            for line in process.stdout:
                line = line.rstrip("\r\n")
                output.append(line)
                self.events.put(("line", line))
                # A bounded tail is enough for a JSON query / diagnostics. UI log
                # has its own bound, so a noisy backend cannot exhaust memory.
                if len(output) > 2000:
                    del output[:1000]
            code = process.wait()
            process.stdout.close()
        except OSError as exc:
            line = "Cannot launch the engine: " + str(exc)
            output.append(line)
            self.events.put(("line", line))
        finally:
            if timer:
                timer.cancel()
                timer.join()
            # A leader can close its output before its detached backend exits.
            # Never release the job or enable another action during cleanup.
            if self._cleanup_thread:
                self._cleanup_thread.join()
            with self._lock:
                self._process = None
                self._thread = None
            self.events.put(("finished", {"code": code, "cancelled": cancelled.is_set(),
                                           "output": output}))


def parse_reply(result):
    if result["code"]:
        raise ValueError("\n".join(result["output"][-12:]) or "The engine did not complete.")
    for line in reversed(result["output"]):
        if not line.startswith("@@"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    raise ValueError("The engine returned no readable result.")


def parse_doctor_reply(result):
    """Missing setup has exit 1, with a useful report; it is never success."""
    if result["cancelled"] or result["code"] not in (0, 1):
        raise ValueError("Setup check did not complete.")
    for line in reversed(result["output"]):
        try:
            report = json.loads(line)
        except ValueError:
            continue
        if (isinstance(report, dict) and isinstance(report.get("ok"), bool)
                and isinstance(report.get("problems"), list)
                and isinstance(report.get("setup"), dict)
                and isinstance(report["setup"].get("ready"), bool)
                and report["ok"] == (result["code"] == 0)
                and (report.get("kit") is None or isinstance(report["kit"], str))):
            return report
    raise ValueError("The engine returned no readable setup check.")


def checked_setup_reply(result):
    """Only a completed, verified host setup can become the active kit."""
    if result["cancelled"]:
        raise ValueError("Setup was stopped.")
    report = parse_reply(result)
    if (not isinstance(report, dict) or report.get("ready") is not True
            or report.get("verified") is not True
            or not isinstance(report.get("kit"), str) or not report["kit"]
            or not isinstance(report.get("resource_version"), str)
            or not report["resource_version"]
            or type(report.get("apps")) is not int or report["apps"] <= 0):
        raise ValueError("Setup is incomplete. Try Download setup files again.")
    return report


class Application:
    def __init__(self, root, resources, runner=None):
        # Import Tk here so headless tests can exercise process/consent logic.
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.resources = Path(resources)
        environment = dict(os.environ, IOS1KIT_EVENTS="1", PYTHONDONTWRITEBYTECODE="1")
        self.runner = runner or ProcessRunner([sys.executable, "-B", str(self.resources / "engine/ios1kit")], environment)
        self.callback = None
        self.closing = False
        self.kit = tk.StringVar(value="")
        self.target = tk.StringVar(value="3.1.3")
        self.custom = tk.StringVar(value="")
        self.nand = tk.StringVar(value="")
        self.status = tk.StringVar(value="Connect only the original iPhone by USB, then refresh.")
        self.activity = tk.StringVar(value="Ready")
        self.setup_status = tk.StringVar(value="Download setup files once to get started.")
        self.guidance = tk.StringVar(value="")
        self.app_summary = tk.StringVar(value="Your apps will appear after setup.")
        self.setup_ready = False
        self._pump_timer = None
        self.activate = tk.BooleanVar(value=True)
        self.ramdisk = tk.IntVar(value=14)
        self.apps = []
        self.selected = set()
        self.info = {}
        self.plan = None
        self.action_widgets = []
        self._read_settings()
        root.title("iPhone2Gkit")
        root.geometry("960x740")
        root.minsize(800, 640)
        root.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(root, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="iPhone2Gkit", font=("TkDefaultFont", 23, "bold")).pack(anchor="w")
        ttk.Label(body, text="Apps and restore tools for the original iPhone.").pack(anchor="w", pady=(2, 12))
        phone = ttk.LabelFrame(body, text="Phone", padding=10)
        phone.pack(fill="x")
        ttk.Label(phone, textvariable=self.status, wraplength=710).pack(side="left", fill="x", expand=True)
        self.button(phone, "Refresh", self.refresh).pack(side="right")
        setup = ttk.LabelFrame(body, text="Setup", padding=10)
        setup.pack(fill="x", pady=(8, 0))
        ttk.Label(setup, textvariable=self.setup_status, wraplength=660).pack(side="left", fill="x", expand=True)
        self.setup_button = self.button(setup, "Download setup files", self.setup_files)
        self.setup_button.pack(side="right")
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="both", expand=True, pady=12)
        apps = ttk.Frame(self.notebook, padding=12)
        restore = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(apps, text="Install apps")
        self.notebook.add(restore, text="Restore / downgrade")
        self._restore_tab(restore)
        self._apps_tab(apps)
        self.advanced_frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.advanced_frame, text="Advanced")
        self._advanced(self.advanced_frame)
        footer = ttk.Frame(body)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.activity, wraplength=710).pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(footer, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="right")
        self.progress = ttk.Progressbar(body, maximum=100)
        self.progress.pack(fill="x", pady=6)
        ttk.Label(body, textvariable=self.guidance, wraplength=900).pack(anchor="w")
        self.target.trace_add("write", lambda *unused: self.invalidate())
        self.custom.trace_add("write", lambda *unused: self.invalidate())
        self.nand.trace_add("write", lambda *unused: self.invalidate())
        self.sync_controls()
        self._pump_timer = self.root.after(50, self.pump)

    def button(self, parent, text, command):
        button = self.ttk.Button(parent, text=text, command=command)
        self.action_widgets.append(button)
        return button

    def _read_settings(self):
        try:
            value = json.loads(config_path().read_text())
            if isinstance(value.get("kit"), str) and Path(value["kit"]).is_dir():
                self.kit.set(value["kit"])
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save_settings(self):
        try:
            path = config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"kit": self.kit.get()}))
            temporary.replace(path)
        except OSError as exc:
            self.write("Could not save kit location: " + str(exc))

    def _restore_tab(self, frame):
        ttk = self.ttk
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="Restore to:").pack(side="left")
        self.version_menu = ttk.Combobox(row, values=VERSIONS, textvariable=self.target, state="readonly", width=10)
        self.version_menu.pack(side="left", padx=8)
        self.action_widgets.append(self.version_menu)
        self.button(row, "Download firmware", self.download).pack(side="left")
        self.button(row, "Choose custom IPSW…", self.pick_ipsw).pack(side="left", padx=8)
        self.button(row, "Use stock", lambda: self.custom.set("")).pack(side="left")
        ttk.Label(frame, textvariable=self.custom, wraplength=870).pack(anchor="w", pady=6)
        ttk.Label(frame, text="Restoring erases the phone. 1.x restore is experimental; activation is available only for 1.0.", wraplength=870).pack(anchor="w", pady=(0, 10))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.button(buttons, "Check restore", self.inspect).pack(side="left")
        self.erase_button = self.button(buttons, "Review warnings and erase…", self.confirm_restore)
        self.erase_button.pack(side="left", padx=8)
        self.erase_button.configure(state="disabled")
        self.plan_text = self.tk.Text(frame, height=6, wrap="word", state="disabled", relief="flat")
        self.plan_text.pack(fill="both", expand=True, pady=(12, 0))
        self.set_plan_text("Choose a version, download its firmware, then Check restore.\n\n"
                           "Auto works only when the phone's earliest supported version is known. "
                           "Serial numbers alone cannot establish compatibility.")

    def _apps_tab(self, frame):
        ttk = self.ttk
        ttk.Label(frame, text="For a phone already running iPhone OS 1.0. Choose apps below, then Install.", wraplength=870).pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.install_button = self.button(row, "Install selected apps…", lambda: self.app_action("install"))
        self.install_button.pack(side="left")
        self.probe_button = self.button(row, "Check phone", lambda: self.app_action("probe"))
        self.probe_button.pack(side="left", padx=6)
        self.launcher_button = self.button(row, "Show Launcher", lambda: self.app_action("launcher"))
        self.launcher_button.pack(side="left")
        self.app_buttons = [self.install_button, self.probe_button, self.launcher_button]
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True, pady=10)
        self.tree = ttk.Treeview(list_frame, columns=("selected", "name", "era"), show="headings", height=8)
        for key, title, width in (("selected", "Use", 45), ("name", "App", 210),
                                  ("era", "Authenticity", 390)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=key in ("name", "era"))
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<ButtonRelease-1>", self.toggle_app)
        self.tree.bind("<space>", self.toggle_app)
        options = ttk.Frame(frame)
        options.pack(fill="x")
        self.button(options, "All apps", self.all_apps).pack(side="left")
        self.button(options, "None", self.no_apps).pack(side="left", padx=6)
        activate = ttk.Checkbutton(options, text="Activate 1.0", variable=self.activate)
        activate.pack(side="left", padx=10)
        self.action_widgets.append(activate)
        ttk.Label(options, textvariable=self.app_summary).pack(side="right")
        ttk.Label(frame, text="Click a row to select it. Launcher opens the other installed apps. Authenticity is shown for each build.", wraplength=870).pack(anchor="w", pady=(8, 0))

    def _advanced(self, frame):
        ttk = self.ttk
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.button(row, "Use other kit…", self.pick_kit).pack(side="left")
        self.button(row, "Use downloaded setup", self.use_downloaded_setup).pack(side="left", padx=6)
        ttk.Label(row, textvariable=self.kit, wraplength=510).pack(side="left", padx=6)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Ramdisk MB:").pack(side="left")
        self.ramdisk_spin = ttk.Spinbox(row, from_=13, to=22, textvariable=self.ramdisk, width=4)
        self.ramdisk_spin.pack(side="left", padx=(6, 14))
        self.action_widgets.append(self.ramdisk_spin)
        ttk.Label(row, text="Known NAND ID:").pack(side="left")
        entry = ttk.Entry(row, textvariable=self.nand, width=16)
        entry.pack(side="left", padx=6)
        self.action_widgets.append(entry)
        self.button(row, "Check USB service", self.check_service).pack(side="left", padx=6)
        self.button(row, "USB setup…", self.usb_help).pack(side="left")
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.repair_button = self.button(row, "Repair iLiberty leftovers", lambda: self.app_action("repair"))
        self.repair_button.pack(side="left")
        self.button(row, "Exit recovery", lambda: self.app_action("kick")).pack(side="left", padx=6)
        self.log = self.tk.Text(frame, height=8, wrap="word", font=("TkFixedFont", 10), state="disabled")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

    def sync_controls(self):
        busy = self.runner.busy or self.closing
        for widget in self.action_widgets:
            widget.configure(state="disabled" if busy else "normal")
        self.version_menu.configure(state="disabled" if busy else "readonly")
        self.setup_button.configure(state="disabled" if busy or self.setup_ready else "normal")
        for widget in self.app_buttons + [self.repair_button]:
            widget.configure(state="normal" if self.setup_ready and not busy else "disabled")
        self.install_button.configure(state="normal" if self.setup_ready and self.apps and not busy else "disabled")
        ready = self.plan and not self.plan.get("transport", {}).get("blocking")
        self.erase_button.configure(state="normal" if ready and not busy else "disabled")

    def _destroy(self):
        if self._pump_timer is not None:
            self.root.after_cancel(self._pump_timer)
            self._pump_timer = None
        self.root.destroy()

    def set_plan_text(self, text):
        self.plan_text.configure(state="normal")
        self.plan_text.delete("1.0", "end")
        self.plan_text.insert("end", text)
        self.plan_text.configure(state="disabled")

    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 2500:
            self.log.delete("1.0", "1000.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def invalidate(self):
        self.plan = None
        self.erase_button.configure(state="disabled")

    def run(self, args, callback=None, timeout=None, use_kit=True):
        if self.runner.busy or self.closing:
            return False
        prefix = ["--kit", self.kit.get()] if use_kit and self.kit.get() else []
        if not self.runner.start(prefix + args, timeout):
            return False
        self.callback = callback
        self.sync_controls()
        self.stop_button.configure(state="normal")
        labels = {"setup": "Downloading setup files…", "doctor": "Checking setup…",
                  "restore-info": "Checking phone…", "list": "Loading apps…"}
        self.activity.set(labels.get(args[0], "Running " + args[0] + "…"))
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.write("$ iphone2gkit " + " ".join(args))
        return True

    def query(self, args, callback, timeout=75):
        from tkinter import messagebox
        def complete(result):
            if result["cancelled"]:
                return
            try:
                callback(parse_reply(result))
            except (ValueError, KeyError, TypeError) as exc:
                messagebox.showerror("Could not complete the check", str(exc), parent=self.root)
        return self.run(args, complete, timeout)

    def pump(self):
        if self._pump_timer is not None:
            self.root.after_cancel(self._pump_timer)
            self._pump_timer = None
        while True:
            try:
                event, data = self.runner.events.get_nowait()
            except queue.Empty:
                break
            if event == "line":
                if data.startswith("@@"):
                    try:
                        self.event(json.loads(data[2:]))
                    except (ValueError, TypeError):
                        self.write(data)
                else:
                    self.write(data)
            elif event == "finished":
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
                self.stop_button.configure(state="disabled")
                self.sync_controls()
                self.activity.set("Stopped. Details are in Advanced." if data["cancelled"] else
                                  "Completed" if data["code"] == 0 else "Did not complete. Details are in Advanced.")
                callback, self.callback = self.callback, None
                if callback and not self.closing:
                    callback(data)
                if self.closing:
                    self._destroy()
                    return
        self._pump_timer = self.root.after(50, self.pump)

    def event(self, event):
        pct = event.get("pct")
        if isinstance(pct, (int, float)):
            self.progress.stop()
            self.progress.configure(mode="determinate", value=max(0, min(100, pct)))
        if event.get("event") == "download":
            label = "setup files" if event.get("version") == "kit" else "firmware " + str(event.get("version", ""))
            self.activity.set("Downloading %s…" % label)
        elif event.get("message"):
            self.activity.set(event["message"])
            self.write(event["message"])
        elif event.get("event") == "need_recovery":
            self.activity.set("Put the phone in recovery mode.")
            self.guidance.set("Power off the iPhone. Hold Home while plugging in USB, then release it when the cable and iTunes picture appear.")
        elif event.get("event") == "recovery_ok":
            self.guidance.set("Keep the USB cable connected until the phone boots.")
        elif event.get("step"):
            self.activity.set(str(event.get("label", "")) + " " + event["step"])

    def refresh(self):
        if self.runner.busy or self.closing:
            return False
        self.invalidate()
        def checked(result):
            if result["cancelled"]:
                return
            try:
                self.apply_doctor(parse_doctor_reply(result))
            except ValueError as exc:
                self.setup_ready = False
                self.setup_status.set("Could not check setup. Try Refresh or Download setup files.")
                self.write(str(exc))
                self.sync_controls()
            self.refresh_phone()
        return self.run(["doctor", "--json"], checked, timeout=180)

    def apply_doctor(self, report):
        self.setup_ready = report.get("ok") is True and report.get("setup", {}).get("ready") is True
        if report.get("kit"):
            self.kit.set(report["kit"])
        if self.setup_ready:
            self.setup_status.set("Setup files verified — ready to install apps.")
            self._save_settings()
        else:
            self.setup_status.set("Download setup files to install apps and activate iPhone OS 1.0.")
            self.apps, self.selected = [], set()
            self.render_apps()
        if report.get("problems"):
            self.write("Setup check: " + "; ".join(map(str, report["problems"])))
        self.sync_controls()

    def setup_files(self):
        if self.runner.busy or self.closing or self.setup_ready:
            return False
        self.invalidate()
        def prepared(result):
            if result["cancelled"]:
                return
            try:
                report = checked_setup_reply(result)
            except ValueError as exc:
                from tkinter import messagebox
                self.setup_status.set("Setup did not finish. Try Download setup files again.")
                messagebox.showerror("Setup did not finish", str(exc), parent=self.root)
                return
            self.kit.set(report["kit"])
            self._save_settings()
            self.refresh()
        # Setup is host-only and always uses its managed download location.
        # An advanced manual override must not redirect the setup command.
        return self.run(["setup"], prepared, use_kit=False)

    def use_downloaded_setup(self):
        if self.runner.busy:
            return
        self.setup_ready = False
        self.kit.set("")
        self._save_settings()
        self.refresh()

    def refresh_phone(self):
        def complete(report):
            self.info = report
            identity = report.get("identity", {})
            transport = report.get("transport", {})
            mode = identity.get("mode")
            if identity.get("multiple"):
                self.status.set("Disconnect other Apple devices, then Refresh.")
            elif mode:
                self.status.set("iPhone connected — " + str(mode) + (" · iPhone OS " + identity["version"] if identity.get("version") else ""))
            else:
                self.status.set("Connect the original iPhone by USB, then Refresh.")
            verdict = report.get("recommendation", {})
            text = "Phone: %s\nSerial: %s\nFirmware: %s\n\n%s\n\n" % (
                identity.get("product_type") or "unconfirmed", identity.get("serial") or "unknown",
                identity.get("version") or "unknown", verdict.get("reason") or "Compatibility unknown.")
            text += "\n".join("%s: %s" % (f["version"], "available" if f.get("available") else "download / import needed")
                              for f in report.get("firmwares", []))
            self.write(text)
            self.write("USB: " + transport.get("message", "Not connected"))
            if self.setup_ready:
                self.load_apps()
        self.query(["restore-info"], complete)

    def inspect(self):
        self.invalidate()
        args = ["restore-plan", "--target", self.target.get()]
        if self.custom.get():
            args += ["--ipsw", self.custom.get()]
        if self.nand.get().strip():
            args += ["--nand-id", self.nand.get().strip()]
        def complete(plan):
            self.plan = plan
            text = "Restore to iPhone OS %s (%s).\n\n" % (plan["version"], plan["build"])
            self.write("Firmware: %s\nSHA-256: %s" % (plan["path"], plan["sha256"]))
            text += "\n\n".join(plan.get("warnings", []))
            text += "\n\n" + plan.get("recommendation", {}).get("reason", "")
            transport = plan.get("transport", {})
            if transport.get("blocking"):
                text += "\n\nUSB: " + transport.get("message", "Not ready.")
                self.erase_button.configure(state="disabled")
            else:
                self.erase_button.configure(state="normal")
            self.set_plan_text(text)
            self.sync_controls()
        self.query(args, complete, timeout=180)

    def download(self):
        from tkinter import messagebox
        version = self.target.get()
        if version == "auto" or self.custom.get():
            messagebox.showinfo("Choose stock firmware", "Choose a numbered stock version and Use stock before downloading. Custom IPSWs are imported from disk.", parent=self.root)
            return
        self.invalidate()
        args = ["fetch-firmware", "--target", version]
        if version == "1.0" and self.kit.get():
            args += ["--import-kit", self.kit.get()]
        self.run(args, lambda result: self.refresh() if result["code"] == 0 else None)

    def pick_ipsw(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="Choose an iPhone1,1 IPSW", filetypes=[("IPSW", "*.ipsw"), ("All files", "*")])
        if path:
            self.custom.set(path)

    def check_service(self):
        # service-unavailable has a useful JSON report but an intentionally nonzero
        # exit. Display that report without treating it as a successful query.
        def complete(result):
            for line in reversed(result["output"]):
                try:
                    report = json.loads(line)
                    self.status.set(report["message"])
                    self.set_plan_text(report.get("detail", "") + "\n" + (report.get("phone_query_error") or ""))
                    return
                except (ValueError, KeyError, TypeError):
                    continue
        self.invalidate()
        self.run(["transport-info", "--json", "--probe-service"], complete, timeout=30)

    def confirm_restore(self):
        from tkinter import messagebox
        if not self.plan or self.runner.busy:
            return
        plan = dict(self.plan)
        dialog = self.tk.Toplevel(self.root)
        dialog.title("Erase and restore the original iPhone")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = self.ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        self.ttk.Label(frame, text="Restore %s (%s)" % (plan["version"], plan["build"]), font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        self.ttk.Label(frame, text="\n\n".join(plan.get("warnings", [])), wraplength=650).pack(anchor="w", pady=12)
        definitions = [("erase", "Erase every photo, app, activation and setting on this phone."),
                       ("model", "I confirm the connected device is the original iPhone A1203 (2G).")]
        if plan.get("experimental"):
            definitions.append(("experimental", "I accept that native 1.x restore is experimental and can fail partway through."))
        if plan.get("version") == "1.0":
            text = "I accept stock 1.0's NAND and baseband compatibility limits."
            if plan.get("recommendation", {}).get("eligibility") != "eligible":
                text += " This NAND may not boot 1.0; I choose it anyway."
            definitions.append(("one_zero", text))
        if plan.get("custom"):
            definitions.append(("custom", "I trust this custom IPSW and accept its bootloader / radio / activation changes."))
        acknowledgements = {key: self.tk.BooleanVar(value=False) for key, _ in definitions}
        for key, text in definitions:
            self.ttk.Checkbutton(frame, text=text, variable=acknowledgements[key]).pack(anchor="w", pady=3)
        def erase():
            try:
                cache = Path(os.environ.get("IOS1KIT_BUILD", str(Path.home() / ".cache/iPhone2Gkit/build")))
                args = restore_arguments(plan, {key: value.get() for key, value in acknowledgements.items()}, cache)
                if self.nand.get().strip():
                    args += ["--nand-id", self.nand.get().strip()]
            except ValueError as exc:
                messagebox.showerror("Consent required", str(exc), parent=dialog)
                return
            if not messagebox.askyesno("Erase now?", "Permanently erase the connected iPhone and restore %s?" % plan["version"], parent=dialog, default="no"):
                return
            dialog.destroy()
            self.invalidate()
            self.run(args)
        buttons = self.ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        self.ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        self.ttk.Button(buttons, text="Erase and restore", command=erase).pack(side="right", padx=8)

    def pick_kit(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self.root, title="Kit folder containing iLiberty-portable and ios1-apps")
        if not path:
            return
        self.setup_ready = False
        self.setup_status.set("Checking the selected setup folder…")
        self.apps, self.selected = [], set()
        self.render_apps()
        self.kit.set(path)
        self.invalidate()
        def checked(report):
            self.apply_doctor(report)
            if not report.get("ok"):
                from tkinter import messagebox
                messagebox.showerror("Kit needs attention", "\n".join(report.get("problems", [])), parent=self.root)
                return
            self.load_apps()
        def prepared(result):
            if result["code"] == 0:
                self.query(["doctor", "--json"], checked, timeout=180)
        # Public bundles obtain the signed kernel from the verified IPSW and
        # import the converter from this kit before doctor verifies the assets.
        self.run(["fetch-firmware", "--target", "1.0", "--import-kit", self.kit.get()], prepared)

    def load_apps(self):
        def complete(apps):
            previous = {app["key"] for app in self.apps}
            self.apps = apps
            self.selected = (self.selected & {app["key"] for app in apps} if previous else
                             {app["key"] for app in apps if app.get("in_apps")})
            self.render_apps()
            self.sync_controls()
        self.query(["list", "--json"], complete, timeout=90)

    def render_apps(self):
        self.tree.delete(*self.tree.get_children())
        for app in self.apps:
            self.tree.insert("", "end", iid=app["key"], values=("✓" if app["key"] in self.selected else "",
                             app["name"], app.get("era", {}).get("label", "Build unverified")))
        self.app_summary.set("%d apps selected" % len(self.selected) if self.apps else "Your apps will appear after setup.")

    def toggle_app(self, event):
        if self.runner.busy:
            return
        item = self.tree.identify_row(event.y) if hasattr(event, "y") and event.type != "2" else self.tree.focus()
        if item:
            self.selected.symmetric_difference_update({item})
            self.tree.set(item, "selected", "✓" if item in self.selected else "")
            self.app_summary.set("%d apps selected" % len(self.selected))

    def all_apps(self):
        self.selected = {app["key"] for app in self.apps if app.get("in_apps")}
        self.render_apps()

    def no_apps(self):
        self.selected.clear()
        self.render_apps()

    def app_action(self, action):
        from tkinter import messagebox
        if self.runner.busy:
            return
        if action != "kick" and not self.setup_ready:
            messagebox.showinfo("Download setup files first", "Click Download setup files above. The app prepares everything it needs on this computer without changing the phone.", parent=self.root)
            return
        if not messagebox.askyesno("Run %s?" % action, "This action requires an original iPhone already restored to 1.0. "
                                  "Keep USB connected and follow the instructions shown below. Continue?", parent=self.root, default="no"):
            return
        args = [action]
        if action != "kick":
            try:
                size = int(self.ramdisk.get())
                if size not in range(13, 23):
                    raise ValueError
            except (ValueError, self.tk.TclError):
                messagebox.showerror("Ramdisk size", "Choose a size between 13 and 22 MB.", parent=self.root)
                return
            args += ["-y", "--ramdisk-mb", str(size)]
        if action == "install":
            args += ["--apps", ",".join(app["key"] for app in self.apps if app["key"] in self.selected) or "none"]
            if not self.activate.get():
                args.append("--no-activate")
        self.invalidate()
        self.guidance.set("Keep the USB cable connected. Instructions appear here when needed.")
        self.run(args)

    def usb_help(self):
        from tkinter import messagebox
        bundle = self.resources.parent
        messagebox.showinfo("Linux USB access", "Run these commands yourself in Terminal only if needed:\n\n"
                            "1. USB permission rule (allows the active desktop session only):\n"
                            "sudo sh '%s/setup-usb.sh'\n\n"
                            "2. If there is no existing usbmuxd service, run the bundled daemon in a separate Terminal:\n"
                            "sudo '%s/resources/bin/usbmuxd' --foreground --user root\n\n"
                            "Keep that Terminal open. Do not start another daemon if a system usbmuxd is already running. "
                            "The app never replaces or stops your system's USB service." % (bundle, bundle), parent=self.root)

    def stop(self):
        self.stop_button.configure(state="disabled")
        self.activity.set("Stopping; waiting for the upload / restore process to exit…")
        self.runner.cancel()

    def close(self):
        from tkinter import messagebox
        if self.runner.busy:
            if not messagebox.askyesno("Stop the current operation?", "Stopping a restore may leave a partially restored phone. Stop and close?", parent=self.root, default="no"):
                return
            self.closing = True
            self.stop()
        else:
            self._destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="iPhone2Gkit Linux GUI")
    parser.add_argument("--resources", type=Path, default=Path(__file__).resolve().parent / "resources")
    parser.add_argument("--smoke-test", action="store_true", help="create/render a window and exit, with no phone actions")
    parser.add_argument("--screenshot", type=Path, help="test-only: save rendered window via ImageMagick")
    args = parser.parse_args(argv)
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as exc:
        print("Cannot open the desktop GUI: %s. Run on an X11/Wayland desktop, or pass a CLI command." % exc, file=sys.stderr)
        return 2
    app = Application(root, args.resources)
    if args.smoke_test:
        root.update_idletasks()
        root.update()
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            # This QA-only flag is never used in normal GUI operation.
            subprocess.run(["/usr/bin/import", "-window", str(root.winfo_id()), str(args.screenshot)], check=True, timeout=15)
        print("GUI smoke test passed: restore and app tabs rendered; no phone actions.", flush=True)
        root.after(100, app._destroy)
    else:
        root.after(150, app.refresh)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
