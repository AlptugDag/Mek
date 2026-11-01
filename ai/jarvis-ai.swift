// Apple Intelligence köprüsü: cihaz üzerindeki Apple dil modeliyle metin üretir.
// Kullanım: echo "istem" | jarvis-ai "talimatlar"   -> stdout'a yanıt
// Çıkış kodu 2: model kullanılamıyor (Apple Intelligence kapalı/desteklenmiyor).
import Foundation
import FoundationModels

let instructions = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
let prompt = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8) ?? ""

let model = SystemLanguageModel.default
guard case .available = model.availability else {
    FileHandle.standardError.write("unavailable: \(model.availability)\n".data(using: .utf8)!)
    exit(2)
}
let sem = DispatchSemaphore(value: 0)
var code: Int32 = 0
Task {
    do {
        let session = LanguageModelSession(instructions: instructions)
        let r = try await session.respond(to: prompt)
        print(r.content)
    } catch {
        FileHandle.standardError.write("error: \(error)\n".data(using: .utf8)!)
        code = 1
    }
    sem.signal()
}
sem.wait()
exit(code)
