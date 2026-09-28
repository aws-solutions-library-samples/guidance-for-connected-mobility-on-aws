import SwiftUI

// MARK: - AvxActionStatusBadge

/// Badge rendering the `AvxActionStatus` enum. All 8 values render with a distinct
/// accessible label, colour, and (in Task 2.4) icon.
///
/// Scaffolding (Task 2.2): colours and labels are final. Full icon styling is Task 2.4.
///
/// Correctness-critical: per Addenda D4 + D5, `dismissed` must NOT collapse to
/// `cancelled`, and `failed` must NOT collapse to `declined`. These are semantically
/// distinct terminal states. Mutation-tested in `AvxActionStatusBadgeTests`
/// (Task 2.1's red-phase skeleton, goes green in Task 2.4).
struct AvxActionStatusBadge: View {
    let status: AvxActionStatus

    var body: some View {
        Text(label)
            .font(.caption2.bold())
            .foregroundStyle(labelColor)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(
                Capsule().fill(labelColor.opacity(0.15))
            )
            .accessibilityIdentifier(accessibilityIdentifier)
            .accessibilityLabel(label)
    }

    // MARK: - Per-status label (distinct for all 8 values)

    /// Human-readable label for the status. Distinct for every case — no collapse.
    var label: String {
        switch status {
        case .pendingExecution: return "Pending"
        case .executed:         return "Executed"
        case .inProgress:       return "In Progress"
        case .completed:        return "Completed"
        case .cancelled:        return "Cancelled"
        case .declined:         return "Declined"
        case .dismissed:        return "Dismissed"   // ≠ Cancelled (Addendum D4)
        case .failed:           return "Failed"      // ≠ Declined (Addendum D5)
        }
    }

    /// Unique accessibility identifier so `AvxActionStatusBadgeTests` can assert
    /// distinctness without relying on rendered colour (which is fragile).
    var accessibilityIdentifier: String {
        "avx-status-badge-\(status.rawValue)"
    }

    // MARK: - Per-status colour

    private var labelColor: Color {
        switch status {
        case .pendingExecution: return .orange
        case .executed:         return .blue
        case .inProgress:       return .blue
        case .completed:        return .green
        case .cancelled:        return .gray
        case .declined:         return .purple
        case .dismissed:        return .secondary    // distinct from .cancelled (.gray)
        case .failed:           return .red
        }
    }
}
