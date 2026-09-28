import SwiftUI

/// Step 1 of ConfiguratorFlow — choose a vehicle category.
///
/// Renders a card grid backed by `AcquireCatalog.categories`.
/// Pre-selects `preselectedCategoryId` when arriving from a Discover handoff.
struct CategoryStep: View {
    let categories: [CatalogCategory]
    let preselectedCategoryId: String?
    let vehicleCategoryLabel: String
    let theme: TenantTheme
    let onSelect: (CatalogCategory) -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                Text("What kind of \(vehicleCategoryLabel.lowercased()) are you looking for?")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 4)

                if categories.isEmpty {
                    emptyPlaceholder
                } else {
                    ForEach(sortedCategories) { cat in
                        categoryCard(cat)
                    }
                }
            }
            .padding()
        }
    }

    private var sortedCategories: [CatalogCategory] {
        categories.sorted { ($0.sortOrder ?? 999) < ($1.sortOrder ?? 999) }
    }

    @ViewBuilder
    private func categoryCard(_ cat: CatalogCategory) -> some View {
        let isPreselected = cat.categoryId == preselectedCategoryId
        Button { onSelect(cat) } label: {
            HStack(spacing: 14) {
                Image(systemName: cat.symbolName ?? "tag.fill")
                    .font(.title2)
                    .foregroundStyle(theme.primary)
                    .frame(width: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text(cat.displayName).font(.headline)
                }
                Spacer()
                if isPreselected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(theme.primary)
                }
                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            }
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(.secondarySystemGroupedBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
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
    private var emptyPlaceholder: some View {
        VStack(spacing: 10) {
            Image(systemName: "rectangle.grid.2x2")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("Catalog loading…")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Text("You can ask the assistant to recommend a category while we load.")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 40)
    }
}
