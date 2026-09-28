import SwiftUI

// MARK: - HealthDeductionRemedy

/// Maps a server-emitted health-score deduction to what recovers it.
///
/// ## Why this is a lookup and not advice
///
/// The deduction `reason` strings are a **stable, closed vocabulary** produced by
/// `api-vehicle-context`'s `_compute_health_score`: `DTC <CODE> CRITICAL|HIGH|
/// MEDIUM|LOW`, `Scheduled service overdue`, and `Vehicle disconnected`. Mapping
/// each to the action that clears it is therefore a table, not a judgement — the
/// server has already decided both what is wrong and what it costs.
///
/// That distinction matters per `~/.kiro/steering/agentic-tiers.md`. The score is a
/// deterministic computation and this view narrates it verbatim; deciding *which
/// fix is worth doing first, for this owner, this week* is judgement and belongs to
/// the agent. So this type answers "what would recover these 21 points" and the
/// assistant answers "what should I actually do".
///
/// If an unrecognised reason appears — the server gains a new deduction type — it is
/// still displayed with its amount, just without a remedy. Showing the deduction and
/// admitting we have no mapping is honest; hiding it would make the arithmetic stop
/// adding up on screen.
struct HealthDeductionRemedy: Equatable {
    /// What the owner can do about it.
    let action: String
    /// Whether the owner can resolve it themselves.
    let isSelfServe: Bool
    let symbolName: String

    /// Resolves a remedy for a deduction reason, or `nil` when unrecognised.
    ///
    /// Matching is prefix/keyword based rather than exact-equality because the DTC
    /// reasons embed a variable code (`DTC P0420 MEDIUM`). Severity is read from the
    /// reason rather than re-derived, so this cannot disagree with the server about
    /// how serious a fault is.
    static func remedy(for reason: String) -> HealthDeductionRemedy? {
        let r = reason.lowercased()

        if r.contains("scheduled service overdue") {
            return HealthDeductionRemedy(
                action: "Book the overdue service — this is the single largest "
                    + "non-fault deduction and it clears completely once the visit is done.",
                isSelfServe: true,
                symbolName: "calendar.badge.exclamationmark")
        }
        if r.contains("vehicle disconnected") {
            return HealthDeductionRemedy(
                action: "Reconnect the vehicle. Health is scored on what we can see, "
                    + "so a disconnected vehicle is penalised for missing data rather "
                    + "than for a fault.",
                isSelfServe: true,
                symbolName: "antenna.radiowaves.left.and.right.slash")
        }
        if r.hasPrefix("dtc") {
            if r.contains("critical") {
                return HealthDeductionRemedy(
                    action: "Have this fault diagnosed before further driving. "
                        + "Critical faults carry the heaviest deduction.",
                    isSelfServe: false,
                    symbolName: "exclamationmark.triangle.fill")
            }
            if r.contains("high") {
                return HealthDeductionRemedy(
                    action: "Book a diagnostic visit. This clears once the fault is "
                        + "resolved and the code stops reporting.",
                    isSelfServe: false,
                    symbolName: "exclamationmark.triangle")
            }
            return HealthDeductionRemedy(
                action: "Mention this code at your next service. It can often be "
                    + "resolved during routine maintenance.",
                isSelfServe: false,
                symbolName: "wrench.and.screwdriver")
        }
        return nil
    }

    /// Points recoverable by owner action alone, across a set of deductions.
    ///
    /// Deliberately counts only `isSelfServe` items. A total that included
    /// fault-clearing would promise the owner points they cannot recover without a
    /// workshop, which is the kind of number that erodes trust the moment it does
    /// not happen.
    static func selfServeRecoverablePoints(
        in deductions: [HealthScoreBreakdown.Deduction]
    ) -> Int {
        deductions.reduce(0) { total, d in
            guard let r = remedy(for: d.reason), r.isSelfServe else { return total }
            return total + d.amount
        }
    }
}

// MARK: - VehicleHealthDetailView

