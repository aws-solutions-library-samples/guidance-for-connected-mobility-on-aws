import SwiftUI
import MapKit

/// Vehicle tab — real CMS-backed view of Stephanie's Chevrolet Equinox.
///
/// Primary data source is `session.currentVehicle` (loaded via /drivers/me
/// at sign-in). The mock `TelemetryFrame` is retained only as a fallback
/// for fields that CMS doesn't aggregate back onto the vehicle record
/// today (tire pressures, ABS status).
///
/// Sections (top to bottom):
///   - Map: last known location with a marker and metadata
///   - Vehicle identity: make/model/year, VIN, plate, color, type, fleet
///   - Live signals: speed, coolant temp, fuel, engine temp, battery
///   - Tires: PSI readings from the mock frame (CMS telemetry path TBD)
struct VehicleTabView: View {
    let frame: TelemetryFrame
    let theme: TenantTheme
    @Environment(AppSession.self) private var session

    var body: some View {
        NavigationStack {
            ScrollView {
                if session.hasLoadedInitialVehicle {
                    VStack(alignment: .leading, spacing: 16) {
                        mapCard
                        identityCard
                        liveSignalsCard
                        activeDtcsCard
                        tiresCard
                    }
                    .padding()
                } else {
                    // Real Vehicle tab leads with a map card + identity
                    // + several signal cards. Five card placeholders
                    // roughly reserves that space.
                    TabLoadingSkeleton(cardCount: 5)
                }
            }
            .refreshable { await refresh(force: true) }
            .task { await refresh(force: false) }
            .navigationTitle("Vehicle")
            .navigationBarTitleDisplayMode(.inline)
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
        }
    }

