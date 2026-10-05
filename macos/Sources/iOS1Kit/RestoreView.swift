import SwiftUI

struct RestoreView: View {
    @EnvironmentObject var m: Model
    @State private var showingConfirmation = false
    @State private var probingTransport = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Restore iPhone 2G").font(.title2.bold())
                Text("A restore erases the phone. Activation and the 1.0 app installer are separate actions after restoration.")
                    .foregroundStyle(.secondary)
                if let info = m.restoreInfo {
                    GroupBox("Phone and compatibility") {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Model: \(info.identity.product_type ?? "not confirmed") · Serial: \(info.identity.serial ?? "unavailable in this mode")")
                                .font(.callout.monospaced())
                            if let decoded = info.identity.serial_decoded, decoded.valid {
                                Text("Factory \(decoded.factory ?? "?") · Year digit \(decoded.year_digit ?? "?") · Week \(decoded.week ?? "?") — display only")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Text(info.recommendation.reason).fixedSize(horizontal: false, vertical: true)
                            if info.identity.multiple {
                                Label("Disconnect other iPhones/iPods before restoring.", systemImage: "exclamationmark.triangle")
                                    .foregroundStyle(.orange)
                            }
                            Button("Read device again") { Task { await m.refreshRestoreInfo() } }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
                    }
                    if let transport = info.transport {
                        USBTransportView(report: transport, checking: $probingTransport)
                    }
                    GroupBox("Stock firmware — download and verify locally") {
                        VStack(alignment: .leading, spacing: 10) {
                            Picker("Restore to", selection: $m.restoreTarget) {
                                Text("Earliest supported 1.x (requires known NAND)").tag("auto")
                                ForEach(info.firmwares) { fw in
                                    Text("\(fw.version) (\(fw.build))\(fw.experimental ? " — experimental" : "")\(fw.available ? "" : " — missing")")
                                        .tag(fw.version)
                                }
                            }
                            .disabled(m.customIPSW != nil)
                            HStack {
                                Button("Choose Custom IPSW…") { m.chooseIPSW() }
                                if m.customIPSW != nil {
                                        Button("Use Stock Firmware") { m.customIPSW = nil }
                                }
                            }
                            Button("Download & Verify Selected Firmware") { m.downloadFirmware() }
                                .disabled(m.restoreTarget == "auto" || m.customIPSW != nil || m.busy || m.inspectingRestore)
                            Text("The public app downloads Apple firmware into its own local cache. Downloads never start a phone restore.")
                                .font(.caption).foregroundStyle(.secondary)
                            if let path = m.customIPSW {
                                Text(path).font(.caption).textSelection(.enabled)
                                Text(info.custom_warning).font(.callout).foregroundStyle(.orange)
                            }
                            DisclosureGroup("Known NAND chip ID (optional)") {
                                VStack(alignment: .leading, spacing: 6) {
                                    TextField("For example: 0x2555D5EC", text: $m.nandID)
                                    Text("Only enter a chip ID read from the phone or an identified chip part. The serial number does not encode it. Unknown hardware requires choosing a version explicitly.")
                                        .font(.caption).foregroundStyle(.secondary)
                                }.padding(.top, 6)
                            }
                        }.padding(6)
                    }
                    if info.backend == nil {
                        Label("Built-in restore engine unavailable. Re-download the app.", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    }
                    Button {
                        Task {
                            await m.inspectRestore()
                            showingConfirmation = m.restorePlan != nil
                        }
                    } label: {
                        HStack {
                            if m.inspectingRestore { ProgressView().controlSize(.small) }
                            Text(m.inspectingRestore ? "Checking IPSW…" : "Inspect & Prepare Restore…")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(info.backend == nil || m.busy || m.inspectingRestore || probingTransport)
                } else {
                    ProgressView("Reading built-in restore tools…")
                    Button("Retry") { Task { await m.refreshRestoreInfo() } }
                }
                if let error = m.restoreError {
                    Text(error).foregroundStyle(.orange).textSelection(.enabled)
                }
                Text("DFU: hold Power + Home for 10 seconds, release Power and hold Home for another 10–15 seconds. The screen stays black. Recovery shows a cable/iTunes picture. Keep only the original iPhone connected.")
                    .font(.callout).foregroundStyle(.secondary)
                Text("Native 1.x restoration is experimental. The app includes the legacy protocol implementation, but this restore path has not been tested on a real phone. No failed restore triggers another erase automatically.")
                    .font(.callout).foregroundStyle(.orange)
            }.padding(20)
        }
        .disabled(m.busy)
        .sheet(isPresented: $showingConfirmation) {
            if let plan = m.restorePlan { RestoreConfirmation(plan: plan) }
        }
    }
}

private struct USBTransportView: View {
    let report: USBTransportReport
    @Binding var checking: Bool
    @State private var serviceReport: USBTransportReport?
    @State private var queryError: String?
    @State private var generation = 0

