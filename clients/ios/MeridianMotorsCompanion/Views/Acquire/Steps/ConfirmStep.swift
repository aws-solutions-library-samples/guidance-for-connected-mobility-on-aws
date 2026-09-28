import SwiftUI

/// Step 8 of ConfiguratorFlow — confirm the reservation.
///
/// Calls `reservation_handoff()` (mocked in this window) which writes the
/// STAR-shaped order record and returns an order number.
/// On success, transitions to the Success step.
struct ConfirmStep: View {
    let model: CatalogModel
    let variant: CatalogVariant
    let color: CatalogColor
    let selectedAccessories: [CatalogAccessory]
    let qualification: FinanceQualification
    let currencySymbol: String
    let tenantId: String
    let discoverSessionId: String?
    let leadId: String?
    let theme: TenantTheme
    let onSuccess: (ReservationResponse) -> Void
    let onBack: () -> Void

    @State private var isSubmitting: Bool = false
    @State private var submitError: String? = nil

    private var configuredPrice: Double {
        let base = model.basePrice ?? 0
        let variantAdder = variant.priceAdder ?? 0
        let colorAdder = color.priceAdder ?? 0
        let accTotal = selectedAccessories.compactMap { $0.price }.reduce(0, +)
        return base + variantAdder + colorAdder + accTotal
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                backRow
                summaryCard
                depositCard
                if let err = submitError {
                    Text(err).font(.caption).foregroundStyle(.red)
                }
                confirmButton
            }
            .padding()
        }
    }

    // MARK: - Subviews

    @ViewBuilder
    private var backRow: some View {
        Button(action: onBack) {
            Label("Back to finance", systemImage: "chevron.left")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Order summary").font(.headline)
            row("Model", model.displayName)
            row("Trim", variant.displayName)
            row("Color", color.displayName)
            if !selectedAccessories.isEmpty {
                row("Accessories", selectedAccessories.map { $0.displayName }.joined(separator: ", "))
            }
            row("Finance tier", qualification.tier)
            row("Rate range", String(format: "%.1f%%–%.1f%% APR", qualification.rateRangeMin, qualification.rateRangeMax))
            Divider()
            HStack {
                Text("Configured price").font(.subheadline.bold())
                Spacer()
                Text("\(currencySymbol)\(Int(configuredPrice))")
                    .font(.subheadline.bold())
                    .foregroundStyle(theme.primary)
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    @ViewBuilder
    private var depositCard: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Due now (refundable deposit)")
                    .font(.subheadline)
                Text("Refundable until manufacturing stage begins.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text("\(currencySymbol)\(Int(qualification.depositAmount))")
                .font(.title3.bold())
                .foregroundStyle(theme.primary)
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.tertiarySystemGroupedBackground))
        )
    }

    @ViewBuilder
    private var confirmButton: some View {
        Button {
            Task { await submit() }
        } label: {
            HStack {
                if isSubmitting { ProgressView().tint(.white) }
                else { Text("Confirm & place order").bold() }
            }
            .frame(maxWidth: .infinity, minHeight: 32)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(theme.primary)
        .disabled(isSubmitting)
    }

    @ViewBuilder
    private func row(_ key: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(key).font(.caption).foregroundStyle(.secondary).frame(width: 100, alignment: .leading)
            Text(value).font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: - Submission (mock — endpoint not provisioned in this window)

    private func submit() async {
        isSubmitting = true
        submitError = nil
        defer { isSubmitting = false }

        // Endpoint not yet provisioned — use local mock that returns the
        // same shape as a real ReservationResponse.
        try? await Task.sleep(nanoseconds: 900_000_000)

        // Build a deterministic fake order number from the model + timestamp.
        let orderSeq = Int.random(in: 1000...9999)
        let dateStr = {
            let f = DateFormatter(); f.dateFormat = "yyyy-MM"; return f.string(from: Date())
        }()
        let orderNumber = "ORD-\(dateStr)-\(orderSeq)"
        let orderId = "ord_\(UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "").prefix(16))"

        let resp = ReservationResponse(
            orderId: orderId,
            orderNumber: orderNumber,
            depositRef: "mock-dep-\(orderSeq)"
        )
        onSuccess(resp)
    }
}
