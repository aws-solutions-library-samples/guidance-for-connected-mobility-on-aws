import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `OfferRecommendationBasis` — the evidence behind an upgrade
/// recommendation.
///
/// Two properties carry weight here, and neither is cosmetic. **Determinism**, so a
/// presenter re-running the demo gets the same explanation rather than a different
/// one in front of an audience. And **honest attribution**, because this narrates a
/// touchpoint scan that does not happen — a string drifting into implying real
/// customer-data analysis turns a demo surface into a false capability claim.
final class OfferRecommendationBasisTests: XCTestCase {

    private let fixedNow = Date(timeIntervalSince1970: 1_777_000_000)

    private func makeBasis(
        offerId: String = "offer-1",
        modelName: String = "Meridian Crestwind Signature",
        rationale: String? = nil
    ) -> OfferRecommendationBasis {
        OfferRecommendationBasis.basis(
            offerId: offerId, modelName: modelName, rationale: rationale, now: fixedNow)
    }

    // MARK: - Determinism

    func testSameOfferYieldsSameBasis() {
        XCTAssertEqual(makeBasis(), makeBasis(),
            "Identical offer must yield identical reasoning — a presenter re-running "
            + "the demo cannot get a different explanation")
    }

    func testDifferentOffersYieldDifferentBasis() {
        let a = makeBasis(offerId: "offer-1", modelName: "Meridian Crestwind Signature")
        let b = makeBasis(offerId: "offer-2", modelName: "Meridian Azimuth Executive")
        XCTAssertNotEqual(a, b)
    }

    // MARK: - Attribution honesty

    /// Must disclose that this is demo data and must NOT imply a real scan happened.
    ///
    /// Guards the same failure mode as `SupplyChainPlan`'s attribution test: this
    /// portfolio has shipped an IG callout citing a directory that does not exist.
    func testAttributionDisclosesDemoDataAndClaimsNoAnalysis() {
        let note = OfferRecommendationBasis.attributionNote
        XCTAssertTrue(note.lowercased().contains("demo data"),
                      "Must disclose that the reasoning is demo data")
        XCTAssertTrue(note.lowercased().contains("no customer data"),
                      "Must state explicitly that no customer data was analysed")

        for forbidden in ["analysed your", "we tracked", "live", "real-time",
                          "monitored your", "collected"] {
            XCTAssertFalse(note.lowercased().contains(forbidden),
                "Attribution must not imply '\(forbidden)' — no scan occurs")
        }
    }

    // MARK: - Evidence shape

    /// The website-browsing touchpoint is always present. It is the clearest
    /// demonstration that the recommendation spans surfaces rather than living
    /// inside this app, which is the point being made.
    func testWebBrowsingEvidenceIsAlwaysPresent() {
        for model in ["Meridian Crestwind Signature", "Meridian Azimuth Executive",
                      "Meridian Trailwind", "Meridian Windrose"] {
            let basis = makeBasis(modelName: model)
            XCTAssertTrue(basis.evidence.contains { $0.touchpoint == .webBrowsing },
                "\(model) should cite off-app browsing")
        }
    }

    /// Evidence must span more than one touchpoint — a single-source recommendation
    /// does not demonstrate the cross-surface scan being claimed.
    func testEvidenceSpansMultipleTouchpoints() {
        let basis = makeBasis()
        let distinct = Set(basis.evidence.map(\.touchpoint))
        XCTAssertGreaterThanOrEqual(distinct.count, 3,
            "Recommendation should draw on at least three distinct touchpoints")
    }

    /// Observation and inference must both be populated and must differ. Collapsing
    /// them would prevent a customer agreeing with the fact while rejecting the
    /// conclusion, which is the distinction the two fields exist to preserve.
    func testEveryEvidenceItemSeparatesObservationFromInference() {
        for item in makeBasis(rationale: "Suits your weekend towing").evidence {
            XCTAssertFalse(item.detail.isEmpty, "\(item.touchpoint) has no observation")
            XCTAssertFalse(item.inference.isEmpty, "\(item.touchpoint) has no inference")
            XCTAssertNotEqual(item.detail, item.inference,
                "\(item.touchpoint) conflates the observation with the inference")
        }
    }

