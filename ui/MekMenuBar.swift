import AVFoundation
import AppKit
import Darwin
import Foundation
import Speech

@MainActor
final class MekMenuBar: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var commandInProgress = false
    private let engine = AVAudioEngine()
    private var listening = false
    private var appIcon: NSImage?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        loadBrandIcon()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        configureStatusButton(suffix: "")

        let menu = NSMenu()
        add("Brifing", to: menu, action: #selector(runBriefing))
        add("Sistem durumu", to: menu, action: #selector(runStatus))
        add("Bellek bütçesi", to: menu, action: #selector(runMemory))
        add("Piyasa izleme listesi", to: menu, action: #selector(runWatchlist))
        add("Güç durumu", to: menu, action: #selector(runPower))
        add("Güvenlik özeti", to: menu, action: #selector(runSecurity))
        menu.addItem(.separator())
        add("Mek'e sor veya komut ver…", to: menu, action: #selector(promptForCommand))
        add("Sesle sor (8 sn dinler)", to: menu, action: #selector(askByVoice))
        add("Komut yardımı", to: menu, action: #selector(runHelp))
        menu.addItem(.separator())
        add("Mek'i kapat", to: menu, action: #selector(quit))
        statusItem.menu = menu

        startWorkspaceObserver()
        startQuestionPolling()
    }

    private func loadBrandIcon() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates = [
            home.appendingPathComponent(".jarvis/mek.png").path,
            home.appendingPathComponent(".jarvis/Mek.icns").path
        ]
        for path in candidates {
            if let img = NSImage(contentsOfFile: path) {
                appIcon = img
                NSApp.applicationIconImage = img
                DispatchQueue.global(qos: .utility).async {
                    for target in [".jarvis", ".jarvis/mek-menubar", ".local/bin/jarvis"] {
                        let p = home.appendingPathComponent(target).path
                        NSWorkspace.shared.setIcon(img, forFile: p)
                    }
                }
                break
            }
        }
    }

    private func configureStatusButton(suffix: String) {
        guard let button = statusItem.button else { return }
        button.image = nil
        button.title = suffix.isEmpty ? "Mek" : "Mek \(suffix)"
    }

    /// Ön plandaki uygulama değiştiğinde (<15 ms) doğrudan Unix soketi üzerinden Mek servisine haber verir.
    /// Böylece Premiere Pro, Photoshop ve Topaz arasında geçiş yapıldığı anda P-çekirdek / E-çekirdek
    /// öncelikleri hiçbir alt süreç (nc vb.) açılmadan sıfır gecikmeyle güncellenir.
    private func startWorkspaceObserver() {
        let nc = NSWorkspace.shared.notificationCenter
        nc.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] note in
            guard let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else { return }
            let pid = app.processIdentifier
            let name = app.localizedName ?? ""
            guard pid > 0, !name.isEmpty else { return }
            self?.sendSilent("odak \(pid) \(name)")
        }
    }

    private nonisolated func sendSilent(_ command: String) {
        let socketPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".jarvis/jarvis.sock").path
        DispatchQueue.global(qos: .userInteractive).async {
            let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            guard fd >= 0 else { return }
            defer { Darwin.close(fd) }

            var addr = sockaddr_un()
            addr.sun_family = sa_family_t(AF_UNIX)
            let pathBytes = socketPath.utf8CString
            let maxLen = MemoryLayout.size(ofValue: addr.sun_path)
            guard pathBytes.count <= maxLen else { return }
            withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
                ptr.withMemoryRebound(to: CChar.self, capacity: maxLen) { dst in
                    for i in 0..<pathBytes.count {
                        dst[i] = pathBytes[i]
                    }
                }
            }
            let addrLen = socklen_t(MemoryLayout<sockaddr_un>.size)
            let connected = withUnsafePointer(to: &addr) { ptr in
                ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                    Darwin.connect(fd, sa, addrLen)
                }
            }
            guard connected == 0 else { return }
            let payload = Array((command + "\n").utf8)
            _ = payload.withUnsafeBytes { buf in
                Darwin.write(fd, buf.baseAddress, buf.count)
            }
        }
    }

    private var lastPromptedQuestionId: String? = nil
    private var questionPollTimer: Timer? = nil

    private func startQuestionPolling() {
        questionPollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.checkPendingQuestion()
            }
        }
    }

    private func makeAlert() -> NSAlert {
        let alert = NSAlert()
        if let icon = appIcon {
            alert.icon = icon
        }
        return alert
    }

    private func checkPendingQuestion() {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".jarvis/state/pending_question.json").path
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let qid = json["id"] as? String,
              let text = json["text"] as? String,
              let options = json["options"] as? [[String: String]] else {
            if lastPromptedQuestionId != nil {
                lastPromptedQuestionId = nil
                configureStatusButton(suffix: "")
            }
            return
        }

        configureStatusButton(suffix: "❓")
        guard lastPromptedQuestionId != qid else { return }
        lastPromptedQuestionId = qid

        let alert = makeAlert()
        alert.messageText = "Mek Soruyor"
        alert.informativeText = text
        for opt in options {
            alert.addButton(withTitle: opt["label"] ?? opt["key"] ?? "Tamam")
        }
        let responseIdx = alert.runModal().rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
        if responseIdx >= 0 && responseIdx < options.count {
            let key = options[responseIdx]["key"] ?? ""
            send("cevap " + key)
        }
    }

    private func add(_ title: String, to menu: NSMenu, action: Selector) {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        menu.addItem(item)
    }

    @objc private func runBriefing() {
        send("brifing")
    }

    @objc private func runStatus() {
        send("durum")
    }

    @objc private func runMemory() {
        send("bellek")
    }

    @objc private func runWatchlist() {
        send("izleme")
    }

    @objc private func runPower() {
        send("güç")
    }

    @objc private func runSecurity() {
        send("güvenlik")
    }

    @objc private func runHelp() {
        send("yardım")
    }

    @objc private func promptForCommand() {
        let alert = makeAlert()
        alert.messageText = "Mek'e sorun veya komut verin"
        alert.informativeText = "Örnek: bilgisayar neden yavaş? · temizle · derin: bu hafta ne yaptın?"
        alert.addButton(withTitle: "Gönder")
        alert.addButton(withTitle: "Vazgeç")

        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        input.placeholderString = "Serbest soru ya da komut"
        alert.accessoryView = input
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        let command = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !command.isEmpty else { return }
        send(command)
    }

    /// Mikrofon yalnızca bu menü öğesine tıklayınca açılır; en fazla 8 saniye sonra kapanır.
    @objc private func askByVoice() {
        guard !listening else { return }
        SFSpeechRecognizer.requestAuthorization { [weak self] status in
            DispatchQueue.main.async {
                guard status == .authorized else {
                    self?.showResult("Konuşma tanıma izni verilmedi (Sistem Ayarları > Gizlilik).")
                    return
                }
                self?.startListening()
            }
        }
    }

    private func startListening() {
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "tr-TR")), recognizer.isAvailable else {
            showResult("Türkçe konuşma tanıma şu an kullanılamıyor.")
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        let input = engine.inputNode
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            request.append(buffer)
        }
        engine.prepare()
        do { try engine.start() } catch {
            showResult("Mikrofon açılamadı: \(error.localizedDescription)")
            return
        }
        listening = true
        configureStatusButton(suffix: "🎙")
        var finished = false
        let finish: (String?) -> Void = { [weak self] text in
            guard let self = self, !finished else { return }
            finished = true
            self.engine.stop()
            self.engine.inputNode.removeTap(onBus: 0)
            request.endAudio()
            self.listening = false
            self.configureStatusButton(suffix: "")
            if let text = text, !text.isEmpty { self.send(text, spoken: true) }
        }
        var best = ""
        recognizer.recognitionTask(with: request) { result, error in
            if let result = result { best = result.bestTranscription.formattedString }
            if (result?.isFinal ?? false) || error != nil { DispatchQueue.main.async { finish(best) } }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { finish(best) }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func send(_ command: String, spoken: Bool = false) {
        guard !commandInProgress else {
            showResult("Mek önceki komutu tamamlıyor; lütfen birkaç saniye sonra yeniden deneyin.")
            return
        }
        commandInProgress = true
        let socketPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".jarvis/jarvis.sock").path

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result: String
            do {
                let process = Process()
                let input = Pipe()
                let output = Pipe()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
                process.arguments = ["-U", "-w", "180", socketPath]
                process.standardInput = input
                process.standardOutput = output
                process.standardError = FileHandle.nullDevice
                try process.run()
                try input.fileHandleForWriting.write(contentsOf: Data((command + "\n").utf8))
                try input.fileHandleForWriting.close()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let response = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
                result = process.terminationStatus == 0
                    ? (response.flatMap { $0.isEmpty ? nil : $0 } ?? "Komut tamamlandı.")
                    : "Mek komut soketine bağlanamadı. Servisin çalıştığını kontrol edin."
            } catch {
                result = "Komut gönderilemedi: \(error.localizedDescription)"
            }

            DispatchQueue.main.async {
                self?.commandInProgress = false
                if command.hasPrefix("söyle ") {
                    return
                } else if result.contains("`mek onay`") {
                    self?.askConfirmation(result)
                } else {
                    self?.showResult(result)
                    if spoken { self?.send("söyle " + result) }
                }
            }
        }
    }

    /// Onay gerektiren eylemler yalnızca burada, sizin tıklamanızla çalışır.
    private func askConfirmation(_ text: String) {
        let alert = makeAlert()
        alert.messageText = "Mek onayınızı istiyor"
        alert.informativeText = text.replacingOccurrences(of: " Onaylamak için `mek onay`, vazgeçmek için `mek iptal` yazın.", with: "")
        alert.addButton(withTitle: "Onayla")
        alert.addButton(withTitle: "Vazgeç")
        send(alert.runModal() == .alertFirstButtonReturn ? "onay" : "iptal")
    }

    private func showResult(_ text: String) {
        let alert = makeAlert()
        alert.messageText = "Mek"
        alert.informativeText = text
        alert.addButton(withTitle: "Tamam")
        alert.runModal()
    }
}

@main
@MainActor
struct MekMenuBarApp {
    static func main() {
        let app = NSApplication.shared
        let delegate = MekMenuBar()
        app.delegate = delegate
        app.run()
    }
}
