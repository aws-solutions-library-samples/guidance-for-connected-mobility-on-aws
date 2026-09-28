import SwiftUI

/// Step 3 of ConfiguratorFlow — choose a variant / trim for the selected model.
struct VariantStep: View {
    let model: CatalogModel
    let variants: [CatalogVariant]
    let basePrice: Double?
    let currencySymbol: String
    let theme: TenantTheme
    let onSelect: (CatalogVariant) -> Void
    let onBack: () -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                backRow
                Text("Choose a trim for your \(model.displayName)")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)

                if filteredVariants.isEmpty {
                    emptyPlaceholder
                } else {
                    ForEach(sortedVariants) { variant in
                        variantCard(variant)
                    }
                }
            }
            .padding()
        }
    }

    private var filteredVariants: [CatalogVariant] {
        variants.filter { $0.modelId == model.modelId }
    }

    private var sortedVariants: [CatalogVariant] {
        filteredVariants.sorted { ($0.sortOrder ?? 999) < ($1.sortOrder ?? 999) }
    }

    @ViewBuilder
    private var backRow: some View {
        Button(action: onBack) {
            Label("Back to models", systemImage: "chevron.left")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)
    }

    @ViewBuilder
    private func variantCard(_ variant: CatalogVariant) -> some View {
        Button { onSelect(variant) } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(variant.displayName).font(.headline)
                        if let overrides = variant.specOverrides, !overrides.isEmpty {
                            Text(overrides.map { "\($0.key): \($0.value)" }.joined(separator: " · "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                    Spacer()
                    priceLabel(variant)
                }
            }
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(.secondarySystemGroupedBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .strokeBorder(theme.primary.opacity(0.18), lineWidth: 1)
                    )
            )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func priceLabel(_ variant: CatalogVariant) -> some View {
        if let base = basePrice, let adder = variant.priceAdder {
            let total = base + adder
            VStack(alignment: .trailing, spacing: 1) {
                Text("\(currencySymbol)\(Int(total))")
                    .font(.subheadline.bold())
                    .foregroundStyle(theme.primary)
                if adder != 0 {
                    let sign = adder > 0 ? "+" : ""
                    Text("\(sign)\(currencySymbol)\(Int(adder))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        } else {
            Image(systemName: "chevron.right").foregroundStyle(.tertiary)
        }
    }

    @ViewBuilder
    private var emptyPlaceholder: some View {
        VStack(spacing: 10) {
            Image(systemName: "square.3.layers.3d")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("No variants found")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        .padding(.top, 40)
    }
}
