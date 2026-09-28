import SwiftUI

/// Vehicle telemetry and history sections, rendered on the Vehicle tab.
///
/// Moved here from `HomeTabView` on 2026-08-19. These four cards are reference
/// data — charge level, the last trip, a driving score, recent activity — and they
/// answer "tell me about my car", which is the Vehicle tab's job. Home answers
/// "does anything need me, and what can I do right now"; stacking reference cards
/// under that question was most of why Home read as a dashboard rather than a
/// companion screen.
///
/// The card bodies are extracted verbatim, including the em-dash-not-zero handling
/// and the EV/ICE conditionals, because those encode fixes for real reported bugs
/// (a literal `0` standing in for a missing reading; "Engine 0°F" on a healthy
/// BEV). Only the host changed.
///
/// Kept as its own view rather than pasted into `VehicleTabView` so neither large
/// view file grows further, and so this set can be reordered in one edit.
struct VehicleTelemetrySections: View {
    @Environment(AppSession.self) private var session
    let theme: TenantTheme

    var body: some View {
        VStack(spacing: 14) {
            // The EV gate was previously applied by Home's call site via
            // `isElectricHome(_:)`; it now reads the shared `VehicleInfo.isElectric`.
            if let v = session.currentVehicle, v.isElectric {
                batteryCard(v)
            }
            // Rental preserves the original Home gate: `showsLastTripCard` is
            // false for the rental segment, where "your last trip" is not a
            // meaningful frame for a car you picked up yesterday. Moving the card
            // must not quietly re-enable it.
            if session.layoutSegment.showsLastTripCard {
                lastTripCard
            }
            safetyScoreCard
            recentActivityCard
        }
    }

