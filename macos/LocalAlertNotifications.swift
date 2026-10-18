import Cocoa
import WebKit
import UserNotifications

// NATIVE_ALERT_POLICY_BEGIN
struct NativeAlertPolicy {
    static func acceptsOrigin(scheme: String?, host: String?, port: Int?, path: String?, mainFrame: Bool, expectedPort: Int) -> Bool {
        mainFrame && scheme == "http" && host == "127.0.0.1" && port == expectedPort && path == "/v5"
    }
    static func acceptsCommand(action: String?, keys: Set<String>) -> Bool {
        guard let action = action else { return false }
        return ["status", "enable", "disable"].contains(action) && keys.isSubset(of: ["action", "language"])
    }
    private(set) var cursor: Int?
    private(set) var generation = 0
    mutating func reset() { generation += 1; cursor = nil }
    mutating func shouldDeliver(cursor next: Int, count: Int) -> Bool {
        guard next >= 0, count >= 0 else { return false }
        guard let previous = cursor else { cursor = next; return false }
        if next < previous || count == 0 { cursor = next; return false }
        return next > previous
    }
    mutating func commit(cursor next: Int, generation expected: Int) {
        if expected == generation { cursor = next }
    }
}
// NATIVE_ALERT_POLICY_END

final class LocalAlertNotifications: NSObject, WKScriptMessageHandler, URLSessionTaskDelegate, UNUserNotificationCenterDelegate {
    weak var webView: WKWebView?
    private let baseURL: URL
    private let token: String
    private let instance: String
    private let version: String
    private let serverAlive: () -> Bool
    private let preference = "FieldworkLocalAlertNotifications.v1"
    private var enabled: Bool { UserDefaults.standard.bool(forKey: preference) }
    private var language = UserDefaults.standard.string(forKey: "FieldworkLocalAlertNotifications.v1.language") == "en" ? "en" : "zh"
    private var authorization = "unknown"
    private var delivery = "disabled"
    private var policy = NativeAlertPolicy()
    private var timer: Timer?
    private var polling = false
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 5
        config.httpCookieStorage = nil
        config.urlCache = nil
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    init(baseURL: URL, token: String, instance: String, version: String, serverAlive: @escaping () -> Bool) {
        self.baseURL = baseURL; self.token = token; self.instance = instance; self.version = version; self.serverAlive = serverAlive
    }
    func start() {
        guard timer == nil else { return }
        UNUserNotificationCenter.current().delegate = self
        policy.reset()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.poll() }
        poll()
    }
    func stop() { policy.reset(); timer?.invalidate(); timer = nil; session.invalidateAndCancel() }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let url = message.frameInfo.request.url,
              NativeAlertPolicy.acceptsOrigin(scheme: url.scheme, host: url.host, port: url.port, path: url.path,
                    mainFrame: message.frameInfo.isMainFrame, expectedPort: baseURL.port ?? 8000),
              let body = message.body as? [String: String], NativeAlertPolicy.acceptsCommand(action: body["action"], keys: Set(body.keys)),
              let action = body["action"] else { return }
        if let locale = body["language"], ["en", "zh"].contains(locale) { language = locale; UserDefaults.standard.set(locale, forKey: preference + ".language") }
        if action == "disable" {
            UserDefaults.standard.set(false, forKey: preference); policy.reset(); delivery = "disabled"; publish(); return
        }
        if action == "status" { refreshAuthorization { [weak self] _ in self?.publish() }; return }
        policy.reset()
        let generation = policy.generation
        delivery = "awaiting_permission"; publish()
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert]) { [weak self] granted, error in
            DispatchQueue.main.async {
                guard let self = self, self.policy.generation == generation else { return }
                UserDefaults.standard.set(granted && error == nil, forKey: self.preference)
                self.delivery = granted && error == nil ? "enabled" : "permission_denied"
                self.refreshAuthorization { [weak self] _ in self?.publish(); self?.poll() }
            }
        }
    }
    private func refreshAuthorization(_ completion: @escaping (Bool) -> Void) {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] settings in
            DispatchQueue.main.async {
                guard let self = self else { return }
                let allowed = settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional
                self.authorization = allowed ? "authorized" : settings.authorizationStatus == .denied ? "denied" : "not_determined"
                if !self.enabled && self.delivery != "awaiting_permission" { self.delivery = "disabled" }
                if self.enabled && !allowed { self.delivery = "permission_denied" }
                if self.enabled && allowed && ["disabled", "permission_denied"].contains(self.delivery) { self.delivery = "enabled" }
                completion(allowed)
            }
        }
    }
    private func publish() {
        let value: [String: Any] = ["enabled": enabled, "authorization": authorization, "state": delivery, "app_running_only": true]
        guard let data = try? JSONSerialization.data(withJSONObject: value), let json = String(data: data, encoding: .utf8) else { return }
        webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('fieldwork:native-alert-state',{detail:\(json)}))", completionHandler: nil)
    }
    private func poll() {
        guard enabled, !polling, serverAlive() else { return }
        polling = true
        let generation = policy.generation
        refreshAuthorization { [weak self] allowed in
            guard let self = self else { return }
            guard allowed, self.enabled, generation == self.policy.generation else { self.polling = false; self.publish(); return }
            var health = URLRequest(url: self.baseURL.appendingPathComponent("health")); health.timeoutInterval = 3
            self.session.dataTask(with: health) { [weak self] _, response, _ in
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    guard self.enabled, generation == self.policy.generation else { self.polling = false; return }
                    guard self.serverAlive(), let response = response as? HTTPURLResponse, response.statusCode == 200,
                          response.value(forHTTPHeaderField: "X-Fieldwork-Instance") == self.instance,
                          response.value(forHTTPHeaderField: "X-Fieldwork-Version") == self.version else {
                        self.polling = false; self.delivery = "read_failed"; self.publish(); return
                    }
                    self.readFeed(generation: generation)
                }
            }.resume()
        }
    }
    private func readFeed(generation: Int) {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/workspace/monitor-alert-native-delivery"), resolvingAgainstBaseURL: false)!
        if let cursor = policy.cursor { components.queryItems = [URLQueryItem(name: "after", value: String(cursor))] }
        var request = URLRequest(url: components.url!); request.setValue(token, forHTTPHeaderField: "X-Fieldwork-Session")
        session.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard self.enabled, generation == self.policy.generation else { self.polling = false; return }
                guard let response = response as? HTTPURLResponse, response.statusCode == 200,
                      let data = data, data.count <= 65536, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let cursor = value["cursor"] as? Int, cursor >= 0, let items = value["items"] as? [[String: Any]], items.count <= 50,
                      let localEnabled = value["local_delivery_enabled"] as? Bool else {
                    self.polling = false; self.delivery = "read_failed"; self.publish(); return
                }
                self.delivery = localEnabled ? "enabled" : "local_disabled"
                if !localEnabled { self.policy.commit(cursor: cursor, generation: generation); self.polling = false; self.publish(); return }
                guard self.policy.shouldDeliver(cursor: cursor, count: items.count) else { self.polling = false; self.publish(); return }
                let content = UNMutableNotificationContent(); content.title = "Fieldwork"
                content.body = self.language == "en" ? "New policy signals in \(items.count) windows. Not independently verified. Review the local inbox." : "\(items.count) 个窗口出现新策略线索。尚未独立复验，请查看本机收件箱。"
                let notification = UNNotificationRequest(identifier: "fieldwork.monitor.\(cursor)", content: content, trigger: nil)
                UNUserNotificationCenter.current().add(notification) { [weak self] error in
                    DispatchQueue.main.async {
                        guard let self = self else { return }
                        self.polling = false
                        guard self.enabled, generation == self.policy.generation else { return }
                        if error == nil { self.policy.commit(cursor: cursor, generation: generation) }
                        else { self.delivery = "delivery_failed" }
                        self.publish()
                    }
                }
            }
        }.resume()
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.notification.request.identifier.hasPrefix("fieldwork.monitor.") {
            DispatchQueue.main.async { [weak self] in
                NSApp.activate(ignoringOtherApps: true)
                self?.webView?.window?.makeKeyAndOrderFront(nil)
                self?.webView?.evaluateJavaScript("location.hash='sentinel'", completionHandler: nil)
            }
        }
        completionHandler()
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) { completionHandler([.banner, .list]) }
}
