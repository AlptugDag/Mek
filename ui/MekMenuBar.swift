import AVFoundation
import AppKit
import Foundation
import Speech

@MainActor
final class MekMenuBar: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var commandInProgress = false
    private let engine = AVAudioEngine()
    private var listening = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "Mek"

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
        startQuestionPolling()
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
                statusItem.button?.title = "Mek"
            }
            return
        }

        statusItem.button?.title = "Mek ❓"
        guard lastPromptedQuestionId != qid else { return }
        lastPromptedQuestionId = qid

        let alert = NSAlert()
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
        let alert = NSAlert()
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
        statusItem.button?.title = "Mek 🎙"
        var finished = false
        let finish: (String?) -> Void = { [weak self] text in
            guard let self = self, !finished else { return }
            finished = true
            self.engine.stop()
            self.engine.inputNode.removeTap(onBus: 0)
            request.endAudio()
            self.listening = false
            self.statusItem.button?.title = "Mek"
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
        let alert = NSAlert()
        alert.messageText = "Mek onayınızı istiyor"
        alert.informativeText = text.replacingOccurrences(of: " Onaylamak için `mek onay`, vazgeçmek için `mek iptal` yazın.", with: "")
        alert.addButton(withTitle: "Onayla")
        alert.addButton(withTitle: "Vazgeç")
        send(alert.runModal() == .alertFirstButtonReturn ? "onay" : "iptal")
    }

    private func showResult(_ text: String) {
        let alert = NSAlert()
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
