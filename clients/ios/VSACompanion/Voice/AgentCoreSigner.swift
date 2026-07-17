import CryptoKit
import Foundation

/// SigV4 signer for AgentCore WebSocket handshakes, query-param style.
///
/// Produces a fully-formed `wss://` URL containing both the SigV4 auth params
/// (`X-Amz-Algorithm`, `X-Amz-Credential`, ..., `X-Amz-Signature`,
/// `X-Amz-Security-Token`) AND any AgentCore custom-header values encoded as
/// `X-Amzn-Bedrock-AgentCore-Runtime-Custom-<Name>=<value>` query params.
///
/// Once we have the URL, the caller just does
/// `URLSession.webSocketTask(with: url)` — no special request headers needed.
/// This avoids URLSession's inconsistent handling of WebSocket upgrade headers
/// across iOS versions (see Phase 3 step 4 plan file gotchas for context).
///
/// ## Reference
///
/// - AWS SigV4: https://docs.aws.amazon.com/general/latest/gr/sigv4-create-canonical-request.html
/// - AgentCore accepts custom headers as query params when the request arrives
///   over WebSocket, per bidi_app.py `_establish_session()`.
///
/// ## Scope
///
/// Single-purpose: SigV4 for `bedrock-agentcore` on WebSocket upgrades. Does
/// not attempt to be a general-purpose signer. If we need SigV4 for another
/// service later, extract a `SigV4Core` type then.
struct AgentCoreSigner {

    /// Inputs needed to build a signed URL. All required.
    struct Request {
        let credentials: AwsCredentialProvider.Credentials
        let region: String
        let runtimeArn: String
        let sessionId: String
        /// Keys like `User-Token`, `Tenant-Id`, `Vin`. The
        /// `X-Amzn-Bedrock-AgentCore-Runtime-Custom-` prefix is added
        /// automatically — pass the short name only.
        let customHeaders: [String: String]
        /// How long the signed URL stays valid. Max 7 days per SigV4; the
        /// WebSocket session itself is capped by AgentCore's 8-min idle
        /// timeout anyway, so 5 min is more than enough.
        let expiresInSeconds: Int
    }

    enum SigningError: Error, LocalizedError {
        case invalidRuntimeArn
        case encodingFailed(String)

        var errorDescription: String? {
            switch self {
            case .invalidRuntimeArn: return "Runtime ARN did not match expected AgentCore format"
            case .encodingFailed(let f): return "Encoding failed for \(f)"
            }
        }
    }

    private static let service = "bedrock-agentcore"
    private static let algorithm = "AWS4-HMAC-SHA256"

