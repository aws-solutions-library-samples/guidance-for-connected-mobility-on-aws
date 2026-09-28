import SwiftUI

/// Remote vehicle controls, presented as a single sheet from the Home hero.
///
/// One sheet rather than per-command buttons in the action row: it keeps the row
/// scannable, groups related commands under the catalog's own categories, and gives
/// one place to explain an offline vehicle instead of repeating that state on every
/// control.
///
/// ## Confirmation model
///
/// A command's POST returning 200 means **published to MQTT** — status `SENT` — not
/// actuated. Confirmation therefore comes from polling `GET /api/commands/{id}` for
/// that `commandId` to reach `SUCCEEDED`.
///
/// The original design confirmed *stateful* commands (locks, charge door) by reading
/// the mutated field back from live state, which is stronger evidence than an ACK.
/// That is not possible today: `VehicleLiveState` exposes fuel, battery, speed,
/// engine temp, odometer and position — and no door, lock or charge-door field. So
/// every command here is confirmed the same way, by its own status. When live state
/// gains those fields, stateful commands should switch to read-back, because an ACK
/// says the vehicle received the instruction while a state read says it obeyed.
///
/// Nothing here reports success on a 200. An unconfirmed command reads as in-flight,
/// and then as "no response from vehicle" — never as done. That distinction is the
/// whole point: a command path that was completely dead once stayed green for six
/// days precisely because a publish was treated as an outcome.
struct VehicleControlsSheet: View {
    let vehicleId: String
    let theme: TenantTheme
    let client: VSAClient
    /// Whether the vehicle is reachable. Controls stay visible but disabled when
    /// false, with the reason stated — hiding them would imply the car has no
    /// remote features at all.
    let isConnected: Bool
    var onDone: () -> Void

    @State private var catalog: [CommandCatalogEntry] = []
    @State private var isLoadingCatalog = true
    @State private var loadError: String?
    /// commandName → outcome of the most recent invocation in this sheet session.
    @State private var outcomes: [String: CommandOutcome] = [:]

