import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `EnergySavingsProjection`.
///
/// This is a **money claim shown to a customer**, which per
/// `~/.kiro/steering/agentic-tiers.md` is a deterministic seam with a verbatim
/// disclosure. The tests here defend three things in that order of importance:
/// the disclosure cannot drift into a quote, the figure is deterministic, and the
/// projection stays silent rather than guessing when it lacks grounds.
final class EnergySavingsProjectionTests: XCTestCase {

    private func project(
        fuelType: String? = "electric",
        odometer: Int? = 39_840,
        yearsOwned: Int = 3,
        offerId: String = "o1",
        model: String = "Meridian Crestwind Signature",
        currency: String = "£"
    ) -> EnergySavingsProjection? {
        EnergySavingsProjection.projection(
            currentFuelType: fuelType, odometerMiles: odometer, yearsOwned: yearsOwned,
            offerId: offerId, modelName: model, currencySymbol: currency)
    }

    // MARK: - Disclosure (highest-stakes assertion here)

    /// The disclosure must say it is an estimate, must say the data is illustrative,
    /// and must explicitly deny being a quote. Any one alone under-discloses: "an
    /// estimate" still implies real inputs, and "demo data" alone does not tell the
    /// customer the number will move with tariffs.
    func testDisclosureIsCompleteAndDeniesBeingAQuote() {
        let d = EnergySavingsProjection.disclosure.lowercased()
        XCTAssertTrue(d.contains("estimated"), "must present the figure as an estimate")
        XCTAssertTrue(d.contains("vary"), "must say actual costs vary")
        XCTAssertTrue(d.contains("illustrative demo data"), "must disclose demo data")
        XCTAssertTrue(d.contains("not a quote"), "must explicitly deny being a quote")
    }

    /// Must not imply a real tariff feed, a real charging history, or a guarantee.
    func testDisclosureDoesNotImplyRealDataOrGuarantee() {
        let d = EnergySavingsProjection.disclosure.lowercased()
        for forbidden in ["guaranteed", "your actual tariff", "based on your charging history",
                         "live pricing", "we will save you"] {
            XCTAssertFalse(d.contains(forbidden), "disclosure must not imply '\(forbidden)'")
        }
    }

    // MARK: - Silence rather than guessing

    /// A non-electric current vehicle must produce nothing.
    ///
    /// Comparing petrol spend against electricity is a much larger claim needing
    /// fuel-price assumptions and a different disclosure. Staying quiet is correct;
    /// an ICE-to-EV comparison deserves its own type.
    func testNonElectricVehicleProducesNoProjection() {
        for ft in ["petrol", "gasoline", "diesel", "hybrid", "phev", "", nil] {
            XCTAssertNil(project(fuelType: ft),
                "fuelType '\(ft ?? "nil")' must not produce an electric-to-electric projection")
        }
    }

    /// All the electric spellings present in seed data must be recognised.
    func testElectricSynonymsAreRecognised() {
        for ft in ["electric", "Electric", "ELECTRIC", "bev", "BEV", "ev", "EV"] {
            XCTAssertTrue(EnergySavingsProjection.isElectric(ft), "\(ft) should count as electric")
            XCTAssertNotNil(project(fuelType: ft))
        }
    }

    /// Missing or nonsensical odometer data must produce nothing rather than a
    /// figure derived from a zero.
    func testMissingOrZeroOdometerProducesNoProjection() {
        XCTAssertNil(project(odometer: nil))
        XCTAssertNil(project(odometer: 0))
        XCTAssertNil(project(odometer: -5))
        XCTAssertNil(project(yearsOwned: 0))
    }

    // MARK: - Determinism

    func testProjectionIsDeterministic() {
        XCTAssertEqual(project(), project(),
            "Same inputs must yield the same figure — a presenter cannot get two "
            + "different savings numbers from one demo")
    }

    func testDifferentOffersProduceDifferentProjections() {
        let a = project(offerId: "o1", model: "Meridian Crestwind Signature")
        let b = project(offerId: "o2", model: "Meridian Windrose")
        XCTAssertNotEqual(a, b)
    }

    // MARK: - Arithmetic sanity

