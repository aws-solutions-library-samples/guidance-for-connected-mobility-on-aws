import SwiftUI

/// Lightweight order confirmation for the kiosk light-configuration path.
///
/// Replaces the full `ConfirmStep` (which carried accessories, review, and finance)
/// for the post-Zone-2-Revision-3 step machine. The kiosk path is:
///   Edition summary → colour → interior style → **this confirm** → success
///
/// ## firstName (task 5.5)
/// First name captured at Beat 0 is included in the reservation request when
/// present, and omitted cleanly (key absent) when not. **No surname field exists
/// anywhere in the payload.** The name is never logged.
///
/// Cross-repo dependency: adding `firstName` to the `order.placed` event that
/// Zone 3 subscribes to is a CVX-side change. This step ensures the client sends
/// it; see task 8.4.
///
/// Tap 4 of 4 from offer-accepted to order-placed.
struct LightConfirmStep: View {
    let edition: IvePackage
    let model: CatalogModel
    let variant: CatalogVariant
    let color: CatalogColor
    let interiorStyle: InteriorStyle
    let currencySymbol: String
    let tenantId: String
    let discoverSessionId: String?
    let leadId: String?
    /// First name from Beat 0 — first name only, never logged.
    let firstName: String?
    /// Accessory ids the visitor selected, sorted for stable display ordering.
    /// Empty when no accessories were chosen — the summary row is omitted in that case.
    let selectedAccessoryIds: [String]
    /// Handover method chosen at `.pickHandover`.
    let handoverMethod: HandoverMethod
    let theme: TenantTheme
    let onSuccess: (ReservationResponse) -> Void
    let onBack: () -> Void

    @Environment(\.adaptiveLayoutContext) private var layoutContext
    @State private var isSubmitting = false
    @State private var submitError: String?

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

                summaryCard
                if let err = submitError {
                    Text(err)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                confirmButton
            }
            .padding(layoutContext.isCompact ? 14 : 20)
        }
    }

    // MARK: - Summary

    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Order summary").font(.headline)
            row("Model", model.displayName)
            row("Trim", variant.displayName)
            row("Edition", edition.displayName)
            row("Colour", color.displayName)
            row("Interior", interiorStyle.displayName)
            if !selectedAccessoryIds.isEmpty {
                row("Accessories", selectedAccessoryIds.joined(separator: ", "))
            }
            // Show handover choice
            switch handoverMethod {
            case .dealerPickup(_, let name):
                row("Pickup", name)
            case .homeDelivery:
                row("Delivery", "Home delivery")
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    @ViewBuilder
    private func row(_ key: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(key)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 80, alignment: .leading)
            Text(value)
                .font(.subheadline)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: - Confirm button

    private var confirmButton: some View {
        Button {
            Task { await submit() }
        } label: {
            HStack {
                if isSubmitting { ProgressView().tint(.white) }
                else { Text("Confirm order").bold() }
            }
            .frame(maxWidth: .infinity, minHeight: 32)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(theme.primary)
        .disabled(isSubmitting)
    }

    // MARK: - Submission

    private func submit() async {
        isSubmitting = true
        submitError = nil
        defer { isSubmitting = false }

        try? await Task.sleep(nanoseconds: 700_000_000)

        let orderSeq = Int.random(in: 1000...9999)
        let dateStr: String = {
            let f = DateFormatter(); f.dateFormat = "yyyy-MM"; return f.string(from: Date())
        }()
        let orderNumber = "ORD-\(dateStr)-\(orderSeq)"
        let orderId = "ord_\(UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "").prefix(16))"

        // firstName: trim and include only when non-empty. Never logged.
        let trimmedFirst = firstName.map { $0.trimmingCharacters(in: .whitespaces) }
            .flatMap { $0.isEmpty ? nil : $0 }

        // Extract handover fields for the wire payload.
        let handoverMethodString: String?
        let handoverCenterId: String?
        switch handoverMethod {
        case .dealerPickup(let centerId, _):
            handoverMethodString = "pickup"
            handoverCenterId = centerId
        case .homeDelivery:
            handoverMethodString = "delivery"
            handoverCenterId = nil
        }

        // Build the reservation request with all Zone 2 fields.
        // The actual POST is mocked (endpoint not provisioned); the request shape
        // is correct and ready for when the service exists.
        _ = ReservationRequest(
            tenantId: tenantId,
            modelId: model.modelId,
            variantId: variant.variantId,
            colorId: color.colorId,
            selectedAccessoryIds: selectedAccessoryIds,
            depositRef: nil,
            qualificationTier: nil,
            discoverSessionId: discoverSessionId,
            leadId: leadId,
            edition: edition.rawValue,
            interiorStyle: interiorStyle.rawValue,
            firstName: trimmedFirst,
            handoverMethod: handoverMethodString,
            handoverCenterId: handoverCenterId
        )

        let resp = ReservationResponse(
            orderId: orderId,
            orderNumber: orderNumber,
            depositRef: nil
        )
        onSuccess(resp)
    }
}
