import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `SupplyChainPlan` — the supply-chain planning narrative model.
///
/// The properties that matter here are **determinism** and **honest attribution**.
/// Neither is cosmetic: a non-deterministic date breaks snapshot baselines and
/// lets a presenter get two different answers from the same demo, and an
/// attribution string that drifts into claiming a live query turns a demo
/// surface into a false capability claim.
final class SupplyChainPlanTests: XCTestCase {

    // MARK: - Fixtures

    /// Fixed clock so no test depends on the day it runs.
    private let fixedNow = Date(timeIntervalSince1970: 1_777_000_000)

    private func makePlan(
        modelId: String = "windrose",
        variantId: String = "std",
        colorId: String = "aurora-white",
        interiorStyle: InteriorStyle = .classic,
        deliveryWindow: AvailabilityContract.DeliveryWindow = .standard,
        handover: HandoverMethod = .homeDelivery,
        now: Date? = nil
    ) -> SupplyChainPlan {
        SupplyChainPlan.plan(
            modelId: modelId,
            variantId: variantId,
            colorId: colorId,
            interiorStyle: interiorStyle,
            deliveryWindow: deliveryWindow,
            handover: handover,
            now: now ?? fixedNow
        )
    }

    // MARK: - Determinism

    /// The same configuration must always produce the same plan.
    ///
    /// This is the test that would fail if someone reached for `Int.random` or
    /// Swift's `hashValue` — the latter is randomly seeded per process, so it
    /// would pass within one run and fail across launches.
    func testSamePlanForSameConfiguration() {
        let a = makePlan()
        let b = makePlan()
        XCTAssertEqual(a, b, "Identical configuration must yield an identical plan")
    }

    func testDeterministicSeedIsStableForSameInput() {
        let s1 = SupplyChainPlan.deterministicSeed(for: ["a", "b", "c"])
        let s2 = SupplyChainPlan.deterministicSeed(for: ["a", "b", "c"])
        XCTAssertEqual(s1, s2)
    }

    func testDeterministicSeedDiffersForDifferentInput() {
        let s1 = SupplyChainPlan.deterministicSeed(for: ["a", "b", "c"])
        let s2 = SupplyChainPlan.deterministicSeed(for: ["a", "b", "d"])
        XCTAssertNotEqual(s1, s2)
    }

    /// The seed must be order-sensitive — otherwise two different configurations
    /// that happen to use the same option ids in a different arrangement would
    /// collide onto one plan.
    func testDeterministicSeedIsOrderSensitive() {
        let s1 = SupplyChainPlan.deterministicSeed(for: ["a", "b"])
        let s2 = SupplyChainPlan.deterministicSeed(for: ["b", "a"])
        XCTAssertNotEqual(s1, s2)
    }

    /// A known-answer test pinning the FNV-1a offset basis and prime.
    /// If someone "optimises" the hash, this catches the behaviour change even
    /// though every other determinism test would still pass.
    func testDeterministicSeedKnownAnswer() {
        // FNV-1a 64-bit of the empty string is the offset basis itself.
        XCTAssertEqual(SupplyChainPlan.deterministicSeed(for: []), 0xcbf2_9ce4_8422_2325)
    }

    func testDifferentColourChangesThePlan() {
        let a = makePlan(colorId: "aurora-white")
        let b = makePlan(colorId: "racing-red")
        XCTAssertNotEqual(a, b,
            "Colour is part of the configuration and must be able to change the plan")
    }

    // MARK: - Delivery window behaviour

    /// A custom window must never be quoted as faster than standard for the same
    /// configuration. This is the one relationship a visitor would notice
    /// immediately if it inverted.
    func testCustomWindowIsNotFasterThanStandard() {
        // Checked across several configurations rather than one, because the
        // stock-build shortcut is configuration-dependent and a single sample
        // could pass by luck.
        let configs = [
            ("windrose", "std", "aurora-white", InteriorStyle.classic),
            ("trailwind", "sport", "cascade-blue", InteriorStyle.sport),
            ("crestwind", "lx", "solstice-grey", InteriorStyle.touring),
            ("azimuth", "gt", "meridian-black", InteriorStyle.classic)
        ]
        for (model, variant, colour, style) in configs {
            let std = SupplyChainPlan.plan(
                modelId: model, variantId: variant, colorId: colour,
                interiorStyle: style, deliveryWindow: .standard, now: fixedNow)
            let cst = SupplyChainPlan.plan(
                modelId: model, variantId: variant, colorId: colour,
                interiorStyle: style, deliveryWindow: .custom, now: fixedNow)
            XCTAssertGreaterThanOrEqual(
                cst.totalDays, std.totalDays,
                "\(model): custom window (\(cst.totalDays)d) must not be quoted "
                + "faster than standard (\(std.totalDays)d)")
        }
    }

