import SwiftUI

@main
struct VSACompanionApp: App {
    @State private var session = AppSession()
    /// User-controlled appearance override. Default `.system` lets iOS
    /// drive light/dark via Settings → Display & Brightness; the
    /// Account tab picker writes here to force a mode app-wide. Stored
    /// in UserDefaults so the choice survives relaunches.
    @AppStorage(AppearancePreference.storageKey)
    private var appearance: AppearancePreference = .system

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .preferredColorScheme(appearance.colorScheme)
        }
    }
}

/// Shared app-level state. Observable via SwiftUI's new @Observable macro.
@Observable
final class AppSession {
    var authState: AuthState = .signedOut
    var tenantConfig: TenantConfig?
    var tenantConfigLoading: Bool = false
    var lastTriage: TriageResponse?
    var triageHistory: [TriageResponse] = []
    var severityFilter: SeverityFilter = .all
    /// The tenant id currently shown. Starts as the Cognito-tied default; can be
    /// overridden via the developer tenant switcher (UI-only, auth stays pinned
    /// to the original tenant's Cognito pool).
    var activeTenantId: String = VSAConfig.defaultTenantId

    // MARK: - Service history (CMS-backed, refreshed on Alerts tab load)

    /// Upcoming bookings for the demo vehicle. Created by book() during a P0
    /// voice session; seeded historical data contains none of these.
    var scheduledService: [ServiceRecord] = []
    /// Completed service rows — the seeded history for the demo vehicle
    /// (~15 rows going back ~2 years). Useful even when no upcoming rows
    /// exist so the Alerts tab's service section isn't empty.
    var completedService: [ServiceRecord] = []
    /// Last successful refresh timestamp; used to debounce auto-refresh.
    var serviceHistoryLoadedAt: Date?
    /// In-flight flag so concurrent callers (onAppear + pull-to-refresh) don't
    /// stampede the backend.
    var serviceHistoryLoading: Bool = false
    /// Last error from a failed fetch; shown in the UI so users aren't left
    /// wondering why the section is empty.
    var serviceHistoryError: String?

    // MARK: - Vehicle context (for Assistant-tab nameplate + voice-prompt priming)

    /// Current vehicle + assigned driver for the demo vehicle. Loaded once at
    /// Assistant-tab open; kept for the rest of the session.
    var vehicleContext: VehicleContextResponse?
    var vehicleContextLoadedAt: Date?
    var vehicleContextLoading: Bool = false
    var vehicleContextError: String?

    /// Realtime connection + telemetry state for the current vehicle. Backed
    /// by /vehicles/{id}/live-state which reads the same Redis hash the CMS
    /// UI reads, so iOS and CMS UI agree on "connected" vs "offline".
    /// Updated in real-time via WebSocket when connected, falls back to HTTP polling.
    var liveState: VehicleLiveState?
    var liveStateLoadedAt: Date?
    var liveStateLoading: Bool = false
    var liveStateError: String?
    
    /// WebSocket client for real-time telemetry push from CMS ws-fanout.
    var wsClient: VehicleWebSocketClient?

    // MARK: - Current signed-in driver (authoritative driver + vehicle)

    /// Resolved at sign-in from GET /drivers/me using the Cognito JWT. When
    /// non-nil, this is the source of truth for "who am I" and overrides the
    /// VSAConfig.demoDriverId / demoVehicleId defaults.
    ///
    /// Nil means either (a) sign-in hasn't completed yet, (b) the /drivers/me
    /// endpoint hasn't been called, or (c) the signed-in Cognito user has no
    /// matching CMS driver row (happens for legacy demo@fleet.example).
    /// Case (c) falls through to the VSAConfig defaults via the accessors
    /// below.
    var currentDriver: CurrentDriver?
    var currentVehicle: VehicleInfo?
    var currentDriverLoadedAt: Date?
    var currentDriverLoading: Bool = false
    var currentDriverError: String?

    /// Driver self-vehicle-claim state. Populated when a signed-in driver has no
    /// assigned vehicle and opens the Home tab claim picker. `claimableVehicles`
    /// is the driver's fleet inventory (CMS GET /api/v1/vehicles, fleet-scoped
    /// server-side). See HomeTabView.noVehicleClaimView + ClaimVehicleSheet.
    var claimableVehicles: [VehicleInfo] = []
    var claimableVehiclesLoading: Bool = false
    var claimError: String?

    /// Effective vehicleId for API calls. Prefers the auth-resolved vehicle;
    /// falls back to VSAConfig.demoVehicleId so the app still works before
    /// /drivers/me completes (or for unlinked Cognito users).
    var effectiveVehicleId: String {
        currentVehicle?.vehicleId ?? VSAConfig.demoVehicleId
    }

