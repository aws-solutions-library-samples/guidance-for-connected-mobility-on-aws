import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the `SupplyChainPlan.plan(selectedAccessoryIds:accessories:)` convenience
/// overload that powers the live "Estimated delivery" chip on `.pickAccessories`.
///
/// Task 5.1 (spec `2026-08-21-cvx-configurator-full-picker-flow`):
/// The overload must:
///   1. Produce a valid date when the selection is empty (the starting chip).
///   2. Change the date when an accessory is added.
///   3. Restore the original date when that accessory is removed.
///   4. Produce a date byte-identical to the underlying `plan(selectedAccessoryIds:
///      accessoryLeadDays:)` overload, proving that the chip and the planning step
///      both use the same single function and cannot drift.
///
/// ## Testability rationale (architect correction 2026-08-21)
/// `provisionalPlan` is `@State private` on a SwiftUI `View` and is unreachable
/// from a unit test.  These tests assert the **overload** instead — which is exactly
/// what makes the spec's headline guarantee true by construction: both the chip and
/// `.supplyChainPlanning` call this function, so the date cannot differ between them.
final class LiveDeliveryEstimateTests: XCTestCase {

    // MARK: - Fixtures

    /// Fixed clock so no test depends on the day it runs.
    private let fixedNow = Date(timeIntervalSince1970: 1_777_000_000)

    private let modelId       = "crestwind-3row"
    private let variantId     = "crestwind-3row-std"
    private let colorId       = "crestwind-deep-slate"
    private let interiorStyle = InteriorStyle.classic
    private let window        = AvailabilityContract.DeliveryWindow.standard

    /// Accessory list matching `CatalogFallback.loadFixture()` so chip-test data
    /// is consistent with the demo fixture that drives the picker.
    private var fallbackAccessories: [CatalogAccessory] {
        [
            CatalogAccessory(
                accessoryId: "family-cargo-rack",
                displayName: "Cargo Roof Rack",
                description: nil, price: 799, categoryId: "family-suv",
                imageKey: nil, sortOrder: 0, leadDays: 3
            ),
            CatalogAccessory(
                accessoryId: "family-third-row-seat",
                displayName: "Premium Third-Row Seat Pack",
                description: nil, price: 1899, categoryId: "family-suv",
                imageKey: nil, sortOrder: 1, leadDays: 14
            ),
            CatalogAccessory(
                accessoryId: "family-tow-package",
                displayName: "Tow Package",
                description: nil, price: 1299, categoryId: "family-suv",
                imageKey: nil, sortOrder: 2, leadDays: 7
            ),
        ]
    }

    // MARK: - Convenience builders

    private func makeViaConvenience(
        selectedAccessoryIds: Set<String>,
        accessories: [CatalogAccessory]
    ) -> SupplyChainPlan {
        SupplyChainPlan.plan(
            modelId:              modelId,
            variantId:            variantId,
            colorId:              colorId,
            interiorStyle:        interiorStyle,
            deliveryWindow:       window,
            selectedAccessoryIds: selectedAccessoryIds,
            accessories:          accessories,
            now:                  fixedNow
        )
    }

    private func makeViaUnderlying(
        selectedAccessoryIds: [String],
        accessories: [CatalogAccessory]
    ) -> SupplyChainPlan {
        let leadDayMap = accessories.reduce(into: [String: Int]()) {
            $0[$1.accessoryId] = $1.leadDays ?? 0
        }
        return SupplyChainPlan.plan(
            modelId:              modelId,
            variantId:            variantId,
            colorId:              colorId,
            interiorStyle:        interiorStyle,
            deliveryWindow:       window,
            selectedAccessoryIds: selectedAccessoryIds,
            accessoryLeadDays:    leadDayMap,
            now:                  fixedNow
        )
    }

    // MARK: - Test 1: Empty selection yields a valid date (the baseline chip)