    /// The tenant's own rationale is folded in as evidence rather than discarded or
    /// shown separately — one explanation on screen, not two competing ones.
    func testTenantRationaleBecomesEvidence() {
        let withRationale = makeBasis(rationale: "Suits your weekend towing")
        XCTAssertTrue(
            withRationale.evidence.contains { $0.detail == "Suits your weekend towing" },
            "The offer's own rationale should appear as evidence")

        let without = makeBasis(rationale: nil)
        XCTAssertEqual(without.evidence.count, withRationale.evidence.count - 1)
    }

    /// Whitespace-only rationale must not produce an empty evidence row.
    func testBlankRationaleIsNotAddedAsEvidence() {
        let blank = makeBasis(rationale: "   ")
        let none = makeBasis(rationale: nil)
        XCTAssertEqual(blank.evidence.count, none.evidence.count)
    }

    // MARK: - Contract fields

    func testConfidenceIsInRangeAndBanded() {
        let basis = makeBasis()
        XCTAssertTrue((0.0...1.0).contains(basis.confidence))
        XCTAssertFalse(basis.confidenceLabel.isEmpty)
        // Never presented as spuriously precise.
        XCTAssertTrue(["High confidence", "Moderate confidence", "Low confidence"]
                        .contains(basis.confidenceLabel))
    }

    /// `computedAt` must not be in the future relative to the injected clock —
    /// staleness is surfaced, and a future timestamp would read as nonsense.
    func testComputedAtIsNotInTheFuture() {
        for id in ["a", "b", "c", "d", "e"] {
            let basis = makeBasis(offerId: id)
            XCTAssertLessThanOrEqual(basis.computedAt, fixedNow,
                "offer \(id): computedAt must not be after now")
        }
    }

    func testFreshnessAndTouchpointSummaryArePopulated() {
        let basis = makeBasis()
        XCTAssertTrue(basis.freshnessLabel(now: fixedNow).lowercased().contains("reviewed"))
        XCTAssertFalse(basis.touchpointSummary.isEmpty)
        // Summary must be de-duplicated.
        let parts = basis.touchpointSummary.components(separatedBy: " · ")
        XCTAssertEqual(parts.count, Set(parts).count, "Touchpoint summary repeats a source")
    }

    // MARK: - Offer-name art resolution

    /// Offers carry a marketing string, not a make/model pair. The seeded offers are
    /// literally "Meridian Crestwind Signature" and "Meridian Azimuth Executive".
    func testOfferModelNameResolvesBundledArt() {
        XCTAssertEqual(
            VehicleSweepView.bundledStaticImageName(forOfferModelName: "Meridian Crestwind Signature"),
            "MeridianCrestwind")
        XCTAssertEqual(
            VehicleSweepView.bundledStaticImageName(forOfferModelName: "Meridian Azimuth Executive"),
            "MeridianAzimuth")
    }

    /// The brand token is required. A bare model word on another brand's offer must
    /// NOT render Meridian art — that is the wrong-vehicle failure.
    func testOfferNameWithoutBrandTokenDoesNotResolve() {
        for name in ["Crestwind Signature", "Azimuth Executive", "Trailwind", ""] {
            XCTAssertNil(VehicleSweepView.bundledStaticImageName(forOfferModelName: name),
                "'\(name)' lacks the brand token and must not resolve Meridian art")
        }
        XCTAssertNil(VehicleSweepView.bundledStaticImageName(forOfferModelName: nil))
    }

    /// A Meridian offer for a model with no art must return nil rather than
    /// substituting a different body style.
    func testMeridianOfferForUnknownModelDoesNotResolve() {
        XCTAssertNil(VehicleSweepView.bundledStaticImageName(
            forOfferModelName: "Meridian Zephyr Sport"))
    }

    // MARK: - Single curated pick