    /// Effective driverId for API calls and voice session headers.
    var effectiveDriverId: String {
        currentDriver?.driverId ?? VSAConfig.demoDriverId
    }

    /// Effective VIN for the voice session headers. Prefers auth-resolved
    /// vehicle's VIN, falls back to the VSAConfig demoVin.
    var effectiveVin: String {
        currentVehicle?.vin ?? VSAConfig.demoVin
    }

    /// True once every resource that backs the Home dashboard has
    /// completed its first load. Used by HomeTabView to decide whether
    /// to render the cards or a single loading skeleton — without this
    /// gate, each card renders immediately with default/stub values
    /// (e.g. health score 100, "0 trips", empty service list) and then
    /// flashes to the real values one resource at a time as each load
    /// completes. Drivers read that as "the app is broken and is
    /// downgrading my numbers."
    ///
    /// Resources tracked here are exactly the ones `refreshAll()` in
    /// HomeTabView fans out to: currentDriver, vehicleContext,
    /// liveState, scheduledService (via serviceHistory), and
    /// recentTrips. Pull-to-refresh on subsequent visits doesn't blank
    /// the dashboard because the timestamps stay non-nil; the cards
    /// update in place when new data lands.
    var hasLoadedInitialDashboard: Bool {
        currentDriverLoadedAt != nil
            && vehicleContextLoadedAt != nil
            && liveStateLoadedAt != nil
            && serviceHistoryLoadedAt != nil
            && recentTripsLoadedAt != nil
    }

    /// True once the Vehicle tab's data sources have loaded at least
    /// once. See `hasLoadedInitialDashboard` for the rationale (avoids
    /// stub-then-update flash on first render). Mirrors what
    /// `VehicleTabView.refresh()` actually fetches.
    var hasLoadedInitialVehicle: Bool {
        currentDriverLoadedAt != nil && liveStateLoadedAt != nil
    }

    /// True once the Alerts tab's data sources have loaded at least
    /// once. Alerts polls vehicle context (active DTCs) and safety
    /// events; the WebSocket-pushed real-time alerts live in
    /// `wsClient.alerts` and are present from connection time, so they
    /// don't gate the load.
    var hasLoadedInitialAlerts: Bool {
        vehicleContextLoadedAt != nil && safetyEventsLoadedAt != nil
    }

    /// True once the Service tab's data sources have loaded at least
    /// once. Single loader covers both scheduled and completed history.
    var hasLoadedInitialService: Bool {
        serviceHistoryLoadedAt != nil
    }

    /// Persona-driven layout selector. Drives tab labels and card
    /// visibility so OEM tenants get a "Dealer" tab instead of
    /// "Service" + a stripped-down Home, rental tenants get a
    /// renter-focused Home with trip-time-remaining + return-to
    /// cards, and fleet tenants get the broadest layout (current
    /// behavior). Resolved from `tenantConfig.segment`; falls back
    /// to `.fleet` when the config hasn't loaded yet or carries an
    /// unrecognized value, so a session always has something usable.
    var layoutSegment: LayoutSegment {
        LayoutSegment.from(tenantConfig?.segment)
    }

    /// Tracks whether the per-persona welcome banner on Home has
    /// been shown for the *current* signed-in session. Flipped to
    /// true the first time HomeTabView renders the banner, and
    /// reset to false on sign-out so a fresh sign-in re-arms it.
    /// Without this flag, the banner would re-fire every time the
    /// driver tabs back to Home — annoying for real use, distracting
    /// during a multi-step demo.
    var hasShownWelcomeForCurrentSession: Bool = false

    /// Active DTCs currently open on the signed-in driver's vehicle.
    /// Populated by the same /vehicles/{id}/context load that backs
    /// `currentVehicle` — no extra round-trip. Empty array when the
    /// endpoint hasn't been called yet OR the vehicle has no open
    /// faults. Read by AlertsTabView to render a "Vehicle faults"
    /// section alongside the existing triage alerts.
    ///
    /// Sort order (2026-05-04): most-critical first so the top-of-list
    /// is the thing drivers should care about without scrolling.
    /// Severity rank: CRITICAL(0) → HIGH(1) → MEDIUM(2) → LOW(3) →
    /// UNKNOWN(4). Ties broken by timestamp DESC (newer first). This
    /// matches the CMS-side VehicleDTCsTable ladder so CMS and iOS
    /// render DTCs in the same order.
    var activeDtcs: [ActiveDtc] {
        let raw = vehicleContext?.activeDtcs ?? []
        return raw.sorted { a, b in
            let ra = Self.severityRank(a.severity)
            let rb = Self.severityRank(b.severity)
            if ra != rb { return ra < rb }
            return (a.timestamp ?? 0) > (b.timestamp ?? 0)
        }
    }

