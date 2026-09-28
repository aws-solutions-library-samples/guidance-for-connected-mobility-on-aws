import Foundation

/// Exchanges a Cognito User Pool IdToken for temporary AWS credentials via a
/// Cognito Identity Pool, so the client can SigV4-sign AgentCore WebSocket
/// handshakes.
///
/// ## Why we do this
///
/// AgentCore runtime endpoints reject unsigned WebSocket upgrades. Signing
/// requires AWS access key + secret key + session token. We get those by:
///
/// 1. Authenticating the user against the Cognito User Pool (done by
///    `AuthService`; yields an IdToken JWT).
/// 2. Calling `cognito-identity:GetId` with that IdToken, which maps the
///    user to a stable IdentityId.
/// 3. Calling `cognito-identity:GetCredentialsForIdentity` with the IdentityId
///    + IdToken, which returns AWS access key + secret + session token +
///    expiration. Those credentials are bound to the Identity Pool's
///    authenticated IAM role, which in turn has
///    `bedrock-agentcore:InvokeAgentRuntime` on our voice runtime.
///
/// ## Caching
///
/// - IdentityId is stable per user, cached forever.
/// - Credentials are cached until `expiration - 60s` to avoid racing the
///   expiry clock. A fresh session is <1 hour typically, so in practice a
///   single credential fetch covers the whole voice session.
///
/// ## No AWS SDK dependency
///
/// Uses raw HTTPS against `cognito-identity.<region>.amazonaws.com` with the
/// `AWSCognitoIdentityService` JSON RPC protocol — same pattern as
/// `AuthService.cognitoCall()`. Keeps the dependency tree minimal.
actor AwsCredentialProvider {

    struct Credentials: Equatable {
        let accessKeyId: String
        let secretKey: String
        let sessionToken: String
        let expiration: Date
    }

    enum ProviderError: Error, LocalizedError {
        case notSignedIn
        case network(String)
        case cognito(status: Int, body: String)
        case missingField(String)

        var errorDescription: String? {
            switch self {
            case .notSignedIn: return "Not signed in to Cognito"
            case .network(let m): return "Network: \(m)"
            case .cognito(let s, let b): return "Cognito Identity \(s): \(b)"
            case .missingField(let f): return "Response missing field: \(f)"
            }
        }
    }

    private let region: String
    private let identityPoolId: String
    private let userPoolId: String
    private let idTokenProvider: @Sendable () -> String?

    private var cachedIdentityId: String?
    private var cachedCredentials: Credentials?
    private let expirySlack: TimeInterval = 60

    init(region: String = VSAConfig.awsRegion,
         identityPoolId: String = VSAConfig.defaultPool.identityPoolId,
         userPoolId: String = VSAConfig.defaultPool.userPoolId,
         idTokenProvider: @escaping @Sendable () -> String?) {
        self.region = region
        self.identityPoolId = identityPoolId
        self.userPoolId = userPoolId
        self.idTokenProvider = idTokenProvider
    }

    /// Get valid AWS temp credentials, refreshing if the cached set is
    /// within `expirySlack` of expiry. Safe to call from multiple tasks.
    func credentials() async throws -> Credentials {
        // `🎤 CRED:` is the diagnostic prefix for AwsCredentialProvider.
        // Filter via:
        //   xcrun simctl spawn booted log stream --predicate \
        //     'eventMessage CONTAINS "🎤"' --style compact
        // Added 2026-05-27 alongside the broader voice-flow instrumentation
        // (cvx/issues/2026-05-27-ios-bidi-websocket-not-connected).
        if let cached = cachedCredentials,
           cached.expiration.timeIntervalSinceNow > expirySlack {
            NSLog("🎤 CRED: credentials() cache hit expiresIn=%.1fs",
                  cached.expiration.timeIntervalSinceNow)
            return cached
        }

        guard let idToken = idTokenProvider(), !idToken.isEmpty else {
            NSLog("🎤 CRED: credentials() idToken missing/empty — throwing notSignedIn")
            throw ProviderError.notSignedIn
        }

        NSLog("🎤 CRED: credentials() cache miss idTokenLen=%d cachedIdentity=%@",
              idToken.count, cachedIdentityId == nil ? "no" : "yes")
        let identityId: String
        do {
            identityId = try await getOrFetchIdentityId(idToken: idToken)
            NSLog("🎤 CRED: credentials() identityId resolved (len=%d)", identityId.count)
        } catch {
            NSLog("🎤 CRED: credentials() getOrFetchIdentityId threw: %@ (%@)",
                  "\(error.localizedDescription)", String(describing: error))
            throw error
        }
        do {
            let creds = try await fetchCredentials(identityId: identityId, idToken: idToken)
            cachedCredentials = creds
            NSLog("🎤 CRED: credentials() fetched fresh creds accessKeyPrefix=%@ exp=%@",
                  String(creds.accessKeyId.prefix(4)), "\(creds.expiration)")
            return creds
        } catch {
            NSLog("🎤 CRED: credentials() fetchCredentials threw: %@ (%@)",
                  "\(error.localizedDescription)", String(describing: error))
            throw error
        }
    }

    /// Drop any cached credentials/identity. Useful on sign-out, or if a
    /// downstream call returned an auth error and we want the next
    /// `credentials()` to start fresh.
    func invalidate() {
        cachedIdentityId = nil
        cachedCredentials = nil
    }

    // MARK: - Private

    private func getOrFetchIdentityId(idToken: String) async throws -> String {
        if let cached = cachedIdentityId {
            return cached
        }
        let loginsKey = "cognito-idp.\(region).amazonaws.com/\(userPoolId)"
        let body: [String: Any] = [
            "IdentityPoolId": identityPoolId,
            "Logins": [loginsKey: idToken],
        ]
        let resp = try await call(target: "AWSCognitoIdentityService.GetId", body: body)
        guard let id = resp["IdentityId"] as? String else {
            throw ProviderError.missingField("IdentityId")
        }
        cachedIdentityId = id
        return id
    }

    private func fetchCredentials(identityId: String, idToken: String) async throws -> Credentials {
        let loginsKey = "cognito-idp.\(region).amazonaws.com/\(userPoolId)"
        let body: [String: Any] = [
            "IdentityId": identityId,
            "Logins": [loginsKey: idToken],
        ]
        let resp = try await call(target: "AWSCognitoIdentityService.GetCredentialsForIdentity", body: body)
        guard let c = resp["Credentials"] as? [String: Any] else {
            throw ProviderError.missingField("Credentials")
        }
        guard let ak = c["AccessKeyId"] as? String else { throw ProviderError.missingField("AccessKeyId") }
        guard let sk = c["SecretKey"] as? String else { throw ProviderError.missingField("SecretKey") }
        guard let st = c["SessionToken"] as? String else { throw ProviderError.missingField("SessionToken") }
        // Expiration arrives as epoch seconds (can be int or double).
        let expDate: Date
        if let expNum = c["Expiration"] as? Double {
            expDate = Date(timeIntervalSince1970: expNum)
        } else if let expInt = c["Expiration"] as? Int {
            expDate = Date(timeIntervalSince1970: TimeInterval(expInt))
        } else {
            throw ProviderError.missingField("Expiration")
        }
        return Credentials(accessKeyId: ak, secretKey: sk, sessionToken: st, expiration: expDate)
    }

    private func call(target: String, body: [String: Any]) async throws -> [String: Any] {
        let url = URL(string: "https://cognito-identity.\(region).amazonaws.com/")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/x-amz-json-1.1", forHTTPHeaderField: "Content-Type")
        req.setValue(target, forHTTPHeaderField: "X-Amz-Target")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            NSLog("🎤 CRED: call(%@) response not HTTPURLResponse", target)
            throw ProviderError.network("not HTTP")
        }
        if !(200..<300).contains(http.statusCode) {
            let errBody = String(data: data, encoding: .utf8) ?? ""
            NSLog("🎤 CRED: call(%@) status=%d bodyLen=%d body=%@",
                  target, http.statusCode, errBody.count, errBody)
            throw ProviderError.cognito(status: http.statusCode, body: errBody)
        }
        NSLog("🎤 CRED: call(%@) status=200 bodyLen=%d", target, data.count)
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ProviderError.network("non-JSON response")
        }
        return json
    }
}
