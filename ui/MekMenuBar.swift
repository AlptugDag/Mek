import AppKit
import Foundation

@MainActor
final class MekMenuBar: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var commandInProgress = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "Mek"

        let menu = NSMenu()
        add("Brifing", to: menu, action: #selector(runBriefing))
        add("Sistem durumu", to: menu, action: #selector(runStatus))
        add("Piyasa izleme listesi", to: menu, action: #selector(runWatchlist))
        add("Güç durumu", to: menu, action: #selector(runPower))
        add("Güvenlik özeti", to: menu, action: #selector(runSecurity))
        menu.addItem(.separator())
        add("Mek'e komut ver…", to: menu, action: #selector(promptForCommand))
        add("Komut yardımı", to: menu, action: #selector(runHelp))
        menu.addItem(.separator())
        add("Mek'i kapat", to: menu, action: #selector(quit))
        statusItem.menu = menu
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
        alert.messageText = "Mek'e komut verin"
        alert.informativeText = "Örnek: izleme ekle THYAO.IS veya temizle"
        alert.addButton(withTitle: "Gönder")
        alert.addButton(withTitle: "Vazgeç")

        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        input.placeholderString = "Komut veya izleme tercihi"
        alert.accessoryView = input
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        let command = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !command.isEmpty else { return }
        send(command)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func send(_ command: String) {
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
                self?.showResult(result)
            }
        }
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
