import XCTest
@testable import MeridianMotorsCompanion

/// Integration tests for the `.pickHandover` step in `ConfiguratorFlow.Step`.
///
/// Verifies that:
/// - Advancing through `.pickHandover` with pickup correctly reaches `.supplyChainPlanning`
///   carrying the pickup method.
/// - Advancing through `.pickHandover` with delivery correctly reaches `.supplyChainPlanning`
///   carrying the delivery method.
/// - The full sequence still reaches `.success`.
/// - `.pickHandover` is not auto-advancing (it costs a visitor tap).
///
/// ## Why these tests exist
/// The handover step is order-sensitive: it MUST come before `.supplyChainPlanning` because
/// the plan reads the handover from the transition. A reordering would silently break the
/// byte-identity guarantee between the chip and the proposal. These tests pin that ordering
/// by exercising the production `advance(using:)` seam, not by reading source.
final class HandoverStepIntegrationTests: XCTestCase {

    // MARK: - Fixtures

    private let model = CatalogModel(
        modelId: "m300", categoryId: "sport", displayName: "Meridian 300",
        basePrice: 8999, imageKey: nil, specBadges: nil, sortOrder: nil)
    private let variant = CatalogVariant(
        variantId: "std", modelId: "m300", displayName: "Standard",
        priceAdder: nil, specOverrides: nil, sortOrder: nil)
    private let color = CatalogColor(
        colorId: "red", variantId: "std", displayName: "Racing Red",
        hexColor: "#C00", imageKey: nil, priceAdder: nil)
    private let resp = ReservationResponse(
        orderId: "ord_abc", orderNumber: "ORD-2026-08-0099", depositRef: nil)

    private func makeSelection(handover: HandoverMethod) -> ConfiguratorFlow.Step.ForwardSelection {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard,
            handover: handover)
        return ConfiguratorFlow.Step.ForwardSelection(
            color: color,
            interiorStyle: .sport,
            reservationResponse: resp,
            supplyChainPlan: plan,
            selectedAccessoryIds: [],
            handover: handover
        )
    }

    // MARK: - Test 1: pickup choice reaches supplyChainPlanning with pickup method

    func testPickupAdvancesToSupplyChainPlanningWithPickupMethod() {
        let pickup = HandoverMethod.dealerPickup(centerId: "ctr1", name: "Meridian of Nashville")
        let selection = makeSelection(handover: pickup)

        let handoverStep = ConfiguratorFlow.Step
            .pickHandover(.family, model, variant, color, .sport, [])

        let next = handoverStep.advance(using: selection)

        guard case .supplyChainPlanning(_, _, _, _, _, _, let carriedMethod) = next else {
            return XCTFail("pickHandover must advance to supplyChainPlanning, got \(String(describing: next))")
        }
        XCTAssertEqual(carriedMethod, pickup,
                       "The pickup handover choice must be carried intact to supplyChainPlanning")
    }

    // MARK: - Test 2: delivery choice reaches supplyChainPlanning with delivery method

    func testDeliveryAdvancesToSupplyChainPlanningWithDeliveryMethod() {
        let delivery = HandoverMethod.homeDelivery
        let selection = makeSelection(handover: delivery)

        let handoverStep = ConfiguratorFlow.Step
            .pickHandover(.family, model, variant, color, .sport, [])

        let next = handoverStep.advance(using: selection)

        guard case .supplyChainPlanning(_, _, _, _, _, _, let carriedMethod) = next else {
            return XCTFail("pickHandover must advance to supplyChainPlanning, got \(String(describing: next))")
        }
        XCTAssertEqual(carriedMethod, delivery,
                       "The delivery handover choice must be carried intact to supplyChainPlanning")
    }

    // MARK: - Test 3: full sequence still reaches .success

    func testFullSequenceReachesSuccess() {
        let selection = makeSelection(handover: .homeDelivery)

        // pickHandover → supplyChainPlanning → deliveryProposal → confirm → success
        let step0 = ConfiguratorFlow.Step.pickHandover(.family, model, variant, color, .sport, [])
        let step1 = step0.advance(using: selection)
        XCTAssertNotNil(step1, "pickHandover must have a forward transition")
        guard case .supplyChainPlanning = step1 else {
            return XCTFail("Expected supplyChainPlanning, got \(String(describing: step1))")
        }

        let step2 = step1!.advance(using: selection)
        guard case .deliveryProposal = step2 else {
            return XCTFail("Expected deliveryProposal, got \(String(describing: step2))")
        }

        let step3 = step2!.advance(using: selection)
        guard case .confirm = step3 else {
            return XCTFail("Expected confirm, got \(String(describing: step3))")
        }

        let step4 = step3!.advance(using: selection)
        guard case .success = step4 else {
            return XCTFail("Expected success, got \(String(describing: step4))")
        }
    }

    // MARK: - Test 4: pickHandover is not auto-advancing

    func testPickHandoverIsNotAutoAdvancing() {
        let step = ConfiguratorFlow.Step.pickHandover(.family, model, variant, color, .sport, [])
        XCTAssertFalse(step.isAutoAdvancing,
                       "pickHandover must require a visitor tap — it is not auto-advancing")
    }
}
