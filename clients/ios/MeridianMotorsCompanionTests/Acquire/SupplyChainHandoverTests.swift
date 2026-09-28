import XCTest
@testable import MeridianMotorsCompanion

// Handover / final-leg coverage for `SupplyChainPlan`.
//
// Authored inside `SupplyChainPlanAccessoriesTests.swift` while Xcode was open and
// `project.pbxproj` could not be written, then relocated here once it closed. Suite
// name and test names are unchanged; only the file differs, so a reader looking for
// handover coverage finds it under the name they would guess.
// Spec: 2026-08-21-cvx-upgrade-flow-continuity, Task 1.2.

// MARK: - Handover / final leg (spec 2026-08-21-cvx-upgrade-flow-continuity, Task 1.2)

final class SupplyChainHandoverTests: XCTestCase {

    // MARK: - Fixtures

    private let fixedNow = Date(timeIntervalSince1970: 1_777_000_000)

    private let modelId       = "trailwind"
    private let variantId     = "trailwind-sport"
    private let colorId       = "cascade-blue"
    private let interiorStyle = InteriorStyle.sport
    private let window        = AvailabilityContract.DeliveryWindow.standard

    private func makePlan(handover: HandoverMethod) -> SupplyChainPlan {
        SupplyChainPlan.plan(
            modelId:        modelId,
            variantId:      variantId,
            colorId:        colorId,
            interiorStyle:  interiorStyle,
            deliveryWindow: window,
            handover:       handover,
            now:            fixedNow
        )
    }

    // MARK: - Test 1: pickup yields finalLegDays == 0

    func testPickupYieldsZeroFinalLegDays() {
        let plan = makePlan(handover: .dealerPickup(centerId: "dealer-001", name: "Meridian Central"))
        XCTAssertEqual(plan.finalLegDays, 0,
                       "Dealer pickup incurs no final leg — the vehicle is already at the dealer")
    }

    // MARK: - Test 2: delivery yields 2...4 finalLegDays

    func testHomeDeliveryYieldsTwoToFourFinalLegDays() {
        let plan = makePlan(handover: .homeDelivery)
        XCTAssertTrue((2...4).contains(plan.finalLegDays),
                      "Home delivery final leg must be 2–4 days; got \(plan.finalLegDays)")
    }

    // MARK: - Test 3: delivery proposedDeliveryDate > pickup proposedDeliveryDate for the same config

    func testDeliveryTotalDaysExceedsPickupForSameConfig() {
        let pickup   = makePlan(handover: .dealerPickup(centerId: "dealer-001", name: "Meridian Central"))
        let delivery = makePlan(handover: .homeDelivery)
        // Home delivery incurs an additional final leg (2–4 days); pickup does not.
        // The delivery date must therefore be later than the pickup date for the
        // same base configuration.
        XCTAssertGreaterThan(delivery.proposedDeliveryDate, pickup.proposedDeliveryDate,
                             "Home delivery must yield a later proposed date than dealer pickup for the same config")
    }

    // MARK: - Test 4: determinism — same config + same handover → same date

    func testDeterminismSameHandoverYieldsSameDate() {
        let a = makePlan(handover: .homeDelivery)
        let b = makePlan(handover: .homeDelivery)
        XCTAssertEqual(a.proposedDeliveryDate, b.proposedDeliveryDate,
                       "Same config + same handover must always produce the same delivery date")
        XCTAssertEqual(a, b, "Entire plan must be equal for identical inputs")
    }

    // MARK: - Test 5: two different centerId values with same .dealerPickup shape → identical date

    func testDifferentCenterIdProducesIdenticalDate() {
        let planA = makePlan(handover: .dealerPickup(centerId: "dealer-north", name: "Meridian Dealer"))
        let planB = makePlan(handover: .dealerPickup(centerId: "dealer-south", name: "Meridian Dealer"))
        XCTAssertEqual(planA.proposedDeliveryDate, planB.proposedDeliveryDate,
                       "centerId must NOT be part of the seed — switching dealers must not move the manufacturing date")
        XCTAssertEqual(planA.totalDays, planB.totalDays,
                       "totalDays must be identical for different centerIds")
        XCTAssertEqual(planA.manufacturingDays, planB.manufacturingDays,
                       "manufacturingDays must be identical for different centerIds")
    }

