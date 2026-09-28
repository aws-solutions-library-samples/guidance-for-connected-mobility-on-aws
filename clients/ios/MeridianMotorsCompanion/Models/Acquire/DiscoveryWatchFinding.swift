import Foundation

// MARK: - DiscoveryWatchFinding

/// A finding produced by the **Discovery Watch** agent — the thing that changed
/// about a saved configuration while the customer was not looking.
///
/// ## Tier classification
///
/// This is the **Tier 2 → Tier 1 interface**, per `~/.kiro/steering/agentic-tiers.md`.
///
/// - **Tier 2** (not built yet — see "What is and is not built"): a scheduled agent
///   owns the goal "tell this customer when the thing they wanted becomes
///   gettable". Nobody is waiting. It reasons across inventory, lead times and
///   incentives, and writes findings.
/// - **Tier 1** (this file's consumers): reads a finding in single-digit
///   milliseconds and narrates it. It computes nothing.
///
/// The whole reason this is worth building as Tier 2 rather than as another tool is
/// the doctrine's own test — *who is waiting?* Nobody. A conversational agent
/// cannot answer "what changed since Tuesday" because it only exists while someone
/// is talking to it. That is the capability, and it is the one thing in the buy flow
/// a chatbot structurally cannot do.
///
/// ## Contract alignment — deliberately not a new shape
///
/// Field names mirror the **deployed** Tier 2 health agent in the CVX repo
/// (`agents/health/`, artifact table `vsa-staging-health-findings`, read by
/// `agents/supervisor/tools/health_findings.py`): `computed_at`, `confidence`,
/// `evidence`, `inputs_hash`. Two conventions are carried over rather than
/// reinvented:
///
/// - **Idempotency is content-addressed.** `inputsHash` is computed over the inputs
///   that produced the finding; the writer guards on
///   `attribute_not_exists(inputs_hash)`. Re-running the agent on unchanged inputs
///   writes nothing, so the customer is not told the same news twice.
/// - **Newest-per-category by `max(computedAt)`.** Selection happens in the reader,
///   which also returns `computedAt` so staleness is surfaced rather than hidden.
///
/// Diverging would mean two artifact shapes in one portfolio for no reason, and the
/// second one would not benefit from the four defects the first one's live-data pass
/// already found.
///
/// ## Pull-only, deliberately
///
/// Nothing here pushes. The customer sees findings when they open the app.
///
/// That is not a limitation, it is the doctrine's sequencing: the health agent
/// shipped pull-only first (its M13) so reasoning quality could be validated before
/// any proactive delivery, and `agentic-tiers.md` § "Delivery & Surfacing" is
/// explicit that the channel decision — quiet hours, consent, frequency — belongs to
/// a broker and *never* to the agent. Building push here would mean building that
/// broker, and shipping an agent that can wake a customer's phone before its
/// judgement has been reviewed is the wrong order.
///
/// ## ⚠️ What is and is not built
///
/// **Built:** this contract, the reader, and the narration.
/// **Not built:** the scheduled agent that would produce these. `findings(for:)`
/// returns a locally-derived fixture.
///
/// `attributionNote` says so and a test enforces it. This matters more than usual
/// here: an artifact type whose whole selling point is "an agent did this overnight"
/// is the single easiest place in the codebase to accidentally claim a capability
/// that does not exist — which this portfolio has already done once, in an
/// Implementation Guide callout citing a `lambdas/proactive/` directory that does
/// not exist.
///
/// Replacing `findings(for:)` with a real read leaves every view unchanged.
struct DiscoveryWatchFinding: Equatable, Identifiable {

    /// What kind of change was detected.
    ///
    /// Each case is something that makes a previously-blocked or worse option
    /// *gettable* — which is the only class of news worth interrupting someone for.
    enum Category: String, CaseIterable {
        /// An option the customer wanted, which was unavailable, has opened up.
        case optionOpened      = "Option now available"
        /// The build/delivery window improved.
        case leadTimeImproved  = "Delivery moved earlier"
        /// Pricing or incentive on the saved configuration changed favourably.
        case incentiveChanged  = "Better terms available"
        /// A different model now fits the observed usage better.
        case betterFitFound    = "Closer match found"

        var symbolName: String {
            switch self {
            case .optionOpened:     return "paintpalette"
            case .leadTimeImproved: return "calendar.badge.checkmark"
            case .incentiveChanged: return "tag"
            case .betterFitFound:   return "sparkle.magnifyingglass"
            }
        }
    }

    // MARK: - Artifact contract

    var id: String { inputsHash }

    let category: Category
    /// The finding, in the customer's terms. One sentence.
    let finding: String
    /// The inputs and intermediate observations that produced it.
    ///
    /// Not optional. Per the doctrine: "Evidence is what makes narration honest.
    /// Without it the Tier 1 agent is asserting a conclusion it cannot support, and
    /// nobody can audit why the system said what it said."
    let evidence: [String]
    /// When the reasoning ran. Surfaced to the customer, never hidden.
    let computedAt: Date
    let confidence: Double
    /// Which reasoning produced this, so a finding can be attributed to a version.
    let agentVersion: String
    /// Content address of the inputs. The idempotency key — a re-run over unchanged
    /// inputs produces the same hash and is not written again.
    let inputsHash: String