    /// Canonical severity rank shared by Home banner / Alerts list /
    /// any future voice-prompting of DTCs. Lower = more severe.
    static func severityRank(_ sev: String?) -> Int {
        switch (sev ?? "").uppercased() {
        case "CRITICAL": return 0
        case "HIGH":     return 1
        case "MEDIUM":   return 2
        case "LOW":      return 3
        default:         return 4
        }
    }

    /// Convenience: count of active DTCs at or above a given severity
    /// rank. Used by HomeTabView to decide whether to render the red
    /// "critical alerts" banner.
    func activeDtcCount(minSeverityRank rank: Int) -> Int {
        activeDtcs.filter { Self.severityRank($0.severity) <= rank }.count
    }

    // MARK: - Recent trips (Home tab)

    var recentTrips: [TripSummary] = []
    var recentTripsLoadedAt: Date?
    var recentTripsLoading: Bool = false
    var recentTripsError: String?

    // MARK: - Safety events (Alerts tab — last 7 days)

    /// Driver-facing safety events over the last 7 days. Refreshed when the
    /// Alerts tab opens. Newest-first, server-sorted; iOS preserves that
    /// order for display.
    var safetyEvents: [SafetyEvent] = []
    var safetyEventsLoadedAt: Date?
    var safetyEventsLoading: Bool = false
    var safetyEventsError: String?

    /// Window actually used by the last successful fetch. Echoed back from
    /// the server; the UI renders it as "Last N days" so if we ever widen
    /// the default server-side, the UI tracks automatically.
    var safetyEventsWindowDays: Int = 7

    /// Per-device set of safety-event IDs the user has marked as reviewed.
    /// Persisted via UserDefaults; lost on reinstall, which is acceptable
    /// for this demo iteration (event rows still exist server-side; the
    /// badge just reverts to counting everything in-window).
    ///
    /// Event IDs are globally unique (safety.<type>-<ts>-<vehicleId>) so we
    /// don't need to namespace by vehicle. Bounded naturally by the 7-day
    /// window — old ack'd IDs stay in the set but cost nothing, and a
    /// periodic prune could be added if it ever matters.
    var reviewedSafetyEventIds: Set<String> = AppSession._loadReviewedIds()

    fileprivate static let _reviewedDefaultsKey = "vsa.reviewed_safety_event_ids"

    /// Load the reviewed-IDs set from UserDefaults at init time. Returns an
    /// empty set on any decode failure so a corrupted defaults blob never
    /// crashes the app — we just treat everything as unreviewed until the
    /// user re-acks.
    fileprivate static func _loadReviewedIds() -> Set<String> {
        guard let data = UserDefaults.standard.data(forKey: _reviewedDefaultsKey) else {
            return []
        }
        return (try? JSONDecoder().decode(Set<String>.self, from: data)) ?? []
    }

    fileprivate func _persistReviewedIds() {
        if let data = try? JSONEncoder().encode(reviewedSafetyEventIds) {
            UserDefaults.standard.set(data, forKey: Self._reviewedDefaultsKey)
        }
    }

    /// Mark a single event reviewed. Writes through to UserDefaults so the
    /// badge doesn't bounce back after an app relaunch.
    func markSafetyEventReviewed(_ eventId: String) {
        guard !reviewedSafetyEventIds.contains(eventId) else { return }
        reviewedSafetyEventIds.insert(eventId)
        _persistReviewedIds()
    }

    /// Mark every currently-loaded safety event reviewed. Used by the "Mark
    /// all reviewed" header affordance in AlertsTabView.
    func markAllSafetyEventsReviewed() {
        var changed = false
        for ev in safetyEvents {
            if !reviewedSafetyEventIds.contains(ev.eventId) {
                reviewedSafetyEventIds.insert(ev.eventId)
                changed = true
            }
        }
        if changed { _persistReviewedIds() }
    }

    /// Count of currently-loaded safety events the user hasn't reviewed yet.
    /// Feeds the Alerts-tab badge alongside critical DTCs.
    var unreviewedSafetyEventsCount: Int {
        safetyEvents.reduce(0) { acc, ev in
            acc + (reviewedSafetyEventIds.contains(ev.eventId) ? 0 : 1)
        }
    }

    /// Count of currently-active CRITICAL DTCs. Derived from `activeDtcs`,
    /// which already handles the severity normalisation + sort order.
    var criticalDtcCount: Int {
        activeDtcs.filter { ($0.severity ?? "").uppercased() == "CRITICAL" }.count
    }