    func testPlanCarriesTheRequestedDeliveryWindow() {
        XCTAssertEqual(makePlan(deliveryWindow: .standard).deliveryWindow, .standard)
        XCTAssertEqual(makePlan(deliveryWindow: .custom).deliveryWindow, .custom)
    }

    // MARK: - Plan sanity

    /// The delivery date must be in the future relative to the injected clock.
    /// A plan quoting a past date is the kind of defect that only shows up on
    /// stage.
    func testDeliveryDateIsAfterNow() {
        let plan = makePlan()
        XCTAssertGreaterThan(plan.proposedDeliveryDate, fixedNow)
    }

    /// Renamed and widened 2026-08-21 (spec `2026-08-21-cvx-upgrade-flow-continuity`).
    ///
    /// The old name asserted a two-leg model that the handover work deliberately
    /// superseded. `totalDays` now covers factory -> dealer -> customer, because it
    /// feeds `weeksRangeLabel` and the proposal's total-days row, which sit beside
    /// `proposedDeliveryDate` — and that has always counted every leg. Leaving the
    /// two-leg formula in place made those figures disagree on any home delivery.
    func testTotalDaysIsSumOfAllThreeLegs() {
        let plan = makePlan()
        XCTAssertEqual(
            plan.totalDays,
            plan.manufacturingDays + plan.transitDays + plan.finalLegDays)
    }

    /// The invariant the renamed test above must not lose: the days figure shown to
    /// the visitor and the date shown to the visitor are the same journey.
    func testTotalDaysMatchesTheProposedDateOffset() {
        for handover in [HandoverMethod.homeDelivery,
                         .dealerPickup(centerId: "c1", name: "Meridian Central")] {
            let now = Date()
            let plan = makePlan(handover: handover, now: now)
            let offset = Calendar.current.dateComponents(
                [.day], from: now, to: plan.proposedDeliveryDate).day
            XCTAssertEqual(offset, plan.totalDays,
                           "\(handover): days row and delivery date must describe "
                           + "the same journey")
        }
    }

    /// Every derived duration must be positive. A zero or negative build or
    /// transit time would render as "0 days" on the proposal screen.
    func testDurationsArePositive() {
        let configs = ["windrose", "trailwind", "crestwind", "azimuth", "unknown"]
        for model in configs {
            for window in [AvailabilityContract.DeliveryWindow.standard, .custom] {
                let plan = makePlan(modelId: model, deliveryWindow: window)
                XCTAssertGreaterThan(plan.manufacturingDays, 0, "\(model)/\(window)")
                XCTAssertGreaterThan(plan.transitDays, 0, "\(model)/\(window)")
            }
        }
    }

    func testFacilityIsAlwaysOneOfTheKnownFacilities() {
        let ids = Set(SupplyChainPlan.facilities.map(\.facilityId))
        for model in ["windrose", "trailwind", "crestwind", "azimuth", "", "zzz"] {
            let plan = makePlan(modelId: model)
            XCTAssertTrue(ids.contains(plan.facility.facilityId),
                          "\(model) resolved to an unknown facility")
        }
    }

    func testLongLeadPartCountIsInRange() {
        for model in ["windrose", "trailwind", "crestwind", "azimuth"] {
            for window in [AvailabilityContract.DeliveryWindow.standard, .custom] {
                let plan = makePlan(modelId: model, deliveryWindow: window)
                XCTAssertTrue((0...2).contains(plan.longLeadPartCount),
                              "\(model)/\(window) long-lead count out of range")
            }
        }
    }

    // MARK: - Planning sequence

    func testPlanningStepsAreInExpectedOrder() {
        XCTAssertEqual(SupplyChainPlan.PlanningStep.allCases, [
            .contacting, .parts, .stockBuilds, .manufacturingTime, .facility, .finalLeg
        ])
    }

    /// The sequence has to read as substantial without becoming the reason a
    /// queue forms — the Zone 2 budget is 3 minutes at 60–80 visitors/hour.
    func testTotalDwellStaysWithinJourneyBudget() {
        XCTAssertLessThanOrEqual(SupplyChainPlan.totalDwellSeconds, 8.0,
            "Planning animation must stay well inside the 3-minute journey budget")
        XCTAssertGreaterThan(SupplyChainPlan.totalDwellSeconds, 3.0,
            "Too fast to read defeats the purpose of showing the sequence")
    }