    /// Shown wherever findings are shown. Test-asserted.
    static let attributionNote =
        "Discovery Watch reviews your saved build between visits. "
        + "Findings in this experience are illustrative demo data."

    // MARK: - Display

    /// "as of 2 days ago" — relative, so staleness is legible without arithmetic.
    func freshnessLabel(now: Date = Date()) -> String {
        let fmt = RelativeDateTimeFormatter()
        fmt.unitsStyle = .full
        return "Checked \(fmt.localizedString(for: computedAt, relativeTo: now))"
    }

    /// True when the finding is old enough that presenting it as current would
    /// mislead.
    ///
    /// A threshold rather than a hard filter: a stale finding is still shown, marked
    /// stale. Hiding it would be worse — the customer would see nothing and conclude
    /// nothing changed, when in fact nothing was *checked*. The deployed health
    /// agent's failure mode is the same shape: both its CloudWatch alarms shipped
    /// with empty `AlarmActions`, so a silent nightly failure yields an artifact
    /// Tier 1 keeps narrating with a receding `computed_at`.
    func isStale(now: Date = Date(), thresholdDays: Int = 7) -> Bool {
        guard let cutoff = Calendar.current.date(
            byAdding: .day, value: -thresholdDays, to: now) else { return false }
        return computedAt < cutoff
    }

    // MARK: - The single data-source seam

    /// Returns findings for a saved configuration, newest first.
    ///
    /// **Replace this body with a read of the findings table when the Tier 2 agent
    /// exists.** Selection must stay newest-per-category by `computedAt`, matching
    /// `health_findings.py`.
    ///
    /// - Parameters:
    ///   - savedModelName: the model the customer configured.
    ///   - savedColorId: the colour they chose, so an availability finding can name it.
    ///   - now: injectable clock.
    static func findings(
        savedModelName: String,
        savedColorId: String?,
        now: Date = Date()
    ) -> [DiscoveryWatchFinding] {
        // [DEMO DERIVATION — no scheduled agent runs; nothing is queried]
        let seed = SupplyChainPlan.deterministicSeed(
            for: [savedModelName, savedColorId ?? "any"])

        let handle = ["Crestwind", "Trailwind", "Windrose", "Azimuth"]
            .first { savedModelName.lowercased().contains($0.lowercased()) } ?? savedModelName

        var out: [DiscoveryWatchFinding] = []

        /// Builds one finding, hashing its own inputs so `inputsHash` is a genuine
        /// content address rather than a random id — two identical findings collide
        /// deliberately, which is what makes the write idempotent.
        func make(_ category: Category, _ finding: String,
                  _ evidence: [String], _ confidence: Double, daysAgo: Int) {
            let hash = SupplyChainPlan.deterministicSeed(
                for: [category.rawValue, finding] + evidence)
            let computed = Calendar.current.date(byAdding: .day, value: -daysAgo, to: now) ?? now
            out.append(DiscoveryWatchFinding(
                category: category,
                finding: finding,
                evidence: evidence,
                computedAt: computed,
                confidence: confidence,
                agentVersion: Self.demoAgentVersion,
                inputsHash: String(format: "%016llx", hash)))
        }

        // Lead time improving is the most universally relevant news, so it is always
        // present — a customer who saved a build cares about when they get it.
        make(.leadTimeImproved,
             "Your \(handle) build slot moved about three weeks earlier",
             ["Production schedule for your trim opened additional capacity",
              "No change to the options you selected",
              "Earlier slot holds while the configuration is unchanged"],
             0.88, daysAgo: Int(seed % 3))

        if (seed / 7) % 2 == 0 {
            let colourPhrase = savedColorId.map { "the \($0.replacingOccurrences(of: "-", with: " ")) finish" }
                ?? "the finish you wanted"
            make(.optionOpened,
                 "\(colourPhrase.capitalizedFirst) is available again on the \(handle)",
                 ["Previously unavailable in the standard delivery window",
                  "Supplier confirmed allocation for this production run",
                  "Adds no time to your current build slot"],
                 0.81, daysAgo: Int((seed / 11) % 5))
        }

        if (seed / 13) % 3 == 0 {
            make(.incentiveChanged,
                 "Finance terms on the \(handle) improved this month",
                 ["Rate on the term you were quoted has come down",
                  "Your trade-in valuation is unchanged",
                  "Applies to the configuration you saved"],
                 0.76, daysAgo: Int((seed / 17) % 6))
        }

        // Newest first, matching the reader convention in health_findings.py.
        return out.sorted { $0.computedAt > $1.computedAt }
    }

    /// Version string for the demo derivation.
    ///
    /// Deliberately says `demo` rather than a plausible semver: a version that looks
    /// real invites the reader to believe a real agent produced the finding.
    static let demoAgentVersion = "discovery-watch-demo"
}

private extension String {
    /// Capitalises only the first character, leaving the rest — `capitalized`
    /// would title-case a model name mid-sentence.
    var capitalizedFirst: String {
        guard let f = first else { return self }
        return String(f).uppercased() + dropFirst()
    }
}