    /// Combined Alerts-tab badge count: critical DTCs + unreviewed safety
    /// events. iOS TabView only supports a single numeric badge per tab, so
    /// we sum them rather than splitting. When either is zero it behaves
    /// exactly as if it was the only source.
    var alertsBadgeCount: Int {
        criticalDtcCount + unreviewedSafetyEventsCount + (wsClient?.alerts.filter { !$0.isRead }.count ?? 0)
    }

    // MARK: - Voice session (pre-warmed for instant mic response)

    /// Long-lived Nova Sonic voice session. Created once after sign-in and
    /// kept warm for the duration of the user's app session. Eliminates the
    /// ~5-second spinner between the user tapping the mic on the Assistant
    /// tab and actually being able to speak — connect + priming happen in
    /// the background while the user browses Home/Vehicle/Alerts tabs.
    ///
    /// Lifecycle:
    ///   - Created after sign-in completes (see SignInView).
    ///   - connect() starts the WebSocket + sends the priming text.
    ///   - Keep-alive pings every 30s so the AgentCore session's 55s idle
    ///     timeout doesn't fire while the user is on other tabs.
    ///   - AssistantTabView reads this existing instance and opens mic
    ///     instantly (no connect wait) when the user taps.
    ///   - Torn down on sign-out or app termination.
    var voiceSession: VoiceSessionViewModel?

    /// Prepend the newest triage to history, cap at 50 for memory hygiene.
    func recordTriage(_ r: TriageResponse) {
        lastTriage = r
        triageHistory.insert(r, at: 0)
        if triageHistory.count > 50 { triageHistory.removeLast(triageHistory.count - 50) }
    }

    /// Fetch the service-history bucket from the VSA REST API. Idempotent and
    /// debounced — safe to call from onAppear. Runs on MainActor because it
    /// mutates @Observable state directly.
    @MainActor
    func loadServiceHistory(client: VSAClient, force: Bool = false) async {
        // 15s freshness window — skip if we refreshed recently unless forced.
        if !force, let loadedAt = serviceHistoryLoadedAt,
           Date().timeIntervalSince(loadedAt) < 15 {
            return
        }
        guard !serviceHistoryLoading else { return }
        serviceHistoryLoading = true
        defer { serviceHistoryLoading = false }
        serviceHistoryError = nil

        do {
            let resp = try await client.getServiceHistory(vehicleId: effectiveVehicleId)
            scheduledService = resp.scheduled
            completedService = resp.completed
            serviceHistoryLoadedAt = Date()
        } catch {
            serviceHistoryError = error.localizedDescription
        }
    }

    /// Delete every CMS service-history row for the active vehicle that
    /// was created via Nova (source = voice-assistant). Called by the
    /// Reset Demo button on the Account tab — keeps the Service tab
    /// from accumulating "Tuesday 9 AM" demo bookings across runs.
    /// Returns a tuple the caller can use to update toast copy.
    /// Side effects: clears `scheduledService` / `completedService`
    /// in place and forces a refetch so the Service tab reflects
    /// reality without the user having to pull-to-refresh.
    @MainActor
    @discardableResult
    func purgeVsaDemoBookings() async -> (deleted: Int, error: String?) {
        guard case .signedIn(let token, _) = authState else {
            return (0, "Not signed in")
        }
        guard let vehicleId = currentVehicle?.vehicleId else {
            return (0, "No active vehicle")
        }
        let client = VSAClient(idTokenProvider: { token })
        do {
            let resp = try await client.deleteVsaServiceRecords(vehicleId: vehicleId)
            // Force a fresh fetch so iOS state matches DDB. Without
            // this the Service tab keeps showing the just-deleted
            // appointments until the 15s freshness window expires.
            await loadServiceHistory(client: client, force: true)
            return (resp.deleted, nil)
        } catch {
            return (0, error.localizedDescription)
        }
    }

    /// Fetch vehicle + driver context for the demo vehicle. Idempotent and
    /// debounced like loadServiceHistory. iOS Assistant tab calls this on
    /// first appear; it's decoupled from the voice session WebSocket so a
    /// slow CMS read doesn't delay connect().
    @MainActor
    func loadVehicleContext(client: VSAClient, force: Bool = false) async {
        // 60s freshness window — vehicle records change rarely, driver assignments rarely.
        if !force, let loadedAt = vehicleContextLoadedAt,
           Date().timeIntervalSince(loadedAt) < 60 {
            return
        }
        guard !vehicleContextLoading else { return }
        vehicleContextLoading = true
        defer { vehicleContextLoading = false }
        vehicleContextError = nil

        do {
            let resp = try await client.getVehicleContext(vehicleId: effectiveVehicleId)
            vehicleContext = resp
            vehicleContextLoadedAt = Date()
        } catch {
            vehicleContextError = error.localizedDescription
        }
    }

