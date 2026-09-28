import SwiftUI

/// A single action offered in the Home hero's action row.
///
/// Deliberately a value type with a *required* handler rather than an enum with a
/// switch at the render site. The reason is a defect class this app has already
/// shipped twice: a control whose backing capability does not exist (the
/// `novaUnresponsive` flag nothing read; the health tile that looked tappable
/// before `VehicleHealthDetailView` existed). If the handler is non-optional and
/// the row is built from handlers the caller actually holds, a dead button
/// cannot be expressed — the code will not compile without something to run.
///
/// Remote commands (lock/unlock/climate) are NOT represented here yet. They need
/// both a send path and a state read, and on the FWE simulation path the
/// presence-loop sidecar currently disconnects immediately after subscribing, so
/// a command would be accepted and never actuate. See spec
/// `2026-08-19-cms-vehicle-ecu-presence-resident`. When that lands, a remote
/// command becomes one more `HomeAction` — the row does not need redesigning,
/// which is the point of making this data-driven now.
struct HomeAction: Identifiable {
    /// Stable identity for tests and for `ForEach` — not the label, because the
    /// label is user-facing copy that may be reworded without changing meaning.
    let id: String
    let title: String
    let systemImage: String
    /// Rendered when the action is momentarily unavailable but still worth
    /// showing (e.g. an action that needs a connected vehicle). `nil` means
    /// available. A non-nil value dims the control and is surfaced to
    /// accessibility, so "why can't I tap this" has an answer on screen.
    let unavailableReason: String?
    let handler: () -> Void

    init(
        id: String,
        title: String,
        systemImage: String,
        unavailableReason: String? = nil,
        handler: @escaping () -> Void
    ) {
        self.id = id
        self.title = title
        self.systemImage = systemImage
        self.unavailableReason = unavailableReason
        self.handler = handler
    }

    var isAvailable: Bool { unavailableReason == nil }
}

/// Horizontal row of primary vehicle actions, sized to sit directly beneath the
/// vehicle image in the Home hero card.
///
/// Layout: evenly distributed circular icon buttons with captions underneath.
/// Four is the design target — five still fits on the narrowest supported width
/// (iPhone SE, 320pt) because each column is capped rather than fixed, but the
/// captions start truncating past that, so prefer promoting an action into the
/// row over growing the row.
struct HomeActionRow: View {
    let actions: [HomeAction]
    let theme: TenantTheme

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(actions) { action in
                Button {
                    action.handler()
                } label: {
                    VStack(spacing: 7) {
                        ZStack {
                            Circle()
                                .fill(theme.primary.opacity(action.isAvailable ? 0.12 : 0.05))
                                .frame(width: 46, height: 46)
                            Image(systemName: action.systemImage)
                                .font(.system(size: 19, weight: .medium))
                                .foregroundStyle(
                                    action.isAvailable ? theme.primary : Color.secondary
                                )
                        }
                        Text(action.title)
                            .font(.caption2)
                            .fontWeight(.medium)
                            // Two lines so "Book service" doesn't truncate to
                            // "Book ser…" at the default text size, and so the
                            // row keeps a single baseline across columns via
                            // the HStack's .top alignment.
                            .lineLimit(2)
                            .multilineTextAlignment(.center)
                            .foregroundStyle(action.isAvailable ? .primary : .secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.plain)
                .disabled(!action.isAvailable)
                .accessibilityLabel(
                    action.isAvailable
                        ? action.title
                        : "\(action.title), unavailable. \(action.unavailableReason ?? "")"
                )
                .accessibilityAddTraits(.isButton)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

#Preview {
    HomeActionRow(
        actions: [
            HomeAction(id: "health", title: "Health", systemImage: "heart.text.square") {},
            HomeAction(id: "service", title: "Book service", systemImage: "wrench.and.screwdriver") {},
            HomeAction(id: "ask", title: "Ask Meridian", systemImage: "sparkles") {},
            HomeAction(
                id: "unlock",
                title: "Unlock",
                systemImage: "lock.open",
                unavailableReason: "Vehicle is offline"
            ) {}
        ],
        theme: .fallback
    )
    .padding()
}