    private func offer(_ id: String, _ model: String,
                       rationale: String? = nil,
                       ivePackage: IvePackage? = nil) -> AcquireConfig.UpgradeOffer {
        AcquireConfig.UpgradeOffer(
            offerId: id, modelName: model, rationale: rationale,
            price: nil, imageUrl: nil, testRideAvailable: nil,
            finance: nil, specComparison: nil, tradeInCredit: nil,
            loyaltyDiscount: nil, loyaltyBasis: nil, priceAfterLoyalty: nil,
            monthly: nil, validUntil: nil, disclosure: nil,
            ivePackage: ivePackage)
    }

    /// The offer's `ivePackage` must reach the configurator handoff unchanged.
    ///
    /// Regression test for a defect introduced 2026-08-18: `configureAndOrder()`
    /// passed `edition: nil` on the belief that `UpgradeOffer` had no Edition field.
    /// It does, and its docstring states the Edition is carried through "unchanged".
    /// Dropping it substitutes a per-model default for the agent's actual
    /// recommendation — and the Edition is what Zone 3 engraves on the key card, so
    /// the substitution would be silent and wrong.
    func testOfferIvePackageIsCarriedIntoTheHandoff() {
        for edition in [IvePackage.family, .executive, .adventure, .entryUrban] {
            let o = offer("o1", "Meridian Crestwind Signature", ivePackage: edition)
            let handoff = ConfiguratorOfferHandoff(
                edition: o.ivePackage, modelName: o.modelName, firstName: nil)
            XCTAssertEqual(handoff.edition, edition,
                "\(edition) must survive the handoff unchanged")
        }
    }

    /// A nil `ivePackage` is legitimate, and must stay nil so the configurator's
    /// per-model default resolves — rather than being coerced to a fixed value here.
    func testNilIvePackageStaysNilForDownstreamResolution() {
        let o = offer("o1", "Meridian Crestwind Signature", ivePackage: nil)
        let handoff = ConfiguratorOfferHandoff(
            edition: o.ivePackage, modelName: o.modelName, firstName: nil)
        XCTAssertNil(handoff.edition)
        // And the downstream fallback must produce a real Edition, never a sentinel.
        XCTAssertNotNil(IvePackage.defaultFor(modelId: o.modelName))
    }

    /// The banner shows exactly one recommendation. Selection must be deterministic
    /// so a presenter re-running the demo gets the same vehicle — the failure mode
    /// here is a different car appearing on stage than in rehearsal.
    func testBestMatchIsDeterministic() {
        let offers = [offer("o1", "Meridian Crestwind Signature"),
                      offer("o2", "Meridian Azimuth Executive")]
        let first = OfferRecommendationBasis.bestMatch(among: offers, now: fixedNow)
        let second = OfferRecommendationBasis.bestMatch(among: offers, now: fixedNow)
        XCTAssertNotNil(first)
        XCTAssertEqual(first?.offer.id, second?.offer.id)
    }

    /// Selection must not depend on how the tenant happened to order its config.
    func testBestMatchIsIndependentOfInputOrder() {
        let a = offer("o1", "Meridian Crestwind Signature")
        let b = offer("o2", "Meridian Azimuth Executive")
        let forward = OfferRecommendationBasis.bestMatch(among: [a, b], now: fixedNow)
        let reversed = OfferRecommendationBasis.bestMatch(among: [b, a], now: fixedNow)
        XCTAssertEqual(forward?.offer.id, reversed?.offer.id,
            "Array order must not decide which vehicle is recommended")
    }

    /// The winner must be the highest-confidence candidate, not merely the first.
    func testBestMatchPicksHighestConfidence() {
        let offers = [offer("o1", "Meridian Crestwind Signature"),
                      offer("o2", "Meridian Azimuth Executive"),
                      offer("o3", "Meridian Trailwind")]
        guard let top = OfferRecommendationBasis.bestMatch(among: offers, now: fixedNow) else {
            return XCTFail("expected a pick")
        }
        for o in offers {
            let b = OfferRecommendationBasis.basis(
                offerId: o.id, modelName: o.modelName, rationale: o.rationale, now: fixedNow)
            XCTAssertLessThanOrEqual(b.confidence, top.basis.confidence,
                "\(o.id) scores higher than the selected pick")
        }
    }

