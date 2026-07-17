import Foundation

/// Minimal Cognito auth over URLSession using USER_PASSWORD_AUTH.
/// No SRP, no big-int math — one HTTPS POST per sign-in.
/// Password travels over TLS; acceptable for our demo use case.
actor AuthService {
    private let pool: VSAConfig.TenantPool
    private let keychain = KeychainStore(service: "com.aws.vsa.companion.tokens")

    private(set) var idToken: String?
    private(set) var refreshToken: String?

    init(pool: VSAConfig.TenantPool = VSAConfig.defaultPool) {
        self.pool = pool
        if let id = keychain.read(key: "id"), let r = keychain.read(key: "refresh") {
            self.idToken = id
            self.refreshToken = r
        }
    }

    func currentIdToken() -> String? { idToken }

    func signIn(email: String, password: String) async throws -> String {
        print("[VSA Auth] signIn start — email=\(email) pool=\(pool.userPoolId)")
        let body: [String: Any] = [
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": pool.clientId,
            "AuthParameters": [
                "USERNAME": email,
                "PASSWORD": password,
            ],
        ]
        let respond = try await cognitoCall(
            target: "AWSCognitoIdentityProviderService.InitiateAuth",
            body: body
        )
        guard let auth = respond["AuthenticationResult"] as? [String: Any],
              let id = auth["IdToken"] as? String,
              let access = auth["AccessToken"] as? String,
              let refresh = auth["RefreshToken"] as? String else {
            print("[VSA Auth] Missing tokens in respond: \(respond)")
            throw AuthError.missingTokens(String(describing: respond))
        }
        print("[VSA Auth] signIn success — idToken.len=\(id.count)")
        self.idToken = id
        self.refreshToken = refresh
        keychain.write(key: "id", value: id)
        keychain.write(key: "refresh", value: refresh)
        _ = access
        return id
    }

    func signOut() {
        idToken = nil; refreshToken = nil
        keychain.delete(key: "id"); keychain.delete(key: "refresh")
    }

    // MARK: - Internals

    private func cognitoCall(target: String, body: [String: Any]) async throws -> [String: Any] {
        let url = URL(string: "https://cognito-idp.\(VSAConfig.awsRegion).amazonaws.com/")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/x-amz-json-1.1", forHTTPHeaderField: "Content-Type")
        req.setValue(target, forHTTPHeaderField: "X-Amz-Target")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw AuthError.network("not HTTP") }
        if !(200..<300).contains(http.statusCode) {
            let errBody = String(data: data, encoding: .utf8) ?? ""
            print("[VSA Auth] Cognito \(target) -> HTTP \(http.statusCode) body=\(errBody)")
            throw AuthError.cognito(status: http.statusCode, body: errBody)
        }
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AuthError.decoding("not a JSON object")
        }
        return json
    }
}

enum AuthError: Error, LocalizedError {
    case network(String)
    case cognito(status: Int, body: String)
    case missingTokens(String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .network(let m): return "Network: \(m)"
        case .cognito(let s, let b): return "Cognito \(s): \(b)"
        case .missingTokens(let m): return "Missing tokens: \(m)"
        case .decoding(let m): return "Decode: \(m)"
        }
    }
}
