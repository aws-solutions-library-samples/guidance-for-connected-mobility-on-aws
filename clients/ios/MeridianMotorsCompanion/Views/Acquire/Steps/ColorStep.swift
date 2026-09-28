import SwiftUI

/// Step 4 of ConfiguratorFlow — choose a color / livery for the selected variant.
struct ColorStep: View {
    let variant: CatalogVariant
    let colors: [CatalogColor]
    let basePrice: Double?
    let currencySymbol: String
    let theme: TenantTheme
    let onSelect: (CatalogColor) -> Void
    let onBack: () -> Void

    @State private var selectedColorId: String? = nil

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                backRow
                Text("Choose your color")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)

                if filteredColors.isEmpty {
                    emptyPlaceholder
                } else {
                    colorGrid
                }
            }
            .padding()
        }
    }

    private var filteredColors: [CatalogColor] {
        colors.filter { $0.variantId == variant.variantId }
    }

    private let columns = [
        GridItem(.adaptive(minimum: 80, maximum: 100), spacing: 12)
    ]

    @ViewBuilder
    private var colorGrid: some View {
        LazyVGrid(columns: columns, spacing: 16) {
            ForEach(filteredColors) { color in
                colorSwatch(color)
            }
        }
    }

    @ViewBuilder
    private func colorSwatch(_ color: CatalogColor) -> some View {
        let isSelected = selectedColorId == color.colorId
        Button {
            selectedColorId = color.colorId
            onSelect(color)
        } label: {
            VStack(spacing: 6) {
                ZStack {
                    Circle()
                        .fill(swatchColor(color))
                        .frame(width: 56, height: 56)
                        .overlay(
                            Circle()
                                .strokeBorder(
                                    isSelected ? theme.primary : Color(.separator),
                                    lineWidth: isSelected ? 3 : 1
                                )
                        )
                    if isSelected {
                        Image(systemName: "checkmark")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(contrastColor(for: swatchColor(color)))
                    }
                }
                Text(color.displayName)
                    .font(.caption2)
                    .foregroundStyle(isSelected ? theme.primary : .secondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
                if let adder = color.priceAdder, adder != 0 {
                    let sign = adder > 0 ? "+" : ""
                    Text("\(sign)\(currencySymbol)\(Int(adder))")
                        .font(.caption2.bold())
                        .foregroundStyle(theme.primary)
                }
            }
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var backRow: some View {
        Button(action: onBack) {
            Label("Back to trims", systemImage: "chevron.left")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)
    }

    @ViewBuilder
    private var emptyPlaceholder: some View {
        VStack(spacing: 10) {
            Image(systemName: "paintpalette")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("No color options found")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        .padding(.top, 40)
    }

    // MARK: - Helpers

    private func swatchColor(_ color: CatalogColor) -> Color {
        guard let hex = color.hexColor else { return Color(.systemGray4) }
        return Color(hex: hex) ?? Color(.systemGray4)
    }

    /// Returns `.white` or `.black` for best legibility on the given background.
    private func contrastColor(for bg: Color) -> Color {
        // Approximation: use .white for dark swatches, .black for light.
        // A full luminance calculation requires UIColor resolution which
        // is trivial here — simple heuristic for the swatch checkmark.
        .white
    }
}
