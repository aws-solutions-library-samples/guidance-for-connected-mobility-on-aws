import SwiftUI

/// Alerts tab — driver-relevant signals only.
///
/// Sections, top to bottom:
///   - Vehicle faults (critical DTCs by default, filter chips for High+/All)
///   - Safety events (last 7 days: harsh braking, phone usage, etc.)
///   - Triage alerts (classifier decisions from voice sessions)
///
/// Rewritten 2026-05-06. Service sections (Upcoming Service + Service
/// History) moved to a dedicated ServiceTabView so Alerts isn't a mixed
/// bag of service-is-scheduled + something-needs-your-attention. See
/// commit notes on the new endpoint GET /vehicles/{id}/safety-events.
struct AlertsTabView: View {
    @Environment(AppSession.self) private var session
    let theme: TenantTheme

    /// Optional callback invoked when the user taps the info (i)
    /// button on a DTC row. Receives a primed prompt string that the
    /// caller (typically MainTabView) hands to the assistant cover so
    /// Nova starts the conversation already focused on that specific
    /// fault. nil hides the info button entirely — useful for
    /// previews and any future read-only Alerts surface.
    var onAskAboutDtc: ((String) -> Void)? = nil

    /// Local filter for the DTC section. Default `critical` so the driver
    /// sees only the things they'd actually call a shop about; they can
    /// widen to High+ or All without leaving the tab. The Home tab's
    /// alerts banner can also override this through MainTabView when
    /// the driver taps a "X high-severity alerts" banner — in that
    /// case the filter is set to `.highPlus` so the alerts the
    /// banner promised are actually visible. State lives on
    /// MainTabView (via a binding) so external nav can update it.
    @Binding var dtcFilter: DtcFilter

    var body: some View {
        @Bindable var session = session
        return NavigationStack {
            ScrollView {
                if session.hasLoadedInitialAlerts {
                    VStack(spacing: 16) {
                        realtimeAlertsSection
                        activeDtcsSection(session: session)
                        safetyEventsSection(session: session)
                    }
                    .padding(.bottom, 24)
                } else {
                    // Three sections (real-time / DTCs / safety events).
                    TabLoadingSkeleton(cardCount: 3)
                }
            }
            .refreshable {
                await refresh(force: true)
            }
            .task {
                // Poll-while-visible. `.task` is cancelled automatically
                // when the view disappears (e.g., tab switch) and fires
                // fresh when it reappears, so the loop's lifetime matches
                // the tab's visible window.
                //
                // Motivation (2026-05-11): drivers watching this tab
                // during a trip-simulator run noticed up to ~5s between
                // a critical fault firing and it appearing. Root cause
                // was the 60s cache on `loadVehicleContext` combined
                // with `.task`-only refresh — even tab-switching back
                // in didn't bust the cache if you returned within the
                // window. Polling at 3s with force-refresh closes that
                // gap end-to-end (trip sim fires DTC → backend writes
                // dtc-history row → next poll tick picks it up).
                //
                // Caveat: this costs ~20 API requests/min per active
                // viewer. For production we'd swap this for a push
                // channel (WebSocket or APNS delivery of DTC-created
                // events); for the demo it's cheap and reliable.
                // Poll-while-visible at 15s (was 3s). The 3s cadence was
                // chosen for trip-sim responsiveness, but it produced
                // ~20 force-refresh requests per minute on the
                // /vehicles/{id}/context Lambda even when the user
                // wasn't on the Alerts tab — TabView keeps `.task` running
                // for all tabs after first mount, so this loop never
                // pauses just because the user switched away. 15s drops
                // load 5× while still picking up new faults within ~the
                // duration the driver would notice anyway. For
                // production we'd replace this with a push channel
                // (WebSocket or APNS) so the polling cadence becomes 0.
                while !Task.isCancelled {
                    await refresh(force: true)
                    try? await Task.sleep(nanoseconds: 15_000_000_000)
                }
            }
            .navigationTitle("Alerts")
            .navigationBarTitleDisplayMode(.inline)
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
        }
    }

    // MARK: - Real-time push alerts (from WebSocket)
    