    /// Lifecycle of one invocation, as observed rather than assumed.
    enum CommandOutcome: Equatable {
        case sending
        /// Published, awaiting the vehicle's acknowledgement.
        case pending(commandId: String)
        case confirmed
        /// Published, but no acknowledgement arrived inside the wait window. This is
        /// deliberately distinct from `.failed`: the command may yet execute, and
        /// claiming failure would be as wrong as claiming success.
        case unacknowledged
        case failed(String)
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoadingCatalog {
                    ProgressView("Loading controls…").tint(theme.primary)
                } else if let loadError {
                    ContentUnavailableView(
                        "Controls unavailable",
                        systemImage: "exclamationmark.triangle",
                        description: Text(loadError)
                    )
                } else {
                    controlsList
                }
            }
            .navigationTitle("Controls")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done", action: onDone)
                }
            }
        }
        .task { await loadCatalog() }
    }

    private var controlsList: some View {
        List {
            if !isConnected {
                Section {
                    Label(
                        "Your vehicle is offline. Commands will not reach it until it reconnects.",
                        systemImage: "wifi.slash"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            }
            ForEach(groupedCategories, id: \.self) { category in
                Section(category.capitalized) {
                    ForEach(entries(in: category)) { entry in
                        controlRow(entry)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func controlRow(_ entry: CommandCatalogEntry) -> some View {
        let outcome = outcomes[entry.commandName]
        Button {
            Task { await send(entry) }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: symbol(for: entry.commandName))
                    .foregroundStyle(theme.primary)
                    .frame(width: 26)
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.label)
                        .foregroundStyle(.primary)
                    if let status = statusText(outcome) {
                        Text(status)
                            .font(.caption2)
                            .foregroundStyle(statusTint(outcome))
                    }
                }
                Spacer()
                switch outcome {
                case .sending, .pending:
                    ProgressView().controlSize(.small)
                case .confirmed:
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                case .unacknowledged:
                    Image(systemName: "questionmark.circle.fill").foregroundStyle(.orange)
                case .failed:
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                case nil:
                    EmptyView()
                }
            }
        }
        .disabled(!isConnected || outcome == .sending || isPending(outcome))
    }

    // MARK: - Presentation helpers

    private func isPending(_ o: CommandOutcome?) -> Bool {
        if case .pending = o { return true }
        return false
    }

    private func statusText(_ o: CommandOutcome?) -> String? {
        switch o {
        case .sending:         return "Sending…"
        case .pending:         return "Sent — waiting for the vehicle"
        case .confirmed:       return "Confirmed by vehicle"
        case .unacknowledged:  return "No response yet — it may still complete"
        case .failed(let m):   return m
        case nil:              return nil
        }
    }

    private func statusTint(_ o: CommandOutcome?) -> Color {
        switch o {
        case .confirmed:      return .green
        case .unacknowledged: return .orange
        case .failed:         return .red
        default:              return .secondary
        }
    }

    /// Icons are chosen here rather than taken from the catalog because the catalog
    /// carries no symbol. Unknown commands fall back to a neutral glyph instead of
    /// a wrong one.
    private func symbol(for commandName: String) -> String {
        switch commandName {
        case "lock_all_doors":        return "lock.fill"
        case "remote_start":          return "power"
        case "start_preconditioning": return "thermometer.sun"
        case "flash_hazards":         return "light.beacon.max"
        case "find_my_vehicle":       return "location.viewfinder"
        case "open_charge_door":      return "bolt.car"
        case "panic_mode":            return "exclamationmark.triangle.fill"
        default:                      return "slider.horizontal.3"
        }
    }

    private var groupedCategories: [String] {
        Array(Set(catalog.map(\.category))).sorted()
    }

    private func entries(in category: String) -> [CommandCatalogEntry] {
        let inCategory = catalog.filter { $0.category == category }
        // Preserve the allowlist's featured ordering; anything outside it sorts after.
        return inCategory.sorted { a, b in
            let ia = RemoteCommandAllowlist.featured.firstIndex(of: a.commandName) ?? .max
            let ib = RemoteCommandAllowlist.featured.firstIndex(of: b.commandName) ?? .max
            return ia == ib ? a.label < b.label : ia < ib
        }
    }

    // MARK: - Networking

    private func loadCatalog() async {
        do {
            let response = try await client.commandCatalog()
            // Flatten, then keep ONLY the featured allowlist. The catalog advertises
            // 48 actuators and 37 of them have no implementation behind them; see
            // `RemoteCommandAllowlist` for the reconciliation.
            let all = response.actuators.values.flatMap { $0 }
            let featured = Set(RemoteCommandAllowlist.featured)
            catalog = all.filter { featured.contains($0.commandName) }
            if catalog.isEmpty {
                loadError = "No supported controls are available for this vehicle."
            }
        } catch {
            loadError = "Could not load controls. \(error.localizedDescription)"
        }
        isLoadingCatalog = false
    }

    private func send(_ entry: CommandCatalogEntry) async {
        outcomes[entry.commandName] = .sending
        do {
            // Booleans are sent as "1". The backend stores `str(value)` and two
            // producer paths already disagree on this shape ("1" vs "1.0") per
            // `issues/2026-08-04-actuator-value-shape-divergence`; sending a JSON
            // `true` would introduce a third. Non-booleans are not offered yet —
            // every featured command is a boolean — so there is no value picker.
            let response = try await client.sendCommand(
                vehicleId: vehicleId,
                commandName: entry.commandName,
                value: "1",
                label: entry.label,
                category: entry.category
            )
            outcomes[entry.commandName] = .pending(commandId: response.commandId)
            await awaitConfirmation(of: response.commandId, for: entry)
        } catch {
            outcomes[entry.commandName] = .failed(error.localizedDescription)
        }
    }

    /// Seconds between history polls while awaiting confirmation.
    static let pollIntervalSeconds: Double = 1.2

    /// Bounds on the wait window.
    ///
    /// The floor is tied to `pollIntervalSeconds` deliberately: a window shorter
    /// than two polls yields at most one chance to observe confirmation, and a
    /// window shorter than one poll yields none at all — every command would
    /// report `.unacknowledged` regardless of what the vehicle did. A catalog
    /// typo of `"3"` (3 ms) would do exactly that.
    ///
    /// The ceiling stops a typo in the other direction (`"100000"`) from holding
    /// the sheet open for a hundred seconds. Same discipline the platform already
    /// applies elsewhere — `route_length` is clamped to [5, 60] and `tripsCount`
    /// to [1, 99] precisely so one bad value cannot hang a run.
    static let minWaitSeconds: Double = pollIntervalSeconds * 2
    static let maxWaitSeconds: Double = 30

    /// Wait window when the catalog does not carry `responseTimeout` at all.
    ///
    /// This is the CATALOG-UNREACHABLE case only. The backend supplies its own
    /// default (`str(act.get('responseTimeout', 5000))`) whenever an entry simply
    /// omits the attribute, so "field absent from the payload" now means the
    /// payload itself is old or the catalog fetch degraded — a different failure
    /// that legitimately deserves a more generous window than the backend's 5 s.
    /// Previously this value competed with the backend's default for the same
    /// condition and only one of the two could ever apply.
    static let fallbackWaitSeconds: Double = 10

    /// Resolve the confirmation wait window from a catalog entry, clamped.
    ///
    /// Pure and static so it is testable without a live sheet.
    static func waitWindowSeconds(for responseTimeout: String?) -> Double {
        guard let raw = responseTimeout,
              let ms = Double(raw.trimmingCharacters(in: .whitespaces)),
              ms.isFinite, ms > 0 else {
            return fallbackWaitSeconds
        }
        return min(max(ms / 1000, minWaitSeconds), maxWaitSeconds)
    }

    /// Poll command history until this command is confirmed, fails, or the window
    /// closes. The window comes from the catalog's own `responseTimeout` where
    /// present, so the wait matches what the platform expects rather than a number
    /// invented here — clamped to a range that keeps it pollable. See
    /// issues/2026-08-19-cms-command-catalog-response-timeout-contract/.
    private func awaitConfirmation(of commandId: String, for entry: CommandCatalogEntry) async {
        let window = Self.waitWindowSeconds(for: entry.responseTimeout)
        let deadline = Date().addingTimeInterval(window)
        while Date() < deadline {
            try? await Task.sleep(nanoseconds: UInt64(Self.pollIntervalSeconds * 1_000_000_000))
            guard let history = try? await client.commandHistory(vehicleId: vehicleId),
                  let row = history.commands.first(where: { $0.commandId == commandId })
            else { continue }
            if row.isConfirmed {
                outcomes[entry.commandName] = .confirmed
                return
            }
            if row.isFailed {
                outcomes[entry.commandName] = .failed("Vehicle reported \(row.status.lowercased())")
                return
            }
        }
        outcomes[entry.commandName] = .unacknowledged
    }
}