    /// Fetch fresh connection/telemetry state. When WebSocket is connected,
    /// this only runs on initial load or forced refresh — real-time updates
    /// come via push. Falls back to 30-second polling if WS is disconnected.
    @MainActor
    func loadLiveState(client: VSAClient, force: Bool = false) async {
        // Skip polling if WebSocket is delivering real-time updates
        if !force, wsClient?.isConnected == true, liveState != nil {
            return
        }
        if !force, let loadedAt = liveStateLoadedAt,
           Date().timeIntervalSince(loadedAt) < 30 {
            return
        }
        guard !liveStateLoading else { return }
        liveStateLoading = true
        defer { liveStateLoading = false }
        liveStateError = nil

        do {
            let resp = try await client.getLiveState(vehicleId: effectiveVehicleId)
            liveState = resp
            liveStateLoadedAt = Date()
        } catch {
            liveStateError = error.localizedDescription
        }
    }

    /// Connect WebSocket for real-time telemetry. Call after driver resolution.
    @MainActor
    func connectWebSocket(token: String) {
        let vehicleId = effectiveVehicleId
        let fleetId = currentVehicle?.fleetId ?? "default"
        let wsUrl = VSAConfig.telemetryWsUrl

        wsClient?.disconnect()
        wsClient = VehicleWebSocketClient(
            wsEndpoint: wsUrl,
            vehicleId: vehicleId,
            fleetId: fleetId,
            token: token
        )
        wsClient?.connect()
        
        // Observe incoming messages and map to liveState
        observeWebSocketMessages()
    }
    
    /// Map WebSocket telemetry messages to the liveState property the UI reads.
    @MainActor
    private func observeWebSocketMessages() {
        // Poll wsClient.lastMessage changes via a lightweight Task.
        // (In production, use Combine's @Published → sink, but @Observable
        // doesn't support Combine subscriptions directly without import.)
        Task { @MainActor [weak self] in
            var lastTimestamp: Double = 0
            while let self, self.wsClient != nil {
                if let msg = self.wsClient?.lastMessage, msg.timestamp != lastTimestamp {
                    lastTimestamp = msg.timestamp
                    // Map telemetry signals to VehicleLiveState
                    let signals = msg.signals
                    self.liveState = VehicleLiveState(
                        vehicleId: msg.vehicleId,
                        connectionStatus: "connected",
                        connectionStatusSource: "websocket",
                        lastConnectedAt: ISO8601DateFormatter().string(from: Date()),
                        lastSeenAgoSeconds: 0,
                        fuelLevel: signals?.fuelLevel,
                        batteryLevel: nil,
                        speed: signals?.speed,
                        engineTemp: signals?.engineTemp,
                        odometer: signals?.odometer,
                        batteryVoltage: signals?.batteryVoltage,
                        latitude: signals?.latitude,
                        longitude: signals?.longitude,
                        heading: nil
                    )
                    self.liveStateLoadedAt = Date()
                }
                try? await Task.sleep(nanoseconds: 500_000_000) // check every 0.5s
            }
        }
    }

    /// Disconnect WebSocket (sign-out or backgrounding).
    @MainActor
    func disconnectWebSocket() {
        wsClient?.disconnect()
        wsClient = nil
    }

