import AVFoundation
import AppKit
import AppIntents
import Darwin
import Foundation
import Speech

// MARK: - Native Darwin Socket Bridge

public enum MekSocketBridge {
    public static func execute(_ command: String) -> String {
        let socketPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".jarvis/jarvis.sock").path
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return "Mek: Soket açılamadı." }
        defer { Darwin.close(fd) }

        var tv = timeval(tv_sec: 15, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = socketPath.utf8CString
        let maxLen = MemoryLayout.size(ofValue: addr.sun_path)
        guard pathBytes.count <= maxLen else { return "Mek: Soket yolu geçersiz." }
        withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
            ptr.withMemoryRebound(to: CChar.self, capacity: maxLen) { dst in
                for i in 0..<pathBytes.count { dst[i] = pathBytes[i] }
            }
        }
        let addrLen = socklen_t(MemoryLayout<sockaddr_un>.size)
        let connected = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                Darwin.connect(fd, sa, addrLen)
            }
        }
        guard connected == 0 else {
            return "Mek arka plan servisine bağlanılamadı. Servis aktif mi?"
        }

        let payload = Array((command.trimmingCharacters(in: .whitespacesAndNewlines) + "\n").utf8)
        let written = payload.withUnsafeBytes { buf in
            Darwin.write(fd, buf.baseAddress, buf.count)
        }
        guard written == payload.count else {
            return "Mek: Komut servise yazılamadı."
        }

        var responseData = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = Darwin.read(fd, &buffer, buffer.count)
            if count <= 0 { break }
            responseData.append(buffer, count: count)
        }
        let response = String(data: responseData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (response?.isEmpty == false) ? response! : "İşlem tamamlandı."
    }
}

// MARK: - AppleScript Scripting Command (Siri & System Automation)

@objc(MekScriptCommand)
public final class MekScriptCommand: NSScriptCommand {
    public override func performDefaultImplementation() -> Any? {
        let name = commandDescription.commandName
        let direct = directParameter as? String ?? ""
        let cmd = name.lowercased()

        if cmd == "durum" {
            return MekSocketBridge.execute("durum")
        } else if cmd == "brifing" {
            let res = MekSocketBridge.execute("brifing")
            _ = MekSocketBridge.execute("söyle " + res)
            return res
        } else if cmd == "temizle" {
            let derin = evaluatedArguments?["derin"] as? Bool ?? false
            return MekSocketBridge.execute(derin ? "temizle derin" : "temizle")
        } else if cmd == "karantina" {
            return MekSocketBridge.execute(direct.isEmpty ? "karantina" : "karantina " + direct)
        } else if cmd == "sor" {
            let query = direct.isEmpty ? (evaluatedArguments?["soru"] as? String ?? "") : direct
            return MekSocketBridge.execute("sor " + query)
        } else if cmd == "cevap" {
            return MekSocketBridge.execute("cevap " + direct)
        } else if cmd == "kurgumodu" {
            return MekSocketBridge.execute("odak zorla")
        } else if cmd == "fansessiz" {
            return MekSocketBridge.execute("fan sessiz")
        } else if cmd == "kurgu" {
            return MekSocketBridge.execute("kurgu durum")
        } else if cmd == "kurguoptimize" {
            return MekSocketBridge.execute("kurgu optimize")
        } else if cmd == "davincirender" {
            return MekSocketBridge.execute("kurgu render")
        } else if cmd == "premiereexport" {
            return MekSocketBridge.execute("kurgu export")
        } else if cmd == "kurgusessizlik" {
            return MekSocketBridge.execute("kurgu sessizlik " + direct)
        } else if cmd == "kurgurenk" {
            return MekSocketBridge.execute("kurgu renk " + direct)
        } else if cmd == "kurguefekt" {
            return MekSocketBridge.execute("kurgu efekt " + direct)
        } else if cmd == "kurgukaba" {
            return MekSocketBridge.execute("kurgu kaba " + direct)
        }
        return MekSocketBridge.execute(direct.isEmpty ? cmd : "\(cmd) \(direct)")
    }
}

// MARK: - App Intents & Apple Intelligence Integration (Siri)

