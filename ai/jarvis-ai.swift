// Mek Apple Foundation Models Köprüsü (Cihaz Üzerinde — Sıfır Ek RAM Yükü).
//
// Kullanım (geriye tam uyumlu):
//   echo "istem" | jarvis-ai "talimatlar"
//   jarvis-ai --status
//   echo "istem" | jarvis-ai --mode text "talimatlar"
//   echo "istem" | jarvis-ai --mode agent "talimatlar"
//   echo "telemetri" | jarvis-ai --mode advisor "talimatlar"
//   echo "yanıt" | jarvis-ai --mode classify --choices kapat,dokunma "talimatlar"
//
// Çıkış kodu 2: Apple Foundation Models kullanılamıyor (kapalı / desteklenmiyor).
import Foundation
import FoundationModels

func writeErr(_ msg: String) {
    if let data = (msg + "\n").data(using: .utf8) {
        FileHandle.standardError.write(data)
    }
}

func emitJSON(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
}

func availabilityReason(_ avail: SystemLanguageModel.Availability) -> (Bool, String) {
    switch avail {
    case .available:
        return (true, "ready")
    case .unavailable(let reason):
        return (false, "\(reason)")
    }
}

var mode = "text"
var choicesRaw = ""
var toolsRaw = ""
var positional: [String] = []
var checkStatusOnly = false

let args = Array(CommandLine.arguments.dropFirst())
var idx = 0
while idx < args.count {
    let a = args[idx]
    if a == "--status" {
        checkStatusOnly = true
        idx += 1
    } else if a == "--mode", idx + 1 < args.count {
        mode = args[idx + 1].lowercased()
        idx += 2
    } else if a == "--choices", idx + 1 < args.count {
        choicesRaw = args[idx + 1]
        idx += 2
    } else if a == "--tools", idx + 1 < args.count {
        toolsRaw = args[idx + 1]
        idx += 2
    } else {
        positional.append(a)
        idx += 1
    }
}

let model = SystemLanguageModel.default
let (isAvail, reason) = availabilityReason(model.availability)

if checkStatusOnly {
    emitJSON([
        "available": isAvail,
        "model": "Apple Foundation Models (On-Device)",
        "reason": reason,
    ])
    exit(isAvail ? 0 : 2)
}

guard isAvail else {
    writeErr("unavailable: \(reason)")
    exit(2)
}

let instructions = positional.first ?? ""
let prompt = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8) ?? ""

let sem = DispatchSemaphore(value: 0)
var exitCode: Int32 = 0

Task {
    do {
        let session = LanguageModelSession(instructions: instructions)
        switch mode {
        case "agent":
            var allowedTools = toolsRaw
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            let toolSchema: DynamicGenerationSchema
            if !allowedTools.isEmpty {
                if !allowedTools.contains("none") {
                    allowedTools.append("none")
                }
                toolSchema = DynamicGenerationSchema(name: "ToolName", anyOf: allowedTools)
            } else {
                toolSchema = DynamicGenerationSchema(type: String.self)
            }
            let root = DynamicGenerationSchema(
                name: "AgentDecision",
                description: "Mek asistanı tek adımlı araç veya yanıt kararı",
                properties: [
                    .init(
                        name: "kind",
                        description: "Karar türü: araç çağıracaksan 'tool', son cevabı vereceksen 'answer'",
                        schema: .init(name: "DecisionKind", anyOf: ["tool", "answer"])
                    ),
                    .init(
                        name: "tool",
                        description: "kind='tool' ise listedeki kesin araç adı; kind='answer' ise 'none'",
                        schema: toolSchema
                    ),
                    .init(
                        name: "arg_key",
                        description: "Araç argüman anahtarı (ör. ad, pid, dakika, kip, no, aç); argüman yoksa boş metin",
                        schema: .init(type: String.self)
                    ),
                    .init(
                        name: "arg_value",
                        description: "Araç argüman değeri (metin olarak); argüman yoksa boş metin",
                        schema: .init(type: String.self)
                    ),
                    .init(
                        name: "answer",
                        description: "kind='answer' ise kullanıcıya verilecek kısa, doğal Türkçe yanıt; aksi halde boş metin",
                        schema: .init(type: String.self)
                    ),
                ]
            )
            let schema = try GenerationSchema(root: root, dependencies: [])
            let opts = GenerationOptions(temperature: 0.15)
            let r = try await session.respond(to: prompt, schema: schema, options: opts)
            print(r.content.jsonString)

        case "advisor":
            let root = DynamicGenerationSchema(
                name: "SystemAdvice",
                description: "Mac performans ve donanım ömrü analizi",
                properties: [
                    .init(
                        name: "health_score",
                        description: "0 ile 100 arasında genel performans ve donanım sağlığı puanı",
                        schema: .init(type: Int.self)
                    ),
                    .init(
                        name: "summary",
                        description: "Anlık donanım, ısı, bellek ve disk durumunu özetleyen 1-2 kısa doğal Türkçe cümle",
                        schema: .init(type: String.self)
                    ),
                    .init(
                        name: "recommended_action",
                        description: "Yalnızca gerçekten gerekliyse güvenli eylem seç; sistem normalse 'none'",
                        schema: .init(
                            name: "SafeAction",
                            anyOf: ["none", "optimize", "fan_boost", "fan_silent", "unload_llm", "clean_cache"]
                        )
                    ),
                    .init(
                        name: "reason",
                        description: "Bu değerlendirmenin kısa teknik nedeni (Türkçe)",
                        schema: .init(type: String.self)
                    ),
                    .init(
                        name: "tip",
                        description: "Performans veya donanım ömrü için kısa ve pratik Türkçe öneri",
                        schema: .init(type: String.self)
                    ),
                ]
            )
            let schema = try GenerationSchema(root: root, dependencies: [])
            let opts = GenerationOptions(temperature: 0.2)
            let r = try await session.respond(to: prompt, schema: schema, options: opts)
            print(r.content.jsonString)

        case "classify":
            var allowed = choicesRaw
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            if !allowed.contains("none") {
                allowed.append("none")
            }
            let root = DynamicGenerationSchema(
                name: "ChoiceClassification",
                description: "Kullanıcı yanıtını geçerli seçeneklerden birine eşleştir",
                properties: [
                    .init(
                        name: "choice",
                        description: "Eşleşen seçenek anahtarı veya belirsizse 'none'",
                        schema: .init(name: "ChoiceKey", anyOf: allowed)
                    )
                ]
            )
            let schema = try GenerationSchema(root: root, dependencies: [])
            let opts = GenerationOptions(temperature: 0.1)
            let r = try await session.respond(to: prompt, schema: schema, options: opts)
            print(r.content.jsonString)

        default:
            let opts = GenerationOptions(temperature: 0.3)
            let r = try await session.respond(to: prompt, options: opts)
            print(r.content)
        }
    } catch {
        writeErr("error: \(error)")
        exitCode = 1
    }
    sem.signal()
}

sem.wait()
exit(exitCode)
