import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `PackEnergy.usable` — the battery card's "N of M kWh usable" line.
///
/// WHY THIS EXISTS
/// ---------------
/// The card computed `capacity × soc` against a **nameplate** denominator, which
/// ignores state of health. On the demo Trailwind (94 kWh nameplate, 94% SoH,
/// 72% SoC) that rendered "68 of 94 kWh" where the pack holds ~64 of a now-88 kWh
/// ceiling.
///
/// The error is proportional to degradation, so it is smallest exactly when nobody
/// would care and largest when it matters — which is why it survived review: on a
/// nearly-new pack the numbers look right. Pinned here at several degradation
/// levels so a regression cannot hide behind a healthy fixture.
final class PackEnergyTests: XCTestCase {

    private let tolerance = 0.05

    // MARK: - The demo vehicle

    /// VEH-FORD-001: 94 kWh nameplate, 94% SoH, 72% SoC.
    /// Present ceiling 88.36 kWh; energy now 63.62 kWh. Previously "68 of 94".
    func testDemoTrailwindIsSohAdjusted() {
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 72, sohPercent: 94)
        XCTAssertNotNil(e)
        XCTAssertEqual(e!.full, 88.36, accuracy: tolerance)
        XCTAssertEqual(e!.now, 63.62, accuracy: tolerance)
    }

    /// The regression in one assertion: the old formula's answer must not recur.
    func testDoesNotReportNameplateFigures() {
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 72, sohPercent: 94)!
        XCTAssertNotEqual(e.full, 94, accuracy: 0.01, "denominator must not be nameplate")
        XCTAssertNotEqual(e.now, 67.68, accuracy: 0.01, "energy must not be capacity × soc")
    }

    // MARK: - Degradation scales the error

    func testHealthyPackMatchesNameplate() {
        let e = PackEnergy.usable(capacityKwh: 100, socPercent: 50, sohPercent: 100)!
        XCTAssertEqual(e.full, 100, accuracy: tolerance)
        XCTAssertEqual(e.now, 50, accuracy: tolerance)
    }

    func testHeavilyDegradedPackDivergesSharply() {
        // 70% SoH is the typical warranty floor — the error is now 30%, not 6%.
        let e = PackEnergy.usable(capacityKwh: 100, socPercent: 80, sohPercent: 70)!
        XCTAssertEqual(e.full, 70, accuracy: tolerance)
        XCTAssertEqual(e.now, 56, accuracy: tolerance)
    }

    func testFullChargeReportsThePresentCeilingNotNameplate() {
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 100, sohPercent: 80)!
        XCTAssertEqual(e.now, e.full, accuracy: tolerance)
        XCTAssertEqual(e.now, 75.2, accuracy: tolerance)
    }

    // MARK: - Absent inputs

    /// Pre-2026-08-21 behaviour for rows that never recorded SoH: fall back to
    /// nameplate rather than hiding a line the driver already had.
    func testAbsentSohFallsBackToNameplate() {
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 72, sohPercent: nil)!
        XCTAssertEqual(e.full, 94, accuracy: tolerance)
        XCTAssertEqual(e.now, 67.68, accuracy: tolerance)
    }

    /// The card's em-dash-not-zero rule: a missing reading is never a number.
    func testAbsentCapacityYieldsNil() {
        XCTAssertNil(PackEnergy.usable(capacityKwh: nil, socPercent: 72, sohPercent: 94))
    }

    func testAbsentSocYieldsNil() {
        XCTAssertNil(PackEnergy.usable(capacityKwh: 94, socPercent: nil, sohPercent: 94))
    }

    func testZeroOrNegativeCapacityYieldsNil() {
        XCTAssertNil(PackEnergy.usable(capacityKwh: 0, socPercent: 72, sohPercent: 94))
        XCTAssertNil(PackEnergy.usable(capacityKwh: -10, socPercent: 72, sohPercent: 94))
    }

    /// Zero SoC is a real reading, not a missing one — it must render "0 of N".
    func testZeroSocIsARealReading() {
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 0, sohPercent: 94)
        XCTAssertNotNil(e)
        XCTAssertEqual(e!.now, 0, accuracy: tolerance)
        XCTAssertEqual(e!.full, 88.36, accuracy: tolerance)
    }

    // MARK: - Out-of-range inputs

    func testSohAboveHundredIsClampedToNameplate() {
        // Freshly-calibrated packs have been seen reporting >100.
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 100, sohPercent: 110)!
        XCTAssertEqual(e.full, 94, accuracy: tolerance,
                       "capacity must never be reported above nameplate")
    }

    func testNegativeSohDoesNotProduceNegativeEnergy() {
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 50, sohPercent: -5)!
        XCTAssertEqual(e.full, 0, accuracy: tolerance)
        XCTAssertEqual(e.now, 0, accuracy: tolerance)
    }

    func testSocAboveHundredIsClamped() {
        let e = PackEnergy.usable(capacityKwh: 94, socPercent: 130, sohPercent: 94)!
        XCTAssertEqual(e.now, e.full, accuracy: tolerance)
    }

    // MARK: - Invariant

    func testEnergyNowNeverExceedsPresentCapacity() {
        for soh in stride(from: 40.0, through: 100.0, by: 10) {
            for soc in stride(from: 0.0, through: 100.0, by: 25) {
                let e = PackEnergy.usable(capacityKwh: 94, socPercent: soc, sohPercent: soh)!
                XCTAssertLessThanOrEqual(e.now, e.full + tolerance,
                                         "soh=\(soh) soc=\(soc)")
            }
        }
    }
}
