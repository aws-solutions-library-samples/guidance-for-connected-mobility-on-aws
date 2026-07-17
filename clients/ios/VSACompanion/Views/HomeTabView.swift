import SwiftUI

/// Driver-centric home screen. Post-2026-04-30 rewrite: the landing page
/// is organized around "you are this driver, this is your vehicle, this is
/// what's upcoming, this is how you're doing" — not around the tenant /
/// telemetry-frame abstraction of the earlier prototype.
///
/// Data sources, all CMS-backed:
///   - session.currentDriver  → GET /drivers/me (JWT-resolved)
///   - session.currentVehicle → GET /drivers/me (assigned vehicle)
///   - session.scheduledService → GET /vehicles/{id}/service-history
///   - session.completedService → GET /vehicles/{id}/service-history
///   - session.recentTrips    → GET /vehicles/{id}/trips
///
/// The triage history + mock telemetry frame remain available for the
/// "Latest alert" strip when present, but they're no longer the primary
/// organizing principle.
struct HomeTabView: View {
    let frame: TelemetryFrame
    let theme: TenantTheme
    /// Invoked when the user taps the "critical alerts" banner. The
    /// parent (MainTabView) sets the TabView selection to .alerts so
    /// the driver lands directly on the list. The argument is the
    /// DTC severity filter that should be active when Alerts mounts —
    /// .critical for a critical-banner tap, .highPlus when only
    /// HIGH alerts exist (so the driver actually sees the row the
    /// banner promised; default Critical-only filter would hide it).
    /// Optional for tests / previews that don't need the behavior.
    /// Added 2026-05-04, filter parameter added 2026-05-19.
    var onJumpToAlerts: ((DtcFilter?) -> Void)? = nil
    @Environment(AppSession.self) private var session

    /// Whether the welcome banner is currently rendered. Starts true,
    /// flips to false 2.8s after first appearance — the .task on the
    /// banner view handles the timer. Once dismissed, the
    /// session-scoped `hasShownWelcomeForCurrentSession` flag prevents
    /// it from re-appearing on subsequent Home visits.
    @State private var welcomeBannerVisible: Bool = true

    /// Drives the vehicle-claim picker sheet shown from the no-vehicle state.
    @State private var showClaimPicker: Bool = false

    var body: some View {
        NavigationStack {
            ScrollView {
                if session.currentDriverLoadedAt != nil && session.currentVehicle == nil {
                    // Driver resolved but has no assigned vehicle. Show a claim
                    // affordance instead of spinning forever on the (impossible)
                    // vehicle-scoped loads. This is the fix for the "Home tab
                    // spins after sign-in" dead-end when a driver is unassigned.
                    noVehicleClaimView
                } else if session.hasLoadedInitialDashboard {
                    dashboardContent
                } else {
                    dashboardSkeleton
                }
            }
            .refreshable { await refreshAll(force: true) }
            .task { await refreshAll(force: false) }
            .navigationTitle("Home")
            .navigationBarTitleDisplayMode(.inline)
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
            .sheet(isPresented: $showClaimPicker) {
                ClaimVehicleSheet(onClaimed: {
                    showClaimPicker = false
                })
            }
        }
    }

    // MARK: - No-vehicle / claim state

