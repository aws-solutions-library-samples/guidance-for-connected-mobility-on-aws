import SwiftUI

/// Light-configuration step: choose how the vehicle reaches the customer.
///
/// ## Two options
/// - **Collect at dealer** — reads the driver's `PreferredDealer`. When none
///   is stored, shows "Choose a dealer" which expands an inline list in place —
///   deliberately not a sheet; see `isChoosingDealer`;
///   on selection it becomes the pickup target. Home delivery is always available
///   so the step never dead-ends (spec § Risks R4).
/// - **Home delivery** — always available. No date is shown here; an
///   estimated date is narrative — the delivery proposal is where the estimate lives.
///
/// ## No capacity-implying language
/// Vocabulary implying dealer capacity this system does not model is absent from
/// all UI strings in this view. Delivery dates are narrative; no commitment is made
/// regarding when or how a handover is arranged.
///
/// Styled consistently with `InteriorStyleStep`.
struct HandoverStep: View {
    /// Driver id for reading `PreferredDealer`. Passed in rather than read from
    /// the environment so the view is testable without `AppSession`.
    let driverId: String?
    let theme: TenantTheme
    let onSelect: (HandoverMethod) -> Void
    let onBack: () -> Void

    @Environment(\.adaptiveLayoutContext) private var layoutContext
    /// Needed only by the inline dealer lookup. `driverId` stays an injected property so
    /// the option cards remain testable without an `AppSession`.
    @Environment(AppSession.self) private var session

    /// Capability used purely to enumerate nearby centres.
    ///
    /// Matches `DealerPickerSheet.browseCapability` verbatim: the endpoint requires a
    /// capability and has no "any" value, so both surfaces must pass the same one or they
    /// rank different dealer sets for the same driver.
    private static let browseCapability = "oil-change"

    /// Whether the inline dealer chooser is expanded.
    ///
    /// **Inline, not a sheet.** This was a `.sheet(DealerPickerSheet)` and it broke the
    /// flow: choosing a dealer returned the visitor to "Your upgrade", i.e. `UpgradeFlow`
    /// was being torn down and rebuilt with fresh `@State`. In `.hosted` chrome this step
    /// already sits three presentations deep — `HomeTabView.sheet(showOfferSheet)` ->
    /// `fullScreenCover(selectedOffer)` -> hosted `ConfiguratorFlow` — and `ConfiguratorFlow`
    /// itself already owns a `.sheet` for order routing. `HomeTabView`'s own comment
    /// records the rule this collides with: *"SwiftUI honours only one presentation
    /// modifier per view — a second one on the same view silently never presents"*, which
    /// is why its cover is attached to the banner rather than the body.
    ///
    /// A wizard step should not open a modal anyway. Expanding in place removes the
    /// presentation level entirely rather than working around its behaviour, and keeps the
    /// visitor inside the stepped flow they are already in.
    @State private var isChoosingDealer = false
    @State private var centers: [ServiceCenter] = []
    @State private var isLoadingCenters = false
    @State private var centersError: String? = nil

    /// Dealer chosen this session (before any navigation back). When set it
    /// becomes the pickup target even if `PreferredDealer.current` doesn't yet
    /// reflect it (the sheet calls `PreferredDealer.set` and then dismisses,
    /// which writes to UserDefaults but does NOT trigger a re-read of the stored
    /// value here — we track it in State so the row updates immediately).
    @State private var selectedDealer: PreferredDealer.Stored? = nil

    /// Effective preferred dealer: session choice takes priority over the stored one.
    private var effectiveDealer: PreferredDealer.Stored? {
        selectedDealer ?? PreferredDealer.current(for: driverId)
    }

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

                Text("Choose delivery method")
                    .font(.headline)

                // --- Option 1: Dealer pickup ---
                dealerPickupCard

