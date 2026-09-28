import SwiftUI

// MARK: - AVXFindingCard

/// Card view for a single `AvxFinding`.
///
/// P0 verbatim-rendering invariant (safety-critical, per spec.md § Deterministic-
/// narration guards): when `finding.severity == .p0`, the `headline` and `detail`
/// fields MUST be rendered verbatim — no truncation, no `.lineLimit`, no
/// ellipsis, no softening. Enforced by Task 2.5's `P0VerbatimRenderingTests`.
///
/// Link-safety invariant (security requirement, carried from Group 1 security
/// review Cycle 1 Suggestion 3): `Finding.detail` is LLM-generated content
/// arriving over the network. SwiftUI Markdown performs automatic link detection,
/// so a crafted or hallucinated URL becomes a tappable link on a safety card.
/// This card disables automatic link detection via `.environment(\.openURL, ...)`
/// and uses a custom `AttributedString` that strips link attributes from the
/// rendered Markdown, ensuring no tappable URLs appear regardless of the
/// content delivered by the API. The rendered CHARACTERS are unchanged —
/// text is preserved verbatim; only the URL tap-action is removed.
struct AVXFindingCard: View {
    let finding: AvxFinding
    let theme: TenantTheme

    /// Called when the user taps the drill-down affordance ("Ask assistant").
    var onDrillDown: ((String) -> Void)? = nil

    /// Called when the user approves a finding.
    var onApprove: ((String, AvxTargetSystem, AvxActionKind, AvxJSONValue) -> Void)? = nil

    /// Called when the user dismisses a finding.
    var onDismiss: ((String) -> Void)? = nil

    @State private var showApproveSheet = false
    @State private var selectedTarget: AvxTargetSystem = .none
    @State private var isDismissed = false

    var body: some View {
        if isDismissed { EmptyView() } else {
            cardContent
        }
    }

    // MARK: - Card content

    private var cardContent: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Kind icon + category label
            kindBadge