    var body: some View {
        let current = serviceReport ?? report
        GroupBox("USB restore readiness — read-only") {
            VStack(alignment: .leading, spacing: 8) {
                Text(current.message).foregroundStyle(current.blocking || current.phone_query_error != nil || current.status == "not_visible" ? Color.orange : Color.primary)
                Text(current.detail).font(.caption).foregroundStyle(.secondary)
                if let error = current.phone_query_error ?? queryError {
                    Text(error).font(.caption).foregroundStyle(.orange)
                }
                HStack {
                    if checking { ProgressView().controlSize(.small) }
                    Button(checking ? "Checking USB service…" : "Check phone service") {
                        checking = true
                        queryError = nil
                        let requestedGeneration = generation
                        Task {
                            defer { checking = false }
                            let (_, data) = await Engine.capture(["transport-info", "--json", "--probe-service"])
                            guard requestedGeneration == generation else { return }
                            if let value = try? JSONDecoder().decode(USBTransportReport.self, from: data) {
                                serviceReport = value
                            } else {
                                queryError = "The USB readiness check could not finish. Read the device again."
                            }
                        }
                    }.disabled(checking)
                }
                Text("This sends no restore, pairing, or reboot request. Recovery/DFU detection is not proof of a completed restore.")
                    .font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
        }
        .onChange(of: report) { _ in
            generation += 1
            serviceReport = nil
            queryError = nil
        }
    }
}

struct RestoreConfirmation: View {
    @EnvironmentObject var m: Model
    @Environment(\.dismiss) private var dismiss
    let plan: RestorePlan
    @State private var eraseOK = false
    @State private var modelOK = false
    @State private var warningsOK = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Restore \(plan.version) (\(plan.build))?").font(.title2.bold())
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(plan.warnings.enumerated()), id: \.offset) { _, warning in
                        Text(warning).fixedSize(horizontal: false, vertical: true)
                    }
                    if let transport = plan.transport, transport.blocking {
                        Text(transport.message + " Cancel, resolve this, and prepare a new plan before erasing.")
                            .foregroundStyle(.orange)
                    }
                    Text("IPSW: \(URL(fileURLWithPath: plan.path).lastPathComponent)")
                    Text("SHA-256: \(plan.sha256)").font(.caption.monospaced()).textSelection(.enabled)
                    Text("Connect only this phone in recovery/DFU. The app rechecks the firmware and USB identity before starting.")
                }
            }.frame(maxHeight: 260)
            Toggle("I understand this erases all data on the phone.", isOn: $eraseOK)
            Toggle("I checked that this is the original aluminum-back iPhone, model A1203.", isOn: $modelOK)
            Toggle("OK — I understand the compatibility and experimental/custom warnings above.", isOn: $warningsOK)
            HStack {
                Button("Cancel") { dismiss() }
                Spacer()
                Button("Erase & Restore", role: .destructive) {
                    dismiss()
                    m.confirmRestore(plan)
                }.disabled(!eraseOK || !modelOK || !warningsOK || m.busy || plan.transport?.blocking == true)
            }
        }.padding(22).frame(width: 640)
    }
}
