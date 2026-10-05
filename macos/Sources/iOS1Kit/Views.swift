import SwiftUI

@main
struct iPhone2GkitApp: App {
    @StateObject private var model = Model()

    var body: some Scene {
        WindowGroup("iPhone2Gkit") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 940, minHeight: 700)
                .onAppear { model.start(); Snapshot.scheduleIfRequested(model) }
        }
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Install Command-Line Tool…") { model.installCommandLineTool() }
                    .disabled(model.busy)
            }
            CommandGroup(replacing: .appTermination) {
                Button("Quit iPhone2Gkit") { NSApplication.shared.terminate(nil) }
                    .keyboardShortcut("q", modifiers: .command)
                    .disabled(model.busy)
            }
        }
        .windowResizability(.contentMinSize)
        .defaultSize(width: 1080, height: 820)
    }
}

struct ContentView: View {
    @EnvironmentObject var m: Model
    @State private var confirm: Job?
    @State private var selectedTab = ProcessInfo.processInfo.environment["IOS1KIT_SNAPSHOT_TAB"] ?? "apps"

    var body: some View {
        VStack(spacing: 0) {
            HSplitView {
                sidebar.frame(minWidth: 300, idealWidth: 320, maxWidth: 380)
                TabView(selection: $selectedTab) {
                    AppPicker().tabItem { Label("1.0 Apps", systemImage: "square.grid.2x2") }.tag("apps")
                    RestoreView().tabItem { Label("Restore", systemImage: "arrow.counterclockwise") }.tag("restore")
                }.frame(minWidth: 520)
            }
            .frame(maxHeight: .infinity)
            .layoutPriority(1)
            Divider()
            StatusPane()
                .frame(height: 210)
        }
        .confirmationDialog(confirmTitle, isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }),
                            presenting: confirm) { job in
            Button(job.title) { m.run(job) }
            Button("Cancel", role: .cancel) {}
        } message: { job in
            Text(confirmMessage(job))
        }
    }

    var confirmTitle: String { confirm.map { "\($0.title)?" } ?? "" }

    func confirmMessage(_ job: Job) -> String {
        switch job {
        case .install:
            let n = m.selected.count
            return "\(m.activate ? "Activation and " : "")\(n) app\(n == 1 ? "" : "s") (about \(String(format: "%.1f", Double(m.selectedKB) / 1024)) MB) will be installed. Keep the phone plugged in until it boots iPhone OS again."
        case .probe:
            return "Boots a ramdisk that only reads the phone and shows firmware, free space and leftovers on its screen. Nothing on the phone's flash is changed."
        case .repair:
            return "Restores SpringBoard/CommCenter if iLiberty left them parked, removes iLiberty leftovers and makes the system partition writable. No apps are installed."
        case .launcher:
            return "Installs Launcher and Finder and makes them visible on the home screen. Launcher opens the other installed apps. Your current layout is backed up; activation is kept."
        case .kick:
            return "Clears the ramdisk boot-args and boots iPhone OS from the phone."
        case .usbtest:
            return "Uploads blank test files of growing size (2–32 MB) to the phone's memory to find the largest upload it accepts. Nothing is booted and nothing on the phone is changed. Takes several minutes."
        case .restore:
            return "Use the Restore tab to inspect firmware and confirm erasure."
        case .download:
            return "Downloads and verifies firmware on the Mac. This does not contact or erase the phone."
        }
    }

    var sidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                PhoneCard()
                SetupCard()
                GroupBox(label: Label("Actions", systemImage: "bolt.fill")) {
                    VStack(alignment: .leading, spacing: 10) {
                        ActionButton(title: "1. Check phone", subtitle: "Read-only test boot. Do this first.",
                                     icon: "stethoscope", job: .probe, confirm: $confirm)
                        ActionButton(title: "2. Install", subtitle: m.activate ? "Activation + selected apps" : "Selected apps",
                                     icon: "square.and.arrow.down.fill", job: .install, confirm: $confirm, prominent: true,
                                     disabled: m.selected.isEmpty && !m.activate)
                        Divider()
                        ActionButton(title: "Show Launcher", subtitle: "Open apps that don't fit on the home screen.",
                                     icon: "square.grid.2x2", job: .launcher, confirm: $confirm)
                        ActionButton(title: "Repair iLiberty leftovers", subtitle: "Black screen after iLiberty? Start here.",
                                     icon: "wrench.and.screwdriver", job: .repair, confirm: $confirm)
                        ActionButton(title: "Test USB upload", subtitle: "Upload failed? Measures the phone's limit.",
                                     icon: "cable.connector", job: .usbtest, confirm: $confirm)
                        ActionButton(title: "Exit recovery mode", subtitle: "Boot iPhone OS as it is",
                                     icon: "power", job: .kick, confirm: $confirm,
                                     disabled: m.phone != "recovery")
                    }
                    .padding(.vertical, 4)
                }
                Spacer(minLength: 0)
            }
            .padding(14)
        }
    }
}

