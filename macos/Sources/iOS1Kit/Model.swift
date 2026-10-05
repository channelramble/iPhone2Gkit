import AppKit
import Foundation
import SwiftUI

enum Job: String {
    case probe, install, launcher, repair, kick, usbtest, restore, download
    var title: String {
        switch self {
        case .probe: return "Check phone"
        case .install: return "Install"
        case .launcher: return "Show Launcher"
        case .repair: return "Repair"
        case .kick: return "Exit recovery mode"
        case .usbtest: return "Test USB upload"
        case .restore: return "Restore iPhone"
        case .download: return "Download firmware"
        }
    }
}

enum Outcome: Equatable {
    case success(String)
    case failure(String)
}

@MainActor
final class Model: ObservableObject {
    // setup
    /// Kit in use (reported by the engine). The app carries its own copy; this is only
    /// a different folder if the user picked one with "Choose Kit…".
    @Published var kitPath: String?
    private var userKit: String? = UserDefaults.standard.string(forKey: "userKitPath")
    @Published var irecovery: String?
    @Published var problems: [String] = []
    @Published var checkedSetup = false

    // phone
    @Published var phone: String?            // "normal" | "recovery" | "dfu" | nil

    // choices
    @Published var apps: [AppItem] = []
    @Published var selected: Set<String> = []
    @Published var activate = true
    @Published var ramdiskMB: Double = 14
    @Published var restoreInfo: RestoreInfo?
    @Published var restorePlan: RestorePlan?
    @Published var restoreTarget = "3.1.3"
    @Published var customIPSW: String?
    @Published var nandID = ""
    @Published var inspectingRestore = false
    @Published var restoreError: String?
    private var authorizedRestore: RestorePlan?
    private var downloadTarget = "3.1.3"

    // running job
    @Published var job: Job?
    @Published var headline = "Ready"
    @Published var detail = ""
    @Published var progress: Double?         // 0...1 during upload
    @Published var needRecovery = false
    @Published var outcome: Outcome?
    @Published var log = ""

    private var process: Process?
    private var pending = ""
    private var uploading = false
    private var pollTimer: Timer?

    var setupOK: Bool { checkedSetup && problems.isEmpty && kitPath != nil }
    var busy: Bool { job != nil }
    var selectedKB: Int { apps.filter { selected.contains($0.key) }.reduce(0) { $0 + $1.kb } }