    /// When no accessories are selected the overload must return a valid plan —
    /// the visitor sees a meaningful starting date before toggling anything.
    func testEmptySelectionYieldsValidDate() {
        let plan = makeViaConvenience(selectedAccessoryIds: [], accessories: fallbackAccessories)

        // The date must be in the future relative to the fixed clock.
        XCTAssertGreaterThan(
            plan.proposedDeliveryDate, fixedNow,
            "Delivery date must be in the future even with no accessories selected"
        )
        XCTAssertFalse(
            plan.formattedDeliveryDate().isEmpty,
            "formattedDeliveryDate() must return a non-empty string"
        )
    }

    // MARK: - Test 2: Adding an accessory changes the date

    /// Selecting an accessory with positive leadDays must push the date forward.
    func testAddingAccessoryChangesDate() {
        let baseline = makeViaConvenience(selectedAccessoryIds: [], accessories: fallbackAccessories)
        let withTow  = makeViaConvenience(
            selectedAccessoryIds: ["family-tow-package"],
            accessories: fallbackAccessories
        )

        XCTAssertGreaterThan(
            withTow.proposedDeliveryDate, baseline.proposedDeliveryDate,
            "Adding 'family-tow-package' (7 lead days) must push the date forward"
        )
    }

    // MARK: - Test 3: Removing the accessory restores the date

    /// Deselecting the accessory (back to empty) must return the same date
    /// as the baseline — the computation is reversible.
    func testRemovingAccessoryRestoresDate() {
        let baseline    = makeViaConvenience(selectedAccessoryIds: [], accessories: fallbackAccessories)
        let withTow     = makeViaConvenience(
            selectedAccessoryIds: ["family-tow-package"],
            accessories: fallbackAccessories
        )
        let restored    = makeViaConvenience(selectedAccessoryIds: [], accessories: fallbackAccessories)

        // Sanity: adding actually changed the date.
        XCTAssertNotEqual(
            withTow.proposedDeliveryDate, baseline.proposedDeliveryDate,
            "Precondition: adding 'family-tow-package' must change the date"
        )
        // Removing restores it.
        XCTAssertEqual(
            restored.proposedDeliveryDate, baseline.proposedDeliveryDate,
            "Removing the accessory must restore the original date"
        )
    }

    // MARK: - Test 4: Chip date == planning-step date (byte-identity guarantee)

    /// The spec guarantees that the date shown on the chip during picking
    /// equals the date on `.deliveryProposal`.  This is only possible if both
    /// surfaces call the **same function** — which is the whole reason the
    /// overload exists.
    ///
    /// This test asserts it by calling both the convenience overload (chip path)
    /// and the underlying `plan(selectedAccessoryIds:accessoryLeadDays:)` (planning
    /// path) with the same logical inputs and verifying they produce an identical
    /// `proposedDeliveryDate`.
    func testChipDateEqualsProposalDateSameArgumentsProduceIdenticalPlan() {
        let selectedIds: Set<String>  = ["family-cargo-rack", "family-tow-package"]
        let accessories               = fallbackAccessories

        // Chip path: convenience overload (what ConfiguratorFlow calls on toggle).
        let chipPlan = makeViaConvenience(
            selectedAccessoryIds: selectedIds,
            accessories: accessories
        )

        // Planning-step path: underlying overload (what .supplyChainPlanning uses
        // after the convenience overload forwards to it).  The lead-day map is built
        // identically here to prove that both paths land on the same function.
        let planningPlan = makeViaUnderlying(
            selectedAccessoryIds: selectedIds.sorted(),
            accessories: accessories
        )

        XCTAssertEqual(
            chipPlan.proposedDeliveryDate, planningPlan.proposedDeliveryDate,
            "Chip date and planning-step date must be identical when given the same inputs"
        )
        XCTAssertEqual(
            chipPlan, planningPlan,
            "Full plan must be identical — not just the date — for chip vs planning paths"
        )
    }
}