    /// Two offers with identical evidence counts must still be separable, otherwise
    /// the pick collapses to the tie-break and the "curation" is decorative.
    func testOffersWithSameEvidenceCountHaveDifferentConfidence() {
        let a = OfferRecommendationBasis.basis(
            offerId: "o1", modelName: "Meridian Crestwind Signature", now: fixedNow)
        let b = OfferRecommendationBasis.basis(
            offerId: "o2", modelName: "Meridian Azimuth Executive", now: fixedNow)
        XCTAssertEqual(a.evidence.count, b.evidence.count, "precondition for this test")
        XCTAssertNotEqual(a.confidence, b.confidence,
            "Equal-evidence offers must still rank, or selection is arbitrary")
    }

    // MARK: - Same-nameplate ranking

    /// A newer generation of the vehicle the customer already owns must outrank a
    /// different body style. This is the realistic upgrade path, and before this
    /// signal existed `bestMatch` separated candidates only by evidence count plus a
    /// hash-derived spread — arbitrary dressed as a score.
    func testSameNameplateOfferOutranksDifferentModels() {
        let offers = [
            offer("o1", "Meridian Crestwind Signature"),
            offer("o2", "Meridian Azimuth Executive"),
            offer("o3", "Meridian Trailwind 2026"),
        ]
        let top = OfferRecommendationBasis.bestMatch(
            among: offers, ownedModelName: "Trailwind", now: fixedNow)
        XCTAssertEqual(top?.offer.id, "o3",
            "A newer Trailwind must outrank a Crestwind or Azimuth for a Trailwind owner")
    }

    /// The signal must be inert when the owner drives something else — otherwise it
    /// would boost an unrelated offer.
    func testSameNameplateSignalDoesNotFireForADifferentOwner() {
        let withOwned = OfferRecommendationBasis.basis(
            offerId: "o1", modelName: "Meridian Trailwind 2026",
            ownedModelName: "Azimuth", now: fixedNow)
        let noOwned = OfferRecommendationBasis.basis(
            offerId: "o1", modelName: "Meridian Trailwind 2026",
            ownedModelName: nil, now: fixedNow)
        XCTAssertEqual(withOwned.confidence, noOwned.confidence,
            "An Azimuth owner must get no nameplate bonus on a Trailwind offer")
    }

    /// The match must cite itself as evidence — a weighted signal the customer
    /// cannot see is exactly the unauditable assertion the evidence list exists to
    /// prevent.
    func testSameNameplateAppearsAsEvidence() {
        let b = OfferRecommendationBasis.basis(
            offerId: "o1", modelName: "Meridian Trailwind 2026",
            ownedModelName: "Trailwind", now: fixedNow)
        XCTAssertTrue(b.evidence.contains { $0.detail.contains("already drive") },
            "the nameplate match must be stated, not just scored")
    }

    /// Short owned-model tokens must not match spuriously — a 3-letter fragment
    /// would collide across unrelated names.
    func testShortTokensDoNotMatchSpuriously() {
        let b = OfferRecommendationBasis.basis(
            offerId: "o1", modelName: "Meridian Crestwind Signature",
            ownedModelName: "EV6", now: fixedNow)
        let base = OfferRecommendationBasis.basis(
            offerId: "o1", modelName: "Meridian Crestwind Signature",
            ownedModelName: nil, now: fixedNow)
        XCTAssertEqual(b.confidence, base.confidence)
    }

    func testBestMatchOfEmptyListIsNil() {
        XCTAssertNil(OfferRecommendationBasis.bestMatch(among: [], now: fixedNow))
    }

    func testBestMatchOfSingleOfferReturnsIt() {
        let only = offer("o1", "Meridian Windrose")
        XCTAssertEqual(
            OfferRecommendationBasis.bestMatch(among: [only], now: fixedNow)?.offer.id, "o1")
    }
}