/// Explains the vehicle health score: what it is, what reduced it, and what to do.
///
/// ## The score is narrated, never recomputed
///
/// `healthScore` and `healthScoreBreakdown` both come from `api-vehicle-context`.
/// `VehicleContextResponse.healthScore`'s own doc comment calls it the "single source
/// of truth — both iOS Home tab and the CMS UI Vehicle Detail page render this value
/// verbatim", and the Home card's comment records that iOS deliberately stopped
/// recomputing it so the two surfaces "can never disagree on the number".
///
/// This view holds that line. It renders the server's score, the server's deductions
/// and the server's `reason` strings verbatim. The only arithmetic it performs is
/// displaying `100 − Σ amounts` so the number is inspectable — and if that does not
/// equal the server's score it says so rather than quietly showing its own total.
/// A client that silently "corrects" an authoritative score is the bug the model
/// comment was written to prevent.
///
/// The `healthScoreBreakdown` field has existed unused on iOS since 2026-05-19, with
/// a comment predicting this screen: *"iOS keeps the field on the model in case the
/// Home tab grows a 'why?' affordance later."*
struct VehicleHealthDetailView: View {
    let score: Int
    let breakdown: HealthScoreBreakdown?
    let theme: TenantTheme
    /// Opens the assistant with a primed prompt. The judgement half.
    var onAskAssistant: ((String) -> Void)?
    let onDone: () -> Void

    @Environment(\.adaptiveLayoutContext) private var layoutContext

    private var deductions: [HealthScoreBreakdown.Deduction] {
        breakdown?.deductions ?? []
    }

    /// Sum of the server's own deduction amounts.
    private var deductionTotal: Int {
        deductions.reduce(0) { $0 + $1.amount }
    }