    @ViewBuilder
    private var realtimeAlertsSection: some View {
        let alerts = session.wsClient?.alerts ?? []
        if !alerts.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: "bolt.fill")
                        .foregroundStyle(.orange)
                    Text("Real-time Alerts")
                        .font(.headline)
                    Spacer()
                    let unread = alerts.filter { !$0.isRead }.count
                    if unread > 0 {
                        Text("\(unread) new")
                            .font(.caption.bold())
                            .foregroundStyle(.white)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(.red))
                    }
                }
                .padding(.horizontal)
                
                ForEach(alerts.prefix(5)) { alert in
                    HStack(spacing: 10) {
                        Circle()
                            .fill(alertColor(alert.severity))
                            .frame(width: 10, height: 10)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(alert.title)
                                    .font(.subheadline.bold())
                                if let dtc = alert.dtcCode {
                                    Text(dtc)
                                        .font(.caption.monospaced())
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Text(alert.description)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer()
                        Text(alert.timestamp, style: .relative)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 6)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(alert.isRead ? Color.clear : Color(.systemGray6))
                    )
                }
            }
            .padding(.top, 8)
        }
    }
    
    private func alertColor(_ severity: Int) -> Color {
        switch severity {
        case 0: return .red
        case 1: return .orange
        case 2: return .yellow
        default: return .blue
        }
    }

    // MARK: - Refresh

    private func refresh(force: Bool) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        let client = VSAClient(idTokenProvider: { token })
        // Three parallel loads feed the three sections. DTCs come in on
        // the vehicle-context payload (so AppSession.activeDtcs updates
        // implicitly). Service history is no longer fetched here — it's
        // driven by the new ServiceTabView.
        async let vcx: Void = session.loadVehicleContext(client: client, force: force)
        async let se: Void = session.loadSafetyEvents(client: client, force: force)
        _ = await (vcx, se)
    }

    // MARK: - DTC section

    @ViewBuilder
    private func activeDtcsSection(session: AppSession) -> some View {
        // Apply persona-driven minimum severity FIRST. Rental drivers
        // see only CRITICAL+HIGH faults — anything they can't safely
        // act on (low tire pressure, sensor calibration drift) is the
        // rental company's problem, not theirs. Fleet/OEM see all
        // severities and rely on the user-controlled chip below to
        // narrow the view.
        let segmentMin = session.layoutSegment.minAlertSeverity
        let all = session.activeDtcs.filter { dtc in
            AlertSeverity.from(dtc.severity) <= segmentMin
        }
        let filtered = filteredDtcs(all)
        let criticalCount = all.filter { ($0.severity ?? "").uppercased() == "CRITICAL" }.count
        // Hide the user-controlled severity picker for rental drivers —
        // they only ever see CRITICAL+HIGH so the chip is misleading.
        let showsUserPicker = session.layoutSegment != .rental

        SectionCard(theme: theme) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(criticalCount > 0 ? .red : theme.primary)
                    Text("Vehicle faults").font(.headline)
                    Spacer()
                    Text(countSummary(all: all))
                        .font(.caption2).foregroundStyle(.secondary)
                }

                // Filter chips — only show when there's something to filter
                // AND the persona allows user-driven severity control.
                // Rental drivers always see CRITICAL+HIGH only (segment-
                // applied above); the chip would be misleading, so we hide it.
                if !all.isEmpty && showsUserPicker {
                    Picker("Filter", selection: $dtcFilter) {
                        ForEach(DtcFilter.allCases) { f in
                            Text(f.label).tag(f)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                if all.isEmpty {
                    dtcEmptyState(allClear: true, other: 0)
                } else if filtered.isEmpty {
                    // No rows match the current filter but other severities
                    // exist — nudge the driver toward a wider filter rather
                    // than showing a raw empty state.
                    let otherCount = all.count
                    dtcEmptyState(allClear: criticalCount == 0, other: otherCount)
                } else {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(filtered) { dtc in
                            dtcRow(dtc)
                            if dtc.id != filtered.last?.id {
                                Divider()
                            }
                        }
                    }
                }
            }
        }
    }

    /// Descriptive header counter like "1 critical · 4 total" or "4 total".
    private func countSummary(all: [ActiveDtc]) -> String {
        if all.isEmpty { return "" }
        let critical = all.filter { ($0.severity ?? "").uppercased() == "CRITICAL" }.count
        if critical == 0 {
            return "\(all.count) total"
        }
        return "\(critical) critical · \(all.count) total"
    }

    @ViewBuilder
    private func dtcEmptyState(allClear: Bool, other: Int) -> some View {
        HStack(spacing: 10) {
            Image(systemName: allClear ? "checkmark.seal.fill" : "line.3.horizontal.decrease.circle")
                .foregroundStyle(allClear ? .green : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(allClear
                     ? "No active faults — all clear."
                     : "No faults match this filter.")
                    .font(.subheadline).bold()
                if !allClear {
                    Text("\(other) active — switch to All to view.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
        .padding(.vertical, 4)
    }

    /// Apply the DTC filter. Uses the same severity-rank helper AppSession
    /// exposes so the ordering and definitions match across the app.
    private func filteredDtcs(_ dtcs: [ActiveDtc]) -> [ActiveDtc] {
        switch dtcFilter {
        case .critical:
            return dtcs.filter { ($0.severity ?? "").uppercased() == "CRITICAL" }
        case .highPlus:
            return dtcs.filter {
                let s = ($0.severity ?? "").uppercased()
                return s == "CRITICAL" || s == "HIGH"
            }
        case .all:
            return dtcs
        }
    }

    /// Single-row rendering of an active DTC. Layout mirrors the card used
    /// on the Vehicle tab so drivers see a consistent shape across places.
    @ViewBuilder
    private func dtcRow(_ dtc: ActiveDtc) -> some View {
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
                            .background(
                                Capsule().fill(severityColor(sev).opacity(0.15))
                            )
                            .foregroundStyle(severityColor(sev))
                    }
                    if let system = dtc.system, !system.isEmpty {
                        Text(system.capitalized)
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let desc = dtc.description, !desc.isEmpty {
                    Text(desc).font(.caption).foregroundStyle(.secondary)
                        .lineLimit(3)
                }
                if let src = dtc.source, !src.isEmpty {
                    Text("via \(src)")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            Spacer(minLength: 0)
            // "Ask Nova about this fault" button. Opens the voice
            // assistant with a primed prompt scoped to this specific
            // code so the driver doesn't have to type or speak the
            // 5-character DTC themselves. Hidden when the parent
            // didn't supply onAskAboutDtc (preview / read-only path).
            if onAskAboutDtc != nil {
                Button {
                    onAskAboutDtc?(dtcPromptFor(dtc))
                } label: {
                    Image(systemName: "info.circle")
                        .foregroundStyle(theme.primary)
                        .font(.title3)
                        .padding(8)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Ask assistant about \(dtc.code)")
            }
        }
        .padding(.vertical, 2)
    }

    /// Build the primed prompt sent to Nova when the driver taps the
    /// info button on a DTC row. The format favors what the agent's
    /// knowledge tool retrieves cleanly: "What does {CODE} mean..."
    /// returns the right RAG hit on the vehicle technical reference
    /// corpus. Including the description gives the driver context
    /// even if knowledge retrieval fails for some reason.
    private func dtcPromptFor(_ dtc: ActiveDtc) -> String {
        let desc = (dtc.description ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if desc.isEmpty {
            return "What does the \(dtc.code) fault mean for my vehicle?"
        }
        return "What does the \(dtc.code) fault — \"\(desc)\" — mean for my vehicle?"
    }

    // MARK: - Safety events section

    @ViewBuilder
    private func safetyEventsSection(session: AppSession) -> some View {
        let events = session.safetyEvents
        let unreviewed = session.unreviewedSafetyEventsCount

        SectionCard(theme: theme) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Image(systemName: "figure.seated.seatbelt")
                        .foregroundStyle(unreviewed > 0 ? .orange : theme.primary)
                    Text("Safety Events").font(.headline)
                    Spacer()
                    Text("Last \(session.safetyEventsWindowDays) days")
                        .font(.caption2).foregroundStyle(.secondary)
                }

                if unreviewed > 0 {
                    HStack(spacing: 8) {
                        Text("\(unreviewed) unreviewed")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button {
                            session.markAllSafetyEventsReviewed()
                        } label: {
                            Text("Mark all reviewed")
                                .font(.caption).bold()
                        }
                        .buttonStyle(.borderless)
                    }
                }

                if let err = session.safetyEventsError, events.isEmpty {
                    HStack(spacing: 10) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Couldn't load safety events").font(.subheadline).bold()
                            Text(err).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                } else if events.isEmpty {
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("No events in the last 7 days")
                                .font(.subheadline).bold()
                            Text(session.safetyEventsLoading
                                 ? "Refreshing…"
                                 : "Harsh braking, phone usage, and other events show up here when they happen.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                } else {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(events) { ev in
                            SafetyEventRow(
                                event: ev,
                                reviewed: session.reviewedSafetyEventIds.contains(ev.eventId),
                                theme: theme,
                                onMarkReviewed: {
                                    session.markSafetyEventReviewed(ev.eventId)
                                }
                            )
                            if ev.id != events.last?.id {
                                Divider()
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - Severity helpers (shared by DTC + safety rows)

    /// Severity → color. Comparison is case-insensitive because upstream
    /// rows use inconsistent casing ("CRITICAL" vs "critical").
    fileprivate static func severityColor(_ severity: String?) -> Color {
        switch (severity ?? "").uppercased() {
        case "CRITICAL": return .red
        case "HIGH": return .orange
        case "MEDIUM", "MODERATE": return .yellow
        case "LOW": return .blue
        default: return .gray
        }
    }

    fileprivate static func severityIcon(_ severity: String?) -> String {
        switch (severity ?? "").uppercased() {
        case "CRITICAL": return "exclamationmark.octagon.fill"
        case "HIGH": return "exclamationmark.triangle.fill"
        case "MEDIUM", "MODERATE": return "exclamationmark.circle.fill"
        case "LOW": return "info.circle.fill"
        default: return "questionmark.circle"
        }
    }

    // Instance wrappers so the existing dtcRow call sites don't need touching.
    private func severityColor(_ s: String?) -> Color { Self.severityColor(s) }
    private func severityIcon(_ s: String?) -> String { Self.severityIcon(s) }
}

// MARK: - DTC filter chip model

/// Pick 'em list for the DTC segmented picker. Severity rank semantics match
/// `AppSession.severityRank`; we duplicate the rule locally so this enum
/// stays self-contained.
enum DtcFilter: String, CaseIterable, Identifiable {
    case critical
    case highPlus
    case all

    var id: String { rawValue }
    var label: String {
        switch self {
        case .critical: return "Critical"
        case .highPlus: return "High+"
        case .all:      return "All"
        }
    }
}

// MARK: - Safety event row

/// One row in the Safety Events list. Visual cues:
///   - leading severity-colour dot (blue = unreviewed, gray = reviewed)
///   - event-type label in `monospaced` small text next to the dot
///   - description line below, truncated
///   - right-hand cluster: relative time + resolved flag when set
///
/// Swipe trailing: "Reviewed" action (blue, non-destructive). Matches
/// Mail.app's "Mark as Read" convention — not a delete, just an ack.
private struct SafetyEventRow: View {
    let event: SafetyEvent
    let reviewed: Bool
    let theme: TenantTheme
    let onMarkReviewed: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(reviewed ? Color.secondary.opacity(0.35) : AlertsTabView.severityColor(event.severity))
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(prettyType(event.eventType))
                        .font(.subheadline).bold()
                    Text(event.severity.uppercased())
                        .font(.caption2).bold()
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(
                            Capsule()
                                .fill(AlertsTabView.severityColor(event.severity).opacity(0.15))
                        )
                        .foregroundStyle(AlertsTabView.severityColor(event.severity))
                    Spacer()
                    if let occurred = event.occurredAt {
                        Text(relativeTimeString(from: occurred))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let desc = event.description, !desc.isEmpty {
                    Text(desc).font(.caption).foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                if event.resolved == true {
                    Text("Resolved")
                        .font(.caption2)
                        .foregroundStyle(.green)
                }
            }
        }
        .opacity(reviewed ? 0.55 : 1.0)
        .padding(.vertical, 2)
        .contentShape(Rectangle())
        .swipeActions(edge: .trailing) {
            if !reviewed {
                Button {
                    onMarkReviewed()
                } label: {
                    Label("Reviewed", systemImage: "checkmark.circle")
                }
                .tint(.blue)
            }
        }
    }

    /// "harsh_acceleration" → "Harsh acceleration". Falls back to the raw
    /// value when there's nothing to prettify.
    private func prettyType(_ raw: String) -> String {
        let cleaned = raw.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
        guard !cleaned.isEmpty else { return "Safety event" }
        return cleaned.prefix(1).uppercased() + cleaned.dropFirst()
    }
}

