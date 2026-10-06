import AppKit

final class GatherHost: NSObject, NSApplicationDelegate {
    var process: Process?
    var window: NSWindow!
    var status: NSTextField!
    var setupURL: URL!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let env = ProcessInfo.processInfo.environment
        let port = env["GATHER_SETUP_PORT"] ?? "47681"
        setupURL = URL(string: "http://127.0.0.1:\(port)/")!
        let root = NSMenu(); let appItem = NSMenuItem(); root.addItem(appItem)
        let menu = NSMenu(); menu.addItem(withTitle: "Open Gather setup", action: #selector(openSetup), keyEquivalent: "o")
        menu.addItem(.separator()); menu.addItem(withTitle: "Quit Gather Host", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = menu; NSApp.mainMenu = root
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 250), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Gather Host"; window.center(); window.isReleasedWhenClosed = false
        let title = NSTextField(labelWithString: "Your church’s translation host")
        title.font = .systemFont(ofSize: 23, weight: .semibold); title.frame = NSRect(x: 28, y: 175, width: 445, height: 35)
        status = NSTextField(wrappingLabelWithString: "Preparing browser setup. The first launch may download a private Python runtime. Keep this app open during services.")
        status.font = .systemFont(ofSize: 14); status.frame = NSRect(x: 28, y: 92, width: 445, height: 75)
        let button = NSButton(title: "Open setup in browser", target: self, action: #selector(openSetup)); button.bezelStyle = .rounded; button.frame = NSRect(x: 28, y: 34, width: 215, height: 35)
        window.contentView?.addSubview(title); window.contentView?.addSubview(status); window.contentView?.addSubview(button)
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        guard let resources = Bundle.main.resourceURL else { status.stringValue = "The host bundle is incomplete. Download Gather Host again."; return }
        let data = env["GATHER_DATA_DIR"] ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Gather").path
        try? FileManager.default.createDirectory(atPath: data, withIntermediateDirectories: true)
        let p = Process(); p.executableURL = resources.appendingPathComponent("bin/uv")
        p.arguments = ["run", "--python", "3.12", "--managed-python", "--no-project", resources.appendingPathComponent("host/setup_server.py").path, "--resources", resources.path, "--uv", resources.appendingPathComponent("bin/uv").path, "--data", data, "--setup-port", port, "--host-port", env["GATHER_HOST_PORT"] ?? "47680"]
        p.environment = env.merging(["UV_PYTHON_INSTALL_DIR":data+"/python", "UV_CACHE_DIR":data+"/cache"]) { _, new in new }
        let logURL = URL(fileURLWithPath:data).appendingPathComponent("launcher.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath:logURL.path, contents:nil) }
        if let log = try? FileHandle(forWritingTo:logURL) { _ = try? log.seekToEnd(); p.standardOutput = log; p.standardError = log }
        p.terminationHandler = { task in DispatchQueue.main.async {
            self.status.stringValue = task.terminationStatus == 0 ? "Gather Host has stopped." : "Setup could not start. Check your internet connection, then reopen Gather Host. Diagnostics are saved in Library/Application Support/Gather/launcher.log."
        }}
        do { try p.run(); process = p; status.stringValue = "Gather setup opens in your browser. Models and progress appear there. Keep Gather Host running throughout the service." }
        catch { status.stringValue = "Gather Host could not launch. Download the app again." }
    }
    @objc func openSetup() { NSWorkspace.shared.open(setupURL) }
    func application(_ application: NSApplication, open urls: [URL]) { if setupURL != nil { openSetup() } }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) { process?.terminate() }
}
let app = NSApplication.shared
let delegate = GatherHost()
app.delegate = delegate
app.run()