    private func refresh(force: Bool) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        let client = VSAClient(idTokenProvider: { token })
        await session.loadCurrentDriver(client: client, force: force)
        // Live state is cheap (single Redis read) — always refresh on tab
        // open so the badge is accurate at first render.
        await session.loadLiveState(client: client, force: force)
    }

    // MARK: - Map card

    @ViewBuilder
    private var mapCard: some View {
        SectionCard("Last Known Location", theme: theme) {
            if let coord = vehicleCoordinate {
                Map(position: .constant(.region(MKCoordinateRegion(
                    center: coord,
                    // Tighter zoom than the mock-frame version — city-level
                    // rather than regional so the marker is the focus.
                    span: MKCoordinateSpan(latitudeDelta: 0.04, longitudeDelta: 0.04))))) {
                    Marker(markerLabel, coordinate: coord)
                        .tint(theme.primary)
                }
                .frame(height: 220)
                .clipShape(RoundedRectangle(cornerRadius: 10))

                // Metadata line: coords + last-seen + current speed
                HStack(alignment: .firstTextBaseline) {
                    Text(String(format: "%.4f, %.4f", coord.latitude, coord.longitude))
                        .font(.caption2).monospaced().foregroundStyle(.secondary)
                        .lineLimit(1)
                    Spacer()
                    if let speed = session.currentVehicle?.lastSpeed ?? frame.speedMph {
                        Image(systemName: "gauge.with.needle")
                            .font(.caption2).foregroundStyle(theme.primary.opacity(0.7))
                        Text(String(format: "%.0f mph", speed))
                            .font(.caption).monospacedDigit()
                    }
                }
                // Prefer the Redis-backed live-state timestamp for the
                // same reason HomeTabView does — the DDB `lastSeenAt`
                // lags the ingestion pipeline by days. See the identical
                // fallback in HomeTabView.vehicleCard.
                if let live = session.liveState, let connectedAt = live.lastConnectedAt {
                    HStack(spacing: 6) {
                        Image(systemName: "clock").font(.caption2).foregroundStyle(.tertiary)
                        Text("Last seen \(relativeTimeString(from: connectedAt))")
                            .font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        connectionBadge
                    }
                } else if let lastSeen = session.currentVehicle?.lastSeenAt {
                    HStack(spacing: 6) {
                        Image(systemName: "clock").font(.caption2).foregroundStyle(.tertiary)
                        Text("Last seen \(relativeTimeString(from: lastSeen))")
                            .font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        connectionBadge
                    }
                }
            } else {
                HStack(spacing: 10) {
                    Image(systemName: "location.slash").foregroundStyle(.secondary)
                    Text("No location on file yet")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    /// Prefer Redis-backed live coords (same signals the CMS UI reads) so
    /// the marker matches the "Vehicle Location" widget on the CMS detail
    /// page. Falls back to the DDB-backed `currentVehicle` coords, then
    /// finally the mock frame. The DDB row lags behind telemetry by days
    /// when the vehicle isn't actively connecting — VEH-0047 showed Phoenix
    /// from Apr 24 while live telemetry had moved to Seattle, hence the
    /// preference flip. Added 2026-05-06.
    private var vehicleCoordinate: CLLocationCoordinate2D? {
        if let lat = session.liveState?.latitude,
           let lng = session.liveState?.longitude {
            return CLLocationCoordinate2D(latitude: lat, longitude: lng)
        }
        if let lat = session.currentVehicle?.lastLatitude,
           let lng = session.currentVehicle?.lastLongitude {
            return CLLocationCoordinate2D(latitude: lat, longitude: lng)
        }
        return frame.coordinate
    }

    private var markerLabel: String {
        if let v = session.currentVehicle {
            return v.licensePlate ?? v.vehicleId
        }
        return "Vehicle"
    }

    @ViewBuilder
    private var connectionBadge: some View {
        // Prefer the Redis-backed live state when loaded; it's the same
        // signal CMS UI shows. Fall back to the DDB field only if live state
        // hasn't loaded yet (e.g. tab opened before the endpoint returned).
        let isConnected: Bool = {
            if let live = session.liveState { return live.isConnected }
            return (session.currentVehicle?.connectionStatus ?? "").lowercased() == "connected"
        }()
        HStack(spacing: 4) {
            Circle()
                .fill(isConnected ? Color.green : Color.gray)
                .frame(width: 5, height: 5)
            Text(isConnected ? "Live" : "Offline")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    // MARK: - Vehicle identity card

    @ViewBuilder
    private var identityCard: some View {
        SectionCard("Vehicle", theme: theme) {
            if let v = session.currentVehicle {
                VStack(alignment: .leading, spacing: 10) {
                    Text(v.displayTitle).font(.headline)
                    if let name = v.name, name != v.displayTitle {
                        Text(name).font(.caption).foregroundStyle(.secondary)
                    }
                    Divider()
                    detailRow("VIN", v.vin ?? "—", monospaced: true)
                    detailRow("License Plate", v.licensePlate ?? "—")
                    detailRow("Color", v.color ?? "—")
                    detailRow("Type", v.vehicleType ?? "—")
                    detailRow("Fuel Type", (v.fuelType ?? "").capitalized)
                    // Fleet: prefer the denormalised fleetName (added to
                    // the API 2026-05-05) with the fleetId as a small
                    // subtitle so operators can still see the ID when
                    // they need it. Matches the CMS Vehicle Detail page's
                    // "Fleet: Construction Fleet 5" presentation while
                    // keeping the raw ID visible for cross-referencing.
                    detailRow("Fleet", fleetDisplay(v))
                    if let enrollment = v.enrollmentStatus, !enrollment.isEmpty {
                        detailRow("Enrollment", enrollment.capitalized)
                    }
                    if let odo = v.odometer ?? v.mileage {
                        detailRow("Odometer", formatMiles(odo))
                    }
                    if let totalTrips = v.totalTrips {
                        detailRow("Total Trips", "\(totalTrips)")
                    }
                    if let purchase = v.purchaseDate, !purchase.isEmpty {
                        detailRow("Purchase Date", formatDate(purchase))
                    }
                    if let price = v.purchasePrice, price > 0 {
                        detailRow("Purchase Price", formatCurrency(price))
                    }
                }
            } else {
                Text("Loading vehicle…").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// Compose fleet display: "Construction Fleet 5" when the API has
    /// resolved the name; fall back to the raw fleetId only when name
    /// isn't available (e.g. older Lambda version or missing fleet
    /// row). Never shows an empty string — dash when both are missing.
    private func fleetDisplay(_ v: VehicleInfo) -> String {
        if let name = v.fleetName, !name.isEmpty {
            if let id = v.fleetId, !id.isEmpty, id != name {
                return "\(name) (\(id))"
            }
            return name
        }
        return v.fleetId ?? "—"
    }

    /// Format an ISO-ish date string like "2024-03-17" into "Mar 17, 2024"
    /// for display. Returns the input unchanged if parsing fails so we
    /// never swallow unexpected formats.
    private func formatDate(_ s: String) -> String {
        let isoFmt = ISO8601DateFormatter()
        isoFmt.formatOptions = [.withFullDate]
        if let date = isoFmt.date(from: s) {
            let out = DateFormatter()
            out.dateStyle = .medium
            return out.string(from: date)
        }
        // Try bare YYYY-MM-DD
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        if let date = f.date(from: s) {
            let out = DateFormatter()
            out.dateStyle = .medium
            return out.string(from: date)
        }
        return s
    }

    private func formatCurrency(_ amount: Double) -> String {
        let nf = NumberFormatter()
        nf.numberStyle = .currency
        nf.currencyCode = "USD"
        nf.maximumFractionDigits = 0
        return nf.string(from: NSNumber(value: amount)) ?? "$\(Int(amount))"
    }

    /// BEV detection: returns true when the vehicle's fuelType is one
    /// of the electric variants we see in the seed data. Keeping this
    /// tolerant of casing + a few synonyms so new tenant data doesn't
    /// have to match our exact enum — "BEV"/"Electric"/"EV" all route
    /// to the battery display path.
    private func isElectric(_ v: VehicleInfo?) -> Bool {
        guard let ft = v?.fuelType?.lowercased(), !ft.isEmpty else { return false }
        return ft == "bev" || ft == "electric" || ft == "ev"
    }

    private func detailRow(_ label: String, _ value: String, monospaced: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Spacer()
            Group {
                if monospaced {
                    Text(value).font(.caption).monospaced()
                } else {
                    Text(value).font(.caption)
                }
            }
            .lineLimit(1).truncationMode(.middle)
        }
    }

    private func formatMiles(_ n: Int) -> String {
        let fmt = NumberFormatter()
        fmt.numberStyle = .decimal
        return "\(fmt.string(from: NSNumber(value: n)) ?? String(n)) mi"
    }

    // MARK: - Live signals

    @ViewBuilder
    private var liveSignalsCard: some View {
        SectionCard("Live Signals", theme: theme) {
            VStack(spacing: 0) {
                // Prefer CMS-backed values; fall back to mock frame.
                signalRow(
                    icon: "speedometer", label: "Speed",
                    value: (session.liveState?.speed ?? session.currentVehicle?.lastSpeed ?? frame.speedMph)
                        .map { String(format: "%.0f mph", $0) } ?? "—"
                )
                Divider().padding(.vertical, 4)
                signalRow(
                    icon: "thermometer.medium", label: "Engine Temp",
                    value: (session.liveState?.engineTemp ?? session.currentVehicle?.engineTemp)
                        .map { String(format: "%.0f°F", $0) }
                        ?? frame.coolantTempC.map { String(format: "%.0f°C", $0) }
                        ?? "—",
                    warn: (session.liveState?.engineTemp ?? session.currentVehicle?.engineTemp ?? 0) >= 230
                        || (frame.coolantTempC ?? 0) >= 115
                )
                Divider().padding(.vertical, 4)
                // Energy level — conditional on fuelType. Both ICE "Fuel"
                // and BEV "Battery" read from the same fuelLevel field
                // (Redis signals alias ev_soc → fuelLevel on the backend
                // for BEVs, so this client-side code can stay simple).
                // Prefer liveState (Redis-sourced) so iOS matches the
                // CMS Vehicle Detail page exactly — earlier iOS read
                // stale DDB values and diverged from CMS when the Redis
                // signals hash had newer numbers.
                let energyValue = session.liveState?.fuelLevel
                    ?? session.currentVehicle?.fuelLevel
                    ?? frame.fuelLevelPct
                if isElectric(session.currentVehicle) {
                    signalRow(
                        icon: "bolt.batteryblock.fill", label: "Battery",
                        value: energyValue.map { "\(Int($0))%" } ?? "—",
                        warn: (energyValue ?? 100) < 20
                    )
                } else {
                    signalRow(
                        icon: "fuelpump.fill", label: "Fuel",
                        value: energyValue.map { "\(Int($0))%" } ?? "—",
                        warn: (energyValue ?? 100) < 15
                    )
                }
                // ABS isn't on the vehicle record — mock-frame only today.
                if let abs = frame.absStatusOk {
                    Divider().padding(.vertical, 4)
                    signalRow(
                        icon: abs ? "checkmark.shield.fill" : "exclamationmark.shield.fill",
                        label: "ABS",
                        value: abs ? "OK" : "FAULT",
                        warn: !abs
                    )
                }
                if frame.stale && session.currentVehicle?.connectionStatus != "connected" {
                    Divider().padding(.vertical, 4)
                    HStack(spacing: 8) {
                        Image(systemName: "clock.badge.exclamationmark")
                            .foregroundStyle(.orange)
                        Text("Some signals may be stale — vehicle offline")
                            .font(.caption).foregroundStyle(.orange)
                        Spacer()
                    }
                }
            }
        }
    }

    private func signalRow(icon: String, label: String, value: String, warn: Bool = false) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(warn ? .red : theme.primary.opacity(0.8))
                .frame(width: 22)
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .foregroundStyle(warn ? .red : .primary)
                .monospacedDigit()
                .contentTransition(.numericText())
        }
        .font(.subheadline)
    }

    // MARK: - Active DTCs (mirrors the DTCs tab on the CMS Vehicle
    //         Detail page so drivers can see faults for their own
    //         vehicle without jumping to the Alerts tab). Added
    //         2026-05-05.

    @ViewBuilder
    private var activeDtcsCard: some View {
        let dtcs = session.activeDtcs  // already severity-sorted (CRITICAL → HIGH → ...)
        if !dtcs.isEmpty {
            SectionCard(theme: theme) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(severityColor(dtcs.first?.severity))
                        Text("Active Faults").font(.headline)
                        Spacer()
                        // Count badge — same shape as the Alerts tab badge
                        // so the two surfaces visually agree on "how many".
                        Text("\(dtcs.count)")
                            .font(.caption).bold().monospaced()
                            .padding(.horizontal, 8).padding(.vertical, 2)
                            .background(Capsule().fill(severityColor(dtcs.first?.severity).opacity(0.15)))
                            .foregroundStyle(severityColor(dtcs.first?.severity))
                    }
                    ForEach(dtcs.prefix(5)) { dtc in
                        dtcRowCompact(dtc)
                        if dtc.id != dtcs.prefix(5).last?.id {
                            Divider()
                        }
                    }
                    if dtcs.count > 5 {
                        Text("+\(dtcs.count - 5) more on Alerts tab")
                            .font(.caption).foregroundStyle(.secondary)
                            .padding(.top, 2)
                    }
                }
            }
        }
    }

    /// Compact single-row rendering for the Vehicle tab. Slimmer than
    /// AlertsTabView.dtcRow — drops the `via <source>` line because
    /// provenance belongs on the Alerts tab (operator view), not the
    /// driver's "what's wrong with my truck" summary.
    @ViewBuilder
    private func dtcRowCompact(_ dtc: ActiveDtc) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: severityIcon(dtc.severity))
                .foregroundStyle(severityColor(dtc.severity))
                .font(.body)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(dtc.code).font(.subheadline).bold().monospaced()
                    if let sev = dtc.severity {
                        Text(sev.uppercased())
                            .font(.caption2).bold()
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Capsule().fill(severityColor(sev).opacity(0.15)))
                            .foregroundStyle(severityColor(sev))
                    }
                    if let system = dtc.system, !system.isEmpty {
                        Text(system.capitalized).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let desc = dtc.description, !desc.isEmpty {
                    Text(desc).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
        }
    }

    /// Color helpers — case-insensitive, matching the AlertsTabView
    /// versions so the two surfaces are visually identical. Duplicated
    /// here rather than shared because pulling a cross-file helper
    /// requires a separate Swift file and the surface area is small.
    private func severityColor(_ sev: String?) -> Color {
        switch (sev ?? "").uppercased() {
        case "CRITICAL": return .red
        case "HIGH":     return .orange
        case "MEDIUM":   return .yellow
        case "LOW":      return .gray
        default:         return .secondary
        }
    }

    private func severityIcon(_ sev: String?) -> String {
        switch (sev ?? "").uppercased() {
        case "CRITICAL": return "exclamationmark.octagon.fill"
        case "HIGH":     return "exclamationmark.triangle.fill"
        case "MEDIUM":   return "exclamationmark.circle.fill"
        case "LOW":      return "info.circle"
        default:         return "questionmark.circle"
        }
    }

    // MARK: - Tires (mock frame today — CMS telemetry aggregation TBD)

    @ViewBuilder
    private var tiresCard: some View {
        SectionCard("Tires", theme: theme) {
            VStack(spacing: 12) {
                HStack {
                    tireCell("FL", psi: frame.tireFlPsi)
                    Spacer()
                    tireCell("FR", psi: frame.tireFrPsi)
                }
                HStack {
                    tireCell("RL", psi: frame.tireRlPsi)
                    Spacer()
                    tireCell("RR", psi: frame.tireRrPsi)
                }
            }
        }
    }

    private func tireCell(_ label: String, psi: Double?) -> some View {
        let warn = (psi ?? 34) < 30
        return VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            HStack(spacing: 6) {
                Image(systemName: "circle.dotted")
                    .foregroundStyle(warn ? .red : theme.primary)
                Text(psi.map { String(format: "%.0f PSI", $0) } ?? "—")
                    .font(.subheadline).monospacedDigit()
                    .foregroundStyle(warn ? .red : .primary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(.tertiarySystemGroupedBackground))
        )
    }
}