    /// Resolve the signed-in Cognito user to their CMS driver + vehicle via
    /// /drivers/me. Call this once after successful sign-in. Idempotent.
    /// Never throws; on failure leaves currentDriver nil so the accessors
    /// fall back to VSAConfig defaults.
    @MainActor
    func loadCurrentDriver(client: VSAClient, force: Bool = false) async {
        // Driver assignment rarely changes within a session — 5-minute cache.
        if !force, let loadedAt = currentDriverLoadedAt,
           Date().timeIntervalSince(loadedAt) < 300 {
            return
        }
        guard !currentDriverLoading else { return }
        currentDriverLoading = true
        defer { currentDriverLoading = false }
        currentDriverError = nil

        do {
            let resp = try await client.getCurrentDriver()
            currentDriver = resp.driver
            currentVehicle = resp.vehicle
            currentDriverLoadedAt = Date()
            // If the user has a CMS driver row, also eagerly warm the
            // vehicle context cache using the same vehicle so the Assistant
            // tab's nameplate lands immediately.
            if let v = resp.vehicle {
                vehicleContext = VehicleContextResponse(
                    vehicleId: v.vehicleId,
                    vehicle: v,
                    driver: resp.driver.map {
                        DriverInfo(
                            driverId: $0.driverId,
                            firstName: $0.firstName,
                            lastName: $0.lastName,
                            email: $0.email,
                            phone: $0.phone,
                            homeBase: $0.homeBase,
                            safetyScore: $0.safetyScore,
                            licenseClass: $0.licenseClass,
                            licenseState: $0.licenseState
                        )
                    },
                    // /drivers/me doesn't return active DTCs (that's a
                    // /vehicles/{id}/context-only field). Pass nil here
                    // and let the Alerts tab's own load refresh populate
                    // it when the user navigates there.
                    activeDtcs: nil,
                    // Same story for the server-computed health score —
                    // /drivers/me doesn't compute it. Leave both nil so
                    // the Home tab shows a neutral 100 (its fallback)
                    // until loadVehicleContext lands and replaces this
                    // stub with the real, server-computed score.
                    healthScore: nil,
                    healthScoreBreakdown: nil,
                    generatedAt: resp.generatedAt
                )
                // Deliberately do NOT set vehicleContextLoadedAt here —
                // this is a stub with activeDtcs=nil, not a real fetch.
                // If we set the timestamp, loadVehicleContext's 60-second
                // cache short-circuits and the Alerts tab shows an empty
                // faults list for the first minute after sign-in (root
                // cause of the "No active faults" mis-report for VEH-0047
                // on 2026-05-06). Let the real context fetch earn its
                // own freshness stamp.
            }
        } catch {
            currentDriverError = error.localizedDescription
        }
    }

    /// Load the vehicles this driver may claim (their fleet's inventory) from the
    /// CMS API. No-op when the CMS API isn't configured (prod until wired) or the
    /// user isn't signed in. Backs the Home tab claim picker.
    @MainActor
    func loadClaimableVehicles() async {
        guard case .signedIn(let token, _) = authState else { return }
        guard VSAConfig.cmsRestApiUrl != nil else {
            claimError = "Vehicle claiming isn't available in this environment."
            return
        }
        guard !claimableVehiclesLoading else { return }
        claimableVehiclesLoading = true
        defer { claimableVehiclesLoading = false }
        claimError = nil
        do {
            let client = VSAClient(idTokenProvider: { token })
            let resp = try await client.getClaimableVehicles()
            // Only show vehicles that aren't already assigned to someone is not
            // knowable from this payload; the backend rejects cross-fleet and the
            // assign call displaces stale holders, so present the fleet list as-is.
            claimableVehicles = resp.vehicles
        } catch {
            claimError = error.localizedDescription
        }
    }

    /// Claim (self-assign) a vehicle via the CMS API, then refresh driver state so
    /// the Home tab dashboard renders. Throws on failure so the caller can surface
    /// an inline error. Requires a resolved `currentDriver` (driverId from
    /// /drivers/me) — the backend guard enforces that a driver may only assign
    /// their own record.
    @MainActor
    func claimVehicle(vehicleId: String) async throws {
        guard case .signedIn(let token, _) = authState else {
            throw APIError.unauthenticated
        }
        guard let driverId = currentDriver?.driverId, !driverId.isEmpty else {
            throw APIError.http(status: -1, body: "No driver profile resolved for this account.")
        }
        let client = VSAClient(idTokenProvider: { token })
        _ = try await client.claimVehicle(driverId: driverId, vehicleId: vehicleId)
        // Re-resolve driver → vehicle (force past the 5-min cache), then re-warm
        // the dashboard's dependent loads against the newly-assigned vehicle.
        await loadCurrentDriver(client: client, force: true)
        async let svc: Void = loadServiceHistory(client: client, force: true)
        async let trips: Void = loadRecentTrips(client: client, force: true)
        async let live: Void = loadLiveState(client: client, force: true)
        async let ctx: Void = loadVehicleContext(client: client, force: true)
        _ = await (svc, trips, live, ctx)
    }

    /// Load recent trips for the Home tab. Uses effectiveVehicleId so it
    /// works whether or not /drivers/me has resolved yet.
    @MainActor
    func loadRecentTrips(client: VSAClient, force: Bool = false) async {
        if !force, let loadedAt = recentTripsLoadedAt,
           Date().timeIntervalSince(loadedAt) < 60 {
            return
        }
        guard !recentTripsLoading else { return }
        recentTripsLoading = true
        defer { recentTripsLoading = false }
        recentTripsError = nil

        do {
            let resp = try await client.getTrips(vehicleId: effectiveVehicleId, limit: 10)
            recentTrips = resp.trips
            recentTripsLoadedAt = Date()
        } catch {
            recentTripsError = error.localizedDescription
        }
    }

