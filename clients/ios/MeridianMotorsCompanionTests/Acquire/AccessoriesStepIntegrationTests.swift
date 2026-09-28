import XCTest
@testable import MeridianMotorsCompanion

/// Integration tests for the `.pickAccessories` step machine transition.
///
/// Task 4.1 (spec `2026-08-21-cvx-configurator-full-picker-flow`):
/// Verifies that advancing through `.pickAccessories` with 0, 1, and 3 selected
/// ids reaches `.supplyChainPlanning` with the selection intact in
/// `ForwardSelection.selectedAccessoryIds`.
///
/// Also verifies that driving the full machine to `.success` is possible —
/// this subsumes the reachability-of-`.success` invariant that
/// `testTapCountFromOfferAcceptedToOrderReadyIsAtMostFive` (retired in Group 6)
/// previously asserted.
final class AccessoriesStepIntegrationTests: XCTestCase {

    // MARK: - Shared fixtures

    private let model = CatalogModel(
        modelId: "crestwind-3row", categoryId: "family-suv",
        displayName: "Crestwind 3-Row",
        basePrice: 42990, imageKey: nil, specBadges: nil, sortOrder: 0)
    private let variant = CatalogVariant(
        variantId: "crestwind-3row-std", modelId: "crestwind-3row",
        displayName: "Standard", priceAdder: nil, specOverrides: nil, sortOrder: 0)
    private let color = CatalogColor(
        colorId: "crestwind-deep-slate", variantId: "crestwind-3row-std",
        displayName: "Deep Slate", hexColor: "#3D4A5C", imageKey: nil, priceAdder: nil)
    private let resp = ReservationResponse(
        orderId: "ord_test", orderNumber: "ORD-2026-08-0001", depositRef: nil)

    private var baseSelection: ConfiguratorFlow.Step.ForwardSelection {
        ConfiguratorFlow.Step.ForwardSelection(
            color: color,
            interiorStyle: .classic,
            reservationResponse: resp
        )
    }

    // MARK: - Accessory selection tests

    /// Advancing `.pickAccessories` with 0 selected ids reaches `.pickHandover`
    /// and the selection on `ForwardSelection` is empty.
    ///
    /// Note: pickAccessories now advances to pickHandover (not supplyChainPlanning directly).
    /// pickHandover is the step that comes BEFORE supplyChainPlanning — the plan reads the
    /// handover choice from that transition.
    func testAdvancePickAccessoriesWithNoSelection() {
        let accessories: [CatalogAccessory] = []
        let step = ConfiguratorFlow.Step.pickAccessories(
            .family, model, variant, color, .classic, accessories)

        var selection = baseSelection
        selection.selectedAccessoryIds = []

        guard let next = step.advance(using: selection) else {
            XCTFail("advance(using:) returned nil for pickAccessories with 0 ids — " +
                    "pickAccessories→pickHandover branch not yet wired")
            return
        }
        guard case .pickHandover(_, _, _, _, _, let ids) = next else {
            XCTFail("Expected pickHandover, got \(next)")
            return
        }
        XCTAssertTrue(ids.isEmpty, "Empty selection must reach pickHandover with empty Set")
    }

    /// Advancing `.pickAccessories` with 1 selected id carries that id through.
    func testAdvancePickAccessoriesWithOneSelection() {
        let accessories: [CatalogAccessory] = []
        let step = ConfiguratorFlow.Step.pickAccessories(
            .family, model, variant, color, .classic, accessories)

        var selection = baseSelection
        selection.selectedAccessoryIds = ["family-cargo-rack"]

        guard let next = step.advance(using: selection) else {
            XCTFail("advance(using:) returned nil for pickAccessories with 1 id — " +
                    "pickAccessories→pickHandover branch not yet wired")
            return
        }
        guard case .pickHandover(_, _, _, _, _, let ids) = next else {
            XCTFail("Expected pickHandover, got \(next)")
            return
        }
        XCTAssertEqual(ids, ["family-cargo-rack"],
                       "1-id selection must be carried intact to pickHandover")
    }

    /// Advancing `.pickAccessories` with 3 selected ids carries all 3 through.
    func testAdvancePickAccessoriesWithThreeSelections() {
        let accessories: [CatalogAccessory] = []
        let step = ConfiguratorFlow.Step.pickAccessories(
            .family, model, variant, color, .classic, accessories)

        var selection = baseSelection
        selection.selectedAccessoryIds = [
            "family-cargo-rack",
            "family-third-row-seat",
            "family-tow-package"
        ]

        guard let next = step.advance(using: selection) else {
            XCTFail("advance(using:) returned nil for pickAccessories with 3 ids — " +
                    "pickAccessories→pickHandover branch not yet wired")
            return
        }
        guard case .pickHandover(_, _, _, _, _, let ids) = next else {
            XCTFail("Expected pickHandover, got \(next)")
            return
        }
        XCTAssertEqual(ids.count, 3,
                       "3-id selection must be carried intact to pickHandover")
        XCTAssertTrue(ids.contains("family-cargo-rack"))
        XCTAssertTrue(ids.contains("family-third-row-seat"))
        XCTAssertTrue(ids.contains("family-tow-package"))
    }

    /// Drives the full step machine from `.pickAccessories` through to `.success`.
    ///
    /// This test subsumes the reachability-of-`.success` invariant previously
    /// held by `testTapCountFromOfferAcceptedToOrderReadyIsAtMostFive`
    /// (retired in Group 6 per `decisions.md § 2026-08-21 "Tap budget retirement"`).
    func testFullSequenceReachesSuccess() {
        let accessories: [CatalogAccessory] = []
        var step: ConfiguratorFlow.Step = ConfiguratorFlow.Step.pickAccessories(
            .family, model, variant, color, .classic, accessories)

        var selection = baseSelection
        selection.selectedAccessoryIds = ["family-cargo-rack"]

        var reachedSuccess = false
        for _ in 0..<15 {
            if case .success = step {
                reachedSuccess = true
                break
            }
            guard let next = step.advance(using: selection) else {
                XCTFail("advance(using:) returned nil before reaching .success at step: \(step)")
                return
            }
            step = next
        }
        XCTAssertTrue(reachedSuccess,
                      "Full machine must reach .success from .pickAccessories within 15 steps")
    }
}
