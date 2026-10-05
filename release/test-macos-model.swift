import Foundation
import Darwin

// Compile beside the real Engine.swift and Model.swift. The only subprocess is
// a fake engine in a temporary directory, so these tests cannot touch a phone.
@main
struct SetupModelChecks {
    @MainActor
    static func wait(_ message: String, until condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(7)
        while !condition() {
            if Date() > deadline { throw CheckError.failed(message) }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    enum CheckError: Error { case failed(String) }

    @MainActor
    static func check(_ ok: Bool, _ message: String) throws {
        if !ok { throw CheckError.failed(message) }
    }

    @MainActor
    static func main() async {
        do { try await checks() }
        catch { print("Mac setup model check failed: \(error)"); exit(1) }
    }

    @MainActor
    static func checks() async throws {
        let env = ProcessInfo.processInfo.environment
        let scenario = env["MODEL_TEST_SCENARIO"]!
        let pidPath = env["MODEL_TEST_PID"]!
        let previous = UserDefaults.standard.object(forKey: "userKitPath")
        UserDefaults.standard.set("/old-invalid-kit", forKey: "userKitPath")
        defer {
            if let previous { UserDefaults.standard.set(previous, forKey: "userKitPath") }
            else { UserDefaults.standard.removeObject(forKey: "userKitPath") }
        }
        let model = Model()
        model.runSetup()
        try check(model.busy, "Setup did not reserve the job slot")
        // Starting another action while a setup job is active must be ignored.
        model.run(.probe)

        if scenario == "cancel" {
            try await wait("Setup subprocess never started") { FileManager.default.fileExists(atPath: pidPath) }
            let pid = Int32(try String(contentsOfFile: pidPath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines))!
            defer { if kill(pid, 0) == 0 { kill(pid, SIGKILL) } }
            model.cancel()
            try await wait("Stop did not cancel the setup subprocess") { !model.busy }
            try check(kill(pid, 0) != 0, "Setup subprocess survived Stop")
            if case .failure = model.outcome {} else { throw CheckError.failed("Cancelled setup reported success") }
            try check(UserDefaults.standard.string(forKey: "userKitPath") == "/old-invalid-kit",
                      "Cancelled setup replaced the selected kit")
        } else {
            try await wait("Setup never finished") { !model.busy && model.outcome != nil }
            if scenario == "success" {
                try await wait("Setup never refreshed doctor") { model.checkedSetup }
                if case .success = model.outcome {} else { throw CheckError.failed("Verified setup failed") }
                try check(model.kitPath == "/managed-kit" && model.setupOK,
                          "Successful setup kept the bad custom kit selected")
            } else {
                if case .failure = model.outcome {} else { throw CheckError.failed("Failed or unverified setup reported success") }
                try check(UserDefaults.standard.string(forKey: "userKitPath") == "/old-invalid-kit",
                          "Failed setup replaced the selected kit")
            }
        }
        print("Mac setup model check passed: \(scenario)")
    }
}