    /// Load safety events for the Alerts tab. 30-second cache — matches the
    /// live-state loader since both are driven by the same pull-to-refresh
    /// on the Alerts tab. Failures are swallowed into `safetyEventsError`
    /// so a flaky load doesn't nuke previously-cached events (stale-while-
    /// error semantics).
    @MainActor
    func loadSafetyEvents(client: VSAClient, force: Bool = false) async {
        if !force, let loadedAt = safetyEventsLoadedAt,
           Date().timeIntervalSince(loadedAt) < 30 {
            return
        }
        guard !safetyEventsLoading else { return }
        safetyEventsLoading = true
        defer { safetyEventsLoading = false }
        safetyEventsError = nil

        do {
            let resp = try await client.getSafetyEvents(
                vehicleId: effectiveVehicleId, days: 7, limit: 50
            )
            safetyEvents = resp.events
            safetyEventsWindowDays = resp.windowDays
            safetyEventsLoadedAt = Date()
        } catch {
            safetyEventsError = error.localizedDescription
            // Mark the attempt as completed even on failure. `hasLoadedInitialAlerts`
            // gates the whole Alerts tab on `safetyEventsLoadedAt != nil`; if a
            // failed load left it nil forever, the tab spun on the loading
            // skeleton indefinitely (observed 2026-06-22 when the
            // /vehicles/{id}/safety-events route was absent on the deployed VSA
            // API). Safety events are non-essential to the tab (DTCs + realtime
            // render independently), and the safety-events section already shows
            // an inline error via `safetyEventsError`, so completing the attempt
            // is the correct fail-soft behavior.
            safetyEventsLoadedAt = Date()
        }
    }

    /// Pre-warm the Nova Sonic voice session in the background so the user
    /// doesn't see a spinner when they tap the mic button. Called after
    /// sign-in (SignInView) and on cached-token app launches
    /// (MainTabView.bootCoordinator). Safe to call repeatedly — idempotent
    /// via the `voiceSession` check.
    ///
    /// The VM's own connect() opens the WebSocket + sends the priming text.
    /// Priming response arrives and completes while the user is still on
    /// Home. By the time they reach Assistant, the session is ready for
    /// immediate mic input.
    ///
    /// Identity-drift guard (added 2026-05-07): if a voiceSession already
    /// exists but its pinned vin/vehicleId/driverId disagree with the
    /// current effective values (e.g. user signed out mid-session and
    /// back in as a different driver without the teardown running), we
    /// tear it down and rebuild. This prevents the "UI says Samantha
    /// but voice session sends Stephanie's headers" leak we hit on
    /// 2026-05-07.
    @MainActor
    func warmVoiceSession() async {
        // Diagnostic prefix `🎤 VOICE:` (shared with VoiceSessionViewModel
        // since this is part of the same lifecycle). Filter via:
        //   xcrun simctl spawn booted log stream --predicate \
        //     'eventMessage CONTAINS "🎤"' --style compact
        // Added 2026-05-27 alongside Voice/AssistantTabView/MainTabView
        // instrumentation (cvx/issues/2026-05-27-ios-bidi-websocket-not-connected).
        guard case .signedIn = authState else {
            NSLog("🎤 VOICE: warmVoiceSession skip — not signed in")
            return
        }

        if let existing = voiceSession {
            // Read the pinned identity fields off the existing VM and
            // compare against the current effective values. If they
            // drift, the session is stale — nuke it and fall through
            // to the re-warm path below.
            let drift = existing.vin != effectiveVin
                || existing.vehicleId != effectiveVehicleId
                || existing.driverId != effectiveDriverId
            if !drift {
                NSLog("🎤 VOICE: warmVoiceSession skip — existing fresh state=%@ vin=%@",
                      "\(existing.state)", existing.vin)
                return  // fresh enough
            }
            print("[VoiceSession] identity drift — tearing down stale session "
                  + "old(vin=\(existing.vin), vehicle=\(existing.vehicleId ?? "-"), driver=\(existing.driverId ?? "-")) "
                  + "new(vin=\(effectiveVin), vehicle=\(effectiveVehicleId), driver=\(effectiveDriverId))")
            NSLog("🎤 VOICE: warmVoiceSession identity drift detected — tearing down stale VM")
            await teardownVoiceSession()
        }

        NSLog("🎤 VOICE: warmVoiceSession creating new VM tenant=%@ vin=%@ vehicleId=%@ driverId=%@",
              activeTenantId, effectiveVin, effectiveVehicleId, effectiveDriverId)
        let vm = VoiceSessionViewModel(
            tenantId: activeTenantId,
            vin: effectiveVin,
            vehicleId: effectiveVehicleId,
            driverId: effectiveDriverId,
            jwtProvider: { [weak self] in
                guard let self else { return nil }
                if case .signedIn(let t, _) = self.authState { return t }
                return nil
            },
            // Pull fresh coords off liveState (Redis-backed, same data
            // the Vehicle tab map shows). Sent as headers so the agent
            // runtime can skip its own /live-state HTTP roundtrip.
            // Returns nil if liveState hasn't loaded yet — the backend
            // will fall back to its own lookup in that case.
            locationProvider: { [weak self] in
                guard let self,
                      let lat = self.liveState?.latitude,
                      let lng = self.liveState?.longitude
                else { return nil }
                return (lat, lng)
            }
        )
        voiceSession = vm
        NSLog("🎤 VOICE: warmVoiceSession calling vm.connect()")
        await vm.connect()
        NSLog("🎤 VOICE: warmVoiceSession vm.connect() returned state=%@", "\(vm.state)")
    }

