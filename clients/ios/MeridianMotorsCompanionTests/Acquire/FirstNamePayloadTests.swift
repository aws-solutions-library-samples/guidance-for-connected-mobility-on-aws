import XCTest
@testable import MeridianMotorsCompanion

/// Tests for task 5.5: first name reaches the order payload.
///
/// Verifies:
/// - Name present → included in the encoded payload.
/// - Name absent → key omitted entirely (not encoded as null/empty string).
/// - Name with leading/trailing whitespace is trimmed.
///
/// Cross-repo dependency: adding `firstName` to the `order.placed` event
/// that Zone 3 subscribes to is a CVX-side change. This test suite exercises
/// the CLIENT half only — that the field is present when supplied and absent
/// when not. The CVX counterpart must be filed before task 5.5 is marked [x].
///
/// Note: No test here logs the name. It is the one piece of visitor-supplied
/// data in the journey.
final class FirstNamePayloadTests: XCTestCase {

    // MARK: - Name present → included in payload

    func testFirstNamePresentIsIncluded() throws {
        let req = makeRequest(firstName: "Samantha")
        let dict = try encoded(req)
        XCTAssertEqual(dict["firstName"] as? String, "Samantha",
                       "firstName must be present in the encoded payload when supplied")
    }

    // MARK: - Name absent → key omitted (not null, not empty string)

    func testFirstNameNilIsOmitted() throws {
        let req = makeRequest(firstName: nil)
        let dict = try encoded(req)
        XCTAssertNil(dict["firstName"],
                     "firstName key must be absent from the payload when nil")
    }

    // MARK: - Whitespace-only input treated as absent

    func testWhitespaceOnlyNameIsOmitted() throws {
        let req = makeRequest(firstName: "   ")
        let dict = try encoded(req)
        XCTAssertNil(dict["firstName"],
                     "whitespace-only firstName must be omitted, not encoded as blank")
    }

    // MARK: - Leading/trailing whitespace is trimmed

    func testLeadingTrailingWhitespaceTrimmed() throws {
        let req = makeRequest(firstName: "  Marcus  ")
        let dict = try encoded(req)
        XCTAssertEqual(dict["firstName"] as? String, "Marcus",
                       "firstName must be trimmed before encoding")
    }

    // MARK: - Helpers

    private func makeRequest(firstName: String?) -> ReservationRequest {
        ReservationRequest(
            tenantId: "t", modelId: "m", variantId: "v",
            colorId: "c", selectedAccessoryIds: [],
            depositRef: nil, qualificationTier: nil,
            discoverSessionId: nil, leadId: nil,
            edition: "family", interiorStyle: "classic",
            firstName: firstName,
            handoverMethod: nil, handoverCenterId: nil
        )
    }

    private func encoded(_ req: ReservationRequest) throws -> [String: Any] {
        let data = try JSONEncoder().encode(req)
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }
}
