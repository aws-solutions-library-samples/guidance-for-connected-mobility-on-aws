import Foundation

// MARK: - EnergySavingsProjection

/// Projected running-cost saving from moving to a newer electric model, derived
/// from the customer's own driving and charging behaviour.
///
/// ## This is a money claim, so it is deterministic — deliberately
///
/// `~/.kiro/steering/agentic-tiers.md` § "What stays deterministic, and why" lists
/// money and offer terms as seams that stay non-agentic:
///
/// > Money and legal transitions want determinism and an audit trail.
/// > […] Disclosure text is presented **verbatim**. No LLM in the path that
/// > generates offer terms.
///
/// A projected annual saving shown next to a vehicle price is exactly that kind of
/// claim. So there is no model in this path, the arithmetic is plain, and the
/// disclosure is a constant rather than generated prose. An agent may *narrate*
/// this number; nothing may invent it.
///
/// ## The assumptions are part of the output, not a footnote
///
/// `assumptions` is a first-class field and every surface that shows the figure must
/// show them. A savings number without its inputs is unfalsifiable — the customer
/// cannot tell whether it assumed 8,000 km/year or 30,000, home charging or
/// motorway rapids, and those choices move the answer by multiples. Surfacing them
/// is the same principle as `OfferRecommendationBasis.evidence`: the conclusion is
/// only honest if the reader can audit it.
///
/// ## ⚠️ Demo derivation — no tariff or telematics source is queried
///
/// `projection(for:)` derives everything locally from the odometer and a
/// deterministic seed. It does not read a charging history, an energy price feed, or
/// a utility tariff. `disclosure` says so, and a test asserts it — the same guard
/// `SupplyChainPlan` and `OfferRecommendationBasis` carry, for the same reason: this
/// portfolio has already shipped documentation citing a component that does not
/// exist.
///
/// Replacing `projection(for:)` with a real energy service leaves every view
/// unchanged. It is the single seam.
struct EnergySavingsProjection: Equatable {

    // MARK: - Inputs the customer can check

    /// Estimated annual distance in **miles**, from odometer over ownership.
    ///
    /// Miles, not kilometres, because that is what the source data means: the seed
    /// script's own comment reads "driven ~40k mi" and its `loyaltyBasis` says
    /// "39,840 mi". An earlier version of this type labelled the same number "km",
    /// which put a unit error inside a displayed money figure.
    ///
    /// The unit is authoritative from the telemetry contract, not inferred:
    /// `signal_catalog_seed.json` defines the `odometer` signal with `unit: "miles"`,
    /// and the CMS web UI renders `mi` off the same rows.
    ///
    /// `HomeTabView.odometerCell` disagreed with this (it rendered `"… km"`) until
    /// 2026-08-19; that is fixed, so the app is now consistent. The root cause was
    /// that `VehicleInfo.odometer` declared no unit — see its doc comment, which now
    /// does.
    let annualDistanceMiles: Int
    /// Share of charging done at home, as a percentage. The dominant lever on cost:
    /// home energy is typically a fraction of public rapid-charge pricing.
    let homeChargePercent: Int
    /// Current vehicle's observed consumption, kWh per 100 km.
    ///
    /// Consumption stays in kWh/100km because that is the standard EV efficiency
    /// unit; the mileage figure is converted internally. Mixing the two without
    /// converting is exactly the defect this replaced.
    let currentConsumptionKwh: Double
    /// Offered model's rated consumption, kWh per 100 km.
    let offeredConsumptionKwh: Double
    /// Assumed home tariff, currency-neutral value per kWh.
    let homeTariffPerKwh: Double
    /// Assumed public tariff per kWh.
    let publicTariffPerKwh: Double
    /// Currency symbol supplied by tenant config; never hardcoded to one market.
    let currencySymbol: String

    // MARK: - Derived

    /// Estimated annual saving in the tenant's currency. Whole units — presenting
    /// this to the cent would imply a precision the inputs do not support.
    let annualSaving: Int

    /// Efficiency improvement as a percentage, for a plain-language line.
    var efficiencyGainPercent: Int {
        guard currentConsumptionKwh > 0 else { return 0 }
        let gain = (currentConsumptionKwh - offeredConsumptionKwh) / currentConsumptionKwh
        return Int((gain * 100).rounded())
    }

    /// The inputs, in the customer's terms. Shown wherever `annualSaving` is shown.
    var assumptions: [String] {
        [
            "\(annualDistanceMiles.formatted()) miles a year, from your odometer",
            "\(homeChargePercent)% of charging at home, \(100 - homeChargePercent)% public",
            String(format: "%.1f vs %.1f kWh/100km — your current vehicle vs this model",
                   currentConsumptionKwh, offeredConsumptionKwh),
            String(format: "%@%.2f per kWh at home, %@%.2f public",
                   currencySymbol, homeTariffPerKwh, currencySymbol, publicTariffPerKwh)
        ]
    }

