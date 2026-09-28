import XCTest
@testable import MeridianMotorsCompanion

// `ConfiguratorFlow.Step.progressStep` coverage — the mapping the hosted upgrade
// flow's combined progress bar is derived from.
//
// Relocated out of `LightConfigurationTests.swift` for the same reason as
// `SupplyChainHandoverTests`: authored there while `project.pbxproj` was locked.
// Spec: 2026-08-21-cvx-upgrade-flow-continuity, Task 1.4.

// MARK: - Configurator progress step (spec 2026-08-21-cvx-upgrade-flow-continuity, Task 1.4)

final class ConfiguratorProgressTests: XCTestCase {

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

    // MARK: - One assertion per step case

    func testProgressStepPickCategory() {
        XCTAssertEqual(ConfiguratorFlow.Step.pickCategory.progressStep, 1)
    }

    func testProgressStepPickModel() {
        let cat = CatalogCategory(categoryId: "c", displayName: "Cat", symbolName: nil, sortOrder: nil)
        XCTAssertEqual(ConfiguratorFlow.Step.pickModel(cat).progressStep, 2)
    }

    func testProgressStepPickVariant() {
        let cat = CatalogCategory(categoryId: "c", displayName: "Cat", symbolName: nil, sortOrder: nil)
        XCTAssertEqual(ConfiguratorFlow.Step.pickVariant(cat, model).progressStep, 3)
    }

    func testProgressStepShowEditionSummary() {
        XCTAssertEqual(ConfiguratorFlow.Step.showEditionSummary(.family, model, variant).progressStep, 4)
    }

    func testProgressStepPickColor() {
        XCTAssertEqual(ConfiguratorFlow.Step.pickColor(.family, model, variant).progressStep, 5)
    }

    func testProgressStepPickInteriorStyle() {
        XCTAssertEqual(ConfiguratorFlow.Step.pickInteriorStyle(.family, model, variant, color).progressStep, 6)
    }

    func testProgressStepPickAccessories() {
        XCTAssertEqual(ConfiguratorFlow.Step.pickAccessories(.family, model, variant, color, .classic, []).progressStep, 7)
    }

    func testProgressStepPickHandover() {
        XCTAssertEqual(ConfiguratorFlow.Step.pickHandover(.family, model, variant, color, .classic, []).progressStep, 8)
    }