    func start() {
        guard pollTimer == nil else { return }
        Task { await refreshSetup() }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.pollPhone() }
        }
    }

    private var kitArgs: [String] { userKit.map { ["--kit", $0] } ?? [] }

    // MARK: setup

    func refreshSetup() async {
        let (_, data) = await Engine.capture(kitArgs + ["doctor", "--json"])
        guard let report = try? JSONDecoder().decode(DoctorReport.self, from: data) else {
            problems = ["The built-in engine did not start. Re-download iPhone2Gkit.app (it may be damaged)."]
            checkedSetup = true
            return
        }
        irecovery = report.irecovery
        problems = report.problems
        kitPath = report.kit
        phone = report.phone
        checkedSetup = true
        if report.kit != nil && apps.isEmpty { await loadApps() }
        await refreshRestoreInfo()
    }

    func loadApps() async {
        let (_, data) = await Engine.capture(kitArgs + ["list", "--json"])
        if let items = try? JSONDecoder().decode([AppItem].self, from: data) {
            apps = items
            selected = Set(items.filter { $0.in_apps }.map { $0.key })
        }
    }

    func refreshRestoreInfo() async {
        guard !busy else { return }
        let (_, data) = await Engine.capture(kitArgs + ["restore-info"])
        restoreInfo = try? JSONDecoder().decode(RestoreInfo.self, from: data)
    }

    func chooseIPSW() {
        let p = NSOpenPanel()
        p.canChooseFiles = true
        p.canChooseDirectories = false
        p.allowsMultipleSelection = false
        p.allowedFileTypes = ["ipsw"]
        p.message = "Choose a stock or custom IPSW for the original iPhone (iPhone1,1)."
        if p.runModal() == .OK, let path = p.url?.path {
            customIPSW = path
            restorePlan = nil
            restoreError = nil
        }
    }

    private var restoreSelectionArgs: [String] {
        var a = ["--target", restoreTarget]
        if let customIPSW { a += ["--ipsw", customIPSW] }
        if !nandID.trimmingCharacters(in: .whitespaces).isEmpty { a += ["--nand-id", nandID] }
        return a
    }

    func inspectRestore() async {
        guard !busy && !inspectingRestore else { return }
        inspectingRestore = true
        restoreError = nil
        restorePlan = nil
        defer { inspectingRestore = false }
        let (rc, data) = await Engine.capture(kitArgs + ["restore-plan"] + restoreSelectionArgs)
        if rc == 0, let plan = try? JSONDecoder().decode(RestorePlan.self, from: data) {
            restorePlan = plan
        } else {
            restoreError = "The restore plan could not be prepared. Check the firmware and device, then try again. "
                + String(decoding: data, as: UTF8.self)
        }
    }

    /// Only the explicit erasure/identity confirmation sheet calls this.
    func confirmRestore(_ plan: RestorePlan) {
        authorizedRestore = plan
        run(.restore)
    }

    func downloadFirmware(_ target: String? = nil) {
        let version = target ?? restoreTarget
        guard ["1.0", "1.1.1", "1.1.3", "3.1.3"].contains(version), !busy else { return }
        downloadTarget = version
        run(.download)
    }

    /// Link /usr/local/bin/ios1kit to the command inside this app (asks for an admin password).
    func installCommandLineTool() {
        let target = Bundle.main.resourceURL!.appendingPathComponent("bin/iphone2gkit").path
        let q = { (s: String) in "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }
        let shell = "mkdir -p /usr/local/bin && ln -sf \(q(target)) /usr/local/bin/iphone2gkit && ln -sf \(q(target)) /usr/local/bin/ios1kit"
        let script = "do shell script \"\(shell.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\""))\" with administrator privileges"
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
        let alert = NSAlert()
        if err == nil {
            alert.messageText = "Installed “iphone2gkit”"
            alert.informativeText = "Open a new Terminal window and run:\n\n  iphone2gkit doctor\n  iphone2gkit restore-info\n\nThe old ios1kit command also works. Keep iPhone2Gkit.app where it is (or install the commands again after moving it)."
            log += "\nInstalled /usr/local/bin/iphone2gkit and ios1kit -> \(target)\n"
        } else {
            alert.messageText = "Not installed"
            alert.informativeText = "You can still run it directly:\n\n\(target)"
        }
        alert.runModal()
    }

    var usingBuiltInKit: Bool { kitPath?.contains("/Contents/Resources/kit-assets") ?? false }
    var hasBuiltInKit: Bool {
        guard let resources = Bundle.main.resourceURL else { return false }
        return FileManager.default.fileExists(atPath: resources.appendingPathComponent("kit-assets").path)
    }

    func useBuiltInKit() {
        userKit = nil
        UserDefaults.standard.removeObject(forKey: "userKitPath")
        apps = []
        Task { await refreshSetup() }
    }

    func chooseKit() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = "Choose the iphone-2g-ios-1-kit-full folder (it contains iLiberty-portable and ios1-apps)."
        panel.prompt = "Use This Folder"
        if panel.runModal() == .OK, let url = panel.url {
            userKit = url.path
            UserDefaults.standard.set(url.path, forKey: "userKitPath")
            apps = []
            Task { await refreshSetup() }
        }
    }

    private func pollPhone() async {
        let (_, data) = await Engine.capture(["status", "--json"])
        if let s = try? JSONDecoder().decode(PhoneStatus.self, from: data) {
            phone = s.mode
            if needRecovery && s.mode == "recovery" { needRecovery = false }
        }
    }

    // MARK: selection presets

    func selectApps() { selected = Set(apps.filter { $0.in_apps }.map { $0.key }) }
    func selectNone() { selected = [] }
    func toggle(_ key: String) {
        if selected.contains(key) { selected.remove(key) } else { selected.insert(key) }
        // SummerBoard and its undo are mutually exclusive.
        if key == "summerboard" && selected.contains(key) { selected.remove("undosummerboard") }
        if key == "undosummerboard" && selected.contains(key) { selected.remove("summerboard") }
    }

    // MARK: jobs

    func run(_ which: Job) {
        guard !busy else { return }
        if which == .restore && authorizedRestore == nil { return }
        var args = kitArgs
        let mb = String(format: "%.0f", ramdiskMB)
        switch which {
        case .probe:
            args += ["probe", "-y", "--out", Engine.buildDir.path, "--ramdisk-mb", mb]
        case .repair:
            args += ["repair", "-y", "--out", Engine.buildDir.path, "--ramdisk-mb", mb]
        case .launcher:
            args += ["launcher", "-y", "--out", Engine.buildDir.path, "--ramdisk-mb", mb]
        case .install:
            let keys = apps.map { $0.key }.filter { selected.contains($0) }
            args += ["install", "-y", "--out", Engine.buildDir.path, "--ramdisk-mb", mb,
                     "--apps", keys.isEmpty ? "none" : keys.joined(separator: ",")]
            if !activate { args.append("--no-activate") }
        case .kick:
            args += ["kick"]
        case .usbtest:
            args += ["usbtest", "--out", Engine.buildDir.path]
        case .download:
            args += ["fetch-firmware", "--target", downloadTarget]
            if downloadTarget == "1.0", let kitPath { args += ["--import-kit", kitPath] }
        case .restore:
            guard let plan = authorizedRestore else { return }
            // Freeze the inspected selection, rather than using controls that may have changed.
            args += ["restore", "--ipsw", plan.path, "-y", "--confirm-original-iphone",
                     "--expected-sha256", plan.sha256, "--out", Engine.buildDir.path]
            if let serial = plan.identity.serial { args += ["--expected-serial", serial] }
            if plan.experimental { args.append("--allow-experimental") }
            if plan.version == "1.0" { args.append("--allow-10") }
            if plan.custom { args.append("--allow-custom") }
            authorizedRestore = nil
        }
        try? FileManager.default.createDirectory(at: Engine.buildDir, withIntermediateDirectories: true)

        job = which
        outcome = nil
        progress = nil
        needRecovery = false
        headline = which == .download ? "Downloading and verifying firmware…" : (which == .kick ? "Sending reboot…" : (which == .restore ? "Preparing restore…" : "Building ramdisk…"))
        detail = ""
        log += "\n$ iphone2gkit " + args.joined(separator: " ") + "\n"

        let p = Engine.makeProcess(args)
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty else { return }
            let text = String(decoding: data, as: UTF8.self)
            Task { @MainActor in self?.consume(text) }
        }
        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            Task { @MainActor in
                pipe.fileHandleForReading.readabilityHandler = nil
                self?.finished(which, status: status)
            }
        }
        do {
            try p.run()
            process = p
        } catch {
            job = nil
            outcome = .failure("Could not start the engine: \(error.localizedDescription)")
        }
    }

    /// Ctrl-C equivalent. After `bootx` the phone works on its own; this only stops watching.
    func cancel() {
        process?.interrupt()
    }

    private func consume(_ text: String) {
        pending += text
        // irecovery redraws its progress bar with \r, so split on both.
        while let r = pending.rangeOfCharacter(from: CharacterSet(charactersIn: "\r\n")) {
            let line = String(pending[..<r.lowerBound])
            let sep = pending[r]
            pending = String(pending[r.upperBound...])
            handle(line: line, isProgressRedraw: sep == "\r")
        }
    }

    private func handle(line: String, isProgressRedraw: Bool) {
        if line.hasPrefix("@@") {
            if let data = line.dropFirst(2).data(using: .utf8),
               let ev = try? JSONDecoder().decode(EngineEvent.self, from: data) {
                apply(ev)
            }
            return
        }
        if uploading, let pct = Self.percent(in: line) {
            progress = min(1, pct / 100)
            if isProgressRedraw { return }    // don't flood the log with redraws
        }
        if !line.isEmpty || !isProgressRedraw {
            log += line + "\n"
            if log.count > 400_000 { log = String(log.suffix(300_000)) }
        }
    }

    private static func percent(in line: String) -> Double? {
        guard let r = line.range(of: #"([0-9]{1,3}(\.[0-9]+)?)\s*%"#, options: .regularExpression) else { return nil }
        return Double(line[r].replacingOccurrences(of: "%", with: "").trimmingCharacters(in: .whitespaces))
    }

    private func apply(_ ev: EngineEvent) {
        switch ev.event {
        case "download":
            headline = "Downloading and verifying firmware…"
            progress = min(1, max(0, (ev.pct ?? 0) / 100))
            detail = "Saved locally. The phone is not contacted by this download."
        case "restore_phase":
            headline = ev.step == "restoring" ? "Restoring iPhone…" : "Preparing restore…"
            detail = ev.message ?? "Keep the phone connected."
            progress = nil
        case "restore_progress":
            progress = min(1, max(0, (ev.pct ?? 0) / 100))
        case "restore_done":
            outcome = .success(ev.message ?? "Restore backend completed. Check the phone boots.")
        case "restore_failed":
            outcome = .failure(ev.message ?? "Restore did not complete; read the log.")
        case "phase":
            let label = ev.label ?? ""
            uploading = ev.step == "uploading"
            switch ev.step {
            case "building": headline = "Building ramdisk…"; detail = ""
            case "waiting": headline = "Waiting for recovery mode (\(label))"; progress = nil
            case "uploading": headline = "Uploading to the phone (\(label))"; progress = 0
                detail = "Ramdisk, then kernel, in 16 KB chunks. Don't unplug."
            case "booting": headline = "Starting the ramdisk (\(label))"; progress = nil; detail = ""
            case "running": headline = "Working on the phone (\(label))"
                detail = "Watch the phone's screen. This can take several minutes. Don't unplug."
            default: break
            }
        case "plan":
            if let n = ev.batches, n > 1 {
                detail = "\(n) batches: after each one the phone boots, then you put it back in recovery mode."
            }
        case "need_recovery": needRecovery = true
        case "recovery_ok", "recovery_timeout": needRecovery = false
        case "batch_ok":
            if let i = ev.index, let n = ev.total, i < n {
                headline = "Batch \(i) of \(n) done"
                detail = "Next: put the phone back in recovery mode."
            }
        case "done":
            uploading = false
            if ev.ok == true {
                outcome = .success(ev.mode == "probe"
                    ? "Check finished. The ramdisk booted and showed its readings on the phone's screen (firmware, free space, leftovers). The phone is booting iPhone OS unchanged."
                    : "Done! The phone is booting iPhone OS.")
            } else {
                outcome = .failure("The phone came back in recovery mode at \(ev.label ?? "this step"). Read the failed step on its screen. Steps after it were not run. “Exit Recovery Mode” boots the phone as it is.")
            }
        case "error":
            outcome = .failure(ev.message ?? "Error")
        case "upload_failed":
            uploading = false
            log += String(format: "  (upload stopped at %.1f%% of %.1f MB)\n", ev.pct ?? 0, ev.mb ?? 0)
        case "usbtest_result":
            uploading = false
            let ok = ev.ok_mb ?? 0
            if ev.failed_mb == nil {
                outcome = .success("USB test passed: uploads up to \(ok) MB work. The default settings are fine.")
            } else if (ev.ramdisk_mb ?? 0) < (ev.min_ramdisk_mb ?? 12) {
                // Below what any install needs: don't recommend settings above the measured limit.
                outcome = .failure("Largest good upload: \(ok) MB. That is too small: installing needs about \(String(format: "%.1f", 9.6 + Double(ev.min_ramdisk_mb ?? 12))) MB (Check/Repair need 19.6 MB). Try another USB port or cable without a hub, replug the phone in recovery mode and test again.")
            } else {
                let rd = min(22, ev.ramdisk_mb!)
                ramdiskMB = Double(rd)
                outcome = .success("Largest good upload: \(ok) MB (\(ev.failed_mb!) MB failed). Ramdisk size set to \(rd) MB; apps will be split into batches if needed. Unplug and replug the phone in recovery mode before the next step.")
            }
        default: break
        }
    }

    private func finished(_ which: Job, status: Int32) {
        if !pending.isEmpty { handle(line: pending, isProgressRedraw: false); pending = "" }
        process = nil
        job = nil
        uploading = false
        progress = nil
        needRecovery = false
        if outcome == nil {
            switch (which, status) {
            case (.kick, 0): outcome = .success("Reboot sent. iPhone OS should start in about 40 seconds.")
            case (.download, 0): outcome = .success("Firmware downloaded and checksum verified. You can now inspect a restore plan.")
            case (_, 0): outcome = .success("Finished.")
            case (.restore, 130): outcome = .failure("Restore cancelled. The phone may be partially restored. Read restore.log and use stock 3.1.3 to recover.")
            case (_, 130): outcome = .failure("Cancelled. If the ramdisk was already running, let the phone finish on its own.")
            default: outcome = .failure("Stopped (exit \(status)). See the log below.")
            }
        }
        headline = "Ready"
        detail = ""
        if which == .restore { Task { await refreshRestoreInfo() } }
        if which == .download { Task { await refreshSetup() } }
    }
}
