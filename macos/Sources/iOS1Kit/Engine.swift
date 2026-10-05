import Foundation

/// Runs the bundled Python engine (`ios1kit`), the same tool as the command line version.
enum Engine {
    /// The engine inside the .app, or IOS1KIT_ENGINE for development runs.
    static var script: URL {
        if let dev = ProcessInfo.processInfo.environment["IOS1KIT_ENGINE"] {
            return URL(fileURLWithPath: dev)
        }
        return Bundle.main.resourceURL!.appendingPathComponent("engine/ios1kit")
    }

    /// Ramdisk blobs go here, not inside the (signed) app bundle.
    static var buildDir: URL {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        return caches.appendingPathComponent("ios1kit/build")
    }

    /// Only the app's own bin/ (irecovery) and macOS's system tools (hdiutil, ditto,
    /// ioreg, ssh-keygen) are on PATH, so nothing installed elsewhere is used by accident.
    static var environment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        let bin = Bundle.main.resourceURL!.appendingPathComponent("bin").path
        env["PATH"] = bin + ":/usr/bin:/bin:/usr/sbin:/sbin"
        env.removeValue(forKey: "PYTHONHOME")
        env.removeValue(forKey: "PYTHONPATH")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["IOS1KIT_EVENTS"] = "1"
        return env
    }

    /// The app's own Python (Resources/python); IOS1KIT_PYTHON overrides it for development.
    static var python: URL {
        if let dev = ProcessInfo.processInfo.environment["IOS1KIT_PYTHON"] {
            return URL(fileURLWithPath: dev)
        }
        return Bundle.main.resourceURL!.appendingPathComponent("python/bin/python3")
    }

    static func makeProcess(_ args: [String]) -> Process {
        let p = Process()
        p.executableURL = python
        p.arguments = [script.path] + args
        p.environment = environment
        return p
    }

    /// Run a short command and return its stdout (used for --json queries).
    static func capture(_ args: [String]) async -> (status: Int32, output: Data) {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = makeProcess(args)
                let out = Pipe()
                p.standardOutput = out
                p.standardError = out
                do {
                    try p.run()
                } catch {
                    cont.resume(returning: (-1, Data()))
                    return
                }
                let data = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                cont.resume(returning: (p.terminationStatus, data))
            }
        }
    }
}

struct DoctorReport: Decodable {
    let ok: Bool
    let problems: [String]
    let kit: String?
    let irecovery: String?
    let phone: String?
}

struct PhoneStatus: Decodable {
    let mode: String?
}

struct AppItem: Decodable, Identifiable, Hashable {
    let key: String
    let name: String
    let cat: String
    let kb: Int
    let note: String
    let desc: String
    let in_apps: Bool
    let era: AppEra?
    var id: String { key }
}

struct AppEra: Decodable, Hashable {
    let status: String
    let label: String
    let detail: String
    let date: String?
    let version: String?
    let sources: [String]
}

/// One `@@{...}` progress line from the engine.
struct EngineEvent: Decodable {
    let event: String
    let label: String?
    let step: String?
    let ok: Bool?
    let mode: String?
    let index: Int?
    let total: Int?
    let batches: Int?
    let message: String?
    let pct: Double?
    let mb: Double?
    let ok_mb: Int?
    let failed_mb: Int?
    let ramdisk_mb: Int?
    let min_ramdisk_mb: Int?
}

struct FirmwareItem: Decodable, Identifiable {
    let version: String
    let build: String
    let available: Bool
    let experimental: Bool
    var id: String { version }
}

struct RestoreIdentity: Decodable {
    let mode: String?
    let product_type: String?
    let serial: String?
    let version: String?
    let capacity_gb: Double?
    let multiple: Bool
    let serial_decoded: SerialReport?
}

struct SerialReport: Decodable {
    let valid: Bool
    let factory: String?
    let year_digit: String?
    let week: String?
    let note: String
}

struct RestoreRecommendation: Decodable {
    let target: String?
    let eligibility: String
    let reason: String
}

struct RestoreInfo: Decodable {
    let backend: String?
    let firmwares: [FirmwareItem]
    let identity: RestoreIdentity
    let recommendation: RestoreRecommendation
    let experimental_warning: String
    let custom_warning: String
    let transport: USBTransportReport?
}

struct RestorePlan: Decodable {
    let path: String
    let version: String
    let build: String
    let experimental: Bool
    let custom: Bool
    let sha256: String
    let identity: RestoreIdentity
    let recommendation: RestoreRecommendation
    let warnings: [String]
    let transport: USBTransportReport?
}

struct USBTransportReport: Decodable, Equatable {
    let status: String
    let message: String
    let detail: String
    let blocking: Bool
    let visible: Bool
    let service_available: Bool
    let device_id: Int?
    let service_type: String?
    let protocol_version: Int?
    let phone_query_error: String?
}
