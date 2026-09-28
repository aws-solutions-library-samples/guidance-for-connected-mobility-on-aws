import SwiftUI

/// Developer-only presenter controls for demo scenarios.
///
/// Contains two sections:
///   1. **Scenarios** — existing telemetry scenarios (unchanged).
///   2. **Acquire scenarios** — new, additive section for the Discover/Buy
///      journey path controls (Path A cold-entry vs Path B returning-owner
///      warm-start mock trigger).
///
/// Reached via the Account tab's developer unlock (4 avatar taps).
struct PresenterControls: View {
    // MARK: - Telemetry (existing, unchanged)

    let currentScenario: TelemetryScenario
    var onScenarioSelected: (TelemetryScenario) -> Void

    // MARK: - Acquire (new, additive)

    /// Current Acquire scenario. Read from `session._pathBActive` so the
    /// checkmark reflects live state when the sheet is re-opened.
    @Environment(AppSession.self) private var session

    @Environment(\.dismiss) private var dismiss

    // MARK: - Body

    var body: some View {
        NavigationStack {
            List {
                // --- Section 0: Instruction ---
                Section {
                    Text("Tap a row to activate a scenario or journey path for the demo.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                // --- Section 1: Telemetry Scenarios (UNCHANGED) ---
                Section("Scenarios") {
                    ForEach(TelemetryScenario.allCases, id: \.rawValue) { scenario in
                        telemetryRow(scenario)
                    }
                }

                // --- Section 2: Acquire Scenarios (NEW, ADDITIVE) ---
                Section {
                    ForEach(AcquireScenario.allCases) { scenario in
                        acquireRow(scenario)
                    }
                } header: {
                    Text("Acquire scenarios")
                } footer: {
                    Text("Path B is in-memory only — no LTM write, no real push infrastructure.")
                        .font(.caption)
                }
            }
            .navigationTitle("Presenter")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    // MARK: - Telemetry row (existing pattern preserved)

    @ViewBuilder
    private func telemetryRow(_ scenario: TelemetryScenario) -> some View {
        Button {
            onScenarioSelected(scenario)
            dismiss()
        } label: {
            HStack {
                Text(scenario.displayName)
                Spacer()
                if scenario == currentScenario {
                    Image(systemName: "checkmark")
                }
            }
        }
    }

    // MARK: - Acquire scenario row (new)

    @ViewBuilder
    private func acquireRow(_ scenario: AcquireScenario) -> some View {
        Button {
            applyAcquireScenario(scenario)
            dismiss()
        } label: {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(scenario.displayName)
                        .font(.subheadline)
                        .foregroundStyle(Color(.label))
                    if let caption = scenario.caption {
                        Text(caption)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if isAcquireScenarioActive(scenario) {
                    Image(systemName: "checkmark")
                        .foregroundStyle(Color.accentColor)
                }
            }
        }
        .accessibilityIdentifier("acquireScenario_\(scenario.rawValue)")
    }

    // MARK: - Acquire scenario logic

    /// True when the given scenario matches the current live session state.
    private func isAcquireScenarioActive(_ scenario: AcquireScenario) -> Bool {
        switch scenario {
        case .pathAAnonymousCold:
            return !session._pathBActive
        case .pathBReturningOwnerWarm:
            return session._pathBActive
        }
    }

    /// Apply the selected Acquire scenario to `AppSession`.
    ///
    /// Path A — clears the Path B flag and persona hint (no-op if already clear).
    /// Path B — seeds the in-memory persona, sets `_pathBActive = true`.
    ///          No LTM write, no real push infrastructure (per spec constraint).
    @MainActor
    private func applyAcquireScenario(_ scenario: AcquireScenario) {
        switch scenario {
        case .pathAAnonymousCold:
            session._pathBActive = false
            session._personaHint = nil

        case .pathBReturningOwnerWarm:
            session._personaHint = AcquireScenario.pathBMockPersona
            session._pathBActive = true
        }
    }
}