    func testEveryPlanningStepHasSymbolAndPositiveDwell() {
        for step in SupplyChainPlan.PlanningStep.allCases {
            XCTAssertFalse(step.symbolName.isEmpty, "\(step) has no symbol")
            XCTAssertGreaterThan(step.dwellSeconds, 0, "\(step) has non-positive dwell")
            XCTAssertFalse(step.rawValue.isEmpty, "\(step) has no label")
        }
    }

    // MARK: - Attribution honesty

    /// The attribution must credit the pattern WITHOUT claiming a live query.
    ///
    /// This is the guard against the failure mode this portfolio has already
    /// shipped once: an Implementation Guide callout citing a `lambdas/proactive/`
    /// directory that does not exist. A demo string that says "live" or
    /// "real-time" here would be the same class of overclaim.
    func testAttributionNamesTheProductWithoutClaimingALiveQuery() {
        let note = SupplyChainPlan.attributionNote
        XCTAssertTrue(note.contains("Amazon Connect Decisions"),
                      "Attribution must name the product")
        XCTAssertTrue(note.lowercased().contains("demo data"),
                      "Attribution must disclose that the data is demo data")

        // Words that would assert a live integration we do not have.
        for forbidden in ["live", "real-time", "realtime", "queried", "fetched"] {
            XCTAssertFalse(note.lowercased().contains(forbidden),
                "Attribution must not claim '\(forbidden)' — nothing is queried")
        }
    }

    // MARK: - Order routing chain

    func testRoutingChainIsOrderedAndComplete() {
        let chain = OrderRoutingStage.chain(for: makePlan())
        XCTAssertEqual(chain.map(\.key),
                       ["intake", "planning", "material", "scheduling",
                        "build", "logistics", "handover"],
                       "Routing chain order is the narrative; it must not drift")
    }

    func testRoutingChainStagesAreAllPopulated() {
        for stage in OrderRoutingStage.chain(for: makePlan()) {
            XCTAssertFalse(stage.title.isEmpty, "\(stage.key) has no title")
            XCTAssertFalse(stage.detail.isEmpty, "\(stage.key) has no detail")
            XCTAssertFalse(stage.symbolName.isEmpty, "\(stage.key) has no symbol")
            XCTAssertFalse(stage.systemLabel.isEmpty, "\(stage.key) has no system label")
        }
    }

    /// The scheduling stage must name the facility the visitor was shown, so the
    /// post-order narrative and the delivery proposal cannot disagree.
    func testSchedulingStageNamesThePlansFacility() {
        let plan = makePlan()
        let chain = OrderRoutingStage.chain(for: plan)
        let scheduling = chain.first { $0.key == "scheduling" }
        XCTAssertNotNil(scheduling)
        XCTAssertTrue(scheduling!.detail.contains(plan.facility.displayName),
            "Scheduling detail must name the same facility shown on the proposal")
    }

    /// The material stage's copy must reflect whether long-lead parts exist,
    /// rather than asserting one case unconditionally.
    func testMaterialStageReflectsLongLeadPartCount() {
        // Find one configuration with long-lead parts and one without, so both
        // branches are actually exercised rather than assumed reachable.
        var sawWith = false
        var sawWithout = false
        for model in ["windrose", "trailwind", "crestwind", "azimuth", "aa", "bb", "cc"] {
            for window in [AvailabilityContract.DeliveryWindow.standard, .custom] {
                let plan = makePlan(modelId: model, deliveryWindow: window)
                let material = OrderRoutingStage.chain(for: plan)
                    .first { $0.key == "material" }!
                if plan.longLeadPartCount > 0 {
                    sawWith = true
                    XCTAssertTrue(material.detail.contains("\(plan.longLeadPartCount)"),
                        "Material detail must state the long-lead count")
                } else {
                    sawWithout = true
                    XCTAssertTrue(material.detail.lowercased().contains("available"),
                        "With no long-lead parts the copy should say parts are available")
                }
            }
        }
        XCTAssertTrue(sawWith, "No configuration produced long-lead parts — branch untested")
        XCTAssertTrue(sawWithout, "No configuration produced zero long-lead parts — branch untested")
    }

    // MARK: - Formatting

    func testFormattedDeliveryDateIsStableForAFixedLocale() {
        let plan = makePlan()
        let a = plan.formattedDeliveryDate(locale: Locale(identifier: "en_GB"))
        let b = plan.formattedDeliveryDate(locale: Locale(identifier: "en_GB"))
        XCTAssertEqual(a, b)
        XCTAssertFalse(a.isEmpty)
    }

    func testWeeksRangeLabelIsNonEmptyAndContainsARange() {
        let label = makePlan().weeksRangeLabel
        XCTAssertTrue(label.contains("–"), "Expected an en-dash range, got \(label)")
        XCTAssertTrue(label.contains("weeks"))
    }
}