    func testProgressStepSupplyChainPlanning() {
        // One capsule per screen since 2026-08-22 (user decision) — no sharing.
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)
        let planningStep = ConfiguratorFlow.Step.supplyChainPlanning(.family, model, variant, color, .classic, [], .homeDelivery).progressStep
        let proposalStep = ConfiguratorFlow.Step.deliveryProposal(.family, model, variant, color, .classic, plan).progressStep
        XCTAssertNotEqual(planningStep, proposalStep,
                       "planning and proposal are separate screens and must occupy separate capsules")
    }

    func testProgressStepDeliveryProposal() {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)
        XCTAssertEqual(ConfiguratorFlow.Step.deliveryProposal(.family, model, variant, color, .classic, plan).progressStep, 10)
    }

    func testProgressStepConfirm() {
        // confirm and success are separate screens
        let confirmStep = ConfiguratorFlow.Step.confirm(.family, model, variant, color, .classic, [], .homeDelivery).progressStep
        let successStep = ConfiguratorFlow.Step.success(resp).progressStep
        XCTAssertNotEqual(confirmStep, successStep,
                       "confirm and success are separate screens and must occupy separate capsules")
    }

    func testProgressStepSuccess() {
        XCTAssertEqual(ConfiguratorFlow.Step.success(resp).progressStep, 12)
    }

    // MARK: - Values are contiguous from 1 with no gaps

    func testProgressStepsAreContiguousFromOne() {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)
        let cat = CatalogCategory(categoryId: "c", displayName: "Cat", symbolName: nil, sortOrder: nil)

        let allSteps: [ConfiguratorFlow.Step] = [
            .pickCategory,
            .pickModel(cat),
            .pickVariant(cat, model),
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

        let values = allSteps.map(\.progressStep)
        let minVal = values.min()!
        let maxVal = values.max()!

        XCTAssertEqual(minVal, 1, "progressStep values must start at 1")

        // Values form a set of contiguous integers from 1..max with no gaps
        let expected = Set(1...maxVal)
        let actual = Set(values)
        XCTAssertEqual(actual, expected,
                       "progressStep values must be contiguous from 1..max with no gaps; got \(values.sorted())")
    }

    // MARK: - progressStepCount equals the observed maximum

    func testProgressStepCountEqualsObservedMaximum() {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)
        let cat = CatalogCategory(categoryId: "c", displayName: "Cat", symbolName: nil, sortOrder: nil)

        let allSteps: [ConfiguratorFlow.Step] = [
            .pickCategory,
            .pickModel(cat),
            .pickVariant(cat, model),
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

        let observedMax = allSteps.map(\.progressStep).max()!
        XCTAssertEqual(ConfiguratorFlow.Step.progressStepCount, observedMax,
                       "progressStepCount must equal the maximum observed progressStep value")
    }

    // MARK: - supplyChainPlanning and deliveryProposal share their step

    /// Renamed 2026-08-22: the sharing it asserted was removed by user decision, so the
    /// old name asserted the opposite of the intent. Planning and proposal are two
    /// screens and must occupy two capsules.
    func testPlanningAndProposalOccupySeparateSteps() {
        let plan = SupplyChainPlan.plan(
            modelId: "m300", variantId: "std", colorId: "red",
            interiorStyle: .sport, deliveryWindow: .standard)
        let planning  = ConfiguratorFlow.Step.supplyChainPlanning(.family, model, variant, color, .classic, [], .homeDelivery)
        let proposal  = ConfiguratorFlow.Step.deliveryProposal(.family, model, variant, color, .classic, plan)
        XCTAssertEqual(proposal.progressStep, planning.progressStep + 1,
                       "proposal must be the capsule immediately after planning")
    }

    // MARK: - confirm and success are separate screens

    func testConfirmAndSuccessOccupySeparateSteps() {
        let confirm = ConfiguratorFlow.Step.confirm(.family, model, variant, color, .classic, [], .homeDelivery)
        let success = ConfiguratorFlow.Step.success(resp)
        XCTAssertEqual(success.progressStep, confirm.progressStep + 1,
                       "success must be the capsule immediately after confirm")
    }
}

// MARK: - Entry-relative progress (issue 2026-08-22)

/// The bar at the top of the hosted configurator must count the steps the visitor
/// will ACTUALLY see.
///
/// Door B (accepted upgrade offer) opens at `.showEditionSummary`, skipping category,
/// model and trim. Reporting an absolute `progressStep` over the whole machine made the
/// bar read "7 of 13 complete" the instant the configurator appeared, and padded the
/// total with three steps that could never be reached.
final class EntryRelativeProgressTests: XCTestCase {

    private let model = CatalogModel(modelId: "trailwind-adv", categoryId: "sport",
                                     displayName: "Trailwind Adventurer", basePrice: 1,
                                     imageKey: nil, specBadges: nil, sortOrder: 0)
    private let variant = CatalogVariant(variantId: "trailwind-adv-std",
                                         modelId: "trailwind-adv",
                                         displayName: "Standard", priceAdder: 0,
                                         specOverrides: nil, sortOrder: 0)
    private let color = CatalogColor(colorId: "slate",
                                     variantId: "trailwind-adv-std",
                                     displayName: "Deep Slate", hexColor: "#3D4A5C",
                                     imageKey: nil, priceAdder: 0)

    /// Door A: entry is step 1, so the report is the absolute value and the full total.
    func testColdEntryReportsFullMachine() {
        let r = ConfiguratorFlow.Step.progressReport(step: .pickCategory,
                                                     entryProgressStep: 1)
        XCTAssertEqual(r.index, 1)
        XCTAssertEqual(r.total, ConfiguratorFlow.Step.progressStepCount)
    }

    /// Door B: entering at the Edition summary must read as step 1 of the REMAINING
    /// steps, not step 4 of all of them.
    func testWarmEntryStartsAtOneOfTheRemainingSteps() {
        let entry = ConfiguratorFlow.Step
            .showEditionSummary(.family, model, variant).progressStep
        let r = ConfiguratorFlow.Step.progressReport(
            step: .showEditionSummary(.family, model, variant),
            entryProgressStep: entry)
        XCTAssertEqual(r.index, 1, "warm entry must read as the first step of what remains")
        XCTAssertEqual(r.total, ConfiguratorFlow.Step.progressStepCount - entry + 1,
                       "total must exclude the skipped picker steps")
        XCTAssertLessThan(r.total, ConfiguratorFlow.Step.progressStepCount,
                          "warm entry has strictly fewer steps than cold entry")
    }