                // --- Option 2: Home delivery (always available) ---
                homeDeliveryCard
            }
            .padding(layoutContext.isCompact ? 14 : 20)
        }
    }

    // MARK: - Dealer pickup card

    private var dealerPickupCard: some View {
        Group {
            if let dealer = effectiveDealer {
                // Preferred dealer is known — offer it as the pickup option.
                Button {
                    onSelect(.dealerPickup(centerId: dealer.centerId, name: dealer.name))
                } label: {
                    optionCard(
                        symbol: "building.2",
                        title: dealer.name,
                        subtitle: dealer.address,
                        badge: "Collect here"
                    )
                }
                .buttonStyle(.plain)
            } else {
                // No dealer stored — invite the visitor to choose one.
                Button {
                    isChoosingDealer = true
                } label: {
                    optionCard(
                        symbol: "building.2.crop.circle",
                        title: "Choose a dealer",
                        subtitle: "Your vehicle ships to the dealer either way. Choose one to collect from.",
                        badge: nil
                    )
                }
                .buttonStyle(.plain)
            }

            if isChoosingDealer {
                inlineDealerChooser
            }
        }
    }

    /// The dealer list, rendered in place beneath the pickup card.
    @ViewBuilder
    private var inlineDealerChooser: some View {
        VStack(alignment: .leading, spacing: 8) {
            if isLoadingCenters {
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Finding dealers near you").font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 6)
            } else if let centersError {
                // Degrade with a reason rather than an empty list: home delivery is still
                // selectable, so the step never dead-ends.
                Text(centersError)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ForEach(centers, id: \.centerId) { center in
                    Button {
                        // Persist the choice, then select it as the handover target.
                        let stored = PreferredDealer.set(center, for: driverId)
                        selectedDealer = stored ?? PreferredDealer.current(for: driverId)
                        isChoosingDealer = false
                        onSelect(.dealerPickup(centerId: center.centerId, name: center.name))
                    } label: {
                        optionCard(symbol: "building.2",
                                   title: center.name,
                                   subtitle: center.address,
                                   badge: nil)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .task(id: isChoosingDealer) {
            guard isChoosingDealer else { return }
            await loadCenters()
        }
    }

    /// Nearby service centres for the browse capability.
    ///
    /// Mirrors `DealerPickerSheet.load()`'s coordinate-fallback chain, which that file
    /// already notes is itself duplicated from `BookingFlow` with a shared helper marked as
    /// the right cleanup. A third copy is not an improvement; it is here only because
    /// removing the modal is the bug fix and extracting the helper is a separate change.
    /// If a fourth caller appears, extract before copying again.
    private func loadCenters() async {
        guard centers.isEmpty, !isLoadingCenters else { return }
        isLoadingCenters = true
        defer { isLoadingCenters = false }
        guard case .signedIn(let token, _) = session.authState else {
            centersError = "Sign in to choose a dealer."
            return
        }
        let lat = session.liveState?.latitude ?? session.currentVehicle?.lastLatitude ?? 0
        let lng = session.liveState?.longitude ?? session.currentVehicle?.lastLongitude ?? 0
        let make = session.currentVehicle?.make ?? ""
        do {
            let client = VSAClient(idTokenProvider: { token })
            let resp = try await client.findServiceCenter(
                FindServiceCenterRequest(
                    capability: Self.browseCapability,
                    latitude: lat,
                    longitude: lng,
                    segment: session.layoutSegment.rawValue,
                    vehicleMake: make.isEmpty ? nil : make,
                    maxResults: 5
                )
            )
            centers = PreferredDealer.pinPreferredFirst(resp.centers, for: driverId)
        } catch {
            centersError = "Could not load dealers. Home delivery is still available."
        }
    }

    // MARK: - Home delivery card

    private var homeDeliveryCard: some View {
        Button {
            onSelect(.homeDelivery)
        } label: {
            optionCard(
                symbol: "house",
                title: "Home delivery",
                subtitle: "Delivered to your address after arriving at the dealer.",
                badge: nil
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Shared card layout

    private func optionCard(
        symbol: String,
        title: String,
        subtitle: String,
        badge: String?
    ) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(theme.primary)
                .frame(width: 32)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(title)
                        .font(.subheadline.bold())
                    if let badge {
                        Text(badge)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(theme.primary)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(
                                Capsule().fill(theme.primary.opacity(0.1))
                            )
                    }
                }
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(Color(.tertiaryLabel))
        }
        .padding(layoutContext.isCompact ? 12 : 14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(theme.primary.opacity(0.12), lineWidth: 1)
        )
    }
}
