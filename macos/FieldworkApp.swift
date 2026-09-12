import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var serverProcess: Process?
    private let projectRoot = "/Users/lizekai/Documents/ChatGPT/SRC漏洞"
    private let python = "/Users/lizekai/anaconda3/bin/python3"
    private let appURL = URL(string: "http://127.0.0.1:8000/new")!

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
        ensureServerThenLoad(attempt: 0)
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
        let html = """
        <html><body style="margin:0;background:#f3f1e7;color:#171815;font-family:-apple-system;display:grid;place-items:center;height:100vh">
        <div style="text-align:center"><div style="font-size:58px">⌕</div><h2>Fieldwork 正在启动</h2><p style="color:#74746d">正在连接本地安全研究服务…</p></div>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func serverIsReady(_ completion: @escaping (Bool) -> Void) {
        // Capability inventory can take many seconds because it probes every
        // installed binary. Use the lightweight UI route for liveness so a
        // healthy server is never mistaken for a stopped one.
        var request = URLRequest(url: URL(string: "http://127.0.0.1:8000/new")!)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { _, response, _ in
            completion((response as? HTTPURLResponse)?.statusCode == 200)
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
        process.arguments = ["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"]
        process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        process.standardOutput = logHandle
        process.standardError = logHandle
        do {
            try process.run()
            serverProcess = process
        } catch {
            showError("无法启动本地服务：\(error.localizedDescription)")
        }
    }

    private func ensureServerThenLoad(attempt: Int) {
        serverIsReady { [weak self] ready in
            guard let self else { return }
            DispatchQueue.main.async {
                if ready {
                    self.webView.load(URLRequest(url: self.appURL))
                    return
                }
                if attempt == 0 { self.startServer() }
                guard attempt < 60 else {
                    self.showError("本地服务启动超时，请查看 logs/fieldwork-app.log")
                    return
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    self.ensureServerThenLoad(attempt: attempt + 1)
                }
            }
        }
    }

    private func showError(_ message: String) {
        let safe = message.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        webView.loadHTMLString("<body style='font-family:-apple-system;padding:48px;background:#f3f1e7'><h2>Fieldwork 启动失败</h2><p>\(safe)</p></body>", baseURL: nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

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