    /// Charge and pack health as a first-class card, for EVs only.
    ///
    /// State of charge previously appeared only as one cell in a dense
    /// three-column grid, at the same weight as odometer and speed — a small
    /// percentage for the single number an EV owner checks most. State of health
    /// was not shown anywhere at all.
    ///
    /// Rendered only when `fuelType` is electric: on an ICE vehicle there is no
    /// pack, and the fuel row in the grid is already the right treatment.
    ///
    /// Both figures degrade to an em dash rather than a zero. The bug this card
    /// was built for was a literal `0` standing in for a missing reading, so
    /// nothing here substitutes a number for absent data.
    @ViewBuilder
    private func batteryCard(_ v: VehicleInfo) -> some View {
        let soc = session.liveState?.fuelLevel ?? v.fuelLevel
        let soh = v.batterySoh
        let capacity = v.batteryCapacityKwh

        SectionCard("Battery", theme: theme) {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(soc.map { "\(Int($0))" } ?? "—")
                        // Display face — battery state of charge, the third large numeral.
                        .font(theme.displayFont(size: 48))
                        .foregroundStyle(socColor(soc))
                        .contentTransition(.numericText())
                    if soc != nil {
                        Text("%")
                            .font(.title3.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    if let energy = PackEnergy.usable(
                        capacityKwh: capacity, socPercent: soc, sohPercent: soh
                    ) {
                        // The kWh remaining is the figure that translates a
                        // percentage into range confidence — so both numbers are
                        // SoH-adjusted. Against nameplate the ceiling is a value
                        // the pack can no longer reach, which reads as a shortfall
                        // rather than a moved ceiling. See `PackEnergy`.
                        VStack(alignment: .trailing, spacing: 1) {
                            Text(String(format: "%.0f of %.0f kWh",
                                        energy.now, energy.full))
                                .font(.caption).foregroundStyle(.secondary)
                            Text("usable")
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }

                // Charge bar. `GeometryReader`-free so it composes inside the
                // card's VStack without collapsing its height.
                ProgressView(value: (soc ?? 0) / 100)
                    .progressViewStyle(.linear)
                    .tint(socColor(soc))
                    .accessibilityLabel("State of charge")
                    .accessibilityValue(soc.map { "\(Int($0)) percent" } ?? "unknown")

                Divider()

                HStack(spacing: 6) {
                    Image(systemName: "heart.text.square.fill")
                        .font(.caption)
                        .foregroundStyle(theme.primary)
                    Text("Battery health")
                        .font(.subheadline)
                    Spacer(minLength: 8)
                    Text(soh.map { "\(Int($0))%" } ?? "—")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(soh.map { $0 < 80 ? .orange : Color(.label) } ?? .secondary)
                }
                .accessibilityElement(children: .combine)
            }
        }
        .padding(.horizontal)
    }
    /// Charge colour. Amber under 20% is the threshold the Vehicle tab already
    /// warns at, so the two surfaces agree rather than each picking a number.
    private func socColor(_ soc: Double?) -> Color {
        guard let soc else { return .secondary }
        if soc < 10 { return .red }
        if soc < 20 { return .orange }
        return theme.primary
    }
    @ViewBuilder
    private var lastTripCard: some View {
        if let trip = session.recentTrips.first {
            SectionCard("Last Trip", theme: theme) {
                HStack(spacing: 20) {
                    tripStat(value: String(format: "%.1f", trip.distance ?? trip.totalDistance ?? 0), unit: "mi", icon: "road.lanes")
                    tripStat(value: String(format: "%.0f", trip.duration ?? 0), unit: "min", icon: "clock")
                    if let avg = trip.averageSpeed {
                        tripStat(value: String(format: "%.0f", avg), unit: "mph", icon: "speedometer")
                    }
                }
            }
        }
    }
    private func tripStat(value: String, unit: String, icon: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(theme.primary)
            VStack(alignment: .leading, spacing: 0) {
                Text(value)
                    .font(.subheadline.bold())
                Text(unit)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
    /// Optional secondary line under an upcoming-service row. Composes
    /// "Provider · Request #" when present so the card shows the same
    /// extra context voice-triage rows have today.
    @ViewBuilder
    private var safetyScoreCard: some View {
        SectionCard(theme: theme) {
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Safety Score").font(.caption).foregroundStyle(.secondary)
                    if let s = session.currentDriver?.safetyScore {
                        Text(String(format: "%.1f", s))
                            .font(.system(size: 34, weight: .bold))
                            .foregroundStyle(safetyColor(s))
                    } else {
                        Text("—").font(.system(size: 34, weight: .bold))
                            .foregroundStyle(.secondary)
                    }
                    Text(safetyCaption)
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if let d = session.currentDriver {
                    VStack(alignment: .trailing, spacing: 4) {
                        metricLine(label: "Trips", value: d.totalTrips.map(String.init))
                        metricLine(label: "Miles", value: d.totalMiles.map { formatWithCommas($0) })
                        metricLine(label: "Incidents", value: d.incidentCount.map(String.init))
                    }
                }
            }
        }
    }
    @ViewBuilder
    private var recentActivityCard: some View {
        SectionCard(theme: theme) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "clock.arrow.circlepath")
                        .foregroundStyle(theme.primary)
                    Text("Recent Activity").font(.headline)
                    Spacer()
                }

                let items = recentActivityItems()
                if items.isEmpty {
                    Text(session.recentTripsLoading || session.serviceHistoryLoading
                         ? "Loading activity…"
                         : "No recent activity yet.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(items.prefix(5), id: \.id) { item in
                        activityRow(item)
                        if item.id != items.prefix(5).last?.id {
                            Divider().padding(.leading, 30)
                        }
                    }
                }
            }
        }
    }
    private func activityRow(_ item: ActivityItem) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: item.icon)
                .font(.caption).foregroundStyle(item.tint)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.subheadline).lineLimit(1)
                if !item.subtitle.isEmpty {
                    Text(item.subtitle).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer()
            Text(relativeTimeString(from: item.timestamp))
                .font(.caption2).foregroundStyle(.tertiary)
        }
    }
    /// Merges recent trips and completed service rows, sorted by date desc.
    private func recentActivityItems() -> [ActivityItem] {
        var items: [ActivityItem] = []
        items.append(contentsOf: session.recentTrips.prefix(5).map {
            ActivityItem(
                id: "trip-\($0.tripId)",
                icon: "car.2.fill",
                title: "Trip · \($0.displaySummary)",
                subtitle: [$0.endLocation?.address, $0.tripType?.capitalized]
                    .compactMap { $0 }.joined(separator: " · "),
                // Use effectiveStartTimeISO so trips produced by the Flink
                // TripProcessor (which omits startTimeISO and only writes
                // the epoch-ms startTime field) show up correctly instead
                // of falling to the bottom of the list with an empty
                // timestamp string. See TripSummary.effectiveStartTimeISO.
                timestamp: $0.effectiveStartTimeISO,
                tint: .blue
            )
        })
        items.append(contentsOf: session.completedService.prefix(5).map {
            ActivityItem(
                id: "svc-\($0.id)",
                icon: "wrench.and.screwdriver.fill",
                title: $0.description ?? ($0.serviceType ?? "Service"),
                subtitle: [$0.provider, $0.cost?.total.map { "$\(Int($0))" }]
                    .compactMap { $0 }.joined(separator: " · "),
                timestamp: $0.serviceDate,
                tint: .orange
            )
        })
        return items.sorted { $0.timestamp > $1.timestamp }
    }
    private func formatWithCommas(_ n: Int) -> String {
        let fmt = NumberFormatter()
        fmt.numberStyle = .decimal
        return fmt.string(from: NSNumber(value: n)) ?? String(n)
    }
    private func metricLine(label: String, value: String?) -> some View {
        HStack(spacing: 6) {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
            Text(value ?? "—").font(.caption).bold()
        }
    }
    private var safetyCaption: String {
        guard let s = session.currentDriver?.safetyScore else { return "Updated after each trip" }
        switch s {
        case 95...: return "Top 5% of fleet · Exemplary"
        case 85...: return "Above average · Keep it up"
        case 70...: return "Watch for coaching opportunities"
        default:    return "Coaching recommended"
        }
    }
    private func safetyColor(_ s: Double) -> Color {
        switch s {
        case 95...: return .green
        case 85...: return theme.primary
        case 70...: return .orange
        default:    return .red
        }
    }
}

extension VehicleInfo {
    /// Whether this vehicle is battery-electric.
    ///
    /// Promoted 2026-08-19 from `HomeTabView.isElectricHome(_:)`. It became shared
    /// rather than copied because the battery card moved to the Vehicle tab while
    /// Home's stats grid still branches on it — and this codebase already carried
    /// two different same-named `formatMiles` helpers from exactly that pattern.
    /// A predicate reading one model field also has no business living on a view.
    ///
    /// Absent or empty `fuelType` is deliberately `false` rather than unknown:
    /// callers choose between a battery and a fuel treatment, and defaulting an
    /// unlabelled vehicle to ICE preserves the prior behaviour.
    var isElectric: Bool {
        guard let ft = fuelType?.lowercased(), !ft.isEmpty else { return false }
        return ft == "bev" || ft == "electric" || ft == "ev"
    }
}

/// Merged view-model for the recent-activity list (trips + service).
private struct ActivityItem: Identifiable, Equatable {
    let id: String
    let icon: String
    let title: String
    let subtitle: String
    let timestamp: String   // ISO-8601; used for sorting + relative format
    let tint: Color
}