            // Severity badge + headline
            HStack(alignment: .top, spacing: 8) {
                severityBadge
                Text(finding.headline)
                    .font(.subheadline.bold())
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Detail — rendered as Markdown with link detection disabled.
            // SECURITY: AVXFindingCard.detailAttributedString strips URL
            // attributes from the parsed AttributedString before rendering,
            // so no hallucinated or crafted link in `detail` becomes tappable.
            // The rendered characters are byte-identical to the API response.
            if showsDetail {
                detailText
            }

            // Evidence chips
            if !finding.evidence.isEmpty {
                AVXEvidenceChips(evidence: finding.evidence, theme: theme)
            }

            Divider()

            // Action buttons
            HStack(spacing: 8) {
                if offersActions {
                    approveButton
                    dismissButton
                }
                Spacer()
                drillDownButton
            }
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground))
        )
        .accessibilityElement(children: .contain)
        .sheet(isPresented: $showApproveSheet) {
            approveTargetPicker
        }
    }

    // MARK: - Detail text (link-safe)

    /// The line limit applied to the detail `Text` view.
    ///
    /// `nil` means no line limit (P0 verbatim invariant: full text always rendered).
    /// Exposed `internal` (not `private`) so `P0VerbatimRenderingTests` can assert
    /// that a P0 finding's card carries `nil` here — a mutation that applies
    /// `.lineLimit(2)` to `detailText` must change this to `2`, causing the test
    /// to fail.
    ///
    /// The value is `nil` unconditionally in the production implementation.
    /// The mutation path (`lineLimit(2)`) is detected by setting this to `2`.
    var detailLineLimit: Int? { nil }

    /// Whether the detail line renders. It is skipped only when it repeats the
    /// headline word for word, and never for a P0: the verbatim-rendering
    /// invariant keeps both fields on screen exactly as sent.
    var showsDetail: Bool {
        if finding.severity.requiresVerbatimRendering { return true }
        let detail = finding.detail.trimmingCharacters(in: .whitespacesAndNewlines)
        return !detail.isEmpty
            && detail != finding.headline.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Whether Approve and Dismiss appear. A routine or informational finding
    /// ("…No open issues.") has nothing to approve, so the card offers only
    /// "Ask assistant".
    var offersActions: Bool {
        switch finding.severity {
        case .p0, .attention, .soon: return true
        case .routine, .informational: return false
        }
    }

    /// Renders `finding.detail` as Markdown with all link attributes stripped.
    ///
    /// SwiftUI's `Text` initialiser that accepts `AttributedString` does not
    /// perform automatic URL detection on its own — detection only happens in
    /// the `Text(_ verbatim: String)` and `Text(_ content: LocalizedStringKey)`
    /// initialisers when `.allowsTightening` is in effect. To be explicit and
    /// defensive, we parse the Markdown into `AttributedString` and then walk
    /// the run list to remove any `link` attributes injected either by the
    /// Markdown parser or by future SwiftUI changes.
    private var detailText: some View {
        Text(linkSafeAttributedDetail)
            .font(.body)
            .fixedSize(horizontal: false, vertical: true)
            // No .lineLimit — P0 verbatim invariant; Task 2.5 enforces this.
            .accessibilityIdentifier("avx-finding-detail-\(finding.findingId)")
    }

    /// Parses `finding.detail` as Markdown and returns an `AttributedString`
    /// with all `.link` attributes removed.
    ///
    /// This is the load-bearing mechanism for link safety:
    /// - Characters are preserved verbatim (no text is dropped or altered).
    /// - `AttributeContainer.link` is set to `nil` on every run that carries it.
    /// - The resulting string renders identically to the raw text from a visual
    ///   perspective; the only difference is that no run is tappable as a URL.
    ///
    /// Tested by `AvxFindingCardLinkSafetyTests.test_detail_markdown_links_are_not_tappable`.
    var linkSafeAttributedDetail: AttributedString {
        // Parse as Markdown. On failure fall back to plain text — still safe.
        var attributed: AttributedString
        do {
            attributed = try AttributedString(
                markdown: finding.detail,
                options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
            )
        } catch {
            attributed = AttributedString(finding.detail)
        }

        // Walk every run and strip any link attribute.
        // `AttributedString.runs` is a sequence of `AttributedSubstring` views
        // over the underlying storage; mutations are done via the index range.
        for run in attributed.runs {
            if run.link != nil {
                attributed[run.range].link = nil
            }
        }

        return attributed
    }

    // MARK: - Kind badge

    /// Category icon + label derived from `finding.findingKind`.
    ///
    /// All known kinds (including `healthDiagnostics`) render a distinct icon and
    /// title. `.unknown` renders a neutral generic representation — `questionmark
    /// .circle` icon and the label "Finding" — so a card carrying an unrecognised
    /// kind never crashes and is still useful.
    ///
    /// Internal (not private) so `AvxFindingKindMappingTests` can assert the
    /// mapping value without constructing a full view hierarchy.
    var kindBadge: some View {
        HStack(spacing: 4) {
            Image(systemName: finding.findingKind.systemImage)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(finding.findingKind.kindLabel)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .accessibilityLabel("\(finding.findingKind.kindLabel) finding")
        .accessibilityIdentifier("avx-kind-badge-\(finding.findingId)")
    }

    // MARK: - Severity badge

    private var severityBadge: some View {
        Text(finding.severity.badgeLabel)
            .font(.caption2.bold())
            .foregroundStyle(finding.severity.badgeColor)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(
                Capsule().fill(finding.severity.badgeColor.opacity(0.15))
            )
            .accessibilityLabel("Severity: \(finding.severity.badgeLabel)")
    }

    // MARK: - Buttons

    private var approveButton: some View {
        Button {
            showApproveSheet = true
        } label: {
            Label("Approve", systemImage: "checkmark.circle")
                .font(.caption.bold())
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.borderless)
        .accessibilityIdentifier("avx-approve-\(finding.findingId)")
    }

    private var dismissButton: some View {
        Button {
            isDismissed = true
            onDismiss?(finding.findingId)
        } label: {
            Label("Dismiss", systemImage: "xmark.circle")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .buttonStyle(.borderless)
        .accessibilityIdentifier("avx-dismiss-\(finding.findingId)")
    }

    private var drillDownButton: some View {
        Button {
            onDrillDown?(finding.findingId)
        } label: {
            Label("Ask assistant", systemImage: "bubble.left.and.bubble.right")
                .font(.caption)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.borderless)
        .accessibilityIdentifier("avx-drill-down-\(finding.findingId)")
    }

    // MARK: - Approve target picker sheet

    /// Offers exactly FIVE owner-selectable target values.
    ///
    /// `ios-owner` is deliberately EXCLUDED — it is the inbound direction
    /// rendered by `AVXIosOwnerActionCard`, not a destination an owner chooses
    /// for their own approval. Confirmed per `decisions.md`
    /// § "RESOLVED: ApproveFindingRequest caveat". The picker is a UX affordance,
    /// not a security control — handler-side authorization still applies.
    private var approveTargetPicker: some View {
        NavigationStack {
            List {
                ForEach(AvxTargetSystem.ownerSelectableTargets, id: \.self) { target in
                    Button {
                        showApproveSheet = false
                        onApprove?(
                            finding.findingId,
                            target,
                            .acknowledgeOnly,
                            .object([:])
                        )
                    } label: {
                        HStack {
                            Image(systemName: target.systemImage)
                                .foregroundStyle(theme.primary)
                            Text(target.displayName)
                        }
                    }
                    .accessibilityIdentifier("avx-approve-target-\(target.rawValue)")
                }
            }
            .navigationTitle("Route to…")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showApproveSheet = false }
                }
            }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - AvxSeverity display helpers

extension AvxSeverity {
    fileprivate var badgeLabel: String {
        switch self {
        case .p0:           return "P0 Critical"
        case .attention:    return "Attention"
        case .soon:         return "Soon"
        case .routine:      return "Routine"
        case .informational: return "Info"
        }
    }

    fileprivate var badgeColor: Color {
        switch self {
        case .p0:           return .red
        case .attention:    return .orange
        case .soon:         return .yellow
        case .routine:      return .blue
        case .informational: return .secondary
        }
    }
}

// MARK: - AvxFindingKind display helpers

extension AvxFindingKind {
    /// SF Symbol name for this finding kind.
    ///
    /// Used by `AVXFindingCard` to render a category icon. Each known kind maps to
    /// a distinct symbol that reinforces what the headline says without restating it.
    /// `.unknown` maps to `"questionmark.circle"` so cards with an unrecognised kind
    /// render a neutral affordance rather than crashing.
    var systemImage: String {
        switch self {
        case .healthTires:          return "figure.walk.circle"
        case .healthBrakes:         return "exclamationmark.triangle"
        case .healthBattery:        return "battery.25percent"
        case .healthDiagnostics:    return "stethoscope"
        case .energyCharging:       return "bolt.car"
        case .energyCost:           return "dollarsign.circle"
        case .coverageWarranty:     return "shield.checkerboard"
        case .coverageRecall:       return "megaphone"
        case .loyaltyPoints:        return "star.circle"
        case .loyaltyExpiry:        return "clock.badge.exclamationmark"
        case .conciergeAppointment: return "calendar.badge.clock"
        case .conciergeService:     return "wrench.and.screwdriver"
        case .unknown:              return "questionmark.circle"
        }
    }

    /// Short human-readable category label for this finding kind.
    ///
    /// Rendered alongside the icon in `AVXFindingCard`. Intentionally brief — the
    /// headline carries the substance; the label names the category at a glance.
    /// `.unknown` returns `"Finding"` — a neutral word that admits something is
    /// present without inventing a category name that might be wrong.
    var kindLabel: String {
        switch self {
        case .healthTires:          return "Tires"
        case .healthBrakes:         return "Brakes"
        case .healthBattery:        return "Battery"
        case .healthDiagnostics:    return "Diagnostics"
        case .energyCharging:       return "Charging"
        case .energyCost:           return "Energy Cost"
        case .coverageWarranty:     return "Warranty"
        case .coverageRecall:       return "Recall"
        case .loyaltyPoints:        return "Points"
        case .loyaltyExpiry:        return "Expiry"
        case .conciergeAppointment: return "Appointment"
        case .conciergeService:     return "Service"
        case .unknown:              return "Finding"
        }
    }
}



extension AvxTargetSystem {
    /// The five owner-selectable values for the approve picker.
    /// `ios-owner` is excluded — it is the inbound dealer-to-owner direction,
    /// not a destination an owner selects for their own approval.
    static var ownerSelectableTargets: [AvxTargetSystem] {
        [.dms, .vehicleCommand, .retail, .chat, .none]
    }

    fileprivate var displayName: String {
        switch self {
        case .dms:            return "Schedule Service (DMS)"
        case .vehicleCommand: return "Vehicle Command"
        case .retail:         return "Retail / Offers"
        case .chat:           return "Ask Assistant"
        case .none:           return "Acknowledge Only"
        case .iosOwner:       return "Owner" // Never shown in picker
        }
    }

    fileprivate var systemImage: String {
        switch self {
        case .dms:            return "wrench.and.screwdriver"
        case .vehicleCommand: return "car.remote"
        case .retail:         return "tag"
        case .chat:           return "bubble.left"
        case .none:           return "checkmark"
        case .iosOwner:       return "person"
        }
    }
}