    /// Advancing from a warm entry increments by one, and the total holds steady.
    func testWarmEntryAdvancesByOne() {
        let entry = ConfiguratorFlow.Step
            .showEditionSummary(.family, model, variant).progressStep
        let first = ConfiguratorFlow.Step.progressReport(
            step: .showEditionSummary(.family, model, variant), entryProgressStep: entry)
        let second = ConfiguratorFlow.Step.progressReport(
            step: .pickColor(.family, model, variant), entryProgressStep: entry)
        XCTAssertEqual(second.index, first.index + 1)
        XCTAssertEqual(second.total, first.total)
    }

    /// The final step must fill the bar exactly — never overshoot, never stop short.
    func testFinalStepFillsTheBarExactly() {
        for entry in 1...ConfiguratorFlow.Step.progressStepCount {
            let r = ConfiguratorFlow.Step.progressReport(
                step: .success(ReservationResponse(orderId: "_", orderNumber: "_",
                                                  depositRef: nil)),
                entryProgressStep: entry)
            XCTAssertEqual(r.index, r.total,
                           "entry \(entry): success is the terminal screen and must be "
                           + "the last capsule")
        }
    }

    /// Going back BELOW the entry point ("Change vehicle") must never produce a
    /// negative or zero index — the clamp is what prevents an out-of-range bar.
    func testGoingBackBelowEntryIsClampedNotNegative() {
        let entry = ConfiguratorFlow.Step
            .showEditionSummary(.family, model, variant).progressStep
        let r = ConfiguratorFlow.Step.progressReport(step: .pickCategory,
                                                     entryProgressStep: entry)
        XCTAssertEqual(r.index, 1)
        XCTAssertEqual(r.total, ConfiguratorFlow.Step.progressStepCount,
                       "dropping back to the pickers widens the bar to the full machine")
    }

    /// Index is always within the bar, for every step and every entry point.
    func testIndexIsAlwaysWithinTotal() {
        let steps: [ConfiguratorFlow.Step] = [
            .pickCategory,
            .showEditionSummary(.family, model, variant),
            .pickColor(.family, model, variant),
            .pickInteriorStyle(.family, model, variant, color),
            .pickAccessories(.family, model, variant, color, .classic, []),
            .pickHandover(.family, model, variant, color, .classic, []),
            .confirm(.family, model, variant, color, .classic, [], .homeDelivery)
        ]
        for entry in 1...ConfiguratorFlow.Step.progressStepCount {
            for step in steps {
                let r = ConfiguratorFlow.Step.progressReport(step: step,
                                                             entryProgressStep: entry)
                XCTAssertGreaterThanOrEqual(r.index, 1, "entry \(entry)")
                XCTAssertLessThanOrEqual(r.index, r.total, "entry \(entry)")
            }
        }
    }
}

// MARK: - Kind: payload-free progress handles (issue 2026-08-22)

/// `Step.Kind` exists so progress arithmetic can name a case without constructing one.
/// These pin the two properties the bar's stability depends on.
final class StepKindProgressTests: XCTestCase {

    /// Every `Kind` maps to a progress step, contiguous from 1 with no gaps — a gap
    /// would leave a capsule that can never light.
    func testKindProgressStepsAreContiguousFromOne() {
        let steps = Set(ConfiguratorFlow.Step.Kind.allCases.map(\.progressStep))
        XCTAssertEqual(steps.min(), 1)
        XCTAssertEqual(steps.max(), ConfiguratorFlow.Step.progressStepCount)
        for n in 1...ConfiguratorFlow.Step.progressStepCount {
            XCTAssertTrue(steps.contains(n), "no Kind occupies progress step \(n)")
        }
    }

    /// **Every screen gets its own capsule** — user decision 2026-08-22, reversing the
    /// earlier sharing. A bar that merges four screens into two capsules leaves the
    /// visitor passing through screens it ignores, which reads as skipped steps.
    func testEveryKindHasItsOwnProgressStep() {
        let steps = ConfiguratorFlow.Step.Kind.allCases.map(\.progressStep)
        XCTAssertEqual(Set(steps).count, ConfiguratorFlow.Step.Kind.allCases.count,
                       "two Kinds share a progress step; a screen would not tick the bar")
        XCTAssertEqual(ConfiguratorFlow.Step.progressStepCount,
                       ConfiguratorFlow.Step.Kind.allCases.count,
                       "the bar must have exactly as many capsules as there are screens")
    }

