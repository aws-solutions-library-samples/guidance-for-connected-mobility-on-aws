import SwiftUI

// MARK: - AVXIosOwnerActionCard

/// Card for dealer-originated proposals surfaced to the vehicle owner.
///
/// Used when `action.targetSystem == .iosOwner` — the inbound direction where
/// a dealer has proposed an action to the owner. The owner can approve or decline.
///
/// This is distinct from `AVXActionCard` (which shows owner-initiated actions).
/// The `ios-owner` target is the inbound path; it is NOT offered in the
/// target-picker for owner-initiated approvals, per the confirmed decision in
/// `decisions.md` § "RESOLVED: ApproveFindingRequest caveat".
///
/// Per Addendum D5: the `.declined` status has a distinct label and colour
/// from all other states, including `.cancelled`.
struct AVXIosOwnerActionCard: View {
    let action: AvxAction
    let theme: TenantTheme

    /// Called when the owner approves the dealer proposal.
    var onApprove: ((String) -> Void)? = nil

    /// Called when the owner declines the dealer proposal.
    /// Per Addendum D5: `declined` is semantically distinct from `cancelled`
    /// and renders with its own badge label and colour.
    var onDecline: ((String) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Header: dealer proposal indicator
            HStack(spacing: 6) {
                Image(systemName: "person.badge.plus")
                    .foregroundStyle(theme.primary)
                    .accessibilityHidden(true)
                Text("Dealer Proposal")
                    .font(.subheadline.bold())
                Spacer()
                AvxActionStatusBadge(status: action.status)
            }

            // Action kind
            Text(action.actionKind.displayName)
                .font(.body)
                .accessibilityIdentifier("avx-ios-owner-kind-\(action.actionId)")

            // Evidence chips (proposal justification)
            if !action.evidence.isEmpty {
                AVXEvidenceChips(evidence: action.evidence, theme: theme)
            }

            // Target reference if already routed
            if let ref = action.targetRef {
                HStack(spacing: 4) {
                    Image(systemName: "doc.text")
                        .font(.caption2)
                        .foregroundStyle(theme.primary)
                    Text(ref)
                        .font(.caption)
                        .foregroundStyle(theme.primary)
                }
            }

            Divider()

            // Approve / Decline row — only shown when action is actionable
            if action.status == .pendingExecution {
                HStack(spacing: 12) {
                    Button {
                        onApprove?(action.actionId)
                    } label: {
                        Label("Approve", systemImage: "checkmark.circle.fill")
                            .font(.subheadline.bold())
                            .foregroundStyle(.white)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .background(Capsule().fill(theme.primary))
                    }
                    .buttonStyle(.borderless)
                    .accessibilityIdentifier("avx-ios-owner-approve-\(action.actionId)")

                    Button {
                        onDecline?(action.actionId)
                    } label: {
                        Label("Decline", systemImage: "xmark.circle")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityIdentifier("avx-ios-owner-decline-\(action.actionId)")

                    Spacer()
                }
            }
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(theme.primary.opacity(0.3), lineWidth: 1)
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("avx-ios-owner-card-\(action.actionId)")
    }
}
