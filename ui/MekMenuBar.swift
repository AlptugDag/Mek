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
        }
        return MekSocketBridge.execute(direct.isEmpty ? cmd : "\(cmd) \(direct)")
    }
}

// MARK: - App Intents & Apple Intelligence Integration (Siri Beta)

@available(macOS 13.0, *)
public struct MekStatusIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Durumu"
    public static var description = IntentDescription("Mek sistem, donanım, fan ve kurgu durumunu bildirir.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("durum")
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
    public static var description = IntentDescription("Mek cihaz içi Apple Intelligence asistanına soru sorar.")
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
public struct MekCreativeModeIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Kurgu Modu"
    public static var description = IntentDescription("Kurgu uygulamaları için en yüksek performansı açar.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("odak zorla")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekSilentFanIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Sessiz Fan"
    public static var description = IntentDescription("Mac Studio fanlarını Akıllı Sessiz kipe alır.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("fan sessiz")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekKurguStatusIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Kurgu Durumu"
    public static var description = IntentDescription("Premiere Pro ve DaVinci Resolve aktif kurgu durumunu bildirir.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("kurgu durum")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekKurguOptimizeIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Kurgu Optimizasyonu"
    public static var description = IntentDescription("Kurgu ortamını en yüksek performans için optimize eder.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("kurgu optimize")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekDaVinciRenderIntent: AppIntent {
    public static var title: LocalizedStringResource = "DaVinci Render Başlat"
    public static var description = IntentDescription("DaVinci Resolve Deliver sayfasındaki render kuyruğunu başlatır.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("kurgu render")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekPremiereExportIntent: AppIntent {
    public static var title: LocalizedStringResource = "Premiere Dışa Aktar"
    public static var description = IntentDescription("Premiere Pro sekansını Adobe Media Encoder kuyruğuna gönderir.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("kurgu export")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekShortcuts: AppShortcutsProvider {
    public static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: MekStatusIntent(),
            phrases: [
                "\(.applicationName) durumu",
                "\(.applicationName) durum",
                "\(.applicationName) rapor ver",
                "\(.applicationName) nasıl"
            ],
            shortTitle: "Mek Durumu",
            systemImageName: "gauge.with.dots.needle.bottom.50percent"
        )
        AppShortcut(
            intent: MekBriefingIntent(),
            phrases: [
                "\(.applicationName) brifing",
                "\(.applicationName) günün brifingi",
                "\(.applicationName) sabah brifingi"
            ],
            shortTitle: "Mek Brifing",
            systemImageName: "sun.max"
        )
        AppShortcut(
            intent: MekCleanIntent(),
            phrases: [
                "\(.applicationName) temizlik yap",
                "\(.applicationName) temizle",
                "\(.applicationName) önbelleği temizle"
            ],
            shortTitle: "Mek Temizle",
            systemImageName: "sparkles"
        )
        AppShortcut(
            intent: MekCreativeModeIntent(),
            phrases: [
                "\(.applicationName) kurgu modu",
                "\(.applicationName) performans modu"
            ],
            shortTitle: "Kurgu Modu",
            systemImageName: "film"
        )
        AppShortcut(
            intent: MekSilentFanIntent(),
            phrases: [
                "\(.applicationName) fanı sessize al",
                "\(.applicationName) sessiz fan"
            ],
            shortTitle: "Sessiz Fan",
            systemImageName: "fanblades"
        )
        AppShortcut(
            intent: MekKurguStatusIntent(),
            phrases: [
                "\(.applicationName) kurgu durumu",
                "\(.applicationName) montaj durumu",
                "\(.applicationName) kurgu nasıl"
            ],
            shortTitle: "Kurgu Durumu",
            systemImageName: "film.stack"
        )
        AppShortcut(
            intent: MekKurguOptimizeIntent(),
            phrases: [
                "\(.applicationName) kurguyu hızlandır",
                "\(.applicationName) kurgu optimize et",
                "\(.applicationName) kurgu hazırla"
            ],
            shortTitle: "Kurgu Optimize Et",
            systemImageName: "bolt.badge.clock"
        )
        AppShortcut(
            intent: MekDaVinciRenderIntent(),
            phrases: [
                "\(.applicationName) davinci render başlat",
                "\(.applicationName) render al",
                "\(.applicationName) render başlat"
            ],
            shortTitle: "DaVinci Render",
            systemImageName: "play.circle"
        )
        AppShortcut(
            intent: MekPremiereExportIntent(),
            phrases: [
                "\(.applicationName) premiere export et",
                "\(.applicationName) premiere dışa aktar"
            ],
            shortTitle: "Premiere Export",
            systemImageName: "arrow.up.forward.app"
        )
    }
}

// MARK: - Menu Bar & Application Delegate

@MainActor
final class MekMenuBar: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var commandInProgress = false
    private let engine = AVAudioEngine()
    private var listening = false
    private var appIcon: NSImage?
    private var lastPromptedQuestionId: String? = nil
    private var questionPollTimer: Timer? = nil

    func applicationWillFinishLaunching(_ notification: Notification) {
        // Raw Apple Event Suite Handler (MEKS: STAT, BRIF, CLEN, ASKA, ANSW, PROF, FANS)
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleMekAppleEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(0x4D454B53), // 'MEKS'
            andEventID: AEEventID(0x2A2A2A2A)      // '****' (typeWildCard)
        )

        // URL Scheme Handler (mek://durum, mek://brifing, mek://temizle, mek://sor?q=...)
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

        let menu = NSMenu()
        add("Brifing", to: menu, action: #selector(runBriefing))
        add("Sistem durumu", to: menu, action: #selector(runStatus))
        add("Bellek bütçesi", to: menu, action: #selector(runMemory))
        add("Ultra-Scratch (400 GB/s RAM Disk)", to: menu, action: #selector(runRamDisk))
        add("Proje Zaman Makinesi (APFS Klon)", to: menu, action: #selector(runTimeMachine))
        add("Termal & Fan (Akıllı Sessiz Kip)", to: menu, action: #selector(runFanSilent))
        add("Piyasa izleme listesi", to: menu, action: #selector(runWatchlist))
        add("Güç durumu", to: menu, action: #selector(runPower))
        add("Güvenlik özeti", to: menu, action: #selector(runSecurity))
        menu.addItem(.separator())
        let kurguItem = NSMenuItem(title: "🎬 Video Kurgu (Apple Intelligence)", action: nil, keyEquivalent: "")
        let kurguSubMenu = NSMenu()
        add("Kurgu Durumu (Premiere / DaVinci)", to: kurguSubMenu, action: #selector(runKurguStatus))
        add("Kurgu Ortamını Optimize Et (P-Core + RAM-disk)", to: kurguSubMenu, action: #selector(runKurguOptimize))
        add("DaVinci Render Kuyruğunu Başlat", to: kurguSubMenu, action: #selector(runDaVinciRender))
        add("Premiere Sekansını Media Encoder'a Aktar", to: kurguSubMenu, action: #selector(runPremiereExport))
        kurguItem.submenu = kurguSubMenu
        menu.addItem(kurguItem)
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
            let derin = event.paramDescriptor(forKeyword: 0x70444552)?.booleanValue ?? false // pDER
            cmd = derin ? "temizle derin" : "temizle"
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
        } else if eventID == 0x4B524731 { // KRG1: kurgu
            cmd = "kurgu durum"
        } else if eventID == 0x4B52474F { // KRGO: kurguOptimize
            cmd = "kurgu optimize"
        } else if eventID == 0x4456524E { // DVRN: davinciRender
            cmd = "kurgu render"
        } else if eventID == 0x504D4558 { // PMEX: premiereExport
            cmd = "kurgu export"
        } else if eventID == 0x4B524753 { // KRGS: kurguSessizlik
            let file = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue ?? ""
            cmd = "kurgu sessizlik " + file
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

    /// Ön plandaki uygulama değiştiğinde (<15 ms) doğrudan Unix soketi üzerinden Mek servisine haber verir.
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
            send("cevap " + key)
        }
    }

    private func add(_ title: String, to menu: NSMenu, action: Selector) {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        menu.addItem(item)
    }

    @objc private func runBriefing() { send("brifing") }
    @objc private func runStatus() { send("durum") }
    @objc private func runMemory() { send("bellek") }
    @objc private func runRamDisk() { send("ramdisk") }
    @objc private func runTimeMachine() { send("zamanmakinesi") }
    @objc private func runFanSilent() { send("fan sessiz") }
    @objc private func runWatchlist() { send("izleme") }
    @objc private func runPower() { send("güç") }
    @objc private func runSecurity() { send("güvenlik") }
    @objc private func runHelp() { send("yardım") }
    @objc private func runKurguStatus() { send("kurgu durum") }
    @objc private func runKurguOptimize() { send("kurgu optimize") }
    @objc private func runDaVinciRender() { send("kurgu render") }
    @objc private func runPremiereExport() { send("kurgu export") }

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

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = MekSocketBridge.execute(command)

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
