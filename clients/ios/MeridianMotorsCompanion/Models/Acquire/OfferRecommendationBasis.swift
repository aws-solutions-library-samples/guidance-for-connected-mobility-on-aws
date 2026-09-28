import Foundation

// MARK: - OfferRecommendationBasis

/// The evidence behind an upgrade recommendation — *why* this vehicle, for this
/// customer, now.
///
/// ## Why this exists as a structured type and not a sentence
///
/// `AcquireConfig.UpgradeOffer.rationale` is already a free-text line ("suits your
/// weekend towing"), and it is not enough. A single assertion gives the customer no
/// way to judge whether the recommendation is reasoned or merely marketed, and gives
/// an OEM audience nothing to inspect. Per `~/.kiro/steering/agentic-tiers.md`
/// § "The Tier 2 artifact contract":
///
/// > **Evidence is what makes narration honest.** Without it the Tier 1 agent is
/// > asserting a conclusion it cannot support, and nobody can audit why the system
/// > said what it said.
///
/// So the recommendation carries its inputs: which customer touchpoints were
/// scanned, what each one contributed, and when the reasoning ran.
///
/// ## Tier classification
///
/// Scanning touchpoints across web, app, service and telemetry and deciding what to
/// recommend is **Tier 2** work — multi-step reasoning over substantial data with
/// nobody waiting. Rendering that conclusion in a banner is **Tier 1**: it reads a
/// pre-computed artifact and narrates it. This type is the interface between them,
/// which is exactly the seam the doctrine describes.
///
/// The fields mirror the doctrine's artifact contract — `computedAt`, `confidence`,
/// `evidence[]` — deliberately, so that when a real Tier 2 recommender is built its
/// output maps onto this shape rather than requiring a new one.
///
/// ## ⚠️ NO TOUCHPOINT SCAN HAPPENS — this is derived locally
///
/// Nothing here queries a CDP, a web-analytics source, a dealer CRM, or the vehicle.
/// `basis(for:)` derives a plausible evidence set from the offer itself. That is the
/// same discipline `SupplyChainPlan` follows, and for the same reason: this portfolio
/// has already shipped an Implementation Guide callout citing a `lambdas/proactive/`
/// directory that does not exist, and an overclaim in a demo becomes an overclaim in
/// a customer conversation.
///
/// Two consequences are load-bearing:
///
/// 1. **`attributionNote` is shown wherever evidence is shown**, and it is
///    test-asserted so it cannot drift into implying a live scan.
/// 2. **`basis(for:)` is the single seam.** Replacing its body with a call to a real
///    recommender leaves every view unchanged.
///
/// ## Why the evidence is deterministic
///
/// Same reasoning as `SupplyChainPlan`: a presenter must be able to re-run the demo
/// and get the same explanation. `SupplyChainPlan.deterministicSeed(for:)` is reused
/// rather than reimplemented — Swift's `hashValue` is randomly seeded per process and
/// would differ between launches.
struct OfferRecommendationBasis: Equatable {

    // MARK: - Touchpoint

    /// Where a piece of evidence came from.
    ///
    /// Named for the customer-facing surface rather than the backing system, because
    /// this vocabulary is shown to the customer. An OEM reader maps these onto their
    /// own systems (CDP, DMS, telematics) without us asserting which they use.
    enum Touchpoint: String, CaseIterable, Identifiable {
        case webBrowsing   = "Website activity"
        case appActivity   = "App activity"
        case serviceVisit  = "Service history"
        case vehicleData   = "Vehicle data"
        case ownership     = "Ownership milestone"

        var id: String { rawValue }

        var symbolName: String {
            switch self {
            case .webBrowsing:  return "safari"
            case .appActivity:  return "iphone"
            case .serviceVisit: return "wrench.and.screwdriver"
            case .vehicleData:  return "gauge.with.dots.needle.33percent"
            case .ownership:    return "calendar.badge.clock"
            }
        }
    }

    // MARK: - Evidence

    /// One observation that contributed to the recommendation.
    struct Evidence: Equatable, Identifiable {
        var id: String { "\(touchpoint.rawValue)#\(detail)" }
        let touchpoint: Touchpoint
        /// What was observed, in the customer's own terms.
        let detail: String
        /// Why it supports this recommendation. Kept separate from `detail` so the
        /// observation and the inference are never conflated — the customer can
        /// agree with the fact and still reject the conclusion.
        let inference: String
    }

