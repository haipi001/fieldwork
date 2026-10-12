import Cocoa
import Security
import WebKit

// STARTUP_RECOVERY_POLICY_BEGIN
struct StartupRecovery {
    enum Probe { case ready, absent, foreign }
    enum Action { case load, rejectForeign, start, poll, wait, stopped }
    private(set) var generation = 0
    mutating func begin() -> Int { generation += 1; return generation }
    mutating func cancel() { generation += 1 }
    func accepts(_ value: Int) -> Bool { generation == value }
    func action(probe: Probe, attempt: Int, ownedProcessAlive: Bool?) -> Action {
        switch probe {
        case .ready: return .load
        case .foreign: return .rejectForeign
        case .absent:
            guard let alive = ownedProcessAlive else { return attempt == 0 ? .start : .stopped }
            guard alive else { return .stopped }
            return attempt < 60 ? .poll : .wait
        }
    }
}
// STARTUP_RECOVERY_POLICY_END

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private typealias ServerProbe = StartupRecovery.Probe
    private var startupRecovery = StartupRecovery()
    private var window: NSWindow!
    private var webView: WKWebView!
    private var serverProcess: Process?
    private var serverLifetime: Pipe?
    private var startupRetryAllowed = false
    private let projectRoot = Bundle.main.object(forInfoDictionaryKey: "FieldworkProjectRoot") as? String ?? ""
    private let python = Bundle.main.object(forInfoDictionaryKey: "FieldworkPythonExecutable") as? String ?? ""
    private let serverPort = (Bundle.main.object(forInfoDictionaryKey: "FieldworkPort") as? NSNumber)?.intValue ?? 8000
    private var appURL: URL { URL(string: "http://127.0.0.1:\(serverPort)/v5")! }
    private let desktopInstance = UUID().uuidString.lowercased()
    private let sessionToken: String = {
        var bytes = [UInt8](repeating: 0, count: 48)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            fatalError("Unable to create Fieldwork desktop session")
        }
        return Data(bytes).base64EncodedString()
    }()
    private var expectedVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMainMenu()

        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1380, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Fieldwork"
        window.titlebarAppearsTransparent = false
        // Let form controls and selected text receive mouse events. The native
        // title bar remains the dedicated drag region for the desktop window.
        window.isMovableByWindowBackground = false
        window.contentView = webView
        window.center()
        window.setFrameAutosaveName("FieldworkMainWindow")
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(webView)
        NSApp.activate(ignoringOtherApps: true)

        showStartingPage()
        guard projectRoot.hasPrefix("/"), python.hasPrefix("/"), (1...65535).contains(serverPort),
              FileManager.default.fileExists(atPath: projectRoot + "/desktop_server.py"),
              FileManager.default.isExecutableFile(atPath: python) else {
            showError("构建包的本地启动配置无效，请重新构建 Fieldwork。")
            return
        }
        beginStartup()
    }

    private func installMainMenu() {
        let mainMenu = NSMenu(title: "Fieldwork")

        let applicationMenuItem = NSMenuItem(title: "Fieldwork", action: nil, keyEquivalent: "")
        let applicationMenu = NSMenu(title: "Fieldwork")
        applicationMenu.addItem(withTitle: "关于 Fieldwork", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "隐藏 Fieldwork", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        applicationMenu.addItem(withTitle: "退出 Fieldwork", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationMenuItem.submenu = applicationMenu
        mainMenu.addItem(applicationMenuItem)

        let editMenuItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        NSApp.mainMenu = mainMenu
    }

    private func showStartingPage() {
        startupRetryAllowed = false
        let html = """
        <html><body style="margin:0;background:#f3f1e7;color:#171815;font-family:-apple-system;display:grid;place-items:center;height:100vh">
        <div style="text-align:center"><div style="font-size:58px">⌕</div><h2>Fieldwork 正在启动</h2><p style="color:#74746d">正在连接本地安全研究服务…</p></div>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func installSessionCookie(_ completion: @escaping () -> Void) {
        let properties: [HTTPCookiePropertyKey: Any] = [
            .domain: "127.0.0.1", .path: "/", .name: "fieldwork_session",
            .value: sessionToken, .discard: "TRUE", .init(rawValue: "HttpOnly"): "TRUE",
            .init(rawValue: "SameSite"): "Strict",
        ]
        guard let cookie = HTTPCookie(properties: properties) else {
            showError("无法建立本机会话 Cookie")
            return
        }
        webView.configuration.websiteDataStore.httpCookieStore.setCookie(cookie, completionHandler: completion)
    }

    private func serverIsReady(_ completion: @escaping (ServerProbe) -> Void) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(serverPort)/health")!)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { _, response, _ in
            guard let response = response as? HTTPURLResponse, response.statusCode == 200 else {
                completion(.absent)
                return
            }
            let instance = response.value(forHTTPHeaderField: "X-Fieldwork-Instance")
            let version = response.value(forHTTPHeaderField: "X-Fieldwork-Version")
            completion(instance == self.desktopInstance && version == self.expectedVersion ? .ready : .foreign)
        }.resume()
    }

    private func startServer() {
        guard serverProcess == nil else { return }
        let logDirectory = projectRoot + "/logs"
        try? FileManager.default.createDirectory(atPath: logDirectory, withIntermediateDirectories: true)
        let logURL = URL(fileURLWithPath: logDirectory + "/fieldwork-app.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let logHandle = try? FileHandle(forWritingTo: logURL)
        _ = try? logHandle?.seekToEnd()

        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-P", "-m", "desktop_server", "--port", String(serverPort)]
        // Resolve modules explicitly; Python must not probe a Documents cwd at startup.
        process.currentDirectoryURL = URL(fileURLWithPath: "/")
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = projectRoot
        environment["FIELDWORK_SESSION_TOKEN"] = sessionToken
        environment["FIELDWORK_DESKTOP_INSTANCE"] = desktopInstance
        environment["FIELDWORK_PORT"] = String(serverPort)
        environment["FIELDWORK_DESKTOP_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        process.environment = environment
        let lifetime = Pipe()
        process.standardInput = lifetime
        process.standardOutput = logHandle
        process.standardError = logHandle
        do {
            try process.run()
            serverProcess = process
            serverLifetime = lifetime
            lifetime.fileHandleForReading.closeFile()
        } catch {
            showError("无法启动本地服务：\(error.localizedDescription)", retryable: true)
        }
    }

    private func beginStartup() {
        let generation = startupRecovery.begin()
        installSessionCookie { [weak self] in
            DispatchQueue.main.async {
                guard let self, self.startupRecovery.accepts(generation) else { return }
                self.ensureServerThenLoad(attempt: 0, generation: generation)
            }
        }
    }

    private func ensureServerThenLoad(attempt: Int, generation: Int) {
        guard startupRecovery.accepts(generation) else { return }
        serverIsReady { [weak self] probe in
            guard let self else { return }
            DispatchQueue.main.async {
                guard self.startupRecovery.accepts(generation) else { return }
                let action = self.startupRecovery.action(probe: probe, attempt: attempt,
                                                       ownedProcessAlive: self.serverProcess?.isRunning)
                switch action {
                case .load:
                    self.startupRecovery.cancel()
                    self.startupRetryAllowed = false
                    self.webView.load(URLRequest(url: self.appURL))
                    return
                case .rejectForeign:
                    self.startupRecovery.cancel()
                    self.showError("端口 \(self.serverPort) 已被其他或旧版服务占用。为保护本机会话，Fieldwork 不会连接该服务。", retryable: true)
                    return
                case .stopped:
                    self.startupRecovery.cancel()
                    self.showError("本地服务已停止，请查看 logs/fieldwork-app.log 后重试连接。", retryable: true)
                    return
                case .start:
                    self.startServer()
                    guard self.serverProcess != nil else { return }
                case .wait:
                    if attempt == 60 { self.showWaitingPage() }
                case .poll: break
                }
                let delay = action == .wait ? 5.0 : 0.5
                DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                    self.ensureServerThenLoad(attempt: attempt + 1, generation: generation)
                }
            }
        }
    }

    private func showWaitingPage() {
        startupRetryAllowed = true
        webView.loadHTMLString("<body style='font-family:-apple-system;padding:48px;background:#f3f1e7'><h2>Fieldwork 仍在启动</h2><p>后台进程仍在运行，正在等待本地服务就绪。就绪后会自动打开工作台；可查看 logs/fieldwork-app.log。</p><p><a href='fieldwork-startup://retry'>立即检查连接</a></p></body>", baseURL: nil)
    }

    private func showError(_ message: String, retryable: Bool = false) {
        startupRetryAllowed = retryable
        let safe = message.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        let retry = retryable ? "<p><a href='fieldwork-startup://retry' style='display:inline-block;padding:12px 20px;background:#171815;color:white;border-radius:6px;text-decoration:none'>重试连接</a></p>" : ""
        webView.loadHTMLString("<body style='font-family:-apple-system;padding:48px;background:#f3f1e7'><h2>Fieldwork 启动失败</h2><p>\(safe)</p>\(retry)</body>", baseURL: nil)
    }

    private func retryStartup() {
        guard startupRetryAllowed else { return }
        showStartingPage()
        // Keep the live owned process. A timeout does not prove it has stopped.
        if let process = serverProcess, !process.isRunning {
            serverLifetime?.fileHandleForWriting.closeFile()
            serverLifetime = nil
            serverProcess = nil
        }
        beginStartup()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        let localSource = navigationAction.sourceFrame.request.url.map {
            $0.scheme == "http" && $0.host == "127.0.0.1" && $0.port == serverPort
        } ?? false
        if navigationAction.shouldPerformDownload && localSource &&
            (url.scheme == "blob" || (url.scheme == "http" && url.host == "127.0.0.1" && url.port == serverPort)) {
            decisionHandler(.download)
            return
        }
        if url.scheme == "fieldwork-startup" {
            decisionHandler(.cancel)
            if url.host == "retry", navigationAction.navigationType == .linkActivated,
               navigationAction.sourceFrame.isMainFrame,
               navigationAction.sourceFrame.request.url?.scheme == "about",
               navigationAction.targetFrame?.isMainFrame == true, startupRetryAllowed {
                retryStartup()
            }
            return
        }
        if url.scheme == "about" || (url.scheme == "http" && url.host == "127.0.0.1" && url.port == serverPort) {
            decisionHandler(.allow)
            return
        }
        if navigationAction.targetFrame?.isMainFrame != false {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let response = navigationResponse.response as? HTTPURLResponse,
           response.url?.host == "127.0.0.1", response.url?.port == serverPort,
           response.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") == true {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.url : nil)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = "Fieldwork"
        alert.informativeText = message
        alert.addButton(withTitle: "确定")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = "请确认操作"
        alert.informativeText = message
        alert.addButton(withTitle: "继续")
        alert.addButton(withTitle: "取消")
        alert.alertStyle = .warning
        alert.beginSheetModal(for: window) { response in
            completionHandler(response == .alertFirstButtonReturn)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = "Fieldwork"
        alert.informativeText = prompt
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        let input = NSTextField(string: defaultText ?? "")
        input.frame = NSRect(x: 0, y: 0, width: 320, height: 24)
        alert.accessoryView = input
        alert.beginSheetModal(for: window) { response in
            completionHandler(response == .alertFirstButtonReturn ? input.stringValue : nil)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        startupRecovery.cancel()
        serverLifetime?.fileHandleForWriting.closeFile()
        if let process = serverProcess, process.isRunning {
            process.terminate()
        }
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
