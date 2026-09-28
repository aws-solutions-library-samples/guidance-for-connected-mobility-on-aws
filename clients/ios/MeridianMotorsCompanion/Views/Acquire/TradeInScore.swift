import Foundation

/// Composite condition score for a trade-in, derived from connected-vehicle
/// data the platform already holds.
///
/// This is the thing a used-price guide structurally cannot do. A book value
/// knows the model, the year and a self-reported odometer. A connected platform
/// knows how the vehicle was actually ridden and whether it was actually
/// serviced — so the valuation can be evidenced instead of asserted.
///
/// ## Honesty constraints baked in
///
/// - It is a **composite indicator**, not an industry standard. `disclaimer`
///   says so, and the UI must show it.
/// - It produces a **range**, never a single figure, and never the words
///   "approved" or "guaranteed" — matching the agent's trade-in rule so the
///   screen and the conversation cannot diverge.
/// - Every factor exposes its own `detail` string naming the evidence, so a
///   sceptical customer (or exec) can audit the number rather than trust it.
/// - Missing inputs are **omitted, not guessed**. A vehicle with no service
///   history scores on the factors we have and says so, rather than assuming
///   the worst or inventing records.
///
/// Pure value type with no I/O so the arithmetic is inspectable in isolation.
struct TradeInScore {

    // MARK: - Inputs

    /// Server-computed vehicle health (0-100) from `GET /vehicles/{id}/context`.
    /// Deliberately reused rather than recomputed: the CMS UI and this screen
    /// must never disagree on a number the customer can see in two places.
    let healthScore: Int?
    let odometerKm: Int?
    let totalTrips: Int?
    let ownedSince: Date?
    /// Completed scheduled services, used as the service-record signal.
    let completedScheduledServices: Int
    /// Completed unscheduled/repair visits — a mild negative signal.
    let completedRepairs: Int
    let now: Date

    // MARK: - Output

    struct Factor: Identifiable {
        var id: String { label }
        let label: String
        /// The evidence, in the customer's terms.
        let detail: String
        /// Contribution to the 0-100 composite. Negative is a deduction.
        let points: Int
    }

    enum Band: String {
        case excellent = "Excellent"
        case good = "Good"
        case fair = "Fair"
        case belowAverage = "Below average"

        var blurb: String {
            switch self {
            case .excellent:    return "Top of its class for age and use"
            case .good:         return "Better than typical for its age"
            case .fair:         return "Typical for its age and mileage"
            case .belowAverage: return "Below typical for its age and mileage"
            }
        }
    }

    /// Typical annual distance for a passenger car, used only to judge whether this
    /// vehicle is above or below the expected curve for its age. A reference point, not a
    /// claim about any individual driver.
    ///
    /// 18,000 km/yr. Was 8,000 — a commuter TWO-WHEELER figure, left over from before this
    /// was an automotive demo, and not a copy problem: it silently skewed the score.
    ///
    /// Measured against the demo vehicle (39,840 mi = 64,100 km over ~3.3 years, i.e.
    /// ~19,400 km/yr, which is unremarkable for a car): on the motorcycle baseline the
    /// ratio came out 2.43 and the mileage factor took the FULL -12 penalty; on this
    /// baseline the ratio is 1.08 and the factor is -1. An 11-point swing on a 0-100 score,
    /// and it made a normally-driven car look heavily over-used — the opposite of what a
    /// trade-in valuation should say about it.
    private static let expectedAnnualKm: Double = 18_000

    /// 0-100 composite. Starts from a neutral baseline so a vehicle with only
    /// partial data lands mid-range rather than being punished for silence.
    private static let baseline = 55

