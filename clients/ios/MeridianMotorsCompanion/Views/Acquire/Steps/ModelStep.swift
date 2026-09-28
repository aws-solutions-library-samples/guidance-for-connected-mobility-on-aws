import SwiftUI

/// Step 2 of ConfiguratorFlow — choose a base model within the selected category.
///
/// Shows a card grid with image placeholder, base price, and spec badges.
/// Pre-selects `preselectedModelId` when arriving from a Discover handoff.
struct ModelStep: View {
    let category: CatalogCategory
    let models: [CatalogModel]
    let preselectedModelId: String?
    let currencySymbol: String
    let assetBaseUrl: String?
    let theme: TenantTheme
    let onSelect: (CatalogModel) -> Void
    let onBack: () -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                backRow
                Text("Choose a \(category.displayName.lowercased()) model")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)

                if filteredModels.isEmpty {
                    emptyPlaceholder
                } else {
                    ForEach(sortedModels) { model in
                        modelCard(model)
                    }
                }
            }
            .padding()
        }
    }

    private var filteredModels: [CatalogModel] {
        models.filter { $0.categoryId == category.categoryId }
    }

    private var sortedModels: [CatalogModel] {
        filteredModels.sorted { ($0.sortOrder ?? 999) < ($1.sortOrder ?? 999) }
    }

    @ViewBuilder
    private var backRow: some View {
        Button(action: onBack) {
            Label("Back to categories", systemImage: "chevron.left")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)
    }

    @ViewBuilder
    private func modelCard(_ model: CatalogModel) -> some View {
        let isPreselected = model.modelId == preselectedModelId
        Button { onSelect(model) } label: {
            VStack(alignment: .leading, spacing: 10) {
                // Model image placeholder — real images are S3-hosted in production
                modelImageView(model)

                VStack(alignment: .leading, spacing: 4) {
                    Text(model.displayName).font(.headline)

                    if let price = model.basePrice {
                        Text("\(currencySymbol)\(Int(price))")
                            .font(.subheadline.bold())
                            .foregroundStyle(theme.primary)
                    }

                    if let badges = model.specBadges, !badges.isEmpty {
                        specBadgeRow(badges)
                    }
                }
                .padding(.horizontal, 4)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color(.secondarySystemGroupedBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(
                                isPreselected ? theme.primary : theme.primary.opacity(0.18),
                                lineWidth: isPreselected ? 2 : 1
                            )
                    )
            )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func modelImageView(_ model: CatalogModel) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(theme.primary.opacity(0.06))
                .frame(maxWidth: .infinity)
                .frame(height: 140)
            Image(systemName: "car.side.fill")
                .font(.system(size: 50))
                .foregroundStyle(theme.primary.opacity(0.35))
        }
    }

    @ViewBuilder
    private func specBadgeRow(_ badges: [CatalogModel.SpecBadge]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(badges.prefix(4), id: \.label) { badge in
                    HStack(spacing: 3) {
                        if let sym = badge.symbolName {
                            Image(systemName: sym).font(.caption2)
                        }
                        Text(badge.label).font(.caption2.bold())
                    }
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(Capsule().fill(theme.primary.opacity(0.12)))
                }
            }
        }
    }

    @ViewBuilder
    private var emptyPlaceholder: some View {
        VStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("No models found for \(category.displayName)")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding(.top, 40)
    }
}