    /// `kind` must agree with the `Step` it came from, or the bar and the machine
    /// disagree about where the visitor is.
    func testKindAgreesWithTheStepItCameFrom() {
        let model = CatalogModel(modelId: "m", categoryId: "c", displayName: "M",
                                 basePrice: 1, imageKey: nil, specBadges: nil, sortOrder: 0)
        let variant = CatalogVariant(variantId: "v", modelId: "m", displayName: "V",
                                     priceAdder: 0, specOverrides: nil, sortOrder: 0)
        let pairs: [(ConfiguratorFlow.Step, ConfiguratorFlow.Step.Kind)] = [
            (.pickCategory, .pickCategory),
            (.showEditionSummary(.family, model, variant), .showEditionSummary),
            (.pickColor(.family, model, variant), .pickColor)
        ]
        for (step, kind) in pairs {
            XCTAssertEqual(step.kind, kind)
            XCTAssertEqual(step.progressStep, kind.progressStep)
        }
    }

    /// The host's nil-report fallback must equal what the configurator will actually
    /// report on a warm entry. If these diverge the capsule count visibly changes the
    /// instant the first report lands — the reported defect.
    func testHostFallbackMatchesTheWarmEntryTotal() {
        let warmEntry = ConfiguratorFlow.Step.Kind.showEditionSummary.progressStep
        let hostFallback = ConfiguratorFlow.Step.progressStepCount - warmEntry + 1

        let model = CatalogModel(modelId: "m", categoryId: "c", displayName: "M",
                                 basePrice: 1, imageKey: nil, specBadges: nil, sortOrder: 0)
        let variant = CatalogVariant(variantId: "v", modelId: "m", displayName: "V",
                                     priceAdder: 0, specOverrides: nil, sortOrder: 0)
        let reported = ConfiguratorFlow.Step.progressReport(
            step: .showEditionSummary(.family, model, variant),
            entryProgressStep: warmEntry).total

        XCTAssertEqual(hostFallback, reported,
            "UpgradeFlow's pre-report capsule count must equal the configurator's first "
            + "reported total, or the bar changes length after 'Configure & order'")
    }
}

// MARK: - No mid-flow change in bar length (issue 2026-08-22, second report)

/// The capsule count must not change between the configurator appearing and its entry
/// step resolving. Two rounds of this defect were both arithmetic, not rendering.
final class ProgressBarStabilityTests: XCTestCase {

    private let model = CatalogModel(modelId: "m", categoryId: "c", displayName: "M",
                                     basePrice: 1, imageKey: nil, specBadges: nil,
                                     sortOrder: 0)
    private let variant = CatalogVariant(variantId: "v", modelId: "m", displayName: "V",
                                         priceAdder: 0, specOverrides: nil, sortOrder: 0)

    /// **The regression test for the reported flicker.** What is reported at `.onAppear`
    /// (entry known, step not yet moved) must equal what is reported once the step
    /// actually reaches that entry. Routing the appear-time report through
    /// `progressReport` produced 10 then 7, because its clamp pulled the entry down to
    /// the still-initial `.pickCategory`.
    func testAppearTimeTotalEqualsEstablishedTotal() {
        let entry = ConfiguratorFlow.Step.Kind.showEditionSummary.progressStep

        let atAppear = ConfiguratorFlow.Step.progressReportAtEntry(entry)
        let established = ConfiguratorFlow.Step.progressReport(
            step: .showEditionSummary(.family, model, variant),
            entryProgressStep: entry)

        XCTAssertEqual(atAppear.total, established.total,
            "bar length must not change between appear and entry resolution")
        XCTAssertEqual(atAppear.index, established.index,
            "fill must not jump between appear and entry resolution")
    }

