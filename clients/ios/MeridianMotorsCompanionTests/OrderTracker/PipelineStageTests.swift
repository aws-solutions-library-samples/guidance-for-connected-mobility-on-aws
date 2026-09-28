import XCTest
@testable import MeridianMotorsCompanion

/// Unit tests for `PipelineStage`.
///
/// Covers:
/// - `from(_:)` round-trip (known values, unknown fallback).
/// - All 10 cases have non-empty `defaultDisplayName`.
/// - All 10 cases have non-empty `symbolName`.
///
/// Per spec Group 2 task 2.3: ≥3 tests.
final class PipelineStageTests: XCTestCase {

    // MARK: - from(_:) round-trip

    /// All 10 `PipelineStage` raw values survive `from(_:)` without fallback.
    func testFromRawValueRoundTrip() {
        let allRaw = PipelineStage.allCases.map { $0.rawValue }
        for raw in allRaw {
            let stage = PipelineStage.from(raw)
            XCTAssertEqual(stage.rawValue, raw,
                           "PipelineStage.from('\(raw)') should return the matching case")
        }
    }

    /// Unknown string falls back to `.orderPlaced` so the UI always renders.
    func testFromUnknownStringFallsBackToOrderPlaced() {
        let stage = PipelineStage.from("some-future-stage-not-in-enum")
        XCTAssertEqual(stage, .orderPlaced,
                       "Unknown raw value must fall back to .orderPlaced per spec")
    }

    /// Empty string also falls back gracefully.
    func testFromEmptyStringFallback() {
        XCTAssertEqual(PipelineStage.from(""), .orderPlaced)
    }

    // MARK: - defaultDisplayName

    /// Every case must provide a non-empty display name (used in the stage list UI).
    func testAllCasesHaveNonEmptyDefaultDisplayName() {
        for stage in PipelineStage.allCases {
            XCTAssertFalse(
                stage.defaultDisplayName.isEmpty,
                "\(stage.rawValue).defaultDisplayName must not be empty"
            )
        }
    }

    /// There are exactly 10 pipeline stages (spec: "10 fixed pipeline stages").
    func testExactlyTenStages() {
        XCTAssertEqual(PipelineStage.allCases.count, 10,
                       "Spec defines exactly 10 pipeline stages")
    }

    // MARK: - symbolName

    /// Every case must provide a non-empty SF Symbol name (used in stage icons).
    func testAllCasesHaveNonEmptySymbolName() {
        for stage in PipelineStage.allCases {
            XCTAssertFalse(
                stage.symbolName.isEmpty,
                "\(stage.rawValue).symbolName must not be empty"
            )
        }
    }

    // MARK: - Stage ordering

    /// `allCases` is ordered from `.orderPlaced` (index 0) to
    /// `.readyForDelivery` (index 9) — the visual order in the stepper.
    func testStageOrderingStartsWithOrderPlaced() {
        XCTAssertEqual(PipelineStage.allCases.first, .orderPlaced)
    }

    func testStageOrderingEndsWithReadyForDelivery() {
        XCTAssertEqual(PipelineStage.allCases.last, .readyForDelivery)
    }
}
