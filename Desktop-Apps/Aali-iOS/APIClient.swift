import Foundation

struct AskResponse: Codable {
    let ok: Bool
    let reply: String?
    let sid: String?
    let error: String?
}

enum APIError: LocalizedError {
    case badURL
    case http(Int)
    case network(Error)
    case decoding
    case server(String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "عنوان الخادم غير صالح"
        case .http(let code): return "الخادم أرجع خطأ (\(code))"
        case .network: return "تعذّر الاتصال بالخادم — تأكد أن الحاسوب والجوال على نفس الشبكة"
        case .decoding: return "رد غير مفهوم من الخادم"
        case .server(let message): return message
        }
    }
}

/// Talks to the Aali brain running on the user's PC.
final class APIClient {
    static let shared = APIClient()

    private var base: String {
        get { UserDefaults.standard.string(forKey: "aali.server") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "aali.server") }
    }

    private var sessionID: String {
        get { UserDefaults.standard.string(forKey: "aali.sid") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "aali.sid") }
    }

    var isConfigured: Bool { !base.isEmpty }

    func serverURL() -> String { base }

    func setServerURL(_ url: String) {
        base = url.trimmingCharacters(in: .whitespacesAndNewline)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    func resetConversation() { sessionID = "" }

    private func makeURL(_ path: String) throws -> URL {
        guard let url = URL(string: base + path), url.scheme != nil else {
            throw APIError.badURL
        }
        return url
    }

    func health() async throws -> Bool {
        let (data, response) = try await URLSession.shared.data(from: try makeURL("/api/health"))
        guard let http = response as? HTTPURLResponse else { throw APIError.decoding }
        _ = data
        return http.statusCode == 200
    }

    func ask(_ message: String) async throws -> String {
        var request = try URLRequest(url: makeURL("/api/ask"))
        request.httpMethod = "POST"
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        if !sessionID.isEmpty {
            request.setValue(sessionID, forHTTPHeaderField: "X-Session-Id")
        }
        var payload: [String: Any] = ["message": message]
        if !sessionID.isEmpty { payload["sid"] = sessionID }
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)

        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw APIError.http(http.statusCode)
        }
        guard let decoded = try? JSONDecoder().decode(AskResponse.self, from: data) else {
            throw APIError.decoding
        }
        if let sid = decoded.sid, !sid.isEmpty { sessionID = sid }
        if let reply = decoded.reply, !reply.isEmpty { return reply }
        if let error = decoded.error, !error.isEmpty { throw APIError.server(error) }
        throw APIError.decoding
    }
}
