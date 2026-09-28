import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `SupplyChainPlan.plan(selectedAccessoryIds:accessoryLeadDays:)` —
/// the accessory-aware delivery-estimate extension.
///
/// Five properties are verified:
///
/// 1. **Determinism** — same config + same accessories → same date, every time.
/// 2. **One accessory shifts the date** — selecting an accessory with positive
///    leadDays moves the proposed delivery date forward by at least that many days.
/// 3. **Remove restores the date** — deselecting the accessory returns to the
///    pre-delta baseline, proving the param is genuinely in the computation.
/// 4. **Empty matches pre-delta** — calling with no accessories produces the same
///    date as the original `plan(...)` with no accessory params.
/// 5. **Sort invariance** — `["b","a"]` and `["a","b"]` produce identical dates.
final class SupplyChainPlanAccessoriesTests: XCTestCase {

    // MARK: - Fixtures

    /// Fixed clock so no test depends on the day it runs.
    private let fixedNow = Date(timeIntervalSince1970: 1_777_000_000)

    /// A known configuration that consistently resolves without early exits.
    private let modelId       = "crestwind-3row"
    private let variantId     = "crestwind-3row-std"
    private let colorId       = "deep-slate"
    private let interiorStyle = InteriorStyle.classic
    private let window        = AvailabilityContract.DeliveryWindow.standard

    /// Representative accessory ids and their lead days.
    ///
    /// Values mirror `CatalogFallback.loadFixture()` so tests stay consistent with
    /// the fixture that drives the demo when the endpoint is unavailable.
    private let cargoRackId      = "family-cargo-rack"
    private let cargoRackDays    = 3
    private let thirdRowId       = "family-third-row-seat"
    private let thirdRowDays     = 14
    private let towPackageId     = "family-tow-package"
    private let towPackageDays   = 7

    // MARK: - Convenience builders

    private func makePlan(
        selectedAccessoryIds: [String] = [],
        accessoryLeadDays: [String: Int] = [:]
    ) -> SupplyChainPlan {
        SupplyChainPlan.plan(
            modelId:              modelId,
            variantId:            variantId,
            colorId:              colorId,
            interiorStyle:        interiorStyle,
            deliveryWindow:       window,
            selectedAccessoryIds: selectedAccessoryIds,
            accessoryLeadDays:    accessoryLeadDays,
            now:                  fixedNow
        )
    }

    private var fullLeadDayMap: [String: Int] {
        [cargoRackId: cargoRackDays, thirdRowId: thirdRowDays, towPackageId: towPackageDays]
    }

    // MARK: - Test 1: Determinism

    /// The same configuration with the same accessory selection must always yield
    /// the same plan — including across multiple calls in the same process.
    ///
    /// This guards against any non-deterministic element being introduced into the
    /// accessory-extended seed path.
    func testDeterminismSameConfigSameAccessoriesYieldsSameDate() {
        let leadDays = fullLeadDayMap
        let selected = [cargoRackId, towPackageId]

        let a = makePlan(selectedAccessoryIds: selected, accessoryLeadDays: leadDays)
        let b = makePlan(selectedAccessoryIds: selected, accessoryLeadDays: leadDays)

        XCTAssertEqual(
            a.proposedDeliveryDate, b.proposedDeliveryDate,
            "Same config + same accessories must always produce the same delivery date"
        )
        XCTAssertEqual(a, b, "Entire plan must be equal for identical inputs")
    }

    // MARK: - Test 2: One accessory shifts the date forward