@available(macOS 13.0, *)
public struct MekStatusIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Durumu"
    public static var description = IntentDescription("Mek sistem, donanım, kalkan ve kurgu durumunu bildirir.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("durum")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekQuarantineIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Karantina"
    public static var description = IntentDescription("Mek tarafından karantinaya alınan zararlı ve şüpheli öğeleri listeler.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("karantina")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekBriefingIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Brifing"
    public static var description = IntentDescription("Günün brifingini seslendirir ve metin olarak iletir.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("brifing")
        _ = MekSocketBridge.execute("söyle " + result)
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekCleanIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Temizlik"
    public static var description = IntentDescription("Önbellek ve RAM rahatlatma temizliğini başlatır.")
    public static var openAppWhenRun: Bool = false

    @Parameter(title: "Derin Temizlik", default: false)
    public var derin: Bool

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let cmd = derin ? "temizle derin" : "temizle"
        let result = MekSocketBridge.execute(cmd)
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekAskIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek'e Sor"
    public static var description = IntentDescription("Mek asistanına doğal dille soru sorar.")
    public static var openAppWhenRun: Bool = false

    @Parameter(title: "Soru")
    public var soru: String

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("sor " + soru)
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekCommandIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek"
    public static var description = IntentDescription("Mek asistanına doğal dille soru sorar veya komut verir.")
    public static var openAppWhenRun: Bool = false

    @Parameter(title: "Komut veya Soru")
    public var prompt: String?

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        var cmd = prompt?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if cmd.isEmpty {
            cmd = try await $prompt.requestValue("Mek dinliyor, ne yapmamı istersiniz?")
        }
        let result = MekSocketBridge.execute(cmd)
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekShortcuts: AppShortcutsProvider {
    public static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: MekCommandIntent(),
            phrases: [
                "\(.applicationName)",
                "\(.applicationName)'e sor",
                "\(.applicationName)'e sor \(\.$prompt)",
                "\(.applicationName) ile konuş",
                "\(.applicationName) \(\.$prompt)"
            ],
            shortTitle: "Mek",
            systemImageName: "sparkles"
        )
        AppShortcut(
            intent: MekQuarantineIntent(),
            phrases: [
                "\(.applicationName) karantina",
                "\(.applicationName) karantinada ne var",
                "\(.applicationName) neleri karantinaya aldın"
            ],
            shortTitle: "Mek Karantina",
            systemImageName: "shield"
        )
        AppShortcut(
            intent: MekStatusIntent(),
            phrases: [
                "\(.applicationName) durum",
                "\(.applicationName) durum nedir",
                "\(.applicationName) sistem durumu"
            ],
            shortTitle: "Mek Durum",
            systemImageName: "gauge"
        )
    }
}

// MARK: - Menu Bar & Application Delegate

@MainActor
final class MekMenuBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var statusMenu: NSMenu!
    private var commandInProgress = false
    private var appIcon: NSImage?
    private var lastPromptedQuestionId: String? = nil
    private var questionPollTimer: Timer? = nil

    func applicationWillFinishLaunching(_ notification: Notification) {
        // Raw Apple Event Suite Handler (MEKS: STAT, BRIF, CLEN, QRNT, ASKA, ANSW, PROF, FANS)
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleMekAppleEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(0x4D454B53), // 'MEKS'
            andEventID: AEEventID(0x2A2A2A2A)      // '****' (typeWildCard)
        )

        // URL Scheme Handler (mek://durum, mek://brifing, mek://temizle, mek://karantina, mek://sor?q=...)
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleGetURLEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        loadBrandIcon()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        configureStatusButton(suffix: "")

        statusMenu = NSMenu()
        statusMenu.delegate = self
        statusItem.menu = statusMenu

        // App Shortcuts ve Siri entegrasyonunu macOS sistemine bildir
        if #available(macOS 13.0, *) {
            MekShortcuts.updateAppShortcutParameters()
        }

        startWorkspaceObserver()
        startQuestionPolling()
    }

    // MARK: - Dynamic Quarantine Menu (Kullanıcı Tıkladığında Canlı Listeleme)

    func menuWillOpen(_ menu: NSMenu) {
        menu.removeAllItems()

        // 1. Durum Başlığı
        let headerItem = NSMenuItem(title: "🛡️ Mek Kalkanı: Aktif (Sistem Korunuyor)", action: nil, keyEquivalent: "")
        headerItem.isEnabled = false
        menu.addItem(headerItem)

        menu.addItem(.separator())

        // 2. Karantina Bölümü
        let qSectionHeader = NSMenuItem(title: "KARANTİNAYA ALINAN ÖĞELER", action: nil, keyEquivalent: "")
        qSectionHeader.isEnabled = false
        menu.addItem(qSectionHeader)

        let rawJson = MekSocketBridge.execute("karantina_json")
        var itemsCount = 0

        if let data = rawJson.data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let items = obj["items"] as? [[String: Any]] {
            itemsCount = items.count
            if items.isEmpty {
                let cleanItem = NSMenuItem(title: "  ✓ Karantina Temiz (0 Şüpheli Dosya)", action: nil, keyEquivalent: "")
                cleanItem.isEnabled = false
                menu.addItem(cleanItem)
            } else {
                for item in items {
                    let no = item["no"] as? Int ?? 1
                    let name = item["name"] as? String ?? "Bilinmeyen Dosya"
                    let date = item["date"] as? String ?? ""
                    let orig = item["original"] as? String ?? ""
                    let dest = item["dest"] as? String ?? ""

                    let itemTitle = "  ⚠️ \(no)) \(name) (\(date))"
                    let mItem = NSMenuItem(title: itemTitle, action: nil, keyEquivalent: "")

                    let sub = NSMenu()

                    let origItem = NSMenuItem(title: "Orijinal Konum: \(orig)", action: nil, keyEquivalent: "")
                    origItem.isEnabled = false
                    sub.addItem(origItem)

                    sub.addItem(.separator())

                    let restoreItem = NSMenuItem(title: "Geri Yükle (Karantinadan Çıkar)", action: #selector(restoreQuarantineItem(_:)), keyEquivalent: "")
                    restoreItem.target = self
                    restoreItem.tag = no
                    sub.addItem(restoreItem)

                    let revealItem = NSMenuItem(title: "Karantinadaki Dosyayı Finder'da Göster", action: #selector(revealQuarantineItem(_:)), keyEquivalent: "")
                    revealItem.target = self
                    revealItem.representedObject = dest
                    sub.addItem(revealItem)

                    mItem.submenu = sub
                    menu.addItem(mItem)
                }
            }
        } else {
            let cleanItem = NSMenuItem(title: "  ✓ Karantina Temiz (Tehdit Yok)", action: nil, keyEquivalent: "")
            cleanItem.isEnabled = false
            menu.addItem(cleanItem)
        }

        menu.addItem(.separator())

        // 3. Karantina Klasör Eylemleri
        let openFolderItem = NSMenuItem(title: "📂 Karantina Klasörünü Aç...", action: #selector(openQuarantineFolder), keyEquivalent: "")
        openFolderItem.target = self
        menu.addItem(openFolderItem)

        if itemsCount > 0 {
            let clearItem = NSMenuItem(title: "🗑️ Karantinayı Boşalt...", action: #selector(clearQuarantine), keyEquivalent: "")
            clearItem.target = self
            menu.addItem(clearItem)
        }

        menu.addItem(.separator())

        // 4. Siri Entegrasyonu Rehberi
        let siriItem = NSMenuItem(title: "🎙️ Siri: \"Hey Siri, Mek\"", action: #selector(siriGuide), keyEquivalent: "")
        siriItem.target = self
        menu.addItem(siriItem)

        menu.addItem(.separator())

        // 5. Çıkış
        let quitItem = NSMenuItem(title: "Mek'i Kapat", action: #selector(quit), keyEquivalent: "")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    @objc private func restoreQuarantineItem(_ sender: NSMenuItem) {
        let no = sender.tag
        let res = MekSocketBridge.execute("karantina geri \(no)")
        showResult(res)
    }

    @objc private func revealQuarantineItem(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String, !path.isEmpty else { return }
        NSWorkspace.shared.selectFile(path, inFileViewerRootedAtPath: "")
    }

    @objc private func openQuarantineFolder() {
        let qPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".jarvis/karantina").path
        NSWorkspace.shared.open(URL(fileURLWithPath: qPath))
    }

    @objc private func clearQuarantine() {
        let alert = makeAlert()
        alert.messageText = "Karantinayı Boşalt"
        alert.informativeText = "Karantinadaki tüm şüpheli dosyalar kalıcı olarak silinecek. Onaylıyor musunuz?"
        alert.addButton(withTitle: "Evet, Boşalt")
        alert.addButton(withTitle: "Vazgeç")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let res = MekSocketBridge.execute("karantina temizle")
        showResult(res)
    }

    @objc private func siriGuide() {
        let alert = makeAlert()
        alert.messageText = "Mek Siri & Ses Entegrasyonu"
        alert.informativeText = """
        Mek ile tamamen Siri üzerinden iletişime geçebilirsiniz.

        Örnek sesli komutlar:
        • "Hey Siri, Mek"
        • "Hey Siri, Mek'e sor sistem durumu nedir?"
        • "Hey Siri, Mek karantinada ne var?"
        • "Hey Siri, Mek kurgu modunu aç"
        • "Hey Siri, Mek temizlik yap"
        """
        alert.addButton(withTitle: "Anladım")
        alert.runModal()
    }

    // MARK: - Apple Event & URL Scheme

    @objc private func handleMekAppleEvent(_ event: NSAppleEventDescriptor, withReplyEvent replyEvent: NSAppleEventDescriptor) {
        let eventID = event.eventID
        var cmd = "durum"
        if eventID == 0x53544154 { // STAT: durum
            cmd = "durum"
        } else if eventID == 0x42524946 { // BRIF: brifing
            let res = MekSocketBridge.execute("brifing")
            _ = MekSocketBridge.execute("söyle " + res)
            if replyEvent.descriptorType != typeNull {
                replyEvent.setParam(NSAppleEventDescriptor(string: res), forKeyword: keyDirectObject)
            }
            return
        } else if eventID == 0x434C454E { // CLEN: temizle
            let derin = event.paramDescriptor(forKeyword: 0x70444552)?.booleanValue ?? false
            cmd = derin ? "temizle derin" : "temizle"
        } else if eventID == 0x51524E54 { // QRNT: karantina
            let direct = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue ?? ""
            cmd = direct.isEmpty ? "karantina" : "karantina " + direct
        } else if eventID == 0x41534B41 { // ASKA: sor
            let q = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue ?? ""
            cmd = "sor " + q
        } else if eventID == 0x414E5357 { // ANSW: cevap
            let a = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue ?? ""
            cmd = "cevap " + a
        } else if eventID == 0x50524F46 { // PROF: kurguModu
            cmd = "odak zorla"
        } else if eventID == 0x46414E53 { // FANS: fanSessiz
            cmd = "fan sessiz"
        }

        let result = MekSocketBridge.execute(cmd)
        if replyEvent.descriptorType != typeNull {
            replyEvent.setParam(NSAppleEventDescriptor(string: result), forKeyword: keyDirectObject)
        }
    }

    @objc private func handleGetURLEvent(_ event: NSAppleEventDescriptor, withReplyEvent replyEvent: NSAppleEventDescriptor) {
        guard let urlString = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
              let url = URL(string: urlString) else { return }

        let action = url.host ?? url.path.replacingOccurrences(of: "/", with: "")
        var command = action.isEmpty ? "durum" : action

        if let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
           let queryItems = components.queryItems {
            for item in queryItems {
                if item.name == "q" || item.name == "soru", let val = item.value {
                    command = "sor " + val
                } else if item.name == "cevap" || item.name == "secenek", let val = item.value {
                    command = "cevap " + val
                }
            }
        }

        let result = MekSocketBridge.execute(command)
        if replyEvent.descriptorType != typeNull {
            replyEvent.setParam(NSAppleEventDescriptor(string: result), forKeyword: keyDirectObject)
        }
    }

    private func loadBrandIcon() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates = [
            "/Applications/Mek.app/Contents/Resources/Mek.icns",
            home.appendingPathComponent(".jarvis/Mek.icns").path,
            home.appendingPathComponent(".jarvis/mek.png").path
        ]
        for path in candidates {
            if let img = NSImage(contentsOfFile: path) {
                appIcon = img
                NSApp.applicationIconImage = img
                break
            }
        }
    }

    private func configureStatusButton(suffix: String) {
        guard let button = statusItem.button else { return }
        button.image = nil
        button.title = suffix.isEmpty ? "Mek" : "Mek \(suffix)"
    }

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
        DispatchQueue.global(qos: .userInteractive).async {
            _ = MekSocketBridge.execute(command)
        }
    }

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
            _ = MekSocketBridge.execute("cevap " + key)
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func showResult(_ text: String) {
        let alert = makeAlert()
        alert.messageText = "Mek"
        alert.informativeText = text
        alert.addButton(withTitle: "Tamam")
        alert.runModal()
    }
}

// MARK: - Entry Point

@main
@MainActor
struct MekAppMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = MekMenuBar()
        app.delegate = delegate
        app.run()
    }
}