    /// Formatted headline, e.g. "£420 a year".
    var savingLabel: String {
        "\(currencySymbol)\(annualSaving.formatted()) a year"
    }

    /// Disclosure, presented **verbatim** wherever the figure appears.
    ///
    /// A constant, not generated text, and test-asserted. Per the doctrine, offer
    /// terms and their disclosures do not pass through a model. It states both that
    /// the figure is an estimate and that the inputs are illustrative, because either
    /// alone would overclaim.
    static let disclosure =
        "Estimated saving based on the assumptions shown. Actual running costs vary "
        + "with driving conditions, energy tariffs and charging habits. Figures in "
        + "this experience are illustrative demo data, not a quote."

    // MARK: - The single data-source seam

    /// Produces a projection, or `nil` when one should not be shown.
    ///
    /// Returns `nil` unless the **current** vehicle is electric. That is a scope
    /// choice, not an oversight: comparing an ICE vehicle's fuel cost against an EV's
    /// electricity cost is a much larger claim requiring fuel-price assumptions and a
    /// different disclosure, and getting it wrong in front of a customer is worse
    /// than staying quiet. An ICE-to-EV comparison deserves its own type.
    ///
    /// - Parameters:
    ///   - currentFuelType: the owned vehicle's `fuelType`.
    ///   - odometerMiles: the owned vehicle's odometer, in miles.
    ///   - yearsOwned: ownership duration, for the annual-distance estimate.
    ///   - offerId: determinism key.
    ///   - modelName: offered model, so consumption varies plausibly by body style.
    ///   - currencySymbol: from tenant config.
    /// Miles per kilometre, for converting the odometer into the unit the
    /// consumption figures use.
    private static let kmPerMile = 1.609_344

    static func projection(
        currentFuelType: String?,
        odometerMiles: Int?,
        yearsOwned: Int,
        offerId: String,
        modelName: String,
        currencySymbol: String
    ) -> EnergySavingsProjection? {
        guard isElectric(currentFuelType) else { return nil }
        guard let odometerMiles, odometerMiles > 0, yearsOwned > 0 else { return nil }

        // [DEMO DERIVATION — no tariff feed, no charging history]
        let seed = SupplyChainPlan.deterministicSeed(for: [offerId, modelName])

        let annualMiles = max(1_000, odometerMiles / yearsOwned)
        // Converted for the kWh/100km arithmetic; the customer-facing assumption
        // stays in miles.
        let annualKm = Double(annualMiles) * kmPerMile

        // Home-charge share, 55–85%. A behaviour signal, and the dominant cost lever.
        let homeShare = 55 + Int(seed % 31)

        // Current consumption 17–21 kWh/100km. The offered model improves on it, but
        // a larger body style improves less — a three-row SUV cannot match a compact.
        let current = 17.0 + Double(seed % 5)
        let improvement: Double
        let m = modelName.lowercased()
        if m.contains("windrose")       { improvement = 0.22 }   // compact
        else if m.contains("azimuth")   { improvement = 0.19 }   // sedan, aero
        else if m.contains("trailwind") { improvement = 0.14 }   // mid crossover
        else if m.contains("crestwind") { improvement = 0.09 }   // large three-row
        else                            { improvement = 0.12 }
        let offered = (current * (1 - improvement) * 10).rounded() / 10

        let homeTariff = 0.28
        let publicTariff = 0.79

        // Blended tariff by charging mix, then cost difference over the year.
        let blended = (Double(homeShare) / 100.0) * homeTariff
                    + (Double(100 - homeShare) / 100.0) * publicTariff
        let currentAnnual = annualKm / 100.0 * current * blended
        let offeredAnnual = annualKm / 100.0 * offered * blended
        let saving = Int((currentAnnual - offeredAnnual).rounded())

        // A non-positive saving is not a selling point — say nothing rather than
        // presenting a zero or negative "saving".
        guard saving > 0 else { return nil }

        return EnergySavingsProjection(
            annualDistanceMiles: annualMiles,
            homeChargePercent: homeShare,
            currentConsumptionKwh: current,
            offeredConsumptionKwh: offered,
            homeTariffPerKwh: homeTariff,
            publicTariffPerKwh: publicTariff,
            currencySymbol: currencySymbol,
            annualSaving: saving
        )
    }

    /// BEV detection, tolerant of casing and the synonyms present in seed data.
    ///
    /// Mirrors the existing private helpers in `HomeTabView` and `VehicleTabView`
    /// rather than introducing a fourth spelling of this check. Those two are
    /// duplicates of each other already; this is the shared one they should
    /// eventually call.
    static func isElectric(_ fuelType: String?) -> Bool {
        guard let ft = fuelType?.lowercased(), !ft.isEmpty else { return false }
        return ft == "bev" || ft == "electric" || ft == "ev"
    }
}
