import SwiftUI

// MARK: - DiscoverCard

/// A single catalog model card rendered inside DiscoverFlow's evolving grid.
///
/// Agent-first rule: the parent DiscoverFlow only adds a card to the cards
/// array AFTER the agent turn that introduced it has been recorded in the
/// conversation. This view is purely presentational — it renders whatever
/// card state it receives and does not read the conversation directly.
struct DiscoverCard: View {
    let card: CatalogCard
    let theme: TenantTheme
    var onSelect: (() -> Void)? = nil
    var onCompare: (() -> Void)? = nil

    var body: some View {
        Button(action: { onSelect?() }) {
            VStack(alignment: .leading, spacing: 8) {
                heroImageView

                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(card.displayName)
                            .font(.headline)
                            .foregroundStyle(Color(.label))
                            .lineLimit(2)
                        Spacer()
                        if card.isSelected {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(theme.primary)
                                .font(.system(size: 18))
                        }
                    }

                    if let tagline = card.tagline {
                        Text(tagline)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }

                    if let priceHint = card.priceHint {
                        Text(priceHint)
                            .font(.subheadline)
                            .fontWeight(.medium)
                            .foregroundStyle(theme.primary)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 12)

                if card.isSelected, let onCompare {
                    Divider()
                    Button(action: onCompare) {
                        Label("Add to compare", systemImage: "arrow.left.arrow.right")
                            .font(.caption)
                            .fontWeight(.medium)
                            .foregroundStyle(theme.primary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                    }
                }
            }
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(
                        card.isSelected ? theme.primary : Color.clear,
                        lineWidth: 2
                    )
            )
            .shadow(color: .black.opacity(card.isSelected ? 0.12 : 0.06),
                    radius: card.isSelected ? 6 : 3, x: 0, y: 2)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityAddTraits(card.isSelected ? [.isSelected] : [])
    }

    // MARK: - Hero image

    @ViewBuilder
    private var heroImageView: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(theme.primary.opacity(0.1))
                .frame(height: 100)
            Image(systemName: categorySystemImage)
                .font(.system(size: 36))
                .foregroundStyle(theme.primary.opacity(0.6))
        }
        .padding(.horizontal, 12)
        .padding(.top, 12)
    }

    private var categorySystemImage: String {
        switch card.categoryKey.lowercased() {
        case "commuter", "city": return "figure.walk.circle"
        case "adventure", "off-road": return "mountain.2.fill"
        case "electric", "ev": return "bolt.circle"
        case "cruiser", "touring": return "road.lanes"
        default: return "motorcycle"
        }
    }

    private var accessibilityLabel: String {
        var parts = [card.displayName]
        if let tagline = card.tagline { parts.append(tagline) }
        if let price = card.priceHint { parts.append(price) }
        if card.isSelected { parts.append("Selected") }
        return parts.joined(separator: ", ")
    }
}

// MARK: - OfferBanner

/// A compact offer banner rendered beneath the catalog card grid
/// when offers_lookup results are introduced by the agent.
struct OfferBanner: View {
    let offer: OfferCard
    let theme: TenantTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "tag.fill")
                    .foregroundStyle(theme.primary)
                Text(offer.displayName)
                    .font(.headline)
                    .foregroundStyle(Color(.label))
            }

            Text(offer.body)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(3)

            // Disclosure text is REQUIRED to render verbatim per spec
            if !offer.disclosureTxt.isEmpty {
                Text(offer.disclosureTxt)
                    .font(.caption2)
                    .foregroundStyle(Color(.tertiaryLabel))
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let deposit = offer.depositAmount, deposit > 0 {
                Label("Deposit: \(depositFormatted(deposit))",
                      systemImage: "creditcard")
                    .font(.caption)
                    .foregroundStyle(theme.primary)
            }
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(theme.primary.opacity(0.25), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilityLabel)
    }

    private func depositFormatted(_ amount: Double) -> String {
        let fmt = NumberFormatter()
        fmt.numberStyle = .currency
        fmt.maximumFractionDigits = 0
        return fmt.string(from: NSNumber(value: amount)) ?? "\(amount)"
    }

    private var accessibilityLabel: String {
        var parts = [offer.displayName, offer.body]
        if !offer.disclosureTxt.isEmpty { parts.append("Disclosure: " + offer.disclosureTxt) }
        return parts.joined(separator: ". ")
    }
}
