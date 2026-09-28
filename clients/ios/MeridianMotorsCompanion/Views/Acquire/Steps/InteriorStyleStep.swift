import SwiftUI

/// Light-configuration step: pick interior style.
///
/// Options are gated by `AvailabilityContract` (task 5.4):
/// - Available options are selectable.
/// - Unavailable options render visibly disabled with a reason, rather than disappearing.
/// - Switching to the custom delivery window re-opens options that are
///   unavailable on standard.
///
/// Task 5.3. Tap 3 of 4 from offer-accepted to order-placed.
struct InteriorStyleStep: View {
    let availability: AvailabilityContract
    let theme: TenantTheme
    let onSelect: (InteriorStyle) -> Void
    let onBack: () -> Void

    @Environment(\.adaptiveLayoutContext) private var layoutContext
    @State private var deliveryWindow: AvailabilityContract.DeliveryWindow = .standard

    private var effectiveAvailability: AvailabilityContract {
        deliveryWindow == .custom ? availability.withCustomWindow() : availability
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: layoutContext.isCompact ? 14 : 20) {
                Button(action: onBack) {
                    Label("Back", systemImage: "chevron.left")
                        .font(.subheadline)
                        .foregroundStyle(theme.primary)
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity, alignment: .leading)

                Text("Choose your interior style")
                    .font(.headline)

                deliveryWindowToggle

                ForEach(InteriorStyle.allCases, id: \.rawValue) { style in
                    let avail = effectiveAvailability.availability(forStyleId: style.rawValue)
                    styleCard(style: style, avail: avail)
                }
            }
            .padding(layoutContext.isCompact ? 14 : 20)
        }
    }

    // MARK: - Delivery window toggle

    private var deliveryWindowToggle: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Delivery window").font(.subheadline.weight(.semibold))
            Picker("Delivery window", selection: $deliveryWindow) {
                Text("Standard (6–12 wks)").tag(AvailabilityContract.DeliveryWindow.standard)
                Text("Custom (12–24 wks)").tag(AvailabilityContract.DeliveryWindow.custom)
            }
            .pickerStyle(.segmented)
            if deliveryWindow == .custom {
                Text("Switching to custom delivery unlocks additional options.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Style card

    private func styleCard(style: InteriorStyle, avail: AvailabilityContract.OptionAvailability) -> some View {
        Button {
            if avail.available { onSelect(style) }
        } label: {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: style == .sport ? "bolt.circle.fill"
                                : style == .touring ? "leaf.circle.fill"
                                : "circle.fill")
                    .font(.title3)
                    .foregroundStyle(avail.available ? theme.primary : Color(.systemGray3))
                    .frame(width: 32)

                VStack(alignment: .leading, spacing: 4) {
                    Text(style.displayName)
                        .font(.subheadline.bold())
                        .foregroundStyle(avail.available ? Color(.label) : Color(.tertiaryLabel))
                    Text(style.description)
                        .font(.caption)
                        .foregroundStyle(avail.available ? .secondary : Color(.quaternaryLabel))
                        .fixedSize(horizontal: false, vertical: true)
                    if !avail.available, let reason = avail.unavailableReason {
                        HStack(spacing: 4) {
                            Image(systemName: "exclamationmark.circle")
                                .font(.caption2)
                            Text(reason)
                                .font(.caption2)
                        }
                        .foregroundStyle(.secondary)
                    }
                    if !avail.available && avail.availableOnCustom
                        && deliveryWindow == .standard {
                        Text("Available with custom delivery →")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(theme.primary)
                    }
                }
                Spacer(minLength: 0)

                if avail.available {
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(Color(.tertiaryLabel))
                }
            }
            .padding(layoutContext.isCompact ? 12 : 14)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(.secondarySystemGroupedBackground))
                    .opacity(avail.available ? 1 : 0.6)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(
                        avail.available ? theme.primary.opacity(0.12) : Color(.systemGray5),
                        lineWidth: 1
                    )
            )
        }
        .buttonStyle(.plain)
        .disabled(!avail.available)
        .accessibilityLabel(style.displayName + (avail.available ? "" : ", unavailable"))
        .accessibilityHint(avail.unavailableReason ?? "")
    }
}