    // MARK: - Test 6: the three legs are all reflected in the proposed delivery date

    func testTotalDaysIsThreeLegSum() {
        for handover: HandoverMethod in [
            .dealerPickup(centerId: "d-001", name: "Meridian North"),
            .homeDelivery
        ] {
            let plan = makePlan(handover: handover)
            // totalDays is manufacturingDays + transitDays (factory→dealer);
            // finalLegDays is the dealer→customer final leg.
            // The proposedDeliveryDate must advance by ALL three legs from the
            // injected clock, so the date offset equals their sum.
            let cal = Calendar.current
            let expectedComponents = DateComponents(day: plan.manufacturingDays + plan.transitDays + plan.finalLegDays)
            let expectedDate = cal.date(byAdding: expectedComponents, to: fixedNow)!
            // Tightened from 86400 to 1 second (review cycle 1, S1). The comment
            // claimed ±1s while the assertion allowed a full day, so the test would
            // have passed with the date a whole day wrong — precisely the drift it
            // exists to catch. Both sides add whole days to the same injected clock
            // via `Calendar`, so the true difference is 0.
            let diff = abs(plan.proposedDeliveryDate.timeIntervalSince(expectedDate))
            XCTAssertLessThan(diff, 1,
                "proposedDeliveryDate must reflect all three legs for \(handover); "
                + "expected \(plan.manufacturingDays + plan.transitDays + plan.finalLegDays) days "
                + "from now, got \(plan.proposedDeliveryDate.timeIntervalSince(fixedNow) / 86400) days"
            )
        }
    }
}

// MARK: - Facility map coordinates (issue 2026-08-22, order tracker)

/// Guards the coordinates the order-tracker map pins.
///
/// Folded in here rather than its own file because Xcode was open and
/// `project.pbxproj` could not be written.
final class FacilityCoordinateTests: XCTestCase {

    func testEveryFacilityHasUsableCoordinates() {
        XCTAssertFalse(SupplyChainPlan.facilities.isEmpty)
        for f in SupplyChainPlan.facilities {
            XCTAssertTrue((-90...90).contains(f.latitude),
                          "\(f.facilityId) latitude \(f.latitude) out of range")
            XCTAssertTrue((-180...180).contains(f.longitude),
                          "\(f.facilityId) longitude \(f.longitude) out of range")
            // (0,0) is in the Atlantic and is the classic uninitialised-coordinate
            // value; a map centred there reads as a bug, so treat it as one.
            XCTAssertFalse(f.latitude == 0 && f.longitude == 0,
                           "\(f.facilityId) has null-island coordinates")
        }
    }

    /// Coordinates must be distinct, or two different plants pin the same place and
    /// the map stops carrying information.
    func testFacilityCoordinatesAreDistinct() {
        let pairs = SupplyChainPlan.facilities.map { "\($0.latitude),\($0.longitude)" }
        XCTAssertEqual(Set(pairs).count, pairs.count,
                       "two facilities share coordinates")
    }

    /// The file's naming constraint forbids a real plant name or locality because both
    /// are one step from identifying a real customer. A pin is a locality, so this
    /// pins the intent: coordinates stay away from the real automotive-plant clusters
    /// the constraint is about.
    func testFacilityCoordinatesAvoidKnownPlantLocalities() {
        // Rough exclusion boxes around well-known automotive manufacturing areas.
        let excluded: [(name: String, lat: ClosedRange<Double>, lon: ClosedRange<Double>)] = [
            ("Detroit/Dearborn", 42.0...42.6, -83.5...(-82.9)),
            ("Chennai",          12.7...13.3,  79.9...80.4),
            ("Wolfsburg",        52.3...52.6,  10.6...10.9)
        ]
        for f in SupplyChainPlan.facilities {
            for box in excluded {
                let hit = box.lat.contains(f.latitude) && box.lon.contains(f.longitude)
                XCTAssertFalse(hit,
                    "\(f.facilityId) pins \(box.name); facilities must be invented "
                    + "localities, not real plant sites")
            }
        }
    }
}
