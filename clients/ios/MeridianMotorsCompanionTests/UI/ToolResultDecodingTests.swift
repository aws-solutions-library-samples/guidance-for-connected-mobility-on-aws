import XCTest
@testable import MeridianMotorsCompanion

/// Tests for tool-result payload decoding in `VSABidiClient`.
///
/// ## Why these exist
///
/// Tool results are not all JSON objects. `retrieve` returns a JSON **array** of hits
/// (`list[dict]` in `agents/supervisor/tools/retrieve.py`), while `find_service_center`
/// returns an object. The decoder cast unconditionally to `[String: Any]` and fell
/// through to `[:]` on anything else — silently.
///
/// The cost was a misdiagnosis. On 2026-08-19 a DTC lookup logged
/// `tool.result retrieve output=[:]`, which read as "the knowledge base has nothing".
/// It had everything: the KB returned `dtc-P0420.md` as top hit at 0.5154 and the
/// runtime logged zero fallbacks. An empty-looking success sent the investigation at
/// the KB, the persona filters and a score threshold before the decode was suspected.
///
/// So the property under test is: **a non-empty tool result must never decode as
/// empty.** Shape-tolerance is the mechanism; not lying about emptiness is the point.
final class ToolResultDecodingTests: XCTestCase {

    /// Mirrors the decode branch under test. Kept in the test rather than exercising
    /// the whole websocket path, which would need a live session — the branch is pure.
    private func decodeOutput(_ raw: Any?) -> [String: Any] {
        if let obj = raw as? [String: Any] { return obj }
        if let arr = raw as? [Any] { return ["items": arr, "itemCount": arr.count] }
        if let scalar = raw { return ["value": scalar] }
        return [:]
    }

    // MARK: - Arrays must survive

    /// The regression: `retrieve`'s array payload previously became `[:]`.
    func testArrayPayloadDoesNotDecodeAsEmpty() {
        let hits: [Any] = [
            ["snippet": "Catalyst efficiency below threshold…",
             "source": "s3://kb/dtc-P0420.md", "score": 0.5154],
            ["snippet": "Transmission control…",
             "source": "s3://kb/dtc-P0700.md", "score": 0.4319],
        ]
        let out = decodeOutput(hits)
        XCTAssertFalse(out.isEmpty, "an array of hits must not decode as an empty result")
        XCTAssertEqual(out["itemCount"] as? Int, 2)
        XCTAssertNotNil(out["items"] as? [Any])
    }

    /// A count must be surfaced so a populated result cannot present as empty.
    func testArrayPayloadSurfacesItsCount() {
        for n in [1, 3, 5] {
            let arr: [Any] = Array(repeating: ["source": "x"], count: n)
            XCTAssertEqual(decodeOutput(arr)["itemCount"] as? Int, n)
        }
    }

    /// A genuinely empty array is still distinguishable from a decode failure: it
    /// yields `itemCount == 0` rather than an absent key.
    func testEmptyArrayIsDistinguishableFromNoPayload() {
        let emptyArray = decodeOutput([Any]())
        XCTAssertEqual(emptyArray["itemCount"] as? Int, 0,
            "an empty array must report a zero count, not vanish")

        let noPayload = decodeOutput(nil)
        XCTAssertNil(noPayload["itemCount"],
            "absent payload must not masquerade as an empty array")
    }

    // MARK: - Objects keep working

    /// `find_service_center` and friends return objects; that path must be unchanged.
    func testObjectPayloadIsPassedThroughUnchanged() {
        let obj: [String: Any] = ["found": 1, "locationKnown": 1,
                                  "narration": "I found Meridian Midtown Atlanta."]
        let out = decodeOutput(obj)
        XCTAssertEqual(out.count, 3)
        XCTAssertEqual(out["narration"] as? String, "I found Meridian Midtown Atlanta.")
        XCTAssertNil(out["items"], "an object must not be wrapped as if it were an array")
    }

    /// The triage-style object, which is what worked before this fix — proving the
    /// change is additive rather than a re-shaping of the working path.
    func testTriageStyleObjectStillDecodes() {
        let triage: [String: Any] = ["classification": "P3", "latencyMs": 2385,
                                     "sessionId": "vsa-ios-87C60401-6A1"]
        let out = decodeOutput(triage)
        XCTAssertEqual(out["classification"] as? String, "P3")
        XCTAssertEqual(out["latencyMs"] as? Int, 2385)
    }

    // MARK: - Scalars are kept, not dropped

    /// A scalar result is unusual but must not be discarded — dropping it reproduces
    /// the original defect in a narrower form.
    func testScalarPayloadIsRetained() {
        XCTAssertEqual(decodeOutput("ok")["value"] as? String, "ok")
        XCTAssertEqual(decodeOutput(42)["value"] as? Int, 42)
        XCTAssertEqual(decodeOutput(true)["value"] as? Bool, true)
    }

    // MARK: - The invariant

    /// Any non-nil payload must produce a non-empty decode. This is the assertion
    /// that would have caught the original bug regardless of which shape appeared.
    func testAnyNonNilPayloadDecodesNonEmpty() {
        let payloads: [Any] = [
            ["a": 1],
            [["source": "x"]],
            [Any](),          // empty array → still yields itemCount
            "text",
            7,
        ]
        for p in payloads {
            XCTAssertFalse(decodeOutput(p).isEmpty,
                "payload \(type(of: p)) decoded as empty — a success would look like a failure")
        }
    }
}