    /// Shown when the signed-in driver has no assigned vehicle. Offers a
    /// self-service claim (when the CMS API is configured) instead of an
    /// indefinite loading spinner.
    @ViewBuilder
    private var noVehicleClaimView: some View {
        VStack(spacing: 18) {
            Image(systemName: "car.2")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
                .padding(.top, 48)
            Text("No vehicle assigned")
                .font(.title3).bold()
            Text("You're signed in, but no vehicle is linked to your driver profile yet.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            if VSAConfig.cmsRestApiUrl != nil {
                Button {
                    showClaimPicker = true
                } label: {
                    Label("Claim a vehicle", systemImage: "plus.circle.fill")
                        .font(.headline)
                        .padding(.horizontal, 20).padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
            } else {
                Text("Contact your fleet administrator to get a vehicle assigned.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }
            Spacer(minLength: 40)
        }
        .frame(maxWidth: .infinity)
    }

    /// Real dashboard. Only rendered once every resource that backs it
    /// has loaded at least once (see `AppSession.hasLoadedInitialDashboard`).
    /// This prevents the "100 → 39" health-score flash and similar
    /// stub-then-update jumps across the cards. Subsequent refreshes
    /// (pull-to-refresh) don't return us to the skeleton — the
    /// LoadedAt timestamps stay non-nil — so cards update in place.
    @ViewBuilder
    private var dashboardContent: some View {
        // Per-persona layout selection. Cards are gated on
        // `LayoutSegment` flags so OEM tenants drop fleet-only
        // metrics (driver safety score, recent trips), rental
        // tenants get a stripped Home plus trip-time-remaining /
        // return-to cards, and fleet tenants see everything.
        // Default segment is `.fleet` so an unconfigured tenant
        // gets the broadest layout.
        let segment = session.layoutSegment
        VStack(alignment: .leading, spacing: 16) {
            // Per-persona welcome card — shown once after sign-in,
            // auto-dismisses after ~3s. The card itself manages its
            // own visibility via .task; HomeTabView just plants it
            // here. Skipped after the first appearance via the
            // session-scoped flag.
            if !session.hasShownWelcomeForCurrentSession {
                welcomeBanner
            }
            identityStrip
            criticalAlertsBanner
            if segment.showsRecallBanner {
                recallBanner
            }
            vehicleHealthCard
            vehicleCard
            if segment.showsRentalTripCards {
                tripTimeRemainingCard
                returnToCard
            }
            if segment.showsNextServiceCountdown {
                nextServiceCountdown
            }
            if segment.showsLastTripCard {
                lastTripCard
            }
            if segment.showsUpcomingServiceCard, !session.scheduledService.isEmpty {
                upcomingServiceCard
            }
            if segment.showsSafetyScoreCard {
                safetyScoreCard
            }
            if segment.showsRecentActivityCard {
                recentActivityCard
            }
            if segment.showsLatestAlertCard, let triage = session.lastTriage {
                latestAlertCard(triage: triage)
            }
        }
        .padding()
    }

    /// Single quiet skeleton shown while the first dashboard load is
    /// in flight. Deliberately bland — no numbers, no labels with
    /// stub values — so the driver doesn't see anything that could
    /// be misread as real data. The ProgressView gives a clear
    /// "still loading" signal while the layout reserves rough space
    /// for the cards that are coming.
    @ViewBuilder
    private var dashboardSkeleton: some View {
        // Identity strip + several card placeholders matches the real
        // Home layout closely enough that the swap-in doesn't jump.
        TabLoadingSkeleton(cardCount: 4, showsIdentityStrip: true)
    }
    
    // MARK: - Welcome banner

    /// Persona-tinted welcome card shown once per signed-in session.
    /// Self-dismisses after ~2.8s via a `.task` timer; flips the
    /// session-scoped flag on dismiss so subsequent Home visits skip
    /// it. The banner uses the tenant's theme primary color (fleet
    /// navy / OEM blue / Enterprise green) so the brand reads
    /// instantly even before the rest of the dashboard finishes
    /// loading.
    @ViewBuilder
    private var welcomeBanner: some View {
        if welcomeBannerVisible {
            let firstName = session.currentDriver?.firstName ?? "Driver"
            let title: String = {
                switch session.layoutSegment {
                case .oem:    return "Welcome back, \(firstName)"
                case .rental: return "Hi \(firstName) — let's get you on the road"
                default:      return "Welcome back, \(firstName)"
                }
            }()
            let subtitle: String = {
                switch session.layoutSegment {
                case .oem:
                    return "Your authorized dealer is one tap away."
                case .rental:
                    return "Quick help while you're on your trip."
                default:
                    return "Live support for your fleet, on demand."
                }
            }()
            HStack(spacing: 14) {
                Image(systemName: welcomeSymbol)
                    .font(.title2)
                    .foregroundStyle(.white)
                    .frame(width: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline.bold()).foregroundStyle(.white)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.85))
                        .lineLimit(2)
                }
                Spacer()
            }
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(theme.primary.gradient)
            )
            .transition(.move(edge: .top).combined(with: .opacity))
            .task {
                // Fire-and-forget timer. SwiftUI cancels this task if
                // the view disappears, which is fine — the banner is
                // already gone visually at that point.
                try? await Task.sleep(nanoseconds: 2_800_000_000)
                withAnimation(.easeInOut(duration: 0.35)) {
                    welcomeBannerVisible = false
                }
                // Persist the "shown for this session" bit so a return
                // visit to Home doesn't re-fire the banner. We set
                // this AFTER the fade so the animation isn't cut short
                // by SwiftUI re-evaluating the parent's `if !flag`
                // gate during the transition.
                session.hasShownWelcomeForCurrentSession = true
            }
        }
    }

    /// Choose a persona-appropriate greeting glyph. Hand-wave for
    /// fleet (familiar driver), key card for rental (renter), car
    /// front for OEM (owner-context).
    private var welcomeSymbol: String {
        switch session.layoutSegment {
        case .oem:    return "car.front.waves.up.fill"
        case .rental: return "key.card.fill"
        default:      return "hand.wave.fill"
        }
    }

    // MARK: - Recall banner
    
    @ViewBuilder
    private var recallBanner: some View {
        // Show if vehicle has open recalls (check for recall-related DTCs or safety events)
        let recallCount = session.vehicleContext?.activeDtcs?.filter { $0.code.uppercased().hasPrefix("RECALL") }.count ?? 0
        if recallCount > 0 {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.shield.fill")
                    .foregroundStyle(.white)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(recallCount) Open Recall\(recallCount > 1 ? "s" : "")")
                        .font(.subheadline.bold())
                        .foregroundStyle(.white)
                    Text("Free repair available — tap to schedule")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.9))
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .foregroundStyle(.white.opacity(0.7))
            }
            .padding()
            .background(RoundedRectangle(cornerRadius: 12).fill(.orange.gradient))
            // Recall banner uses no specific filter — recalls show up
            // across all severities, so the user's last-chosen filter
            // (or the Critical default) is fine. Pass nil to leave
            // the filter as-is.
            .onTapGesture { onJumpToAlerts?(nil) }
        }
    }
    
    // MARK: - Vehicle health score
    
    @ViewBuilder
    private var vehicleHealthCard: some View {
        // Only show once vehicle context has loaded (prevents 100→0 flash)
        if session.vehicleContext != nil {
            // Source of truth is the server: GET /vehicles/{id}/context
            // returns `healthScore` (0..100) computed by the
            // api-vehicle-context Lambda. iOS no longer recomputes the
            // score — keeping a single formula on the backend means
            // the Home tab and the CMS UI Vehicle Detail page can
            // never disagree on the number. Default to 100 only as a
            // graceful fallback for older Lambda deploys that don't
            // emit the field; the `vehicleContext != nil` guard above
            // already prevents the pre-load flash.
            let score = session.vehicleContext?.healthScore ?? 100
            SectionCard("Vehicle Health", theme: theme) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("\(score)")
                            .font(.system(size: 42, weight: .bold, design: .rounded))
                            .foregroundStyle(healthColor(score))
                        Text(healthLabel(score))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    ZStack {
                        Circle()
                            .stroke(Color(.systemGray5), lineWidth: 8)
                            .frame(width: 70, height: 70)
                        Circle()
                            .trim(from: 0, to: Double(score) / 100.0)
                            .stroke(healthColor(score), style: StrokeStyle(lineWidth: 8, lineCap: .round))
                            .frame(width: 70, height: 70)
                            .rotationEffect(.degrees(-90))
                        Image(systemName: score >= 80 ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .foregroundStyle(healthColor(score))
                    }
                }
            }
        }
    }
    
    private func healthColor(_ score: Int) -> Color {
        if score >= 80 { return .green }
        if score >= 60 { return .orange }
        return .red
    }
    
    private func healthLabel(_ score: Int) -> String {
        if score >= 90 { return "Excellent" }
        if score >= 80 { return "Good" }
        if score >= 60 { return "Needs Attention" }
        return "Service Required"
    }
    
    // MARK: - Next service countdown
    
    @ViewBuilder
    private var nextServiceCountdown: some View {
        if let nextService = session.scheduledService.first {
            SectionCard("Next Service", theme: theme) {
                HStack(spacing: 12) {
                    Image(systemName: "wrench.and.screwdriver.fill")
                        .font(.title2)
                        .foregroundStyle(theme.primary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(nextService.serviceType ?? "Scheduled Service")
                            .font(.subheadline.bold())
                        let date = ISO8601DateFormatter().date(from: nextService.serviceDate)
                        if let date {
                            let days = Calendar.current.dateComponents([.day], from: Date(), to: date).day ?? 0
                            if days > 0 {
                                Text("In \(days) day\(days == 1 ? "" : "s")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            } else if days == 0 {
                                Text("Today")
                                    .font(.caption.bold())
                                    .foregroundStyle(.orange)
                            } else {
                                Text("Overdue by \(abs(days)) day\(abs(days) == 1 ? "" : "s")")
                                    .font(.caption.bold())
                                    .foregroundStyle(.red)
                            }
                        }
                        if let provider = nextService.provider {
                            Text(provider)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                }
            }
        }
    }
    
    // MARK: - Last trip card
    
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

    // MARK: - Critical alerts banner
    //
    // Renders at-most-one compact banner near the top of Home when the
    // driver's vehicle has active DTCs. Red variant when any CRITICAL
    // DTC is present; amber variant when there's no CRITICAL but at
    // least one HIGH. MEDIUM / LOW do not get a banner — those live on
    // the Alerts tab only. Tapping the banner jumps straight to the
    // Alerts tab via the onJumpToAlerts callback. Added 2026-05-04 so
    // drivers can't miss a serious vehicle fault just because they
    // never open the Alerts tab.
    @ViewBuilder
    private var criticalAlertsBanner: some View {
        let critical = session.activeDtcCount(minSeverityRank: 0)
        let high = session.activeDtcCount(minSeverityRank: 1) - critical
        if critical > 0 {
            alertsBannerRow(
                icon: "exclamationmark.octagon.fill",
                tint: .red,
                title: "\(critical) critical vehicle alert\(critical == 1 ? "" : "s")",
                subtitle: "Tap to review on the Alerts tab",
                // Critical banner → land on Critical filter so the
                // count the user just saw matches the rows on Alerts.
                jumpFilter: .critical
            )
        } else if high > 0 {
            alertsBannerRow(
                icon: "exclamationmark.triangle.fill",
                tint: .orange,
                title: "\(high) high-severity alert\(high == 1 ? "" : "s")",
                subtitle: "Tap to review on the Alerts tab",
                // High-severity banner → land on High+ filter so the
                // alerts are visible. Default Critical filter would
                // produce a misleading "no alerts" empty state.
                jumpFilter: .highPlus
            )
        }
    }

    @ViewBuilder
    private func alertsBannerRow(icon: String, tint: Color, title: String, subtitle: String, jumpFilter: DtcFilter) -> some View {
        Button(action: { onJumpToAlerts?(jumpFilter) }) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.title3)
                    .foregroundStyle(tint)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline).bold().foregroundStyle(.primary)
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.footnote).foregroundStyle(.tertiary)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(tint.opacity(0.12))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(tint.opacity(0.4), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Refresh

    private func refreshAll(force: Bool) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        let client = VSAClient(idTokenProvider: { token })
        // Load driver first so effectiveVehicleId resolves correctly for
        // the subsequent calls. loadCurrentDriver eager-warms vehicleContext
        // internally, so we don't need to call that separately.
        await session.loadCurrentDriver(client: client, force: force)
        // These run in parallel — they all key off effectiveVehicleId.
        async let svc: Void = session.loadServiceHistory(client: client, force: force)
        async let trips: Void = session.loadRecentTrips(client: client, force: force)
        async let live: Void = session.loadLiveState(client: client, force: force)
        async let ctx: Void = session.loadVehicleContext(client: client, force: force)
        _ = await (svc, trips, live, ctx)
    }

    // MARK: - Identity strip

    @ViewBuilder
    private var identityStrip: some View {
        HStack(spacing: 12) {
            avatarCircle
            VStack(alignment: .leading, spacing: 2) {
                Text(greetingText)
                    .font(.title2).bold()
                Text(identitySubtext)
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1)
                // Secondary stats line (2026-05-05): total trips /
                // miles / last trip date. Mirrors what the CMS driver
                // detail card shows under "Driver Statistics" so the
                // two surfaces agree on lifetime metrics. Only renders
                // when we have the data to avoid a spurious blank line.
                if let statsLine = driverStatsLine {
                    Text(statsLine)
                        .font(.caption2).foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }
            Spacer()
        }
    }

    /// Second-line driver stats: "4,096 trips · 360K mi · last Apr 20".
    /// Returns nil when there's nothing meaningful to show so the
    /// Text() above is dropped entirely (keeps the identity strip
    /// compact for new drivers with no data yet).
    private var driverStatsLine: String? {
        guard let d = session.currentDriver else { return nil }
        var parts: [String] = []
        if let t = d.totalTrips, t > 0 {
            parts.append(Self.formatCount(t) + " trips")
        }
        if let m = d.totalMiles, m > 0 {
            parts.append(Self.formatMiles(m) + " mi")
        }
        if let last = d.lastTripDate, !last.isEmpty {
            parts.append("last \(Self.shortDate(last))")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// "4096" → "4.1K"; "360180" → "360K". Match the CMS compact style.
    private static func formatCount(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 10_000    { return "\(n / 1000)K" }
        if n >= 1_000     { return String(format: "%.1fK", Double(n) / 1000) }
        return "\(n)"
    }

    private static func formatMiles(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000     { return "\(n / 1000)K" }
        return "\(n)"
    }

    /// "2026-04-20" or full ISO → "Apr 20". Falls back to raw string
    /// when parsing fails so we never swallow unexpected formats.
    private static func shortDate(_ s: String) -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withFullDate]
        let date: Date?
        if let d = iso.date(from: s) {
            date = d
        } else {
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd"
            date = f.date(from: String(s.prefix(10)))
        }
        guard let date else { return s }
        let out = DateFormatter()
        out.dateFormat = "MMM d"
        return out.string(from: date)
    }

    private var greetingText: String {
        let hour = Calendar.current.component(.hour, from: Date())
        let timeOfDay: String
        switch hour {
        case 5..<12: timeOfDay = "Good morning"
        case 12..<17: timeOfDay = "Good afternoon"
        case 17..<22: timeOfDay = "Good evening"
        default:      timeOfDay = "Hello"
        }
        if let firstName = session.currentDriver?.firstName {
            return "\(timeOfDay), \(firstName)"
        }
        return timeOfDay
    }

    private var identitySubtext: String {
        guard let d = session.currentDriver else {
            return "Loading profile…"
        }
        var parts: [String] = []
        if let home = d.homeBase { parts.append(home) }
        if let lic = d.licenseClass { parts.append(lic) }
        if let yrs = d.yearsExperience { parts.append("\(yrs) yrs") }
        return parts.joined(separator: " · ")
    }

    private var avatarCircle: some View {
        let initials = session.currentDriver?.initials ?? "?"
        return ZStack {
            Circle()
                .fill(theme.primary.opacity(0.15))
                .frame(width: 52, height: 52)
            Text(initials)
                .font(.title3).bold()
                .foregroundStyle(theme.primary)
        }
        .overlay(
            Circle().strokeBorder(theme.primary.opacity(0.4), lineWidth: 1.5)
        )
    }

    // MARK: - Vehicle card

    @ViewBuilder
    private var vehicleCard: some View {
        let v = session.currentVehicle
        SectionCard(theme: theme) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Image(systemName: "car.fill")
                        .font(.title3).foregroundStyle(theme.primary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(v?.displayTitle ?? "Loading vehicle…")
                            .font(.headline)
                        if let license = v?.licensePlate {
                            Text("\(license) · \(v?.color ?? "")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    connectionBadge
                }
                if v != nil {
                    Divider()
                    vehicleStatsGrid(v!)
                    // Prefer the Redis-backed live-state timestamp, which
                    // reflects the most recent connectivity check (5-min
                    // window). Fall back to the DDB `lastSeenAt` only if
                    // live-state hasn't loaded yet — that field is written
                    // by the ingestion pipeline and can lag by days, which
                    // produced the misleading "Last seen 1w ago" on the
                    // home card even when the vehicle was currently active.
                    if let live = session.liveState, let connectedAt = live.lastConnectedAt {
                        Text("Last seen \(relativeTimeString(from: connectedAt))")
                            .font(.caption2).foregroundStyle(.tertiary)
                    } else if let lastSeen = v?.lastSeenAt {
                        Text("Last seen \(relativeTimeString(from: lastSeen))")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var connectionBadge: some View {
        // Prefer Redis-backed liveState; fall back to DDB field if unloaded.
        let isConnected: Bool = {
            if let live = session.liveState { return live.isConnected }
            return (session.currentVehicle?.connectionStatus ?? "").lowercased() == "connected"
        }()
        let status = session.currentVehicle?.status ?? ""
        let color: Color = isConnected ? .green : (status.lowercased() == "active" ? .orange : .gray)
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(isConnected ? "Connected" : "Offline")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func vehicleStatsGrid(_ v: VehicleInfo) -> some View {
        let columns = [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())]
        // 2026-05-05: grid updated to match the Vehicle tab + CMS:
        // - Odometer/Fuel/Engine come from liveState when fresh, falling
        //   back to the DDB values on the vehicle record.
        // - "Battery voltage" row dropped — it was a mechanic-concern
        //   value, not driver-relevant. Energy status is already
        //   conveyed by the Fuel/Battery row via fuelType below.
        // - Fuel vs Battery row flips based on fuelType (BEV → Battery,
        //   ICE → Fuel) matching the Vehicle tab conditional.
        // - Fleet cell uses fleetName (denormalised server-side in the
        //   /drivers/me + /vehicles/{id}/context Lambdas) falling back
        //   to fleetId so ICE-era vehicles without a fleet row don't
        //   show a blank cell.
        let live = session.liveState
        let odo = (live?.odometer).map { Int($0) } ?? v.odometer ?? v.mileage
        let fuel = live?.fuelLevel ?? v.fuelLevel
        let temp = live?.engineTemp ?? v.engineTemp
        let speed = live?.speed ?? v.lastSpeed
        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            statCell("Odometer", value: odo.map { "\($0) mi" } ?? "—", systemImage: "speedometer")
            if isElectricHome(v) {
                statCell("Battery", value: fuel.map { "\(Int($0))%" } ?? "—", systemImage: "bolt.batteryblock.fill")
            } else {
                statCell("Fuel", value: fuel.map { "\(Int($0))%" } ?? "—", systemImage: "fuelpump.fill")
            }
            statCell("Engine", value: temp.map { "\(Int($0))°F" } ?? "—", systemImage: "thermometer.high")
            statCell("Speed", value: speed.map { String(format: "%.0f mph", $0) } ?? "—", systemImage: "gauge.with.needle")
            statCell("Fleet", value: fleetCell(v), systemImage: "building.2.fill")
        }
    }

    /// BEV detection for the Home tab's stats grid. Duplicates the
    /// Vehicle tab helper because Swift private helpers don't cross
    /// files and the logic is 3 lines.
    private func isElectricHome(_ v: VehicleInfo?) -> Bool {
        guard let ft = v?.fuelType?.lowercased(), !ft.isEmpty else { return false }
        return ft == "bev" || ft == "electric" || ft == "ev"
    }

    /// Fleet cell value: prefer fleetName, fall back to fleetId, then
    /// dash. Keeps the dense 3-col grid compact by not appending the
    /// ID (Vehicle tab shows "name (id)" because it has more room).
    private func fleetCell(_ v: VehicleInfo) -> String {
        if let name = v.fleetName, !name.isEmpty { return name }
        return v.fleetId ?? "—"
    }

    private func statCell(_ label: String, value: String, systemImage: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: systemImage)
                .font(.caption).foregroundStyle(theme.primary.opacity(0.7))
                .frame(width: 14)
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.caption2).foregroundStyle(.secondary)
                Text(value).font(.caption).bold().lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: - Upcoming service

    @ViewBuilder
    private var upcomingServiceCard: some View {
        SectionCard(theme: theme) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: "calendar.badge.clock")
                        .foregroundStyle(theme.primary)
                    Text("Upcoming Service").font(.headline)
                    Spacer()
                    if session.scheduledService.count > 1 {
                        Text("\(session.scheduledService.count)")
                            .font(.caption2).foregroundStyle(.secondary)
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .background(Capsule().fill(Color(.tertiarySystemFill)))
                    }
                }
                ForEach(session.scheduledService.prefix(2)) { r in
                    upcomingRow(r)
                }
            }
        }
    }

    @ViewBuilder
    private func upcomingRow(_ r: ServiceRecord) -> some View {
        let level = AlertLevel(from: r.triagePriority)
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                StatusBadge(level: level)
                // Title precedence: description (human-readable, e.g.
                // "DTC P0217: Engine coolant critically overheated") →
                // prettified serviceType → generic fallback. Prior
                // versions showed the raw serviceType enum "DIAGNOSTIC_
                // REPAIR" when description was missing — see prettyType
                // below for the mapping.
                Text(r.description ?? prettyServiceType(r.serviceType) ?? "Scheduled service")
                    .font(.subheadline).bold().lineLimit(1)
                Spacer()
                if let when = r.scheduledFor ?? r.serviceDate as String? {
                    Text(relativeTimeString(from: when))
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            // Secondary line: provider · request number. Matches the
            // richer shape of voice-triage bookings so both kinds of
            // scheduled service look consistent on this card.
            if let secondary = upcomingSecondaryLine(r), !secondary.isEmpty {
                Text(secondary)
                    .font(.caption2).foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
    }

    /// Convert a schema-ish serviceType enum ("DIAGNOSTIC_REPAIR") into
    /// a human-readable label ("Diagnostic Repair"). Returns nil when
    /// the input is nil so the caller can chain `??`. Handles a short
    /// list of known values with proper casing; falls back to a
    /// split-and-title-case for anything else.
    private func prettyServiceType(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        // Known mappings — preserves cases like "VSA" that title-case
        // would otherwise mangle.
        let known: [String: String] = [
            "DIAGNOSTIC_REPAIR":   "Diagnostic Repair",
            "VSA_VOICE_TRIAGE":    "Voice-triage booking",
            "STARTER_MOTOR":       "Starter Motor",
            "COOLANT_FLUSH":       "Coolant Flush",
            "OIL_CHANGE":          "Oil Change",
            "BRAKE_SERVICE":       "Brake Service",
            "TIRE_ROTATION":       "Tire Rotation",
            "INSPECTION":          "Inspection",
        ]
        if let hit = known[raw] { return hit }
        // Generic: underscores → spaces, words → title-case.
        return raw
            .split(separator: "_")
            .map { $0.prefix(1).uppercased() + $0.dropFirst().lowercased() }
            .joined(separator: " ")
    }

    /// Optional secondary line under an upcoming-service row. Composes
    /// "Provider · Request #" when present so the card shows the same
    /// extra context voice-triage rows have today.
    private func upcomingSecondaryLine(_ r: ServiceRecord) -> String? {
        var parts: [String] = []
        if let p = r.provider, !p.isEmpty { parts.append(p) }
        if let req = r.requestNumber, !req.isEmpty { parts.append(req) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // MARK: - Safety score

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

    private func metricLine(label: String, value: String?) -> some View {
        HStack(spacing: 6) {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
            Text(value ?? "—").font(.caption).bold()
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

    private var safetyCaption: String {
        guard let s = session.currentDriver?.safetyScore else { return "Updated after each trip" }
        switch s {
        case 95...: return "Top 5% of fleet · Exemplary"
        case 85...: return "Above average · Keep it up"
        case 70...: return "Watch for coaching opportunities"
        default:    return "Coaching recommended"
        }
    }

    private func formatWithCommas(_ n: Int) -> String {
        let fmt = NumberFormatter()
        fmt.numberStyle = .decimal
        return fmt.string(from: NSNumber(value: n)) ?? String(n)
    }

    // MARK: - Recent activity (trips + recent service merged, newest first)

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

    // MARK: - Latest alert (kept from old Home for backward visual continuity)

    @ViewBuilder
    private func latestAlertCard(triage: TriageResponse) -> some View {
        let level = AlertLevel(from: triage.classification)
        SectionCard(theme: theme) {
            HStack(spacing: 12) {
                StatusBadge(level: level)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Latest triage").font(.caption).foregroundStyle(.secondary)
                    Text(level.title).font(.subheadline).bold()
                }
                Spacer()
                Text(relativeTimeString(from: triage.decidedAt))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Rental-only cards

    /// "Trip Time Remaining" card. Shown only when the active tenant's
    /// segment is `rental`. The rental return time isn't part of the
    /// CMS vehicle record today, so we synthesize a relative window
    /// from the vehicle's purchase/enrollment timestamp ("rented for
    /// the last X days, return due Y") — good enough for the demo,
    /// real implementation would read a `rentalEndsAt` attribute.
    @ViewBuilder
    private var tripTimeRemainingCard: some View {
        SectionCard("Trip Time Remaining", theme: theme) {
            HStack(alignment: .center, spacing: 14) {
                Image(systemName: "hourglass")
                    .font(.title)
                    .foregroundStyle(theme.primary)
                    .frame(width: 44)
                VStack(alignment: .leading, spacing: 4) {
                    // Demo placeholder window: "3 days, 4 hours remaining".
                    // Hardcoded for visual; a real implementation would
                    // compute from rentalEndsAt - now.
                    Text("3 days, 4 hours")
                        .font(.title3).bold()
                    Text("Return by Friday at 3:00 PM")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
    }

    /// "Return To" card. Shown only for rental tenants. The rental
    /// pickup location lives on the seeded vehicle attributes
    /// (`rentalReturnLocation`) but isn't surfaced through the iOS
    /// VehicleContextResponse model today, so for the demo we
    /// hardcode a plausible Enterprise drop-off keyed off the
    /// driver's home base. A production implementation would
    /// surface the attributes map through `/vehicles/{id}/context`
    /// so the location reflects what's actually in CMS.
    @ViewBuilder
    private var returnToCard: some View {
        let homeBase = session.currentDriver?.homeBase ?? "your rental city"
        let returnLocation = "Enterprise — \(homeBase) Airport"
        SectionCard("Return To", theme: theme) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "mappin.and.ellipse")
                    .font(.title)
                    .foregroundStyle(theme.primary)
                    .frame(width: 44)
                VStack(alignment: .leading, spacing: 4) {
                    Text(returnLocation)
                        .font(.subheadline).bold()
                        .lineLimit(2)
                    Text("Drop the keys at the desk inside.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
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

// MARK: - Vehicle-claim picker

/// Vehicle-claim picker presented when a signed-in driver has no assigned
/// vehicle. Lists the driver's fleet inventory (CMS GET /api/v1/vehicles,
/// fleet-scoped server-side) and lets them self-assign one
/// (CMS PUT /api/v1/drivers/{self}). On success the parent dismisses and the
/// Home tab re-resolves the driver → dashboard renders.
///
/// Reuses the same CMS capability the Fleet web UI's "Assign vehicle" action
/// uses; the backend constrains driver tokens to this self-service path.
/// Lives in HomeTabView.swift (not its own file) because the Xcode project
/// uses explicit file references, not file-system-synchronized groups.
struct ClaimVehicleSheet: View {
    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss

    /// Called after a successful claim so the parent can dismiss + refresh.
    var onClaimed: () -> Void

    @State private var claimingVehicleId: String?
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            Group {
                if session.claimableVehiclesLoading && session.claimableVehicles.isEmpty {
                    ProgressView("Loading vehicles…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if session.claimableVehicles.isEmpty {
                    emptyState
                } else {
                    vehicleList
                }
            }
            .navigationTitle("Claim a vehicle")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task { await session.loadClaimableVehicles() }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "car.2")
                .font(.system(size: 36))
                .foregroundStyle(.secondary)
            Text(session.claimError ?? "No vehicles available to claim in your fleet.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button("Try again") {
                Task { await session.loadClaimableVehicles() }
            }
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var vehicleList: some View {
        List {
            if let errorText {
                Section {
                    Text(errorText)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }
            Section {
                ForEach(session.claimableVehicles, id: \.vehicleId) { vehicle in
                    Button {
                        Task { await claim(vehicle) }
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "car.fill")
                                .foregroundStyle(.tint)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(vehicle.displayTitle).font(.headline)
                                Text(secondaryLine(vehicle))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if claimingVehicleId == vehicle.vehicleId {
                                ProgressView()
                            } else {
                                Image(systemName: "chevron.right")
                                    .font(.caption).foregroundStyle(.tertiary)
                            }
                        }
                    }
                    .disabled(claimingVehicleId != nil)
                }
            } header: {
                Text("Available in your fleet")
            }
        }
    }

    private func secondaryLine(_ v: VehicleInfo) -> String {
        var parts: [String] = [v.vehicleId]
        if let vin = v.vin, !vin.isEmpty { parts.append("VIN \(vin)") }
        return parts.joined(separator: " · ")
    }

    @MainActor
    private func claim(_ vehicle: VehicleInfo) async {
        guard claimingVehicleId == nil else { return }
        errorText = nil
        claimingVehicleId = vehicle.vehicleId
        defer { claimingVehicleId = nil }
        do {
            try await session.claimVehicle(vehicleId: vehicle.vehicleId)
            onClaimed()
            dismiss()
        } catch {
            errorText = error.localizedDescription
        }
    }
}
