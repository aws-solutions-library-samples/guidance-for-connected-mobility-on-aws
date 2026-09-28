import SwiftUI

/// Choose or change your dealer, without booking anything.
///
/// WHY THIS EXISTS
/// ---------------
/// The first cut of the preferred-dealer feature wired the card's "Change" button to the
/// booking flow, and offered adoption only on the booking SUCCESS step. That meant the only
/// way to change your dealer was to book a real service appointment — a five-step commitment
/// ending in a confirmation number — in order to edit a preference. Setting a preference and
/// scheduling work on your car are different intents and must not share a path.
///
/// So this is a read-only picker: it lists dealers, you choose one, it closes. Nothing is
/// booked, nothing is sent. The booking flow keeps its own adoption prompt, because a dealer
/// you have just committed to visiting is a natural thing to be asked about — but it is no
/// longer the ONLY way in.
///
/// WHY IT ASKS FOR A CAPABILITY IT DOES NOT USE
/// --------------------------------------------
/// `/find-service-center` requires a capability, so this sends a deliberately broad one
/// (`_browseCapability`) purely to enumerate the network. That is a wart of the endpoint,
/// not of the intent — the honest request would be "list dealers near me" and no such route
/// exists. Recorded here rather than hidden so the next person knows the parameter is
/// scaffolding, and so a future `/dealers` route has an obvious call site to replace.
struct DealerPickerSheet: View {
    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss
    let theme: TenantTheme

    @State private var centers: [ServiceCenter] = []
    @State private var isLoading = false
    @State private var errorText: String?

    /// Broad capability used only to enumerate the network. Every Meridian dealer lists it,
    /// so it filters nothing in practice. See the type's doc comment.
    ///
    /// MUST be a hyphenated backend token, matching `BookingCapability.backendCapability`.
    /// The first cut used `"oil change"` with a space, which is not a key in the Lambda's
    /// `_CAPABILITY_MAP` and is not a value in any centre's `capabilities` list — so it fell
    /// through the resolver unchanged and the capability filter dropped all 84 centres. The
    /// endpoint answered 200 with `found: 0`, which looks exactly like "no dealers near you"
    /// rather than like a bad parameter.
    private static let browseCapability = "oil-change"

    private var driverId: String? { session.vehicleContext?.driver?.driverId }
    private var current: PreferredDealer.Stored? { PreferredDealer.current(for: driverId) }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && centers.isEmpty {
                    ProgressView("Finding dealers near you")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let errorText, centers.isEmpty {
                    emptyState(errorText)
                } else if centers.isEmpty {
                    emptyState("No dealers found near you.")
                } else {
                    List {
                        Section {
                            ForEach(centers) { center in
                                row(center)
                            }
                        } footer: {
                            Text("Your dealer is used as the default when you book service. You can still choose a different one for any individual visit.")
                                .font(.caption)
                        }
                    }
                }
            }
            .navigationTitle(current == nil ? "Choose your dealer" : "Change your dealer")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task { await load() }
        }
    }

    // MARK: - Rows

    private func row(_ center: ServiceCenter) -> some View {
        let isCurrent = PreferredDealer.isPreferred(center, for: driverId)
        return Button {
            // Choosing is the whole interaction — store and close. No confirmation step,
            // because the action is trivially reversible: pick a different one.
            PreferredDealer.set(center, for: driverId)
            dismiss()
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: isCurrent ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isCurrent ? theme.primary : Color.secondary)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 3) {
                    Text(center.name).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                    Text(center.address).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let dist = center.distanceMiles {
                        Text(String(format: "%.1f mi away", dist))
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func emptyState(_ message: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: "building.2")
                .font(.largeTitle).foregroundStyle(.tertiary)
            Text(message)
                .font(.subheadline).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Load

    private func load() async {
        guard centers.isEmpty, !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        guard case .signedIn(let token, _) = session.authState else {
            errorText = "Sign in to choose a dealer."
            return
        }
        // Same coordinate fallback chain the booking flow uses, so both surfaces rank
        // against the same origin. Duplicated rather than shared because the booking flow's
        // copy is the one with the persona comment; a shared helper is the right cleanup and
        // is out of scope here.
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
                    // More than the booking flow's 3: this is a "who is out there" list, and
                    // choosing a long-term dealer from three options is not a choice.
                    maxResults: 5
                )
            )
            centers = PreferredDealer.pinPreferredFirst(resp.centers, for: driverId)
            PreferredDealer.refresh(from: resp.centers, for: driverId)
        } catch {
            errorText = error.localizedDescription
        }
    }
}