struct PhoneCard: View {
    @EnvironmentObject var m: Model

    var body: some View {
        GroupBox(label: Label("iPhone", systemImage: "iphone")) {
            HStack(spacing: 10) {
                Circle().fill(color).frame(width: 14, height: 14)
                    .shadow(color: color.opacity(0.6), radius: 4)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline)
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
            .padding(.vertical, 4)
        }
    }

    var color: Color {
        switch m.phone {
        case "normal": return .green
        case "recovery": return .orange
        case "dfu": return .red
        default: return .gray
        }
    }
    var title: String {
        switch m.phone {
        case "normal": return "Connected – iPhone OS running"
        case "recovery": return "Recovery mode"
        case "dfu": return "DFU mode"
        default: return m.busy ? "Not visible (ramdisk running?)" : "Not connected"
        }
    }
    var subtitle: String {
        switch m.phone {
        case "normal": return "Actions will ask you to switch it to recovery mode."
        case "recovery": return "Ready for Check / Install / Repair."
        case "dfu": return "Use the Restore tab. The app verifies the model before starting."
        default: return m.busy ? "Normal while the ramdisk works: the phone leaves USB." : "Plug in the iPhone 2G with its USB cable."
        }
    }
}

struct SetupCard: View {
    @EnvironmentObject var m: Model

