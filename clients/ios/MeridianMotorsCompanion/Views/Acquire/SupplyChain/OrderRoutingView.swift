import SwiftUI

/// Post-order view showing how the order is routed through an OEM's operational
/// chain — order intake, planning, material commitment, scheduling, build,
/// logistics, dealer handover.
///
/// ## Why this exists
/// The order-placed screen otherwise ends the story at "thanks". For an OEM
/// audience the interesting part is what happens *next*: an order is a demand
/// signal that has to compete for parts and capacity. Surfacing the chain is
/// what connects the customer experience to the supply-chain story.
///
/// ## What it is not
/// Rendered from `OrderRoutingStage.chain(for:)`, which is local. No stage here
/// reflects live operational state — the live per-stage progression a visitor can
/// actually track is `OrderTrackerView`, which polls a real order record. This
/// view explains the shape of the chain; the tracker reports real position in it.
///
/// Presented as a sheet from `SuccessStep` so it is opt-in — a visitor in a hurry
/// takes their confirmation and leaves, and a visitor who asks "what happens now?"
/// gets the answer.
struct OrderRoutingView: View {
    let plan: SupplyChainPlan
    let orderNumber: String?
    let theme: TenantTheme
    let onDone: () -> Void

    @Environment(\.adaptiveLayoutContext) private var layoutContext

    private var stages: [OrderRoutingStage] {
        OrderRoutingStage.chain(for: plan)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: layoutContext.isCompact ? 14 : 18) {
                    header
                    stageChain
                    attribution
                }
                .padding(layoutContext.isCompact ? 16 : 24)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("What happens next")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done", action: onDone)
                }
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let orderNumber {
                Text(orderNumber)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            Text("Your order is now in the chain")
                .font(.title3.bold())
            Text("Estimated delivery \(plan.formattedDeliveryDate()) — "
                 + "built at \(plan.facility.displayName).")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Stage chain

    private var stageChain: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(stages.enumerated()), id: \.element.id) { index, stage in
                stageRow(stage: stage, isLast: index == stages.count - 1)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private func stageRow(stage: OrderRoutingStage, isLast: Bool) -> some View {
        HStack(alignment: .top, spacing: 12) {
            // Rail: icon plus the connector down to the next stage.
            VStack(spacing: 0) {
                ZStack {
                    Circle()
                        .fill(theme.primary.opacity(0.15))
                        .frame(width: 30, height: 30)
                    Image(systemName: stage.symbolName)
                        .font(.caption)
                        .foregroundStyle(theme.primary)
                }
                if !isLast {
                    Rectangle()
                        .fill(theme.primary.opacity(0.2))
                        .frame(width: 2)
                        .frame(minHeight: 26)
                }
            }
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(stage.title)
                    .font(.subheadline.bold())
                Text(stage.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(stage.systemLabel)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .padding(.top, 1)
            }
            .padding(.bottom, isLast ? 0 : 14)

            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(stage.title). \(stage.detail). Handled in \(stage.systemLabel).")
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
}
