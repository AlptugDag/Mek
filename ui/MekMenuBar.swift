import AVFoundation
import AppKit
import AppIntents
import Darwin
import Foundation
import Speech

// MARK: - Native Darwin Socket Bridge

public enum MekSocketBridge {
    /// Siri bir intent'i çalıştırırken uzun süre bekleyemez; 25 saniye
    /// Siri'nin kendi sınırının altında kalır ama ağır komutlara da yeter.
    private static let timeoutSeconds: Int = 25

    public static func execute(_ command: String) -> String {
        let socketPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".jarvis/jarvis.sock").path
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return "Mek: Soket açılamadı." }
        defer { Darwin.close(fd) }

        var tv = timeval(tv_sec: timeoutSeconds, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))

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
            return "Mek arka plan servisine bağlanılamadı. Servis aktif mi? "
                 + "Terminalde kontrol: launchctl list | grep com.alptug.jarvis"
        }

        let payload = Array((command.trimmingCharacters(in: .whitespacesAndNewlines) + "\n").utf8)
        var sent = 0
        while sent < payload.count {
            let n = payload.withUnsafeBytes { buf -> Int in
                Darwin.write(fd, buf.baseAddress!.advanced(by: sent), payload.count - sent)
            }
            if n > 0 {
                sent += n
            } else if n < 0 && errno == EINTR {
                continue
            } else {
                return "Mek: Komut servise yazılamadı."
            }
        }
        shutdown(fd, SHUT_WR)

        var responseData = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = Darwin.read(fd, &buffer, buffer.count)
            if count > 0 {
                responseData.append(buffer, count: count)
            } else if count < 0 && errno == EINTR {
                continue
            } else {
                break
            }
        }
        let response = String(data: responseData, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
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
        } else if cmd == "performansmodu" || cmd == "optimize" {
            return MekSocketBridge.execute("performans")
        } else if cmd == "donanimsagi" || cmd == "saglik" {
            return MekSocketBridge.execute("sağlık")
        } else if cmd == "analiz" || cmd == "aianaliz" {
            return MekSocketBridge.execute("analiz")
        } else if cmd == "fansessiz" {
            return MekSocketBridge.execute("fan sessiz")
        }
        return MekSocketBridge.execute(direct.isEmpty ? cmd : "\(cmd) \(direct)")
    }
}

// MARK: - App Intents & Apple Intelligence Integration (Siri)

@available(macOS 13.0, *)
public struct MekStatusIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Durumu"
    public static var description = IntentDescription("Mek sistem, donanım, kalkan ve performans durumunu bildirir.")
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
public struct MekPerformanceModeIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Performans Modu"
    public static var description = IntentDescription("Sistem ve tüm uygulamalar için tam performans ve donanım korumasını etkinleştirir.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("performans")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekHardwareHealthIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Donanım Sağlığı"
    public static var description = IntentDescription("CPU/GPU/NVMe SSD sıcaklıkları, fan RPM, bellek ve disk ömrü özetini bildirir.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("sağlık")
        return .result(dialog: IntentDialog(stringLiteral: result))
    }
}

@available(macOS 13.0, *)
public struct MekAIAnalyzeIntent: AppIntent {
    public static var title: LocalizedStringResource = "Mek Sistem Analizi"
    public static var description = IntentDescription("Cihaz içi Apple Foundation Models ile performans ve donanım ömrü analizi yapar.")
    public static var openAppWhenRun: Bool = false

    public init() {}

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let result = MekSocketBridge.execute("analiz")
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
        AppShortcut(
            intent: MekBriefingIntent(),
            phrases: [
                "\(.applicationName) brifing",
                "\(.applicationName) brifingi ver",
                "\(.applicationName) günün brifingi"
            ],
            shortTitle: "Mek Brifing",
            systemImageName: "sun.horizon"
        )
        AppShortcut(
            intent: MekCleanIntent(),
            phrases: [
                "\(.applicationName) temizlik",
                "\(.applicationName) temizlik yap",
                "\(.applicationName) belleği rahatlat"
            ],
            shortTitle: "Mek Temizlik",
            systemImageName: "sparkle"
        )
        AppShortcut(
            intent: MekPerformanceModeIntent(),
            phrases: [
                "\(.applicationName) performans modu",
                "\(.applicationName) sistemi hızlandır",
                "\(.applicationName) optimize et"
            ],
            shortTitle: "Mek Performans",
            systemImageName: "bolt.fill"
        )
        AppShortcut(
            intent: MekHardwareHealthIntent(),
            phrases: [
                "\(.applicationName) donanım sağlığı",
                "\(.applicationName) sıcaklık",
                "\(.applicationName) sistem ömrü"
            ],
            shortTitle: "Mek Sağlık",
            systemImageName: "cpu"
        )
        AppShortcut(
            intent: MekAIAnalyzeIntent(),
            phrases: [
                "\(.applicationName) sistem analizi",
                "\(.applicationName) analiz et",
                "\(.applicationName) performans analizi"
            ],
            shortTitle: "Mek AI Analiz",
            systemImageName: "brain.head.profile"
        )
    }
}

