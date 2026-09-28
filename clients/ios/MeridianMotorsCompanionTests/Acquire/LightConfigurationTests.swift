import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the LightConfiguration path: step machine shape, auto-advance
/// behaviour, and the supply-chain planning transition chain.
///
/// The step machine for the offer-accepted path (as of 2026-08-21) is:
///   showEditionSummary → pickColor → pickInteriorStyle
///     → pickAccessories → supplyChainPlanning → deliveryProposal
///     → confirm → success
///
/// `supplyChainPlanning` auto-advances when the planning animation completes
/// and costs the visitor no tap.
///
/// ## Tap budget — retired 2026-08-21
/// The tap-budget test was deleted, not weakened. Its ≤5 assertion became
/// invalid when `.pickAccessories` was inserted into the path, adding a tap.
/// Changing `5` to a larger number was explicitly rejected: a new bound would
/// imply intentional calibration when it was not. See `decisions.md` §
/// "2026-08-21 — Tap budget retirement" for the full rationale and for the
/// reachability-of-`.success` invariant that subsumes the removed test's
/// secondary purpose (via `AccessoriesStepIntegrationTests.testFullSequenceReachesSuccess`).
final class LightConfigurationTests: XCTestCase {

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

    /// The planning step must auto-advance, and it must be the only one that does.
    ///
    /// Asserted explicitly because the tap budget depends on it — if a second step
    /// silently became auto-advancing, the visitor journey would grow invisibly.
    func testOnlyPlanningStepAutoAdvances() {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)

        let autoAdvancing: [ConfiguratorFlow.Step] = [
            .supplyChainPlanning(.family, model, variant, color, .classic, [], .homeDelivery)
        ]
        let tapCosting: [ConfiguratorFlow.Step] = [
            .pickCategory,
            .showEditionSummary(.family, model, variant),
            .pickColor(.family, model, variant),
            .pickInteriorStyle(.family, model, variant, color),
            .pickAccessories(.family, model, variant, color, .classic, []),
            .pickHandover(.family, model, variant, color, .classic, []),
            .deliveryProposal(.family, model, variant, color, .classic, plan),
            .confirm(.family, model, variant, color, .classic, [], .homeDelivery),
            .success(resp)
        ]

