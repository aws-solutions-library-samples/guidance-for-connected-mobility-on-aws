import SwiftUI

/// Animated supply-chain planning sequence, shown after configuration and
/// before the delivery proposal.
///
/// ## What this is
/// Five planning lines advance in sequence, each marking complete before the
/// next begins, then the view auto-advances via `onComplete`. **The visitor does
/// not tap.** That is deliberate: the Zone 2 tap budget is a throughput
/// constraint at 60–80 visitors/hour, and a screen whose only purpose is to
/// narrate work should not cost a tap.
///
/// ## What this is not
/// It contacts nothing. See `SupplyChainPlan` for the full constraint — the
/// short version is that the attribution names Amazon Connect Decisions as the
/// *pattern* without claiming a live query, and `SupplyChainPlan.attributionNote`
/// is test-asserted so that wording cannot drift into an overclaim.
///
/// ## Accessibility
/// The sequence is decorative in the sense that no information is lost by
/// skipping it — the delivery proposal that follows carries every fact. So the
/// whole stack is announced as one live region rather than as five separately
/// focusable rows, and a Reduce Motion setting drops the animation to a plain
/// state change.
struct SupplyChainPlanningView: View {
    let theme: TenantTheme
    /// Called once the sequence finishes. The caller advances the step machine.
    let onComplete: () -> Void

    @Environment(\.adaptiveLayoutContext) private var layoutContext
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Index of the step currently in progress. `nil` once all are complete.
    @State private var activeIndex: Int? = 0
    /// Steps already marked complete.
    @State private var completedCount: Int = 0
    /// Guards against `onComplete` firing twice if the task is restarted.
    @State private var hasCompleted = false

    private var steps: [SupplyChainPlan.PlanningStep] {
        SupplyChainPlan.PlanningStep.allCases
    }

    var body: some View {
        VStack(alignment: .leading, spacing: layoutContext.isCompact ? 16 : 22) {
            header
            planningRows
            Spacer(minLength: 0)
            attribution
        }
        .padding(layoutContext.isCompact ? 16 : 24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.systemGroupedBackground))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilityNarration)
        .task { await runSequence() }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                    .foregroundStyle(theme.primary)
                Text("Planning your build")
                    .font(.title3.bold())
            }
            Text("Checking parts, stock and factory capacity for the exact vehicle you configured.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Planning rows

    private var planningRows: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(Array(steps.enumerated()), id: \.element.id) { index, step in
                planningRow(step: step, index: index)
            }
        }
    }

    @ViewBuilder
    private func planningRow(step: SupplyChainPlan.PlanningStep, index: Int) -> some View {
        let isComplete = index < completedCount
        let isActive = activeIndex == index
        let isPending = !isComplete && !isActive

        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(isComplete ? theme.primary.opacity(0.15)
                          : Color(.secondarySystemGroupedBackground))
                    .frame(width: 32, height: 32)

                if isComplete {
                    Image(systemName: "checkmark")
                        .font(.caption.bold())
                        .foregroundStyle(theme.primary)
                } else if isActive {
                    ProgressView()
                        .controlSize(.small)
                        .tint(theme.primary)
                } else {
                    Image(systemName: step.symbolName)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }

            Text(step.rawValue)
                .font(.subheadline.weight(isActive ? .semibold : .regular))
                .foregroundStyle(isPending ? .secondary : .primary)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 0)
        }
        .opacity(isPending ? 0.55 : 1)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.25), value: completedCount)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.25), value: activeIndex)
    }

    // MARK: - Attribution

    private var attribution: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle")
                .font(.caption2)
                .foregroundStyle(.tertiary)
            Text(SupplyChainPlan.attributionNote)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Sequence driver

    /// Advances one step per dwell interval, then fires `onComplete` exactly once.
    ///
    /// Uses `Task.sleep` rather than a `Timer` so cancellation is automatic when
    /// the view goes away — a timer surviving a dismissal would call
    /// `onComplete` against a step machine that has already moved on, which on a
    /// kiosk reset would drop the next visitor into a stale delivery proposal.
    private func runSequence() async {
        guard !hasCompleted else { return }

        for (index, step) in steps.enumerated() {
            activeIndex = index
            do {
                try await Task.sleep(nanoseconds: UInt64(step.dwellSeconds * 1_000_000_000))
            } catch {
                return          // cancelled — view dismissed, do not complete
            }
            guard !Task.isCancelled else { return }
            completedCount = index + 1
        }

        activeIndex = nil
        hasCompleted = true
        onComplete()
    }

    // MARK: - Accessibility

    private var accessibilityNarration: String {
        "Planning your build. "
            + steps.map(\.rawValue).joined(separator: ". ")
            + ". " + SupplyChainPlan.attributionNote
    }
}
