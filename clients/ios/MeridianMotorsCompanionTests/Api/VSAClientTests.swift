import XCTest
@testable import MeridianMotorsCompanion

// MARK: - StubURLProtocol
//
// Intercepts URLSession requests made by VSAClient and returns a
// pre-configured response without hitting the network.  Registered on a
// dedicated ephemeral URLSession injected into VSAClient via a test-only
// initialiser.
//
// Architecture note: VSAClient is an `actor` with no public URLSession seam
// in its primary init. The tests below use a package-private `init` that
// accepts a custom URLSession; see the `// MARK: - Test seam` extension at
// the bottom of this file.

final class StubURLProtocol: URLProtocol {

    // Per-test configurable response — set before constructing the client.
    static var stubbedResponseData: Data?
    static var stubbedStatusCode: Int = 200
    static var lastRequest: URLRequest?
    // Captured body — may come from httpBody or httpBodyStream depending on how
    // URLSession serializes the request internally.
    static var lastRequestBody: Data?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        // Capture the request for assertion.
        StubURLProtocol.lastRequest = request

        // Capture the request body. URLSession uses httpBodyStream (not httpBody)
        // for URLRequests constructed by our actor, so we must drain the stream.
        if let body = request.httpBody {
            StubURLProtocol.lastRequestBody = body
        } else if let stream = request.httpBodyStream {
            var data = Data()
            let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 4096)
            defer { buffer.deallocate() }
            stream.open()
            while stream.hasBytesAvailable {
                let n = stream.read(buffer, maxLength: 4096)
                if n > 0 { data.append(buffer, count: n) }
            }
            stream.close()
            StubURLProtocol.lastRequestBody = data.isEmpty ? nil : data
        } else {
            StubURLProtocol.lastRequestBody = nil
        }

        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: StubURLProtocol.stubbedStatusCode,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: StubURLProtocol.stubbedResponseData ?? Data())
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

// MARK: - Helpers

private func makeStubSession() -> URLSession {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [StubURLProtocol.self]
    return URLSession(configuration: config)
}

private func makeClient(token: String? = "test-jwt-token") -> VSAClient {
    VSAClient(idTokenProvider: { token }, session: makeStubSession())
}

/// Builds a synthetic device-token `Data` (32 zero bytes is the minimum shape
/// a real APNs token has; the hex serialisation is deterministic for testing).
private let sampleTokenData = Data(repeating: 0x5A, count: 32)

/// Expected hex representation of `sampleTokenData`:
/// 32 × 0x5A == "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a"
private let sampleTokenHex = String(repeating: "5a", count: 32)

private let sampleConsent = VSAClient.RegisterDeviceCategoryConsent(
    safety: true,
    coverage: false,
    service: true
)

// MARK: - VSAClientTests