    /// The same must hold for a cold entry, where entry and initial step agree.
    func testColdEntryIsAlsoStable() {
        let entry = ConfiguratorFlow.Step.Kind.pickCategory.progressStep
        let atAppear = ConfiguratorFlow.Step.progressReportAtEntry(entry)
        let established = ConfiguratorFlow.Step.progressReport(step: .pickCategory,
                                                              entryProgressStep: entry)
        XCTAssertEqual(atAppear.total, established.total)
        XCTAssertEqual(atAppear.index, established.index)
    }

    /// Entry-time report always starts the bar at its first capsule, for any entry.
    func testEntryReportAlwaysStartsAtOne() {
        for entry in 1...ConfiguratorFlow.Step.progressStepCount {
            let r = ConfiguratorFlow.Step.progressReportAtEntry(entry)
            XCTAssertEqual(r.index, 1, "entry \(entry)")
            XCTAssertGreaterThanOrEqual(r.total, 1, "entry \(entry)")
            XCTAssertLessThanOrEqual(r.total, ConfiguratorFlow.Step.progressStepCount)
        }
    }

    /// And it must agree with the host's pre-report fallback, which is the other half of
    /// the same invariant.
    func testEntryReportAgreesWithHostFallback() {
        let warmEntry = ConfiguratorFlow.Step.Kind.showEditionSummary.progressStep
        let hostFallback = ConfiguratorFlow.Step.progressStepCount - warmEntry + 1
        XCTAssertEqual(ConfiguratorFlow.Step.progressReportAtEntry(warmEntry).total,
                       hostFallback)
    }
}

// MARK: - The view's CHOICE of formula, under test

/// Closes the gap a mutation test exposed: `ProgressBarStabilityTests` asserts both
/// formulae are individually right, and still passed when the view was reverted to
/// calling the wrong one. These assert on `progressReportOnAppear`, which is what the
/// view actually calls, so routing it back through the clamping formula fails here.
final class AppearReportChoiceTests: XCTestCase {

    private let model = CatalogModel(modelId: "m", categoryId: "c", displayName: "M",
                                     basePrice: 1, imageKey: nil, specBadges: nil,
                                     sortOrder: 0)
    private let variant = CatalogVariant(variantId: "v", modelId: "m", displayName: "V",
                                         priceAdder: 0, specOverrides: nil, sortOrder: 0)

    /// Warm entry: what the view publishes on appear must equal what it publishes once
    /// the step machine reaches the entry step. Unequal totals ARE the visible flicker.
    func testWarmAppearReportMatchesEstablishedReport() {
        let onAppear = ConfiguratorFlow.Step.progressReportOnAppear(hasOfferHandoff: true)
        let established = ConfiguratorFlow.Step.progressReport(
            step: .showEditionSummary(.family, model, variant),
            entryProgressStep: onAppear.entry)

        XCTAssertEqual(onAppear.total, established.total,
            "the view's appear-time total must equal its established total, or the bar "
            + "changes length right after 'Configure & order'")
        XCTAssertEqual(onAppear.index, established.index)
    }

    func testColdAppearReportMatchesEstablishedReport() {
        let onAppear = ConfiguratorFlow.Step.progressReportOnAppear(hasOfferHandoff: false)
        let established = ConfiguratorFlow.Step.progressReport(
            step: .pickCategory, entryProgressStep: onAppear.entry)
        XCTAssertEqual(onAppear.total, established.total)
        XCTAssertEqual(onAppear.index, established.index)
    }

    /// Warm entry must be strictly shorter than cold, or the skipped pickers are still
    /// being counted.
    func testWarmAppearIsShorterThanCold() {
        let warm = ConfiguratorFlow.Step.progressReportOnAppear(hasOfferHandoff: true)
        let cold = ConfiguratorFlow.Step.progressReportOnAppear(hasOfferHandoff: false)
        XCTAssertLessThan(warm.total, cold.total)
        XCTAssertEqual(cold.total, ConfiguratorFlow.Step.progressStepCount)
    }

    /// The entry the view seeds must be the entry the report was computed from.
    func testSeededEntryMatchesTheReportedOne() {
        for hasOffer in [true, false] {
            let r = ConfiguratorFlow.Step.progressReportOnAppear(hasOfferHandoff: hasOffer)
            XCTAssertEqual(r.entry,
                           ConfiguratorFlow.Step.entryStep(hasOfferHandoff: hasOffer))
            XCTAssertEqual(r.total,
                           ConfiguratorFlow.Step.progressStepCount - r.entry + 1)
        }
    }
}
