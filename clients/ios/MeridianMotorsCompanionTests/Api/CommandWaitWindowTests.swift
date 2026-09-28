import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the confirmation wait window derived from the catalog's
/// `responseTimeout`.
///
/// Context: `issues/2026-08-19-cms-command-catalog-response-timeout-contract/`.
/// The window is taken from the catalog rather than invented here, which is the
/// right call — the correct value is per-command (3 s for door locks, 10 s for
/// slower actuators) and the backend already publishes it. But an unclamped,
/// unvalidated read has two failure modes worth pinning:
///
/// 1. A window shorter than the poll interval yields ZERO chances to observe
///    confirmation, so every command reports `.unacknowledged` no matter what the
///    vehicle did. A catalog typo of `"3"` (3 ms) does exactly that.
/// 2. A window of `"100000"` holds the sheet open for 100 seconds.
///
/// The floor is expressed as a multiple of `pollIntervalSeconds` so the two cannot
/// drift apart silently — the same reason the platform pins its heartbeat interval
/// against the API's staleness window with a test rather than a comment.
final class CommandWaitWindowTests: XCTestCase {

    func testCatalogValueIsHonouredWhenSane() {
        // 3000 ms -> 3 s, comfortably inside the clamp.
        XCTAssertEqual(VehicleControlsSheet.waitWindowSeconds(for: "3000"), 3.0, accuracy: 0.001)
        XCTAssertEqual(VehicleControlsSheet.waitWindowSeconds(for: "10000"), 10.0, accuracy: 0.001)
    }

    func testAbsentValueFallsBackToTheUnreachableCatalogDefault() {
        // The backend now always emits a string, defaulting to "5000" when an entry
        // omits the attribute. So nil here means the payload itself is old or the
        // catalog fetch degraded — a different failure, deliberately more generous.
        XCTAssertEqual(
            VehicleControlsSheet.waitWindowSeconds(for: nil),
            VehicleControlsSheet.fallbackWaitSeconds,
            accuracy: 0.001
        )
    }

    func testUnparseableValueFallsBack() {
        XCTAssertEqual(
            VehicleControlsSheet.waitWindowSeconds(for: "not-a-number"),
            VehicleControlsSheet.fallbackWaitSeconds,
            accuracy: 0.001
        )
        XCTAssertEqual(
            VehicleControlsSheet.waitWindowSeconds(for: ""),
            VehicleControlsSheet.fallbackWaitSeconds,
            accuracy: 0.001
        )
    }

    func testAbsurdlySmallValueIsRaisedToTheFloor() {
        // "3" ms would otherwise produce a window with no poll in it at all.
        XCTAssertEqual(
            VehicleControlsSheet.waitWindowSeconds(for: "3"),
            VehicleControlsSheet.minWaitSeconds,
            accuracy: 0.001
        )
    }

    func testAbsurdlyLargeValueIsCappedAtTheCeiling() {
        XCTAssertEqual(
            VehicleControlsSheet.waitWindowSeconds(for: "100000"),
            VehicleControlsSheet.maxWaitSeconds,
            accuracy: 0.001
        )
    }

    func testZeroAndNegativeFallBackRatherThanClampToZero() {
        // A zero or negative window is not a small window, it is a nonsense value;
        // treating it as "no useful hint" is more honest than clamping to the floor.
        XCTAssertEqual(
            VehicleControlsSheet.waitWindowSeconds(for: "0"),
            VehicleControlsSheet.fallbackWaitSeconds,
            accuracy: 0.001
        )
        XCTAssertEqual(
            VehicleControlsSheet.waitWindowSeconds(for: "-5000"),
            VehicleControlsSheet.fallbackWaitSeconds,
            accuracy: 0.001
        )
    }

    func testWhitespaceIsTolerated() {
        XCTAssertEqual(VehicleControlsSheet.waitWindowSeconds(for: " 3000 "), 3.0, accuracy: 0.001)
    }

    /// The coupling that makes the floor meaningful. If the poll interval ever
    /// rises past the floor, a window at the floor stops being pollable.
    func testFloorLeavesRoomForAtLeastTwoPolls() {
        XCTAssertGreaterThanOrEqual(
            VehicleControlsSheet.minWaitSeconds,
            VehicleControlsSheet.pollIntervalSeconds * 2,
            "the floor must leave room for at least two history polls"
        )
        XCTAssertLessThan(
            VehicleControlsSheet.minWaitSeconds,
            VehicleControlsSheet.maxWaitSeconds
        )
    }
}
