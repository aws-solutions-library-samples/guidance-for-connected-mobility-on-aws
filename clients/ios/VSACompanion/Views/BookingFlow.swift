import SwiftUI

/// Native multi-step booking flow presented as a sheet from the
/// Service / Dealer tab. Three steps:
///
///   1. Pick a service capability  (cards: oil change, tire pressure, brakes, …)
///   2. Pick a center + slot       (calls /find-service-center, lists 1-3 options)
///   3. Confirm                    (calls /book, shows requestNumber on success)
///
/// Persona-aware via `session.layoutSegment`:
///   - fleet  → all capability cards visible, broad center mix
///   - oem    → all capability cards, dealers-only filter on the backend
///   - rental → simplified capability list (renters can't book major work),
///              chains-first center list
///
/// The flow is intentionally *not* a NavigationStack — each step is a
/// dedicated view layered in a switch on currentStep, so there's no
/// nav-bar churn and back/cancel always go to clear destinations.
struct BookingFlow: View {
    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss
    let theme: TenantTheme

    /// Step machine. Linear forward; `dismiss()` from any step returns
    /// to the Service tab.
    private enum Step {
        case pickCapability
        case pickCenterAndSlot(BookingCapability)
        case confirm(BookingDraft)
        case success(BookResponse)
    }
    @State private var step: Step = .pickCapability

    /// Network state for the center-list step.
    @State private var centersLoading: Bool = false
    @State private var centersError: String? = nil
    @State private var centers: [ServiceCenter] = []