// MARK: - Menu Bar & Application Delegate

@MainActor
final class MekMenuBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var statusMenu: NSMenu!
    private var appIcon: NSImage?
    private var lastPromptedQuestionId: String? = nil
    private var questionPollTimer: Timer? = nil

    func applicationWillFinishLaunching(_ notification: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleMekAppleEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(0x4D454B53), // 'MEKS'
            andEventID: AEEventID(0x2A2A2A2A)      // '****' (typeWildCard)
        )

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

        if #available(macOS 13.0, *) {
            MekShortcuts.updateAppShortcutParameters()
        }

        startWorkspaceObserver()
        startQuestionPolling()
    }

    // MARK: - Live Performance, Longevity & Quarantine Menu

    func menuWillOpen(_ menu: NSMenu) {
        menu.removeAllItems()

        // 1. Canlı Performans & Donanım Sağlığı Bölümü
        let rawStatus = MekSocketBridge.execute("status_json")
        if let sData = rawStatus.data(using: .utf8),
           let st = try? JSONSerialization.jsonObject(with: sData) as? [String: Any] {
            let profile = (st["profile"] as? String ?? "normal").lowercased()
            let profBadge: String
            switch profile {
            case "performance": profBadge = "⚡ Performans Modu (Tier-0 P-Core)"
            case "ready":       profBadge = "🟢 Hazır Modu (Uygulama Beklemede)"
            default:            profBadge = "🛡️ Mek Aktif (Dengeli Koruma)"
            }
            let hItem = NSMenuItem(title: profBadge, action: nil, keyEquivalent: "")
            hItem.isEnabled = false
            menu.addItem(hItem)

            let cpu = st["cpu"] as? Double ?? 0
            let gpuStr = (st["gpu"] as? Int).map { "%\($0)" } ?? "—"
            let mem = st["mem_pct"] as? Double ?? 0
            let swap = st["swap_gb"] as? Double ?? 0
            let loadItem = NSMenuItem(
                title: String(format: "  CPU %%%.0f  |  GPU %@  |  RAM %%%.0f  |  Takas %.1f GB", cpu, gpuStr, mem, swap),
                action: nil, keyEquivalent: ""
            )
            loadItem.isEnabled = false
            menu.addItem(loadItem)

            var thermParts: [String] = []
            if let cpuC = st["cpu_c"] as? Double { thermParts.append(String(format: "CPU %.0f°C", cpuC)) }
            if let gpuC = st["gpu_c"] as? Double { thermParts.append(String(format: "GPU %.0f°C", gpuC)) }
            if let ssdC = st["ssd_c"] as? Double { thermParts.append(String(format: "SSD %.0f°C", ssdC)) }
            if let f0 = st["fan0_rpm"] as? Int, let f1 = st["fan1_rpm"] as? Int {
                thermParts.append("Fan \(f0)/\(f1) RPM")
            }
            if !thermParts.isEmpty {
                let tItem = NSMenuItem(title: "  " + thermParts.joined(separator: "  |  "), action: nil, keyEquivalent: "")
                tItem.isEnabled = false
                menu.addItem(tItem)
            }

            let scratchMounted = st["scratch_mounted"] as? Bool ?? false
            let scratchMB = st["scratch_used_mb"] as? Double ?? 0
            let scratchGB = st["scratch_size_gb"] as? Int ?? 12
            let diskFree = st["disk_free_gb"] as? Double ?? 0
            let scratchLabel = scratchMounted
                ? String(format: "  RAM-Disk %.0f MB / %d GB  |  Boş SSD %.0f GB", scratchMB, scratchGB, diskFree)
                : String(format: "  RAM-Disk kapalı  |  Boş SSD %.0f GB", diskFree)
            let sItem = NSMenuItem(title: scratchLabel, action: nil, keyEquivalent: "")
            sItem.isEnabled = false
            menu.addItem(sItem)

            let fmAvail = st["fm_available"] as? Bool ?? false
            let fmItem = NSMenuItem(
                title: fmAvail ? "  🧠 Apple Foundation Models: Hazır (Cihaz İçi)" : "  🧠 Apple Foundation Models: Kapalı",
                action: nil, keyEquivalent: ""
            )
            fmItem.isEnabled = false
            menu.addItem(fmItem)
        } else {
            let headerItem = NSMenuItem(title: "🛡️ Mek Kalkanı: Aktif (Sistem Korunuyor)", action: nil, keyEquivalent: "")
            headerItem.isEnabled = false
            menu.addItem(headerItem)
        }

        menu.addItem(.separator())

        // 2. Hızlı Performans & Ömür Eylemleri
        let optItem = NSMenuItem(title: "⚡ Sistemi & Uygulamaları Optimize Et", action: #selector(runOptimize), keyEquivalent: "")
        optItem.target = self
        menu.addItem(optItem)

        let fanParent = NSMenuItem(title: "🌀 Fan & Termal Kipi", action: nil, keyEquivalent: "")
        let fanMenu = NSMenu()
        for (title, mode) in [
            ("Akıllı Sessiz (1100 RPM / >=52°C Otomatik)", "sessiz"),
            ("Apple Fabrika Otomatik (1330 RPM taban)", "otomatik"),
            ("Performans Ön-Soğutma (1800 RPM)", "performans"),
        ] {
            let fi = NSMenuItem(title: title, action: #selector(setFanMode(_:)), keyEquivalent: "")
            fi.target = self
            fi.representedObject = mode
            fanMenu.addItem(fi)
        }
        fanParent.submenu = fanMenu
        menu.addItem(fanParent)

        let healthItem = NSMenuItem(title: "🩺 Donanım Ömrü & Bellek Raporu", action: #selector(showHealthReport), keyEquivalent: "")
        healthItem.target = self
        menu.addItem(healthItem)

        let aiItem = NSMenuItem(title: "🧠 Apple AI Performans & Ömür Analizi", action: #selector(runAIAnalysis), keyEquivalent: "")
        aiItem.target = self
        menu.addItem(aiItem)

        let cleanItem = NSMenuItem(title: "🧹 Akıllı Önbellek Temizliği Başlat", action: #selector(runClean), keyEquivalent: "")
        cleanItem.target = self
        menu.addItem(cleanItem)

        menu.addItem(.separator())

        // 3. Karantina Bölümü
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
                let okItem = NSMenuItem(title: "  ✓ Karantina Temiz (0 Şüpheli Dosya)", action: nil, keyEquivalent: "")
                okItem.isEnabled = false
                menu.addItem(okItem)
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
            let okItem = NSMenuItem(title: "  ✓ Karantina Temiz (Tehdit Yok)", action: nil, keyEquivalent: "")
            okItem.isEnabled = false
            menu.addItem(okItem)
        }

        menu.addItem(.separator())

        let openFolderItem = NSMenuItem(title: "📂 Karantina Klasörünü Aç...", action: #selector(openQuarantineFolder), keyEquivalent: "")
        openFolderItem.target = self
        menu.addItem(openFolderItem)

        if itemsCount > 0 {
            let clearItem = NSMenuItem(title: "🗑️ Karantinayı Boşalt...", action: #selector(clearQuarantine), keyEquivalent: "")
            clearItem.target = self
            menu.addItem(clearItem)
        }

        menu.addItem(.separator())

        let siriItem = NSMenuItem(title: "🎙️ Siri: \"Hey Siri, Mek\"", action: #selector(siriGuide), keyEquivalent: "")
        siriItem.target = self
        menu.addItem(siriItem)

        menu.addItem(.separator())

        let quitItem = NSMenuItem(title: "Mek'i Kapat", action: #selector(quit), keyEquivalent: "")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    @objc private func runOptimize() {
        let res = MekSocketBridge.execute("performans")
        showResult(res)
    }

    @objc private func setFanMode(_ sender: NSMenuItem) {
        guard let mode = sender.representedObject as? String else { return }
        let res = MekSocketBridge.execute("fan \(mode)")
        showResult(res)
    }

    @objc private func showHealthReport() {
        let h = MekSocketBridge.execute("sağlık")
        let b = MekSocketBridge.execute("bellek")
        showResult(h + "\n\n" + b)
    }

    @objc private func runAIAnalysis() {
        let res = MekSocketBridge.execute("analiz")
        showResult(res)
    }

    @objc private func runClean() {
        let res = MekSocketBridge.execute("temizle")
        showResult(res)
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
        Mek doğrudan Siri'ye bağlıdır. Kestirmeler uygulamasında hiçbir şey \
        kurmanız veya açmanız gerekmez.

        Sesle:
        • "Hey Siri, Mek"  → Mek ne istediğinizi sorar
        • "Hey Siri, Mek durum"
        • "Hey Siri, Mek performans modu"
        • "Hey Siri, Mek donanım sağlığı"
        • "Hey Siri, Mek temizlik yap"
        • "Hey Siri, Mek brifing"
        • "Hey Siri, Mek karantinada ne var"

        Yazarak:
        Sistem Ayarları > Erişilebilirlik > Siri > "Siri'ye Yazın" açık olsun. \
        Siri'yi çağırıp aynı cümleleri yazabilirsiniz.
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
        } else if eventID == 0x50524F46 { // PROF: performansModu
            cmd = "performans"
        } else if eventID == 0x484C5448 { // HLTH: donanimSagligi
            cmd = "sağlık"
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