    /// Produce the signed WebSocket URL. Pure function.
    static func sign(_ r: Request) throws -> URL {
        let host = "bedrock-agentcore.\(r.region).amazonaws.com"

        // Path: /runtimes/<url-encoded-arn>/ws. The ARN's colons and slashes
        // get percent-encoded for the wire path. The CANONICAL path in the
        // canonical request is URL-encoded AGAIN (double-encoding) because
        // bedrock-agentcore uses botocore's default signing model, which
        // applies `quote(path, safe='/~')` on top of the already-encoded
        // path — see botocore/auth.py `_normalize_url_path`. S3 and a few
        // others opt out of this with the `disableDoubleEncoding` flag;
        // bedrock-agentcore does not.
        let encodedArn = try percentEncodePathSegment(r.runtimeArn)
        let wirePath = "/runtimes/\(encodedArn)/ws"
        let canonicalPath = canonicalizePathForSigning(wirePath)

        // Build all query params in the exact order that would appear in the
        // final URL. Canonical query string requires alphabetical sorting
        // by key, then by value, with RFC3986 encoding.
        let now = Date()
        let amzDate = Self.amzDateFormatter.string(from: now)
        let dateStamp = Self.dateStampFormatter.string(from: now)
        let credentialScope = "\(dateStamp)/\(r.region)/\(service)/aws4_request"
        let credential = "\(r.credentials.accessKeyId)/\(credentialScope)"

        // The AgentCore session id header is standard, not a custom one.
        // It still rides as a query param for consistency.
        var params: [(String, String)] = [
            ("X-Amz-Algorithm", Self.algorithm),
            ("X-Amz-Credential", credential),
            ("X-Amz-Date", amzDate),
            ("X-Amz-Expires", String(r.expiresInSeconds)),
            ("X-Amz-Security-Token", r.credentials.sessionToken),
            // SignedHeaders is just `host` — all our other data rides as
            // query params, not headers.
            ("X-Amz-SignedHeaders", "host"),
            ("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id", r.sessionId),
        ]
        for (k, v) in r.customHeaders {
            params.append(("X-Amzn-Bedrock-AgentCore-Runtime-Custom-\(k)", v))
        }

        // Canonical query string: sort by (key, value) lexicographically,
        // each piece encoded with the strict RFC3986 set (unreserved only
        // left unescaped). This MUST include every query param — SigV4
        // mandates all query params get folded into the canonical request,
        // except X-Amz-Signature itself (which we haven't added yet).
        let sorted = params.sorted {
            if $0.0 != $1.0 { return $0.0 < $1.0 }
            return $0.1 < $1.1
        }
        let canonicalQuery = sorted
            .map { "\(Self.rfc3986Encode($0.0))=\(Self.rfc3986Encode($0.1))" }
            .joined(separator: "&")

        // Canonical headers — just host. AgentCore expects Host without port
        // even though the connection uses 443. Low-stakes bug magnet; test
        // this end-to-end.
        let canonicalHeaders = "host:\(host)\n"
        let signedHeaders = "host"

        // Payload hash: for an upgrade request there's no body. Unlike S3's
        // presigned URLs (which use the literal "UNSIGNED-PAYLOAD" string),
        // botocore's SigV4QueryAuth for non-S3 services computes the SHA256
        // of the (empty) body. We match that: sha256("") in hex.
        let payloadHash = Self.sha256Hex("")

        let canonicalRequest = [
            "GET",
            canonicalPath,
            canonicalQuery,
            canonicalHeaders,
            signedHeaders,
            payloadHash,
        ].joined(separator: "\n")

        let canonicalRequestHash = Self.sha256Hex(canonicalRequest)
        let stringToSign = [
            Self.algorithm,
            amzDate,
            credentialScope,
            canonicalRequestHash,
        ].joined(separator: "\n")

        // Derive signing key: kSecret -> kDate -> kRegion -> kService -> kSigning
        let kSecret = Data("AWS4\(r.credentials.secretKey)".utf8)
        let kDate = Self.hmacSHA256(key: kSecret, data: Data(dateStamp.utf8))
        let kRegion = Self.hmacSHA256(key: kDate, data: Data(r.region.utf8))
        let kService = Self.hmacSHA256(key: kRegion, data: Data(service.utf8))
        let kSigning = Self.hmacSHA256(key: kService, data: Data("aws4_request".utf8))
        let signatureBytes = Self.hmacSHA256(key: kSigning, data: Data(stringToSign.utf8))
        let signatureHex = signatureBytes.map { String(format: "%02x", $0) }.joined()

        // Final URL: original sorted params + signature, all encoded.
        let finalQuery = (sorted + [("X-Amz-Signature", signatureHex)])
            .sorted { $0.0 < $1.0 }
            .map { "\(Self.rfc3986Encode($0.0))=\(Self.rfc3986Encode($0.1))" }
            .joined(separator: "&")

        guard let url = URL(string: "wss://\(host)\(wirePath)?\(finalQuery)") else {
            throw SigningError.encodingFailed("final URL")
        }
        return url
    }

    // MARK: - Encoding helpers

    /// RFC3986 strict percent-encoding: only unreserved chars (A-Z a-z 0-9 - _ . ~)
    /// left unescaped. Critically, `/` and `:` ARE encoded, unlike the default
    /// `.urlQueryAllowed` set. This is what SigV4 requires.
    private static func rfc3986Encode(_ s: String) -> String {
        let allowed = CharacterSet(charactersIn:
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }

    /// Path segment encoding — same as rfc3986Encode. Reusing the function
    /// avoids ambiguity between "path" vs "query" encoding rules; SigV4's
    /// canonical path encoding for this service is strict.
    private static func percentEncodePathSegment(_ s: String) throws -> String {
        let out = rfc3986Encode(s)
        if out.isEmpty { throw SigningError.encodingFailed("runtime arn segment") }
        return out
    }

    /// Apply the second round of percent-encoding that botocore's default
    /// signing model performs. Equivalent to Python's
    /// `quote(already_encoded_path, safe='/~')` — every char not in
    /// `[A-Za-z0-9_.-~/]` gets encoded. Critically, `%` becomes `%25`, so
    /// an already-encoded `%3A` becomes `%253A` in the canonical request.
    ///
    /// Required for `bedrock-agentcore` (and most AWS services). S3 and a
    /// handful of others set `disableDoubleEncoding` and skip this step;
    /// we don't care about those here.
    private static func canonicalizePathForSigning(_ encodedPath: String) -> String {
        // Same character set as Python's quote(safe='/~'): unreserved +
        // '/' + '~'. Importantly, `%` is NOT in the safe set, so existing
        // percent-encodings get re-encoded.
        let allowed = CharacterSet(charactersIn:
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~/")
        return encodedPath.addingPercentEncoding(withAllowedCharacters: allowed) ?? encodedPath
    }

    // MARK: - Crypto helpers

    private static func sha256Hex(_ s: String) -> String {
        let digest = SHA256.hash(data: Data(s.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    private static func hmacSHA256(key: Data, data: Data) -> Data {
        let mac = HMAC<SHA256>.authenticationCode(for: data, using: SymmetricKey(data: key))
        return Data(mac)
    }

    // MARK: - Date formatters

    private static let amzDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        return f
    }()

    private static let dateStampFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyyMMdd"
        return f
    }()
}
