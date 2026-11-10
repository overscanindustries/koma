// The host app's only job: ask macOS to activate / deactivate the bundled camera extension. A system extension can
// only be installed from an app in /Applications, and the user approves it once in System Settings.
// DRAFT: typechecked only, never built or run.
import SwiftUI
import SystemExtensions

let extensionID = "com.forthright.komacam.extension"

final class Installer: NSObject, ObservableObject, OSSystemExtensionRequestDelegate {
    @Published var status = "Not requested."

    func activate() {
        let r = OSSystemExtensionRequest.activationRequest(forExtensionWithIdentifier: extensionID, queue: .main)
        r.delegate = self
        OSSystemExtensionManager.shared.submitRequest(r)
        status = "Requested activation..."
    }

    func deactivate() {
        let r = OSSystemExtensionRequest.deactivationRequest(forExtensionWithIdentifier: extensionID, queue: .main)
        r.delegate = self
        OSSystemExtensionManager.shared.submitRequest(r)
        status = "Requested deactivation..."
    }

    func request(_ request: OSSystemExtensionRequest, actionForReplacingExtension existing: OSSystemExtensionProperties,
                 withExtension ext: OSSystemExtensionProperties) -> OSSystemExtensionRequest.ReplacementAction {
        .replace
    }

    func requestNeedsUserApproval(_ request: OSSystemExtensionRequest) {
        status = "Approve it in System Settings > General > Login Items & Extensions > Camera Extensions."
    }

    func request(_ request: OSSystemExtensionRequest, didFinishWithResult result: OSSystemExtensionRequest.Result) {
        status = result == .completed ? "Done. Pick \"Komacam (GR DIGITAL)\" in Zoom." : "Done after a reboot."
    }

    func request(_ request: OSSystemExtensionRequest, didFailWithError error: Error) {
        status = "Failed: \(error.localizedDescription)"
    }
}

@main
struct KomacamHostApp: App {
    @StateObject private var installer = Installer()

    var body: some Scene {
        WindowGroup {
            VStack(alignment: .leading, spacing: 12) {
                Text("Komacam camera extension").font(.headline)
                Text("Run komacam first: it serves http://127.0.0.1:8770/hd.mjpg.").font(.caption)
                HStack {
                    Button("Install") { installer.activate() }
                    Button("Uninstall") { installer.deactivate() }
                }
                Text(installer.status).font(.caption).foregroundStyle(.secondary)
            }
            .padding(20)
            .frame(minWidth: 420)
        }
    }
}