    var body: some View {
        GroupBox(label: Label("Setup", systemImage: "gearshape")) {
            VStack(alignment: .leading, spacing: 6) {
                if !m.checkedSetup {
                    HStack { ProgressView().controlSize(.small); Text("Checking…") }
                } else {
                    row(ok: m.kitPath != nil && !m.problems.contains { $0.contains("kit") || $0.contains("checksum") || $0.contains("missing") },
                        text: m.kitPath == nil ? "Kit files not found"
                            : m.usingBuiltInKit ? "Kit: built in (verified)"
                            : "Kit: " + (m.kitPath! as NSString).lastPathComponent)
                    row(ok: m.irecovery != nil, text: m.irecovery.map { "USB: irecovery \($0), built in" } ?? "USB tool missing from the app")
                    if m.kitPath == nil {
                        Text("Restore firmware is downloaded in the Restore tab. The 1.0 app installer needs your historical kit folder.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else if !m.setupOK {
                        Button("Prepare 1.0 Resources") { m.downloadFirmware("1.0") }
                            .disabled(m.busy)
                    }
                    ForEach(m.problems.filter { !$0.contains("irecovery") && $0 != "kit folder not found" }, id: \.self) { p in
                        Label(p, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.caption)
                    }
                    HStack {
                        Button("Use Other Kit…") { m.chooseKit() }
                        if !m.usingBuiltInKit && m.kitPath != nil {
                            Button("Use Built-in") { m.useBuiltInKit() }
                        }
                        Button("Re-check") { Task { await m.refreshSetup() } }
                        Button("Terminal Command…") { m.installCommandLineTool() }
                            .help("Installs `iphone2gkit` and the `ios1kit` alias in /usr/local/bin")
                    }
                    .controlSize(.small)
                    .disabled(m.busy)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    func row(ok: Bool, text: String) -> some View {
        Label(text, systemImage: ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
            .foregroundStyle(ok ? .green : .red)
            .lineLimit(1).truncationMode(.middle)
    }
}

struct ActionButton: View {
    @EnvironmentObject var m: Model
    let title: String
    let subtitle: String
    let icon: String
    let job: Job
    @Binding var confirm: Job?
    var prominent = false
    var disabled = false

    var body: some View {
        Button { confirm = job } label: {
            HStack(spacing: 10) {
                Image(systemName: icon).frame(width: 22)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).fontWeight(.semibold)
                    Text(subtitle).font(.caption).opacity(0.8)
                }
                Spacer()
                if m.job == job { ProgressView().controlSize(.small) }
            }
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(CardButtonStyle(prominent: prominent))
        .disabled(m.busy || !m.setupOK || disabled)
    }
}

/// Two-line action buttons; the stock bordered style clips multi-line labels.
struct CardButtonStyle: ButtonStyle {
    var prominent: Bool
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .foregroundStyle(prominent ? Color.white : Color.primary)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(prominent ? Color.accentColor : Color(nsColor: .controlBackgroundColor))
            )
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(prominent ? 0 : 0.3)))
            .opacity(enabled ? (configuration.isPressed ? 0.75 : 1) : 0.45)
            .contentShape(Rectangle())
    }
}

struct AppPicker: View {
    @EnvironmentObject var m: Model
    private let order = ["Utility", "Game", "Reading", "Network", "Advanced", "Tweak"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Apps to install").font(.title3.bold())
                Spacer()
                Button("All apps") { m.selectApps() }
                Button("None") { m.selectNone() }
            }
            .padding([.horizontal, .top], 14)
            .padding(.bottom, 8)
            .disabled(m.busy)

            HStack(spacing: 16) {
                Toggle("Activate iPhone OS 1.0 (patched lockdownd)", isOn: $m.activate)
                Spacer()
                Text(sizeText).font(.caption).foregroundStyle(m.selectedKB > 22 * 1024 ? .orange : .secondary)
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 8)
            .disabled(m.busy)

            Text("Era labels describe the bundled app build. Catalog dates alone are unverified; iPhone2Gkit and all install wrappers are modern.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 14).padding(.bottom, 8)

            Divider()
            if m.apps.isEmpty {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "folder.badge.questionmark").font(.largeTitle).foregroundStyle(.secondary)
                    Text(m.checkedSetup ? "Choose the kit folder to see the apps." : "Loading…").foregroundStyle(.secondary)
                    Spacer()
                }.frame(maxWidth: .infinity)
            } else {
                List {
                    ForEach(order, id: \.self) { cat in
                        let items = m.apps.filter { $0.cat == cat }
                        if !items.isEmpty {
                            Section(cat == "Tweak" ? "Tweaks (change the home screen; optional)" : cat) {
                                ForEach(items) { app in AppRow(app: app) }
                            }
                        }
                    }
                }
                .listStyle(.inset)
                .disabled(m.busy)
            }

            DisclosureGroup("Advanced") {
                HStack {
                    Text("Ramdisk size")
                    Slider(value: $m.ramdiskMB, in: 10...22, step: 1).frame(maxWidth: 220)
                    Text("\(Int(m.ramdiskMB)) MB").monospacedDigit().frame(width: 50)
                    Text("Largest ramdisk per boot. Start with 14 MB; apps install in batches. Larger sizes are experimental.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(14)
            .disabled(m.busy)
        }
    }

    var sizeText: String {
        let mb = Double(m.selectedKB) / 1024
        let size = m.selectedKB < 1024 ? "\(m.selectedKB) KB" : String(format: "%.1f MB", mb)
        return "\(m.selected.count) selected · \(size) of ~24 MB free" + (mb > 22 ? " (tight)" : "")
    }
}

struct AppRow: View {
    @EnvironmentObject var m: Model
    let app: AppItem