    /// True when the server's score and its own deductions disagree.
    ///
    /// Surfaced rather than hidden. A mismatch means either a deduction type the
    /// breakdown omits or a scoring change that did not reach the breakdown — both
    /// worth knowing, and neither something a client should paper over.
    private var arithmeticDisagrees: Bool {
        breakdown != nil && (100 - deductionTotal) != score
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: layoutContext.isCompact ? 14 : 18) {
                    scoreHeader
                    if deductions.isEmpty {
                        noDeductionsCard
                    } else {
                        arithmeticCard
                        ForEach(deductions) { deductionCard($0) }
                        recoverableSummary
                    }
                    askAgentCard
                    provenanceFooter
                }
                .padding(layoutContext.isCompact ? 16 : 24)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Vehicle health")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done", action: onDone)
                }
            }
        }
    }

    // MARK: - Header

    private var scoreHeader: some View {
        HStack(alignment: .center, spacing: 16) {
            ZStack {
                Circle()
                    .stroke(Color(.tertiarySystemGroupedBackground), lineWidth: 9)
                Circle()
                    .trim(from: 0, to: Double(score) / 100.0)
                    .stroke(healthColor, style: StrokeStyle(lineWidth: 9, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Text("\(score)")
                    .font(.title2.bold())
                    .foregroundStyle(healthColor)
            }
            .frame(width: 78, height: 78)
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(healthLabel)
                    .font(.headline)
                    .foregroundStyle(healthColor)
                Text(deductions.isEmpty
                     ? "Nothing is currently reducing your score."
                     : "\(deductions.count) item\(deductions.count == 1 ? "" : "s") reduced this score by \(deductionTotal) points.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Vehicle health \(score) out of 100, \(healthLabel). "
            + (deductions.isEmpty ? "Nothing is reducing it."
               : "Reduced by \(deductionTotal) points across \(deductions.count) items."))
    }

    // MARK: - Arithmetic

    /// Shows 100 → deductions → score, so the number is checkable rather than asserted.
    private var arithmeticCard: some View {
        card {
            Text("How the score is calculated")
                .font(.subheadline.bold())
            row(label: "Starting score", value: "100")
            ForEach(deductions) { d in
                row(label: d.reason, value: "− \(d.amount)", muted: true)
            }
            Divider()
            row(label: "Your score", value: "\(score)", emphasised: true)

            if arithmeticDisagrees {
                // Do not silently reconcile — see `arithmeticDisagrees`.
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.caption2)
                        .foregroundStyle(Color(.systemOrange))
                    Text("These items total \(deductionTotal) points, which does not "
                         + "match the score above. The score shown is the one reported "
                         + "for your vehicle.")
                        .font(.caption2)
                        .foregroundStyle(Color(.systemOrange))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 2)
            }
        }
    }

    // MARK: - Per-deduction

    @ViewBuilder
    private func deductionCard(_ d: HealthScoreBreakdown.Deduction) -> some View {
        let remedy = HealthDeductionRemedy.remedy(for: d.reason)
        card {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: remedy?.symbolName ?? "minus.circle")
                    .foregroundStyle(theme.primary)
                    .frame(width: 20)
                VStack(alignment: .leading, spacing: 3) {
                    // Reason rendered VERBATIM — it is a stable server string and the
                    // CMS UI shows the same text off the same field.
                    Text(d.reason)
                        .font(.subheadline.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                    Text("−\(d.amount) points")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                Spacer(minLength: 0)
                if let remedy {
                    Text(remedy.isSelfServe ? "You can fix" : "Needs service")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(
                            Capsule().fill(remedy.isSelfServe
                                           ? theme.primary.opacity(0.15)
                                           : Color(.systemOrange).opacity(0.15)))
                        .foregroundStyle(remedy.isSelfServe
                                         ? theme.primary : Color(.systemOrange))
                }
            }

            if let remedy {
                Text(remedy.action)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                // Unrecognised reason: shown with its amount, no invented remedy.
                Text("This is counted in your score. We don't have specific guidance "
                     + "for it yet — the assistant can look into it.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(d.reason), minus \(d.amount) points. "
            + (remedy.map { ($0.isSelfServe ? "You can fix this. " : "Needs a service visit. ") + $0.action }
               ?? "No specific guidance available."))
    }

    // MARK: - Recoverable

    @ViewBuilder
    private var recoverableSummary: some View {
        let recoverable = HealthDeductionRemedy.selfServeRecoverablePoints(in: deductions)
        if recoverable > 0 {
            card {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.up.circle.fill")
                        .foregroundStyle(theme.primary)
                    Text("\(recoverable) points you can recover yourself")
                        .font(.subheadline.weight(.semibold))
                    Spacer(minLength: 0)
                }
                // Only self-serve points are promised. Counting fault-clearing here
                // would offer the owner points they cannot recover without a workshop.
                Text("Would take your score to \(min(100, score + recoverable)). "
                     + "Items needing a service visit are not included in this figure.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var noDeductionsCard: some View {
        card {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.seal.fill").foregroundStyle(theme.primary)
                Text("No deductions reported")
                    .font(.subheadline.weight(.semibold))
                Spacer(minLength: 0)
            }
            Text("Your vehicle has no open faults, no overdue service and is "
                 + "reporting normally.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - The judgement half

    /// Hands prioritisation to the assistant rather than inventing it here.
    ///
    /// The table above answers "what would recover these points". Which fix is worth
    /// doing first — given cost, how the owner drives, and what is already scheduled —
    /// is judgement, and per the tier doctrine judgement belongs to the agent. The
    /// prompt is seeded with the score and the verbatim reasons so the agent starts
    /// from the same facts the owner is looking at.
    @ViewBuilder
    private var askAgentCard: some View {
        if let onAskAssistant {
            Button {
                onAskAssistant(primedPrompt)
            } label: {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "sparkles")
                        .foregroundStyle(theme.primary)
                        .frame(width: 20)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Ask about raising this score")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Color(.label))
                        // Does not promise booking. The seed deliberately asks the
                        // agent NOT to book — promising it here and then asking the
                        // agent not to do it is how the two drift apart. Booking stays
                        // available by simply asking in the conversation.
                        Text("Get these prioritised in plain language, using what's "
                             + "already on this screen.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(theme.primary)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 12)
                    .fill(theme.primary.opacity(0.08)))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Ask the assistant about raising your health score")
        }
    }

    /// Seeds the agent with the score and the verbatim reasons.
    ///
    /// ## Why this prompt is narrow, and deliberately asks for no lookups
    ///
    /// The first version ended "…what would each one actually take? Book anything
    /// that needs a visit." That provoked five sequential tool calls — `triage`,
    /// `retrieve` ×3, `find_service_center` — and **broke the voice session**:
    /// `triage` alone returned in 2385 ms, Nova Sonic went silent after the last tool
    /// result, and the app's own watchdog marked the session unresponsive
    /// (`nova-silence-watchdog fired reason=after-tool-result-find_service_center`,
    /// observed 2026-08-19).
    ///
    /// That is the constraint `~/.kiro/steering/agentic-tiers.md` documents rather
    /// than a defect to patch: Nova's bidi loop assumes tool calls return inside
    /// **~1 second**, so "no meaningful reasoning can happen anywhere inside a
    /// conversational turn". Asking a voice agent to research several faults and then
    /// book is a Tier 2 workload wearing a Tier 1 costume.
    ///
    /// So the seed now carries **all the facts the answer needs** — score, verbatim
    /// reasons, amounts — and asks only for prioritisation and plain-language
    /// framing. Nothing here requires a lookup, because the screen already knows
    /// everything: the deductions come from the server and the remedies from
    /// `HealthDeductionRemedy`. The agent narrates; it does not compute.
    ///
    /// **This is a prompt-level boundary, and the doctrine is explicit that "a
    /// boundary that exists only in a prompt is a boundary that will be crossed."**
    /// It reduces the tool cascade rather than preventing it — the agent may still
    /// choose to call something. The durable fix is a tool-scoped or text-path entry
    /// point for this surface; see the note in `askAgentCard`.
    var primedPrompt: String {
        var s = "Here is my vehicle health summary — please use only what I give you"
            + " and do not look anything up.\n"
        s += "Score: \(score) out of 100."
        if deductions.isEmpty {
            s += " Nothing is currently reducing it."
        } else {
            s += " What reduced it:\n"
            for d in deductions {
                let owner = HealthDeductionRemedy.remedy(for: d.reason)?
                    .isSelfServe == true ? "I can fix this myself" : "needs a service visit"
                s += "- \(d.reason): −\(d.amount) points (\(owner))\n"
            }
        }
        s += "In two or three sentences: which should I deal with first, and why that"
            + " one? Do not book anything yet."
        return s
    }

    // MARK: - Provenance

    private var provenanceFooter: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle")
                .font(.caption2).foregroundStyle(.tertiary)
            Text(provenanceText)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// States where the number came from, and when.
    ///
    /// The score is computed server-side so both this app and the fleet console show
    /// the same value; saying so is what makes it credible, and `computedAt` means a
    /// stale score is visible rather than presented as live.
    private var provenanceText: String {
        let base = "Score and deductions are calculated for your vehicle and shown "
            + "exactly as reported."
        guard let at = breakdown?.computedAt, !at.isEmpty else { return base }
        return base + " Last calculated \(at)."
    }

    // MARK: - Chrome

    private var healthColor: Color {
        switch score {
        case 85...:    return theme.primary
        case 70..<85:  return Color(.systemOrange)
        default:       return Color(.systemRed)
        }
    }

    private var healthLabel: String {
        switch score {
        case 85...:    return "Healthy"
        case 70..<85:  return "Needs attention"
        default:       return "Action required"
        }
    }

    private func row(label: String, value: String,
                     emphasised: Bool = false, muted: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(emphasised ? .subheadline.bold() : .caption)
                .foregroundStyle(muted ? .secondary : .primary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Text(value)
                .font(emphasised ? .subheadline.bold() : .caption)
                .foregroundStyle(emphasised ? healthColor : (muted ? .secondary : .primary))
                .monospacedDigit()
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label): \(value)")
    }

    @ViewBuilder
    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) { content() }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground)))
    }
}