    /// Network state for the confirm step.
    @State private var confirmInFlight: Bool = false
    @State private var confirmError: String? = nil

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                switch step {
                case .pickCapability:
                    capabilityPicker
                case .pickCenterAndSlot(let capability):
                    centerAndSlotPicker(capability: capability)
                case .confirm(let draft):
                    confirmStep(draft: draft)
                case .success(let response):
                    successStep(response: response)
                }
            }
            .navigationTitle(navTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Close") { dismiss() }
                }
            }
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
        }
    }

    private var navTitle: String {
        switch step {
        case .pickCapability:        return "What's the issue?"
        case .pickCenterAndSlot:     return "Pick a location"
        case .confirm:               return "Confirm booking"
        case .success:               return "Booked"
        }
    }

    // MARK: - Step 1: capability picker

    @ViewBuilder
    private var capabilityPicker: some View {
        ScrollView {
            VStack(spacing: 12) {
                Text("Pick the service you need. We'll find a nearby \(centerNoun) and a time that works.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 4)
                ForEach(visibleCapabilities()) { cap in
                    Button {
                        step = .pickCenterAndSlot(cap)
                        Task { await loadCenters(for: cap) }
                    } label: {
                        capabilityCard(cap)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding()
        }
    }

    /// Capabilities offered to the driver, filtered by persona. Rental
    /// gets a smaller list because renters can't authorize major
    /// work — anything heavier than the listed quick fixes routes
    /// through the rental company.
    private func visibleCapabilities() -> [BookingCapability] {
        switch session.layoutSegment {
        case .rental:
            return BookingCapability.allCases.filter { $0.rentalAllowed }
        default:
            return BookingCapability.allCases
        }
    }

    @ViewBuilder
    private func capabilityCard(_ cap: BookingCapability) -> some View {
        HStack(spacing: 14) {
            Image(systemName: cap.symbolName)
                .font(.title2)
                .foregroundStyle(theme.primary)
                .frame(width: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(cap.label).font(.headline)
                Text(cap.subtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: "chevron.right")
                .foregroundStyle(.tertiary)
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(theme.primary.opacity(0.18), lineWidth: 1)
                )
        )
    }

    // MARK: - Step 2: center list + slot pick (combined)

    @ViewBuilder
    private func centerAndSlotPicker(capability: BookingCapability) -> some View {
        ScrollView {
            VStack(spacing: 14) {
                if centersLoading {
                    ProgressView("Finding nearby \(centerNoun)s…")
                        .padding(.top, 40)
                } else if let err = centersError {
                    errorCard(message: err) {
                        Task { await loadCenters(for: capability) }
                    }
                } else if centers.isEmpty {
                    emptyCard(
                        title: "No nearby \(centerNoun)s",
                        subtitle: "We didn't find any \(centerNoun.lowercased()) that handle \(capability.label.lowercased()) near your current location. Try a different service or use the voice assistant for a wider search."
                    )
                } else {
                    ForEach(centers) { center in
                        centerCard(center: center, capability: capability)
                    }
                }
            }
            .padding()
        }
    }

    @ViewBuilder
    private func centerCard(center: ServiceCenter, capability: BookingCapability) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(center.name).font(.headline).lineLimit(2)
                    Text(center.address).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer()
                if let dist = center.distanceMiles {
                    Text("\(dist, specifier: "%.1f") mi")
                        .font(.caption.bold())
                        .foregroundStyle(theme.primary)
                }
            }
            HStack(spacing: 10) {
                if let rating = center.rating, rating > 0 {
                    Label(String(format: "%.1f", rating), systemImage: "star.fill")
                        .font(.caption2).foregroundStyle(.orange)
                }
                Text(typeLabel(center.type))
                    .font(.caption2.bold())
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Capsule().fill(theme.primary.opacity(0.15)))
                if center.fleetDiscount {
                    Text("FLEET")
                        .font(.caption2.bold())
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Capsule().fill(.green.opacity(0.18)))
                        .foregroundStyle(.green)
                }
            }
            if center.nextAvailableSlots.isEmpty {
                Text("No upcoming slots available — call ahead.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("Pick a time").font(.caption.bold()).foregroundStyle(.secondary)
                VStack(spacing: 6) {
                    ForEach(Array(center.nextAvailableSlots.enumerated()), id: \.offset) { _, slot in
                        Button {
                            let draft = BookingDraft(
                                capability: capability,
                                center: center,
                                slot: slot
                            )
                            step = .confirm(draft)
                        } label: {
                            HStack {
                                Image(systemName: "clock")
                                Text(slot).font(.subheadline)
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                            }
                            .padding(.vertical, 8).padding(.horizontal, 10)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(theme.primary.opacity(0.08))
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(theme.primary.opacity(0.18), lineWidth: 1)
                )
        )
    }

    // MARK: - Step 3: confirm

    @ViewBuilder
    private func confirmStep(draft: BookingDraft) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Review your booking").font(.headline)
                rowKV("Service", draft.capability.label)
                rowKV("\(centerNoun.capitalized)", draft.center.name)
                rowKV("Address", draft.center.address)
                rowKV("Time", draft.slot)
                if let phone = draft.center.phone {
                    rowKV("Phone", phone)
                }
                if let err = confirmError {
                    Text(err).font(.caption).foregroundStyle(.red)
                }
                Button {
                    Task { await confirm(draft: draft) }
                } label: {
                    HStack {
                        if confirmInFlight {
                            ProgressView().tint(.white)
                        } else {
                            Text("Confirm booking").bold()
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(theme.primary)
                .disabled(confirmInFlight)
                .padding(.top, 6)
            }
            .padding()
        }
    }

    @ViewBuilder
    private func rowKV(_ key: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(key).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.subheadline)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 10).fill(Color(.tertiarySystemGroupedBackground))
        )
    }

    // MARK: - Step 4: success

    @ViewBuilder
    private func successStep(response: BookResponse) -> some View {
        VStack(spacing: 18) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.green)
                .padding(.top, 40)
            Text("You're booked").font(.title2.bold())
            Text("\(response.centerName) — \(response.slot)")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Text("Confirmation #\(response.requestNumber)")
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
            Spacer()
            Button("Done") { dismiss() }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(theme.primary)
                .padding(.bottom, 24)
        }
        .frame(maxWidth: .infinity)
        .padding()
    }

    // MARK: - Helpers

    /// Singular noun used in copy: "service center" for fleet/rental,
    /// "dealer" for OEM. Keeps the wording matched to the persona
    /// without scattering segment checks throughout the view.
    private var centerNoun: String {
        session.layoutSegment == .oem ? "dealer" : "service center"
    }

    private func typeLabel(_ raw: String) -> String {
        switch raw {
        case "dealer":           return "DEALER"
        case "independent":      return "INDEPENDENT"
        case "fleet-service":    return "FLEET"
        case "quick-service":    return "QUICK"
        case "body-shop":        return "BODY"
        case "tire-specialist":  return "TIRE"
        default:                 return raw.uppercased()
        }
    }

    @ViewBuilder
    private func errorCard(message: String, retry: @escaping () -> Void) -> some View {
        VStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundStyle(.orange)
            Text("Couldn't load \(centerNoun)s").font(.subheadline.bold())
            Text(message).font(.caption).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Try again", action: retry)
                .buttonStyle(.bordered)
        }
        .padding()
    }

    @ViewBuilder
    private func emptyCard(title: String, subtitle: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)
            Text(title).font(.subheadline.bold())
            Text(subtitle).font(.caption).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
    }

    // MARK: - Network calls

    private func loadCenters(for capability: BookingCapability) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        let lat = session.liveState?.latitude
            ?? session.currentVehicle?.lastLatitude
            ?? 0
        let lng = session.liveState?.longitude
            ?? session.currentVehicle?.lastLongitude
            ?? 0
        // Per persona: OEM filters by the vehicle's make on the backend
        // anyway, but passing the make here ensures the filter applies
        // consistently when iOS adds rental/fleet-specific tweaks later.
        let make = session.currentVehicle?.make ?? ""
        centersLoading = true
        centersError = nil
        defer { centersLoading = false }
        do {
            let client = VSAClient(idTokenProvider: { token })
            let req = FindServiceCenterRequest(
                capability: capability.backendCapability,
                latitude: lat,
                longitude: lng,
                segment: session.layoutSegment.rawValue,
                vehicleMake: make.isEmpty ? nil : make,
                maxResults: 3
            )
            let resp = try await client.findServiceCenter(req)
            centers = resp.centers
        } catch {
            centersError = error.localizedDescription
        }
    }

    private func confirm(draft: BookingDraft) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        confirmInFlight = true
        confirmError = nil
        defer { confirmInFlight = false }
        do {
            let client = VSAClient(idTokenProvider: { token })
            let req = BookRequest(
                vehicleId: session.effectiveVehicleId,
                vin: session.effectiveVin,
                driverId: session.effectiveDriverId,
                tenantId: session.activeTenantId,
                centerId: draft.center.centerId,
                centerName: draft.center.name,
                centerAddress: draft.center.address,
                slot: draft.slot,
                capability: draft.capability.backendCapability,
                reportedSymptom: draft.capability.label,
                narrative: "Native-app booking: \(draft.capability.label) at \(draft.center.name) for \(draft.slot)."
            )
            let resp = try await client.book(req)
            step = .success(resp)
            // Refresh service history in the background so the new
            // booking shows up under "Upcoming" when the user dismisses.
            Task {
                await session.loadServiceHistory(
                    client: VSAClient(idTokenProvider: { token }),
                    force: true
                )
            }
        } catch {
            confirmError = error.localizedDescription
        }
    }
}

