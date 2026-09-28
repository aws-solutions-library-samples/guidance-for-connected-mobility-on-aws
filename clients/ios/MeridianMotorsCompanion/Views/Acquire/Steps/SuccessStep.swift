import SwiftUI

/// Step 9 of ConfiguratorFlow — success screen after a reservation is placed.
///
/// Shows the order number, offers a link to `OrderTrackerView`, and provides
/// a secondary "Book a test drive" CTA.
struct SuccessStep: View {
    let reservation: ReservationResponse
    let vehicleCategoryLabel: String
    let theme: TenantTheme
    let onOpenOrderTracker: (String) -> Void   // passes orderId
    /// Hands the test ride to the agent, which owns the `book` tool.
    ///
    /// Previously documented as "opens BookingFlow via Service stage" while the
    /// call site did nothing but `dismiss()`. Both were wrong: `BookingFlow` is
    /// the service-appointment sheet, and a test ride is a sales action.
    /// Optional: a test drive is booked by handing off to the assistant, so this is nil
    /// when no assistant is plumbed.
    ///
    /// **Nil hides the button.** It previously fell back to `dismiss()`, which meant a
    /// button labelled "Book a test drive" silently returned the visitor to Home — the
    /// reported behaviour. A missing wire should read as an absent affordance, not as
    /// surprise navigation: the first is obviously incomplete, the second looks like the
    /// app deciding to leave.
    var onBookTestRide: (() -> Void)? = nil
    let onDone: () -> Void                     // dismisses ConfiguratorFlow

    var body: some View {
        VStack(spacing: 20) {
            Spacer()

            // Success icon
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 64))
                .foregroundStyle(.green)

            Text("Your reservation is placed")
                .font(.title2.bold())
                .multilineTextAlignment(.center)

            Text("Order #\(reservation.orderNumber)")
                .font(.callout.monospaced())
                .foregroundStyle(.secondary)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color(.tertiarySystemGroupedBackground))
                )

            if let depositRef = reservation.depositRef {
                Text("Deposit ref: \(depositRef)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text("You can track your \(vehicleCategoryLabel.lowercased()) as it moves through manufacturing and delivery.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 20)

            Spacer()

            VStack(spacing: 12) {
                // Primary CTA — track the order
                Button {
                    onOpenOrderTracker(reservation.orderId)
                } label: {
                    Label("Track your order", systemImage: "map.fill")
                        .bold()
                        .frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(theme.primary)

                // Secondary CTA — book a test ride via the existing book() path.
                // Rendered only when there is somewhere for it to go.
                if let onBookTestRide {
                Button {
                    onBookTestRide()
                } label: {
                    Label("Book a test drive", systemImage: "calendar.badge.plus")
                        .frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .tint(theme.primary)
                }

                Button("Done") { onDone() }
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
            }
            .padding(.horizontal)
            .padding(.bottom, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemGroupedBackground).ignoresSafeArea())
    }
}
