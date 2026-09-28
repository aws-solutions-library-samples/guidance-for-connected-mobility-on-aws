import SwiftUI

/// Narrates what the Discovery Watch agent found about a saved build since the
/// customer's last visit.
///
/// ## This view computes nothing
///
/// It is the **Tier 1** half of the split described in `DiscoveryWatchFinding`: read
/// a pre-computed artifact, render it, surface how fresh it is. If this view ever
/// starts deriving a conclusion, the tier boundary has been crossed — the reasoning
/// belongs in the scheduled agent, where nobody is waiting for it.
///
/// ## Why the evidence and the timestamp are not optional chrome
///
/// The claim being made is "something changed while you were away". A customer has
/// no way to verify that from the sentence alone, so:
///
/// - **`evidence` is always rendered.** Per the doctrine, narration without evidence
///   is an assertion the agent cannot support.
/// - **`computedAt` is always rendered**, and a stale finding is labelled stale
///   rather than dropped. Dropping it would show the customer nothing, from which
///   they would conclude nothing changed — when in fact nothing was *checked*. That
///   is the exact failure the deployed health agent is exposed to: both its alarms
///   shipped with empty `AlarmActions`, so a silent nightly failure leaves Tier 1
///   narrating an artifact with a receding timestamp.
///
/// ## Pull-only
///
/// This renders when the customer opens the app. There is no push, by design — see
/// `DiscoveryWatchFinding`.
struct DiscoveryWatchCard: View {
    let findings: [DiscoveryWatchFinding]
    /// What is being watched, e.g. "Meridian Crestwind Signature".
    ///
    /// Passed in rather than hardcoded as "your saved build" because there is no
    /// saved-build store in the client yet. Calling it a saved build when the agent
    /// is actually watching the *recommended* configuration would be a claim the app
    /// cannot support — the same overclaim discipline the artifact's attribution
    /// carries. Whichever it is, the caller knows.
    let watchedItemLabel: String
    let theme: TenantTheme
    /// Opens the watched configuration. Nil hides the CTA.
    var onOpenBuild: (() -> Void)?
    /// Injectable clock, so freshness and staleness are testable.
    var now: Date = Date()

    @State private var expandedIds: Set<String> = []

    var body: some View {
        if !findings.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                header
                ForEach(findings) { finding in
                    findingRow(finding)
                }
                Text(DiscoveryWatchFinding.attributionNote)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
                if let onOpenBuild {
                    Button(action: onOpenBuild) {
                        HStack(spacing: 6) {
                            Text("Open this build")
                                .font(.caption.weight(.semibold))
                            Image(systemName: "chevron.right")
                                .font(.system(size: 9, weight: .bold))
                        }
                        .foregroundStyle(theme.primary)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "moon.stars")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
            VStack(alignment: .leading, spacing: 1) {
                Text("While you were away")
                    .font(.subheadline.weight(.semibold))
                Text(headerSubtitle)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
    }

    /// Counts the findings rather than asserting they are all good news — the agent
    /// reports what changed, and "1 update" is honest where "1 improvement" would be
    /// editorialising on the agent's behalf.
    private var headerSubtitle: String {
        let n = findings.count
        return n == 1 ? "1 update on the \(watchedItemLabel)"
                      : "\(n) updates on the \(watchedItemLabel)"
    }

    // MARK: - Finding row

    @ViewBuilder
    private func findingRow(_ f: DiscoveryWatchFinding) -> some View {
        let isOpen = expandedIds.contains(f.id)
        let stale = f.isStale(now: now)

        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    if isOpen { expandedIds.remove(f.id) } else { expandedIds.insert(f.id) }
                }
            } label: {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: f.category.symbolName)
                        .font(.caption)
                        .foregroundStyle(theme.primary)
                        .frame(width: 16)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(f.finding)
                            .font(.caption.weight(.medium))
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                            .foregroundStyle(Color(.label))
                        HStack(spacing: 5) {
                            Text(f.freshnessLabel(now: now))
                            if stale {
                                Text("· may be out of date")
                                    .foregroundStyle(Color(.systemOrange))
                            }
                        }
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: isOpen ? "chevron.up" : "chevron.down")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(theme.primary)
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(accessibilityLabel(for: f, stale: stale, isOpen: isOpen))

            if isOpen {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(f.evidence, id: \.self) { line in
                        HStack(alignment: .top, spacing: 5) {
                            Text("·").font(.caption2).foregroundStyle(.tertiary)
                            Text(line)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .padding(.leading, 24)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(theme.primary.opacity(0.05))
        )
    }

    /// Includes staleness in the spoken label, so a VoiceOver user gets the same
    /// caveat a sighted user does.
    private func accessibilityLabel(for f: DiscoveryWatchFinding,
                                    stale: Bool, isOpen: Bool) -> String {
        var s = "\(f.category.rawValue). \(f.finding). \(f.freshnessLabel(now: now))."
        if stale { s += " This may be out of date." }
        s += isOpen ? " Showing why." : " Tap to see why."
        return s
    }
}