/// Service capabilities offered in the native booking picker. The
/// backend's capability_map fuzzy-matches loose strings, but we keep
/// the labels and backendCapability values explicit so the UI doesn't
/// drift from the data.
enum BookingCapability: String, CaseIterable, Identifiable {
    case oilChange
    case tirePressure
    case brakes
    case battery
    case engineWarning
    case bodyWork

    var id: String { rawValue }

    /// Human-readable label shown on the capability card.
    var label: String {
        switch self {
        case .oilChange:      return "Oil change"
        case .tirePressure:   return "Tire pressure / inflation"
        case .brakes:         return "Brakes"
        case .battery:        return "Battery / electrical"
        case .engineWarning:  return "Check engine / diagnostics"
        case .bodyWork:       return "Body damage / dent"
        }
    }

    /// One-line description shown under the label.
    var subtitle: String {
        switch self {
        case .oilChange:      return "Routine oil & filter service"
        case .tirePressure:   return "Inflate, rotate, or replace tires"
        case .brakes:         return "Squeal, fade, ABS warning"
        case .battery:        return "Won't start, low voltage, charging"
        case .engineWarning:  return "Diagnose check-engine codes"
        case .bodyWork:       return "Dents, paint, glass"
        }
    }

    /// SF Symbol for the card icon.
    var symbolName: String {
        switch self {
        case .oilChange:      return "drop.fill"
        case .tirePressure:   return "tire"
        case .brakes:         return "octagon"
        case .battery:        return "bolt.fill"
        case .engineWarning:  return "exclamationmark.triangle.fill"
        case .bodyWork:       return "hammer.fill"
        }
    }

    /// Value sent to the backend's capability field. Matches the
    /// backend's capability_map keys so the fuzzy match resolves
    /// cleanly to a "tires" / "engine" / etc. bucket.
    var backendCapability: String {
        switch self {
        case .oilChange:      return "oil-change"
        case .tirePressure:   return "tire-pressure"
        case .brakes:         return "brakes"
        case .battery:        return "battery"
        case .engineWarning:  return "diagnostics"
        case .bodyWork:       return "body-work"
        }
    }

    /// Whether this capability is bookable for rental drivers. Major
    /// repairs (engine, brakes, body work) are gated to the rental
    /// company's own process; tire/oil/battery quick fixes are fine
    /// for renters to self-book.
    var rentalAllowed: Bool {
        switch self {
        case .oilChange, .tirePressure, .battery: return true
        case .brakes, .engineWarning, .bodyWork:  return false
        }
    }
}

/// Intermediate state held between Step 2 (pick a slot at a center)
/// and Step 3 (confirm). Everything the /book request needs is
/// derivable from this + AppSession's vehicle/driver fields.
struct BookingDraft: Equatable {
    let capability: BookingCapability
    let center: ServiceCenter
    let slot: String
}
