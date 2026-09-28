import SwiftUI

// MARK: - AVXActionCard

/// Card view for a single `AvxAction`.
///
/// Renders the action status ladder using `AvxActionStatusBadge`. All 8 status
/// values render distinctly — `dismissed` is NOT collapsed to `cancelled`,
/// `failed` is NOT collapsed to `declined` (Addenda D4 + D5 semantic distinctness).
struct AVXActionCard: View {
    let action: AvxAction
    let theme: TenantTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Status badge (all 8 values render distinctly)
            HStack(spacing: 8) {
                AvxActionStatusBadge(status: action.status)
                Spacer()
                Text(action.actionKind.displayName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // Target reference line — shown when the executor returned an RO/reference
            if let ref = action.targetRef {
                HStack(spacing: 4) {
                    Image(systemName: action.targetSystem.referenceIcon)
                        .font(.caption2)
                        .foregroundStyle(theme.primary)
                    Text(ref)
                        .font(.caption)
                        .foregroundStyle(theme.primary)
                }
                .accessibilityLabel("Reference: \(ref)")
                .accessibilityIdentifier("avx-action-ref-\(action.actionId)")
            }

            // Evidence chips (if present)
            if !action.evidence.isEmpty {
                AVXEvidenceChips(evidence: action.evidence, theme: theme)
            }

            // Audit trail summary — show most recent transition
            if let lastEntry = action.auditTrail.last {
                auditSummary(lastEntry)
            }
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground))
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("avx-action-card-\(action.actionId)")
    }

    // MARK: - Audit summary

    private func auditSummary(_ entry: AvxActionAuditEntry) -> some View {
        HStack(spacing: 4) {
            Image(systemName: "clock.arrow.circlepath")
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text("\(entry.fromStatus.rawValue) → \(entry.toStatus.rawValue)")
                .font(.caption2)
                .foregroundStyle(.secondary)
            if let note = entry.note {
                Text("· \(note)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .accessibilityLabel("Last status change: from \(entry.fromStatus.rawValue) to \(entry.toStatus.rawValue)")
    }
}

// MARK: - AvxActionKind display helpers

extension AvxActionKind {
    var displayName: String {
        switch self {
        case .bookCombinedVisit:     return "Book Visit"
        case .scheduleInspection:    return "Schedule Inspection"
        case .updateChargingSchedule: return "Update Charging"
        case .fileWarrantyPre:       return "Warranty Claim"
        case .surfaceRecall:         return "Recall Notice"
        case .acknowledgeOnly:       return "Acknowledged"
        }
    }
}

extension AvxTargetSystem {
    fileprivate var referenceIcon: String {
        switch self {
        case .dms:            return "doc.text"
        case .vehicleCommand: return "car.remote"
        case .retail:         return "tag"
        case .chat:           return "bubble.left"
        case .none:           return "checkmark.circle"
        case .iosOwner:       return "person.circle"
        }
    }
}