    // MARK: - Contract fields

    let evidence: [Evidence]
    /// When the reasoning ran. Surfaced so a stale basis is visible rather than
    /// presented as live — the doctrine's "staleness is surfaced, not hidden".
    let computedAt: Date
    /// How strongly the evidence supports the recommendation, 0…1.
    let confidence: Double

    /// Attribution shown wherever evidence is shown.
    ///
    /// Test-asserted. Credits the *pattern* without claiming a live scan happened.
    static let attributionNote =
        "Recommendation reasoning is illustrative demo data for this experience. "
        + "No customer data was analysed."

    // MARK: - Display helpers

    var confidenceLabel: String {
        switch confidence {
        case 0.85...:     return "High confidence"
        case 0.6..<0.85:  return "Moderate confidence"
        default:          return "Low confidence"
        }
    }

    /// "as of 3 days ago" — relative so it reads naturally and so staleness is
    /// obvious without the reader doing date arithmetic.
    func freshnessLabel(now: Date = Date()) -> String {
        let fmt = RelativeDateTimeFormatter()
        fmt.unitsStyle = .full
        return "Reviewed \(fmt.localizedString(for: computedAt, relativeTo: now))"
    }

    /// Distinct touchpoints scanned, for a compact summary line.
    var touchpointSummary: String {
        let names = evidence.map(\.touchpoint.rawValue)
        var seen: [String] = []
        for n in names where !seen.contains(n) { seen.append(n) }
        return seen.joined(separator: " · ")
    }

    // MARK: - The single data-source seam