    /// Tear down the voice session. Called on sign-out.
    @MainActor
    func teardownVoiceSession() async {
        NSLog("🎤 VOICE: teardownVoiceSession entry voiceSession=%@",
              voiceSession == nil ? "nil" : "non-nil")
        if let vm = voiceSession {
            await vm.disconnect()
        }
        voiceSession = nil
        NSLog("🎤 VOICE: teardownVoiceSession complete")
    }

    /// Fully sign the current user out and reset all per-user state.
    /// Called from AccountTabView when the user taps "Sign Out".
    ///
    /// Previously (<=2026-05-07) sign-out was just `authState = .signedOut`,
    /// which left every user-scoped field in place. When the user signed
    /// back in as a different driver (demo with Samantha after using
    /// Stephanie), the Assistant tab still displayed Stephanie's cached
    /// driver + vehicle context, and the pre-warmed voice session was
    /// still pinned to Stephanie's VIN — so triage fired against the
    /// wrong vehicle and no P0 classification landed. This method wipes
    /// every user-scoped field so the next sign-in starts from a clean
    /// slate.
    ///
    /// Keeps: tenantConfig, activeTenantId, severityFilter,
    /// reviewedSafetyEventIds (per-device, not per-user).
    @MainActor
    func signOut() async {
        // Stop the live voice session first so its WebSocket is gone
        // before we drop the token — otherwise the server sees a
        // mid-session auth revocation instead of a clean disconnect.
        await teardownVoiceSession()
        
        // Disconnect telemetry WebSocket
        disconnectWebSocket()

        // Clear Cognito tokens + refresh from keychain.
        await AuthService().signOut()

        // Null every user-scoped field. @Observable broadcasts the
        // change so any view currently on-screen (Account, Home,
        // Vehicle, Alerts, Service, Assistant) re-renders with empty
        // state before the SignInView takes over.
        currentDriver = nil
        currentVehicle = nil
        currentDriverLoadedAt = nil
        currentDriverError = nil

        vehicleContext = nil
        vehicleContextLoadedAt = nil
        vehicleContextError = nil

        liveState = nil
        liveStateLoadedAt = nil
        liveStateError = nil

        scheduledService = []
        completedService = []
        serviceHistoryLoadedAt = nil
        serviceHistoryError = nil

        recentTrips = []
        recentTripsLoadedAt = nil
        recentTripsError = nil

        safetyEvents = []
        safetyEventsLoadedAt = nil
        safetyEventsError = nil

        lastTriage = nil
        triageHistory = []

        // Reset the per-session welcome flag so the next sign-in
        // re-arms the banner even if the same persona signs back in.
        hasShownWelcomeForCurrentSession = false

        // Finally flip the auth flag — after this the root view swaps
        // MainTabView for SignInView and the user can sign in fresh.
        authState = .signedOut
    }

    var filteredTriageHistory: [TriageResponse] {
        switch severityFilter {
        case .all:
            return triageHistory
        case .highOnly:
            return triageHistory.filter { $0.classification == "P0" || $0.classification == "P1" }
        case .lowOnly:
            return triageHistory.filter { $0.classification == "P2" || $0.classification == "P3" }
        }
    }
}

enum SeverityFilter: String, CaseIterable, Identifiable {
    case all, highOnly, lowOnly
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all:      return "All"
        case .highOnly: return "P0 · P1"
        case .lowOnly:  return "P2 · P3"
        }
    }
}

enum AuthState: Equatable {
    case signedOut
    case signingIn
    case signedIn(idToken: String, email: String)
    case failed(String)
}
