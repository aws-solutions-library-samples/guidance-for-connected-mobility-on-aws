import SwiftUI

/// Shows the delivery date the planning sequence produced, with the reasoning
/// behind it, and routes forward to order confirmation.
///
/// This is the screen that pays off the animation: the visitor sees a concrete
/// date plus *why* it is that date (facility, stock match, long-lead parts).
/// Showing the reasoning is the difference between a plausible demo and a
/// number that looks made up.
///
/// See `SupplyChainPlan` for the standing constraint — the date is derived
/// locally and the attribution credits the pattern, not a live query.
struct DeliveryProposalView: View {
    let plan: SupplyChainPlan
    let model: CatalogModel
    let theme: TenantTheme
    /// Advance to confirmation.
    let onAccept: () -> Void
    /// Return to interior-style selection.
    let onBack: () -> Void

    @Environment(\.adaptiveLayoutContext) private var layoutContext

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: layoutContext.isCompact ? 14 : 18) {
                    dateHeadline
                    timelineCard
                    facilityCard
                    if plan.matchedStockBuild || plan.longLeadPartCount > 0 {
                        planningNotesCard
                    }
                    attribution
                }
                .padding(layoutContext.isCompact ? 16 : 24)
            }
            footer
        }
        .background(Color(.systemGroupedBackground))
    }

    // MARK: - Headline

    private var dateHeadline: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Estimated delivery")
                .font(.caption)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)

            Text(plan.formattedDeliveryDate())
                .font(.largeTitle.bold())
                .foregroundStyle(theme.primary)
                .accessibilityLabel("Estimated delivery \(plan.formattedDeliveryDate())")

            Text("About \(plan.weeksRangeLabel) for your \(model.displayName)")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Timeline breakdown

    private var timelineCard: some View {
        card {
            VStack(alignment: .leading, spacing: 12) {
                Text("How we got to that date")
                    .font(.subheadline.bold())

                timelineRow(
                    symbol: "wrench.and.screwdriver",
                    label: plan.matchedStockBuild ? "Build (stock match)" : "Manufacturing",
                    value: "\(plan.manufacturingDays) days"
                )
                Divider()
                timelineRow(
                    symbol: "truck.box",
                    label: "Transit",
                    value: "\(plan.transitDays) days"
                )
                Divider()
                // The final leg. Without this row the card showed a plant-to-dealer
                // journey and then a total that silently included a leg it never
                // named — and for pickup, the destination was never stated at all.
                timelineRow(
                    symbol: handoverSymbol,
                    label: handoverLabel,
                    value: handoverValue
                )
                Divider()
                timelineRow(
                    symbol: "calendar",
                    label: "Total",
                    value: "\(plan.totalDays) days",
                    emphasised: true
                )
            }
        }
    }

    // MARK: - Handover row

    private var handoverSymbol: String {
        switch plan.handover {
        case .dealerPickup: return "building.columns"
        case .homeDelivery: return "house"
        }
    }

    private var handoverLabel: String {
        switch plan.handover {
        case .dealerPickup: return "Collect from"
        case .homeDelivery: return "Home delivery"
        }
    }

    /// Pickup names the destination and costs no extra days — the vehicle is
    /// already at the dealer transit delivered it to. Delivery states the extra
    /// days instead, which is what actually moves the date.
    private var handoverValue: String {
        switch plan.handover {
        case .dealerPickup(_, let name): return name
        case .homeDelivery:              return "\(plan.finalLegDays) days"
        }
    }

    private func timelineRow(symbol: String, label: String,
                             value: String, emphasised: Bool = false) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.caption)
                .foregroundStyle(emphasised ? theme.primary : .secondary)
                .frame(width: 18)
            Text(label)
                .font(emphasised ? .subheadline.bold() : .subheadline)
            Spacer(minLength: 8)
            Text(value)
                .font(emphasised ? .subheadline.bold() : .subheadline)
                .foregroundStyle(emphasised ? theme.primary : .primary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label): \(value)")
    }

    // MARK: - Facility

    private var facilityCard: some View {
        card {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: "building.2")
                        .foregroundStyle(theme.primary)
                    Text("Built at")
                        .font(.subheadline.bold())
                }
                Text(plan.facility.displayName)
                    .font(.subheadline)
                Text(plan.facility.regionLabel)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(plan.facility.selectionRationale)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 2)
            }
        }
    }

    // MARK: - Planning notes

    private var planningNotesCard: some View {
        card {
            VStack(alignment: .leading, spacing: 10) {
                Text("Planning notes")
                    .font(.subheadline.bold())

                if plan.matchedStockBuild {
                    noteRow(
                        symbol: "square.stack.3d.up.fill",
                        text: "A stock build already on the line matches your configuration, "
                            + "which brings your date forward."
                    )
                }
                if plan.longLeadPartCount > 0 {
                    noteRow(
                        symbol: "clock.badge.exclamationmark",
                        text: "\(plan.longLeadPartCount) part(s) in your configuration have a "
                            + "longer supplier lead time and are reserved ahead of the build."
                    )
                }
                if plan.deliveryWindow == .custom {
                    noteRow(
                        symbol: "slider.horizontal.3",
                        text: "You chose a custom build window, which opens more options "
                            + "but extends the timeline."
                    )
                }
            }
        }
    }

    private func noteRow(symbol: String, text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: symbol)
                .font(.caption)
                .foregroundStyle(theme.primary)
                .frame(width: 16)
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
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

    // MARK: - Footer

    private var footer: some View {
        VStack(spacing: 8) {
            Button(action: onAccept) {
                Text("Continue to order")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(theme.primary)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            Button(action: onBack) {
                Text("Change configuration")
                    .font(.subheadline)
                    .foregroundStyle(theme.primary)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.ultraThinMaterial)
    }

    // MARK: - Card chrome

    @ViewBuilder
    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) { content() }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