    /// Produces the basis for an offer.
    ///
    /// **Replace this body with a call to a real recommender when one exists.** Every
    /// view reads `OfferRecommendationBasis` and is unaffected by where it came from.
    ///
    /// - Parameters:
    ///   - offerId: stable offer identifier, used as the determinism key.
    ///   - modelName: marketing model name, so the narrative can name the vehicle.
    ///   - rationale: the offer's existing free-text rationale, folded in as evidence
    ///     when present rather than discarded — it is the tenant's own reasoning.
    ///   - now: injectable clock so tests are not date-dependent.
    static func basis(
        offerId: String,
        modelName: String,
        rationale: String? = nil,
        ownedModelName: String? = nil,
        now: Date = Date()
    ) -> OfferRecommendationBasis {
        // [DEMO DERIVATION — NO TOUCHPOINT SCAN OCCURS]
        let seed = SupplyChainPlan.deterministicSeed(for: [offerId, modelName])

        // Short model handle, e.g. "Meridian Crestwind Signature" -> "Crestwind".
        let handle = ["Crestwind", "Trailwind", "Windrose", "Azimuth"]
            .first { modelName.lowercased().contains($0.lowercased()) } ?? modelName

        var items: [Evidence] = []

        // The touchpoint the user specifically called out: browsing seen off-app.
        // Always present, because it is the clearest demonstration that the
        // recommendation spans surfaces rather than living inside this app.
        items.append(Evidence(
            touchpoint: .webBrowsing,
            detail: "Viewed the \(handle) three times on the website in the last month",
            inference: "Sustained interest in this model specifically, not general browsing"
        ))

        // Ownership milestone — varies deterministically so the demo is not identical
        // across models.
        let yearsOwned = 3 + Int(seed % 3)
        items.append(Evidence(
            touchpoint: .ownership,
            detail: "\(yearsOwned) years into ownership, approaching typical replacement",
            inference: "Timing aligns with when owners in this segment usually change vehicle"
        ))

        if (seed / 5) % 2 == 0 {
            items.append(Evidence(
                touchpoint: .vehicleData,
                detail: "Average trip length up 40% over the last six months",
                inference: "Usage has outgrown the current vehicle's range profile"
            ))
        } else {
            items.append(Evidence(
                touchpoint: .vehicleData,
                detail: "Regularly carrying four or more occupants",
                inference: "A larger cabin would fit how the vehicle is actually used"
            ))
        }

        items.append(Evidence(
            touchpoint: .serviceVisit,
            detail: "Two unscheduled service visits in the past year",
            inference: "Rising maintenance is a common trigger for considering a change"
        ))

        // Same-nameplate continuity — the strongest single signal available, and the
        // reason it is weighted rather than merely listed.
        //
        // An owner's most likely next vehicle is a newer version of the one they
        // already have: the driving position, the footprint and the software are all
        // already familiar, so the switching cost is near zero. Ranking on this is a
        // real heuristic, not a thumb on the scale — before it, `bestMatch` separated
        // candidates only by evidence count plus a hash-derived spread, which is
        // arbitrary dressed as a score.
        let isSameNameplate = ownedModelName.map { owned in
            let ownedTokens = owned.lowercased()
                .split(whereSeparator: { !$0.isLetter })
                .filter { $0.count > 3 }
            let offered = modelName.lowercased()
            return ownedTokens.contains { offered.contains($0) }
        } ?? false

        if isSameNameplate, let owned = ownedModelName {
            items.append(Evidence(
                touchpoint: .ownership,
                detail: "You already drive a \(owned)",
                inference: "Same driving position, footprint and software — nothing to relearn"
            ))
        }

        // The tenant's own rationale is evidence, not decoration — folding it in
        // keeps a single explanation rather than two competing ones on screen.
        if let rationale, !rationale.trimmingCharacters(in: .whitespaces).isEmpty {
            items.append(Evidence(
                touchpoint: .appActivity,
                detail: rationale,
                inference: "Matches the preferences recorded in your profile"
            ))
        }

        // Confidence rises with corroborating evidence, plus a small per-offer term
        // so two offers with the same evidence count are still separable. Banded on
        // display so it never reads as spuriously precise.
        //
        // The per-offer term is what makes `bestMatch(among:)` able to choose. A real
        // Tier 2 recommender would produce a genuine ranking here; this stands in for
        // it deterministically rather than picking arbitrarily.
        let spread = Double((seed / 3) % 7) * 0.01
        // Same-nameplate outranks the hash spread by an order of magnitude, so it
        // decides the pick rather than being outvoted by noise.
        let nameplateBonus = isSameNameplate ? 0.12 : 0.0
        let confidence = min(0.94, 0.55 + Double(items.count) * 0.06 + spread + nameplateBonus)

        let daysAgo = Int((seed / 11) % 5)
        let computed = Calendar.current.date(byAdding: .day, value: -daysAgo, to: now) ?? now

        return OfferRecommendationBasis(
            evidence: items,
            computedAt: computed,
            confidence: confidence
        )
    }

    /// Picks the single best-matching offer.
    ///
    /// ## Why one, not a list
    ///
    /// A list of offers is a catalogue: it puts the choosing back on the customer and
    /// demonstrates nothing about reasoning. One recommendation, with its evidence
    /// attached, is what makes the system look like it formed a view — which is the
    /// whole claim. Showing several also means showing several *vehicles*, so the
    /// surface stops being "the car for you" and becomes a brochure.
    ///
    /// Selection is by `confidence`, tie-broken by `offerId` so it is stable across
    /// launches. A real Tier 2 recommender owns this ranking; this reproduces the
    /// *shape* of that decision — score every candidate, surface the winner, keep the
    /// evidence — so swapping it in changes the score's provenance and nothing else.
    ///
    /// Returns `nil` for an empty list rather than forcing a caller to handle a
    /// sentinel.
    static func bestMatch(
        among offers: [AcquireConfig.UpgradeOffer],
        ownedModelName: String? = nil,
        now: Date = Date()
    ) -> (offer: AcquireConfig.UpgradeOffer, basis: OfferRecommendationBasis)? {
        let scored = offers.map { offer in
            (offer: offer,
             basis: basis(offerId: offer.id,
                          modelName: offer.modelName,
                          rationale: offer.rationale,
                          ownedModelName: ownedModelName,
                          now: now))
        }
        return scored.max { a, b in
            if a.basis.confidence != b.basis.confidence {
                return a.basis.confidence < b.basis.confidence
            }
            // Deterministic tie-break — never leave the pick to array order, which
            // depends on how the tenant happened to seed the config.
            return a.offer.id > b.offer.id
        }
    }
}