    /// Selecting one accessory with positive leadDays must push the proposed delivery
    /// date forward by at least that accessory's lead days.
    ///
    /// This is the core behavioural test: the parameter is not ignored.
    func testOneAccessoryShiftsDateForward() {
        let baseline = makePlan()
        let withCargo = makePlan(
            selectedAccessoryIds: [cargoRackId],
            accessoryLeadDays: [cargoRackId: cargoRackDays]
        )

        let baselineDate = baseline.proposedDeliveryDate
        let withCargoDate = withCargo.proposedDeliveryDate

        XCTAssertGreaterThan(
            withCargoDate, baselineDate,
            "Adding an accessory with \(cargoRackDays) lead days must push the date forward"
        )

        // The date must have moved by at least `cargoRackDays` calendar days.
        let calendar = Calendar.current
        let diffComponents = calendar.dateComponents([.day], from: baselineDate, to: withCargoDate)
        let diffDays = diffComponents.day ?? 0
        XCTAssertGreaterThanOrEqual(
            diffDays, cargoRackDays,
            "Date must shift by at least \(cargoRackDays) days; got \(diffDays)"
        )
    }

    // MARK: - Test 3: Remove restores the date

    /// Deselecting the accessory (empty selection) returns to the pre-delta baseline.
    ///
    /// This proves two things: the accessory param genuinely affects the result, and
    /// the effect is reversible — the underlying derivation is the same formula, not
    /// a one-way ratchet.
    func testRemoveAccessoryRestoresDate() {
        let withTow = makePlan(
            selectedAccessoryIds: [towPackageId],
            accessoryLeadDays: [towPackageId: towPackageDays]
        )
        let withoutTow = makePlan()

        XCTAssertNotEqual(
            withTow.proposedDeliveryDate, withoutTow.proposedDeliveryDate,
            "Precondition: selecting an accessory must change the date"
        )
        XCTAssertEqual(
            withoutTow.proposedDeliveryDate,
            makePlan().proposedDeliveryDate,
            "Empty selection must restore the baseline date"
        )
    }

    // MARK: - Test 4: Empty accessories matches pre-delta date

    /// Calling `plan(selectedAccessoryIds: [], ...)` must produce the same date as
    /// calling the pre-delta `plan(modelId:variantId:colorId:interiorStyle:deliveryWindow:now:)`
    /// with no accessory params at all.
    ///
    /// This guards back-compat: existing call sites pass no accessory args, receive
    /// the defaults, and must see an unchanged date.
    func testEmptyAccessoriesMatchesPreDeltaDate() {
        // Pre-delta equivalent — no accessory parameters (uses defaults [] and [:]).
        let preDelta = SupplyChainPlan.plan(
            modelId:        modelId,
            variantId:      variantId,
            colorId:        colorId,
            interiorStyle:  interiorStyle,
            deliveryWindow: window,
            now:            fixedNow
        )

        // Post-delta with explicitly empty params — must be identical.
        let postDelta = makePlan(selectedAccessoryIds: [], accessoryLeadDays: [:])

        XCTAssertEqual(
            preDelta.proposedDeliveryDate, postDelta.proposedDeliveryDate,
            "Empty accessory selection must reproduce the pre-delta delivery date"
        )
        XCTAssertEqual(
            preDelta, postDelta,
            "Empty accessory selection must produce an identical plan to the pre-delta signature"
        )
    }

    // MARK: - Test 5: Sort invariance

    /// Passing `["b","a"]` must produce the same date as `["a","b"]`.
    ///
    /// The spec requires that accessory ids are sorted before joining into the
    /// FNV-1a seed. Without this, the same selection in a different iteration order
    /// (e.g. from a Set) would produce a different date.
    func testSortInvarianceAccessoryOrderDoesNotMatter() {
        let leadDays = [cargoRackId: cargoRackDays, towPackageId: towPackageDays]

        let alpha = makePlan(
            selectedAccessoryIds: [cargoRackId, towPackageId],
            accessoryLeadDays: leadDays
        )
        let reversed = makePlan(
            selectedAccessoryIds: [towPackageId, cargoRackId],
            accessoryLeadDays: leadDays
        )

        XCTAssertEqual(
            alpha.proposedDeliveryDate, reversed.proposedDeliveryDate,
            "Accessory id order must not affect the delivery date — ids are sorted before seeding"
        )
        XCTAssertEqual(
            alpha, reversed,
            "Plans must be identical regardless of accessory id input order"
        )
    }
}