    /// A saving must be positive to be shown at all — a zero or negative "saving"
    /// is not a selling point and must be suppressed.
    func testOnlyPositiveSavingsAreReturned() {
        for id in ["a", "b", "c", "d", "e", "f"] {
            if let p = project(offerId: id) {
                XCTAssertGreaterThan(p.annualSaving, 0, "offer \(id) returned a non-positive saving")
            }
        }
    }

    /// The offered model must be more efficient than the current vehicle, or there
    /// would be no saving to claim.
    func testOfferedModelIsMoreEfficientThanCurrent() {
        guard let p = project() else { return XCTFail("expected a projection") }
        XCTAssertLessThan(p.offeredConsumptionKwh, p.currentConsumptionKwh)
        XCTAssertGreaterThan(p.efficiencyGainPercent, 0)
    }

    /// A larger body style must not claim a bigger efficiency gain than a compact —
    /// a three-row SUV outperforming a city car would be visibly implausible to an
    /// OEM audience.
    func testEfficiencyGainRanksByBodyStyle() {
        let compact = project(model: "Meridian Windrose")
        let threeRow = project(model: "Meridian Crestwind Signature")
        guard let compact, let threeRow else { return XCTFail("expected projections") }
        XCTAssertGreaterThan(compact.efficiencyGainPercent, threeRow.efficiencyGainPercent,
            "The compact should claim a larger efficiency gain than the three-row SUV")
    }

    /// Annual distance must derive from the odometer, not a constant.
    func testAnnualDistanceDerivesFromOdometer() {
        let low = project(odometer: 30_000, yearsOwned: 3)
        let high = project(odometer: 90_000, yearsOwned: 3)
        guard let low, let high else { return XCTFail("expected projections") }
        XCTAssertGreaterThan(high.annualDistanceMiles, low.annualDistanceMiles)
        XCTAssertEqual(low.annualDistanceMiles, 10_000)
    }

    func testHomeChargeShareIsAPercentage() {
        guard let p = project() else { return XCTFail("expected a projection") }
        XCTAssertTrue((0...100).contains(p.homeChargePercent))
    }

    /// Home energy must be assumed cheaper than public rapid charging, or the
    /// charging-mix lever works backwards.
    func testHomeTariffIsCheaperThanPublic() {
        guard let p = project() else { return XCTFail("expected a projection") }
        XCTAssertLessThan(p.homeTariffPerKwh, p.publicTariffPerKwh)
    }

    // MARK: - Assumptions are surfaced

    /// Every input that moves the answer must appear in `assumptions`. A savings
    /// figure without its inputs is unfalsifiable.
    func testAssumptionsExposeEveryInputThatMovesTheAnswer() {
        guard let p = project() else { return XCTFail("expected a projection") }
        let joined = p.assumptions.joined(separator: " | ")
        XCTAssertTrue(joined.contains("miles a year"), "annual distance must be stated")
        XCTAssertTrue(joined.contains("charging at home"), "charging mix must be stated")
        XCTAssertTrue(joined.contains("kWh/100km"), "consumption comparison must be stated")
        XCTAssertTrue(joined.contains("per kWh"), "tariffs must be stated")
        XCTAssertGreaterThanOrEqual(p.assumptions.count, 4)
    }

    /// Currency must come from tenant config, never a hardcoded glyph — the same
    /// defect `UpgradeFlow` already fixed once, where a rupee symbol prefixed dollar
    /// amounts.
    func testCurrencySymbolIsHonoured() {
        for symbol in ["£", "$", "₹", "€"] {
            guard let p = project(currency: symbol) else { return XCTFail("expected a projection") }
            XCTAssertTrue(p.savingLabel.hasPrefix(symbol), "\(symbol) not honoured")
            XCTAssertTrue(p.assumptions.joined().contains(symbol))
        }
    }

    /// An empty currency symbol must still render a usable figure rather than
    /// crashing or inventing a glyph.
    func testEmptyCurrencySymbolStillRendersTheFigure() {
        guard let p = project(currency: "") else { return XCTFail("expected a projection") }
        XCTAssertFalse(p.savingLabel.isEmpty)
        XCTAssertTrue(p.savingLabel.contains("a year"))
    }
}