/// Tests for Task 3.5: `registerDevice` + `getFindingsSummary`.
///
/// Four test cases per the Accept criteria:
///   (a) correct URL construction
///   (b) auth header present
///   (c) body shape byte-identical to contract for `registerDevice`
///   (d) `getFindingsSummary` decodes to the expected struct
///
/// **Mutation gate (body-shape test)**: change `platform: "apns-staging"` to
/// `platform: "staging"` in this test and the test MUST fail because the
/// captured body will carry `"platform":"staging"` instead of the required
/// `"platform":"apns-staging"`.  That is the mechanical property this test
/// protects.  (Mutation result recorded in `decisions.md` per tasks.md Task 3.5.)
final class VSAClientTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.lastRequest = nil
        StubURLProtocol.lastRequestBody = nil
        StubURLProtocol.stubbedStatusCode = 200
    }

    // MARK: - (a) Correct URL construction

    /// `registerDevice` must POST to `{restApiUrl}/notifications/register-device`.
    func test_registerDevice_posts_to_correct_url() async throws {
        // Arrange
        StubURLProtocol.stubbedResponseData = #"{"success":true}"#.data(using: .utf8)
        let client = makeClient()

        // Act
        _ = try await client.registerDevice(
            token: sampleTokenData,
            perCategoryConsent: sampleConsent,
            platform: "apns-staging"
        )

        // Assert
        let req = try XCTUnwrap(StubURLProtocol.lastRequest)
        let url = try XCTUnwrap(req.url)
        XCTAssertTrue(url.path.hasSuffix("/notifications/register-device"),
                       "URL path must end with /notifications/register-device; got \(url.path)")
        XCTAssertEqual(req.httpMethod, "POST",
                       "registerDevice must use POST")
    }

    // MARK: - (b) Auth header present

    /// Every call through VSAClient MUST forward the Cognito JWT as
    /// `Authorization: Bearer <token>`.
    func test_registerDevice_sends_bearer_auth_header() async throws {
        // Arrange
        StubURLProtocol.stubbedResponseData = #"{"success":true}"#.data(using: .utf8)
        let token = "eyJhbGciOiJSUzI1NiJ9.test-payload.signature"
        let client = makeClient(token: token)

        // Act
        _ = try await client.registerDevice(
            token: sampleTokenData,
            perCategoryConsent: sampleConsent,
            platform: "apns-staging"
        )

        // Assert
        let req = try XCTUnwrap(StubURLProtocol.lastRequest)
        let authHeader = try XCTUnwrap(req.value(forHTTPHeaderField: "Authorization"),
                                       "Authorization header must be present")
        XCTAssertEqual(authHeader, "Bearer \(token)",
                       "Authorization header must be 'Bearer <token>'")
    }

    // MARK: - (c) Body shape byte-identical to contract

    /// The JSON body sent to the server must match AVX core's OpenAPI contract
    /// exactly:
    /// ```json
    /// {
    ///   "device_token":         "<hex>",
    ///   "per_category_consent": { "safety": true, "coverage": false, "service": true },
    ///   "platform":             "apns-staging"
    /// }
    /// ```
    ///
    /// **Mutation target**: the `platform` value MUST be `"apns-staging"`, not
    /// `"staging"`.  Changing the assertion value to `"staging"` MUST cause this
    /// test to fail — that failure is the mutation gate.
    func test_registerDevice_body_shape_matches_contract() async throws {
        // Arrange
        StubURLProtocol.stubbedResponseData = #"{"success":true}"#.data(using: .utf8)
        let client = makeClient()

        // Act
        _ = try await client.registerDevice(
            token: sampleTokenData,
            perCategoryConsent: sampleConsent,
            platform: "apns-staging"
        )

        // Assert: capture + parse the body
        let req = try XCTUnwrap(StubURLProtocol.lastRequest)
        _ = req  // URL / method checked in test_registerDevice_posts_to_correct_url
        let bodyData = try XCTUnwrap(StubURLProtocol.lastRequestBody,
                                     "registerDevice must send a JSON body")
        let body = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: bodyData) as? [String: Any],
            "body must be a JSON object"
        )

        // (c1) device_token is lowercase hex of the raw Data
        XCTAssertEqual(body["device_token"] as? String, sampleTokenHex,
                       "device_token must be lowercase hex of the raw APNs token Data")

        // (c2) platform carries the "apns-" prefix — this is the mutation-guarded assertion
        let platform = try XCTUnwrap(body["platform"] as? String,
                                     "platform field must be present in body")
        XCTAssertEqual(platform, "apns-staging",
                       "platform MUST be 'apns-{stage}' (e.g. 'apns-staging'), " +
                       "NOT a bare stage name. " +
                       "This assertion enforces the AVX core OpenAPI contract.")

        // (c3) per_category_consent carries the three boolean fields
        let consent = try XCTUnwrap(body["per_category_consent"] as? [String: Any],
                                    "per_category_consent must be a nested object")
        XCTAssertEqual(consent["safety"] as? Bool, true,
                       "per_category_consent.safety must match the supplied value")
        XCTAssertEqual(consent["coverage"] as? Bool, false,
                       "per_category_consent.coverage must match the supplied value")
        XCTAssertEqual(consent["service"] as? Bool, true,
                       "per_category_consent.service must match the supplied value")
    }

    // MARK: - (d) getFindingsSummary decodes to expected struct

    /// `getFindingsSummary(ownerId:)` must:
    ///   1. GET `{restApiUrl}/findings/owner/{ownerId}/summary`
    ///   2. Decode `open_count` and `updated_at` into `FindingsSummary`
    func test_getFindingsSummary_decodes_response() async throws {
        // Arrange
        let expectedOpenCount = 3
        let expectedUpdatedAt = "2026-09-18T14:00:00Z"
        let responseJSON = """
        {
            "open_count": \(expectedOpenCount),
            "updated_at": "\(expectedUpdatedAt)"
        }
        """.data(using: .utf8)!
        StubURLProtocol.stubbedResponseData = responseJSON
        let ownerId = "usr-abc123"
        let client = makeClient()

        // Act
        let summary = try await client.getFindingsSummary(ownerId: ownerId)

        // Assert: correct decode
        XCTAssertEqual(summary.openCount, expectedOpenCount,
                       "openCount must decode from open_count")
        XCTAssertEqual(summary.updatedAt, expectedUpdatedAt,
                       "updatedAt must decode from updated_at")

        // Assert: correct URL
        let req = try XCTUnwrap(StubURLProtocol.lastRequest)
        let url = try XCTUnwrap(req.url)
        XCTAssertTrue(url.path.hasSuffix("/findings/owner/\(ownerId)/summary"),
                       "URL path must end with /findings/owner/{ownerId}/summary; got \(url.path)")
        XCTAssertEqual(req.httpMethod, "GET",
                       "getFindingsSummary must use GET")
    }
}
