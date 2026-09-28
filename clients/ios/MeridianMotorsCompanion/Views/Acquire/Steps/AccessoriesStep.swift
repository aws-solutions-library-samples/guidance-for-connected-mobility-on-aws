import SwiftUI

/// Step 5 of ConfiguratorFlow — choose optional accessories.
///
/// Shows a checklist of catalog accessories with per-item price impact
/// and a running sub-total of selected accessories.
struct AccessoriesStep: View {
    let categoryId: String
    let accessories: [CatalogAccessory]
    let currencySymbol: String
    let theme: TenantTheme
    @Binding var selectedAccessoryIds: Set<String>
    let onContinue: () -> Void
    let onBack: () -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                backRow
                headerRow

                if visibleAccessories.isEmpty {
                    emptyPlaceholder
                } else {
                    ForEach(sortedAccessories) { acc in
                        accessoryRow(acc)
                    }
                }

                Divider().padding(.vertical, 4)
                subtotalRow

                Button(action: onContinue) {
                    Text("Continue")
                        .bold()
                        .frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(theme.primary)
                .padding(.top, 4)
            }
            .padding()
        }
    }

    // MARK: - Filtering

    /// Accessories are either universal (nil categoryId) or scoped to
    /// the current category. Show both.
    private var visibleAccessories: [CatalogAccessory] {
        accessories.filter { $0.categoryId == nil || $0.categoryId == categoryId }
    }

    private var sortedAccessories: [CatalogAccessory] {
        visibleAccessories.sorted { ($0.sortOrder ?? 999) < ($1.sortOrder ?? 999) }
    }

    private var accessorySubtotal: Double {
        sortedAccessories
            .filter { selectedAccessoryIds.contains($0.accessoryId) }
            .compactMap { $0.price }
            .reduce(0, +)
    }

    // MARK: - Subviews

    @ViewBuilder
    private var backRow: some View {
        Button(action: onBack) {
            Label("Back to colors", systemImage: "chevron.left")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)
    }

    @ViewBuilder
    private var headerRow: some View {
        Text("Add optional accessories")
            .font(.subheadline)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func accessoryRow(_ acc: CatalogAccessory) -> some View {
        let isSelected = selectedAccessoryIds.contains(acc.accessoryId)
        Button {
            if isSelected {
                selectedAccessoryIds.remove(acc.accessoryId)
            } else {
                selectedAccessoryIds.insert(acc.accessoryId)
            }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: isSelected ? "checkmark.square.fill" : "square")
                    .font(.title3)
                    .foregroundStyle(isSelected ? theme.primary : Color(.systemGray3))
                VStack(alignment: .leading, spacing: 2) {
                    Text(acc.displayName).font(.subheadline)
                    if let desc = acc.description {
                        Text(desc).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                Spacer()
                if let price = acc.price {
                    Text("+\(currencySymbol)\(Int(price))")
                        .font(.caption.bold())
                        .foregroundStyle(isSelected ? theme.primary : .secondary)
                }
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color(.secondarySystemGroupedBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .strokeBorder(
                                isSelected ? theme.primary : theme.primary.opacity(0.18),
                                lineWidth: isSelected ? 1.5 : 1
                            )
                    )
            )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var subtotalRow: some View {
        HStack {
            Text(selectedAccessoryIds.isEmpty ? "No accessories selected" : "\(selectedAccessoryIds.count) selected")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            if accessorySubtotal > 0 {
                Text("Accessories: +\(currencySymbol)\(Int(accessorySubtotal))")
                    .font(.caption.bold())
                    .foregroundStyle(theme.primary)
            }
        }
    }

    @ViewBuilder
    private var emptyPlaceholder: some View {
        VStack(spacing: 10) {
            Image(systemName: "wrench.fill")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("No accessories available")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        .padding(.top, 20)
    }
}