    var body: some View {
        Toggle(isOn: Binding(get: { m.selected.contains(app.key) }, set: { _ in m.toggle(app.key) })) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(app.name).fontWeight(.medium)
                    Text(app.note.isEmpty ? app.desc : app.note)
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    Text(app.era?.label ?? "Era unverified")
                        .font(.caption)
                        .foregroundStyle(app.era?.status == "period2007" ? Color.green : Color.orange)
                        .help((app.era?.detail ?? "No verified date for this build.")
                              + "\n" + (app.era?.sources.joined(separator: "\n") ?? ""))
                }
                Spacer()
                Text(app.kb >= 1024 ? String(format: "%.1f MB", Double(app.kb) / 1024) : "\(app.kb) KB")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
        .toggleStyle(.checkbox)
        .help(app.desc)
    }
}

struct StatusPane: View {
    @EnvironmentObject var m: Model
    @State private var confirmStopRestore = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                statusIcon
                VStack(alignment: .leading, spacing: 4) {
                    Text(headlineText).font(.headline)
                    if let o = m.outcome, !m.busy {
                        Text(outcomeText(o)).font(.callout).fixedSize(horizontal: false, vertical: true)
                    } else if !m.detail.isEmpty {
                        Text(m.detail).font(.callout).foregroundStyle(.secondary)
                    }
                    if let p = m.progress {
                        ProgressView(value: p) { EmptyView() } currentValueLabel: {
                            Text("\(Int(p * 100))%").monospacedDigit()
                        }
                        .frame(maxWidth: 420)
                    }
                }
                Spacer()
                if m.busy {
                    Button("Stop") {
                        if m.job == .restore { confirmStopRestore = true } else { m.cancel() }
                    }
                    .help(m.job == .restore ? "Interrupting a restore may leave it incomplete." : "Stops the Mac side. Once the ramdisk runs, the phone finishes on its own.")
                }
            }
            if m.needRecovery { RecoveryHelp() }
            LogView()
        }
        .padding(12)
        .alert("Stop the restore?", isPresented: $confirmStopRestore) {
            Button("Keep Restoring", role: .cancel) {}
            Button("Stop Restore", role: .destructive) { m.cancel() }
        } message: {
            Text("Interrupting can leave the phone partially restored. Keep USB connected; another restore may be needed to recover it.")
        }
    }

    var headlineText: String {
        if m.busy { return m.headline }
        switch m.outcome {
        case .success: return "Success"
        case .failure: return "Did not complete"
        case nil: return "Ready"
        }
    }

    func outcomeText(_ o: Outcome) -> String {
        switch o {
        case .success(let s), .failure(let s): return s
        }
    }

    @ViewBuilder var statusIcon: some View {
        if m.busy {
            ProgressView().controlSize(.regular)
        } else {
            switch m.outcome {
            case .success: Image(systemName: "checkmark.seal.fill").font(.title).foregroundStyle(.green)
            case .failure: Image(systemName: "exclamationmark.triangle.fill").font(.title).foregroundStyle(.orange)
            case nil: Image(systemName: "iphone.gen1").font(.title).foregroundStyle(.secondary)
            }
        }
    }
}

struct RecoveryHelp: View {
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "hand.point.up.left.fill").font(.title2).foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 3) {
                Text("Put the iPhone in recovery mode").font(.headline)
                Text("1. Unplug it, hold Sleep/Wake and slide to power off.")
                Text("2. Hold the Home button and plug the USB cable back in. Keep holding Home.")
                Text("3. Let go when the “Connect to iTunes” picture appears. The app continues by itself.")
            }
            .font(.callout)
            Spacer()
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.12)))
    }
}

struct LogView: View {
    @EnvironmentObject var m: Model

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                Text(m.log.isEmpty ? "Log output appears here." : m.log)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(m.log.isEmpty ? .secondary : .primary)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                Color.clear.frame(height: 1).id("end")
            }
            .background(RoundedRectangle(cornerRadius: 6).fill(Color(nsColor: .textBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.25)))
            .onChange(of: m.log) { _ in proxy.scrollTo("end", anchor: .bottom) }
        }
    }
}