        for step in autoAdvancing {
            XCTAssertTrue(step.isAutoAdvancing, "\(step) should auto-advance")
        }
        for step in tapCosting {
            XCTAssertFalse(step.isAutoAdvancing, "\(step) should require a visitor tap")
        }
    }

    // MARK: - Supply-chain steps are in the machine

    /// The planning step must lead to the delivery proposal, and the proposal to
    /// confirm. Asserted on the production `advance(using:)` so a reordering is
    /// caught here rather than on the show floor.
    ///
    /// Transition chain: pickInteriorStyle → pickAccessories → pickHandover
    ///   → supplyChainPlanning → deliveryProposal → confirm.
    func testPlanningLeadsToProposalThenConfirm() {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)
        let selection = ConfiguratorFlow.Step.ForwardSelection(
            color: color,
            interiorStyle: .sport,
            reservationResponse: resp,
            supplyChainPlan: plan,
            selectedAccessoryIds: ["family-cargo-rack"],
            handover: .homeDelivery
        )

        // pickInteriorStyle → pickAccessories (new step, 2026-08-21)
        let afterStyle = ConfiguratorFlow.Step
            .pickInteriorStyle(.family, model, variant, color)
            .advance(using: selection)
        guard case .pickAccessories = afterStyle else {
            return XCTFail("Interior style must advance to pickAccessories, got \(String(describing: afterStyle))")
        }

        // pickAccessories → pickHandover (added in Task 4.1)
        let afterAccessories = afterStyle!.advance(using: selection)
        guard case .pickHandover = afterAccessories else {
            return XCTFail("pickAccessories must advance to pickHandover, got \(String(describing: afterAccessories))")
        }

        // pickHandover → supplyChainPlanning
        let afterHandover = afterAccessories!.advance(using: selection)
        guard case .supplyChainPlanning = afterHandover else {
            return XCTFail("pickHandover must advance to supplyChainPlanning, got \(String(describing: afterHandover))")
        }

        // supplyChainPlanning → deliveryProposal
        let afterPlanning = afterHandover!.advance(using: selection)
        guard case .deliveryProposal(_, _, _, _, _, let carriedPlan) = afterPlanning else {
            return XCTFail("Planning must advance to deliveryProposal, got \(String(describing: afterPlanning))")
        }
        XCTAssertEqual(carriedPlan, plan, "The proposal must carry the plan it was given")

        // deliveryProposal → confirm
        let afterProposal = afterPlanning!.advance(using: selection)
        guard case .confirm = afterProposal else {
            return XCTFail("Proposal must advance to confirm, got \(String(describing: afterProposal))")
        }
    }

    // MARK: - Step machine shape

    /// The post-2026-08-21 machine includes `.pickHandover` and `.pickAccessories`
    /// but not `.review` or `.pickFinance`.
    ///
    /// ## Case count history
    /// - 9 pre-Zone-2
    /// - 10 post-Zone-2 (added `.supplyChainPlanning` + `.deliveryProposal`,
    ///   removed `.pickAccessories` / `.review` / `.pickFinance`)
    /// - 11 post-2026-08-21 (re-added `.pickAccessories` per user direction)
    /// - 12 post-Task-4.1 (added `.pickHandover` between pickAccessories and supplyChainPlanning)
    ///
    /// The array below documents intent. The exhaustive switch that follows is
    /// the actual enforcement: adding or removing a `Step` case without updating
    /// the switch breaks the build, which the array-count assertion cannot do.
    func testStepMachineIncludesAccessoriesButNotReviewOrFinance() {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)

        let cases: [ConfiguratorFlow.Step] = [
            .pickCategory,
            .pickModel(CatalogCategory(categoryId: "x", displayName: "x", symbolName: nil, sortOrder: nil)),
            .pickVariant(
                CatalogCategory(categoryId: "x", displayName: "x", symbolName: nil, sortOrder: nil),
                model
            ),
            .showEditionSummary(.family, model, variant),
            .pickColor(.family, model, variant),
            .pickInteriorStyle(.family, model, variant, color),
            .pickAccessories(.family, model, variant, color, .classic, []),
            .pickHandover(.family, model, variant, color, .classic, []),
            .supplyChainPlanning(.family, model, variant, color, .classic, [], .homeDelivery),
            .deliveryProposal(.family, model, variant, color, .classic, plan),
            .confirm(.family, model, variant, color, .classic, [], .homeDelivery),
            .success(resp)
        ]
        XCTAssertEqual(cases.count, 12,
                       "Step machine should have exactly 12 cases as of Task 4.1 (pickHandover added)")

        // Exhaustive switch — no `default`, no `@unknown default`.
        // The compiler's exhaustiveness check is the real guard: adding a case
        // to `ConfiguratorFlow.Step` without adding an arm here is a build error.
        // The array-count assertion above cannot detect that; this switch can.
        func marker(for step: ConfiguratorFlow.Step) -> String {
            switch step {
            case .pickCategory:
                return "pickCategory"
            case .pickModel:
                return "pickModel"
            case .pickVariant:
                return "pickVariant"
            case .showEditionSummary:
                return "showEditionSummary"
            case .pickColor:
                return "pickColor"
            case .pickInteriorStyle:
                return "pickInteriorStyle"
            case .pickAccessories:
                return "pickAccessories"
            case .pickHandover:
                return "pickHandover"
            case .supplyChainPlanning:
                return "supplyChainPlanning"
            case .deliveryProposal:
                return "deliveryProposal"
            case .confirm:
                return "confirm"
            case .success:
                return "success"
            }
        }

        let expectedMarkers: Set<String> = [
            "pickCategory", "pickModel", "pickVariant", "showEditionSummary",
            "pickColor", "pickInteriorStyle", "pickAccessories", "pickHandover",
            "supplyChainPlanning", "deliveryProposal", "confirm", "success"
        ]
        let actualMarkers = Set(cases.map { marker(for: $0) })
        XCTAssertEqual(actualMarkers, expectedMarkers,
                       "Switch arms must cover every expected case and no unexpected ones")
        // `.review` and `.pickFinance` are absent — their absence is enforced
        // by the exhaustive switch above: if either were re-added to the enum,
        // the marker function would fail to compile until an arm is added here.
    }

    // MARK: - InteriorStyle covers all expected values

    func testInteriorStyleAllCasesPresent() {
        let styles = InteriorStyle.allCases
        XCTAssertTrue(styles.contains(.classic))
        XCTAssertTrue(styles.contains(.sport))
        XCTAssertTrue(styles.contains(.touring))
        XCTAssertEqual(styles.count, 3)
    }

    func testInteriorStyleRawValueRoundTrip() {
        for style in InteriorStyle.allCases {
            let decoded = InteriorStyle(rawValue: style.rawValue)
            XCTAssertEqual(decoded, style, "\(style.rawValue) must round-trip")
        }
    }
}
