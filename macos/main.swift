import AppKit
import Foundation
import WebKit

// Native macOS launcher for Music Suite. It starts the bundled API + Next.js
// server through start.command and shows the local UI in a WKWebView window.
// Runtime state is kept in ~/Library/Application Support/Music Suite so the
// .app bundle can stay read-only inside /Applications or on a DMG volume.

private func logLine(_ message: String) {
    let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(message)\n"
    let data = Data(line.utf8)
    FileHandle.standardError.write(data)

    let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Music Suite", isDirectory: true)
    try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
    let path = support.appendingPathComponent("launcher-native.log").path
    if let handle = FileHandle(forWritingAtPath: path) {
        handle.seekToEndOfFile()
        handle.write(data)
        handle.closeFile()
    } else {
        try? line.write(toFile: path, atomically: true, encoding: .utf8)
    }
}

final class SuiteLauncher: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusField: NSTextField!
    private var launcherTask: Process?
    private var launcherLog: FileHandle?
    private var suiteRoot = ""
    private var stateURL: URL!
    private var polling = false
    private var polls = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        let resources = Bundle.main.resourceURL
            ?? URL(fileURLWithPath: Bundle.main.bundlePath).appendingPathComponent("Contents/Resources")
        suiteRoot = resources.appendingPathComponent("suite", isDirectory: true).path

        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Music Suite", isDirectory: true)
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        stateURL = support.appendingPathComponent(".music-suite-processes.json")
        try? FileManager.default.removeItem(at: stateURL)

        buildWindow()
        startSuite(supportPath: support.path)
        pollForUI()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        let stop = Process()
        stop.executableURL = URL(fileURLWithPath: "/bin/bash")
        stop.arguments = ["-c", "exec ./stop.command"]
        stop.currentDirectoryURL = URL(fileURLWithPath: suiteRoot)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["MUSIC_SUITE_STATE_DIR"] = stateURL.deletingLastPathComponent().path
        stop.environment = env
        do {
            try stop.run()
            stop.waitUntilExit()
        } catch {
            logLine("stop.command failed: \(error)")
        }
        launcherTask?.terminate()
    }

    private func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Music Suite"
        window.minSize = NSSize(width: 800, height: 600)
        window.center()

        let root = window.contentView!
        statusField = NSTextField(labelWithString: "Starting Music Suite…")
        statusField.font = NSFont.systemFont(ofSize: 12)
        statusField.textColor = .secondaryLabelColor
        statusField.translatesAutoresizingMaskIntoConstraints = false

        webView = WKWebView(frame: .zero)
        webView.navigationDelegate = self
        webView.translatesAutoresizingMaskIntoConstraints = false
        webView.isHidden = true
        root.addSubview(statusField)
        root.addSubview(webView)
        NSLayoutConstraint.activate([
            statusField.topAnchor.constraint(equalTo: root.topAnchor, constant: 10),
            statusField.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 16),
            statusField.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -16),
            webView.topAnchor.constraint(equalTo: statusField.bottomAnchor, constant: 8),
            webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        window.makeKeyAndOrderFront(nil)
    }

    private func startSuite(supportPath: String) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = ["-c", "exec ./start.command"]
        task.currentDirectoryURL = URL(fileURLWithPath: suiteRoot)

        let logPath = (supportPath as NSString).appendingPathComponent("launcher.log")
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        let handle = FileHandle(forWritingAtPath: logPath)
        handle?.seekToEndOfFile()
        launcherLog = handle
        task.standardOutput = handle
        task.standardError = handle

        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["MUSIC_SUITE_NO_BROWSER"] = "1"
        env["MUSIC_SUITE_STATE_DIR"] = supportPath
        env["MUSIC_SUITE_LOG_DIR"] = (supportPath as NSString).appendingPathComponent("logs")
        env["MUSIC_SUITE_INSTALL_AI"] = "0"
        env["MUSIC_SUITE_SKIP_INSTALL"] = "1"
        task.environment = env

        task.terminationHandler = { [weak self] finished in
            guard let self else { return }
            DispatchQueue.main.async {
                let code = finished.terminationStatus
                logLine("start.command exited with \(code); see \(logPath)")
                if code != 0 {
                    self.statusField.stringValue =
                        "Music Suite stopped (exit \(code)). Log: \(logPath)"
                }
            }
        }

        do {
            try task.run()
            launcherTask = task
            logLine("started start.command in \(suiteRoot)")
        } catch {
            statusField.stringValue = "Could not start Music Suite: \(error.localizedDescription)"
            logLine("start failed: \(error)")
        }
    }

    private func pollForUI() {
        guard !polling else { return }
        polling = true
        polls += 1
        if polls % 10 == 0 {
            logLine("poll #\(polls) enter")
        }

        var webPort: Int?
        if let data = try? Data(contentsOf: stateURL),
           let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            webPort = json["web_port"] as? Int
        }

        if polls % 10 == 0 {
            logLine("poll #\(polls) state=\(FileManager.default.fileExists(atPath: stateURL.path)) port=\(webPort.map(String.init) ?? "none")")
        }
        guard let port = webPort else {
            if polls % 10 == 0 {
                statusField.stringValue = "Starting Music Suite… (\(polls * 2)s)"
                logLine("still waiting for state file: \(stateURL.path)")
            }
            polling = false
            schedulePoll()
            return
        }

        let candidate = URL(string: "http://127.0.0.1:\(port)/")!
        let request = URLRequest(url: candidate, timeoutInterval: 2)
        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            if !ok {
                logLine("probe \(candidate.absoluteString): status=\((response as? HTTPURLResponse)?.statusCode ?? -1) error=\(error?.localizedDescription ?? "none")")
            }
            DispatchQueue.main.async {
                guard let self else { return }
                self.polling = false
                if ok {
                    self.show(candidate)
                } else {
                    self.schedulePoll()
                }
            }
        }.resume()
    }

    private func schedulePoll() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.pollForUI()
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        logLine("webview finished: \(webView.url?.absoluteString ?? "unknown")")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        logLine("webview failed: \(error.localizedDescription)")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        logLine("webview provisional failure: \(error.localizedDescription)")
    }

    private func show(_ url: URL) {
        statusField.stringValue = "Music Suite · 127.0.0.1:\(url.port ?? 0)"
        webView.isHidden = false
        webView.load(URLRequest(url: url))
        logLine("UI ready at \(url.absoluteString), hidden=\(webView.isHidden)")
    }
}

let application = NSApplication.shared
let launcher = SuiteLauncher()
application.delegate = launcher
application.setActivationPolicy(.regular)
application.activate(ignoringOtherApps: true)
application.run()