    var factors: [Factor] {
        var out: [Factor] = []

        if let health = healthScore {
            // Health is the heaviest single input: it is the only factor
            // computed server-side from live diagnostics rather than inferred.
            let pts = Int(((Double(health) - 70.0) / 30.0) * 15.0).clamped(to: -15...15)
            out.append(Factor(
                label: "Vehicle health",
                detail: "\(health)/100 — the same live diagnostic score shown on your home screen",
                points: pts))
        }

        if let km = odometerKm, let years = yearsOwned, years > 0.3 {
            let expected = Self.expectedAnnualKm * years
            let ratio = Double(km) / max(expected, 1)
            // Under the expected curve is good; well over it is not.
            let pts = Int((1.0 - ratio) * 20.0).clamped(to: -12...12)
            let pct = Int(((ratio - 1.0) * 100).rounded())
            let phrase = pct <= 0 ? "\(abs(pct))% below" : "\(pct)% above"
            out.append(Factor(
                label: "Distance for age",
                detail: "\(km.formattedWithSeparator) km over "
                      + String(format: "%.1f", years) + " years — \(phrase) typical",
                points: pts))
        }

        if let avg = averageTripKm {
            // Short, consistent urban trips are gentle on drivetrain and
            // brakes, though harder on cold-start wear — a modest positive.
            let pts = avg <= 25 ? 6 : (avg <= 45 ? 3 : 0)
            out.append(Factor(
                label: "Usage pattern",
                detail: String(format: "%.1f km average over %d trips", avg, totalTrips ?? 0),
                points: pts))
        }

        if completedScheduledServices > 0 {
            let pts = min(completedScheduledServices * 2, 10)
            out.append(Factor(
                label: "Service record",
                detail: "\(completedScheduledServices) scheduled services completed "
                      + "at authorised centres",
                points: pts))
        }

        if completedRepairs > 0 {
            let pts = -min(completedRepairs * 2, 14)
            out.append(Factor(
                label: "Repair history",
                detail: "\(completedRepairs) unscheduled repair "
                      + (completedRepairs == 1 ? "visit" : "visits"),
                points: pts))
        }

        return out
    }

    var score: Int {
        (Self.baseline + factors.reduce(0) { $0 + $1.points }).clamped(to: 0...100)
    }

    var band: Band {
        switch score {
        case 88...:   return .excellent
        case 74..<88: return .good
        case 60..<74: return .fair
        default:      return .belowAverage
        }
    }

    /// How far the composite moves the valuation away from the tenant's base
    /// figure, as a multiplier. Bounded hard: a condition score is evidence for
    /// a modest adjustment, not a licence to reprice the vehicle.
    var valuationMultiplier: Double {
        // The tenant's figure is the BEST CASE for this model, so condition
        // scales down from it and never above it. A score of 100 earns the full
        // figure; a poor score earns 80% of it. Bounded deliberately: a
        // condition indicator justifies a modest adjustment, not a repricing.
        (0.80 + (Double(score) / 100.0) * 0.20).clamped(to: 0.80...1.00)
    }

    /// Indicative range around the adjusted figure. A range, never a point
    /// estimate — the spread is the honesty.
    func indicativeRange(baseCredit: Int) -> (low: Int, high: Int) {
        let centre = Double(baseCredit) * valuationMultiplier
        return (Int((centre * 0.94) / 500) * 500, Int((centre * 1.06) / 500) * 500)
    }

    var disclaimer: String {
        "This is our own composite condition indicator built from your "
        + "vehicle's connected data — not an industry-standard valuation. "
        + "It is indicative only, not a firm offer, and the final figure "
        + "depends on physical inspection."
    }

    // MARK: - Derived helpers

    var yearsOwned: Double? {
        guard let since = ownedSince else { return nil }
        return now.timeIntervalSince(since) / (365.25 * 24 * 3600)
    }

    var averageTripKm: Double? {
        guard let km = odometerKm, let trips = totalTrips, trips > 0 else { return nil }
        return Double(km) / Double(trips)
    }
}

// MARK: - Small helpers

extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}

extension Int {
    /// Grouped with the locale's separator. Used for odometer readings, which
    /// look wrong unpunctuated at five digits.
    var formattedWithSeparator: String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: self)) ?? "\(self)"
    }
}
