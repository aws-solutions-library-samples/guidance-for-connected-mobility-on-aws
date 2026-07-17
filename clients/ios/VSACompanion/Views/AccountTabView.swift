import SwiftUI

struct AccountTabView: View {
    let telemetry: MockTelemetryClient
    let theme: TenantTheme
    let coordinator: TriageCoordinator?
    let onTenantSwitch: (String) async -> Void

    @Environment(AppSession.self) private var session
    @State private var avatarTaps = 0
    @State private var showPresenter = false
    @State private var devUnlocked = false
    @State private var tenantSwitchError: String?
    /// Transient confirmation message after demo controls fire (e.g.
    /// "Demo cleared"). Auto-clears after a couple seconds via
    /// `.task` on the toast view; keeps the user informed without a
    /// modal alert.
    @State private var demoToast: String?
    /// User's appearance override. Read/written via @AppStorage so the
    /// choice persists across launches and stays in sync with the same
    /// key the app root reads in `VSACompanionApp` to drive
    /// `.preferredColorScheme`.
    @AppStorage(AppearancePreference.storageKey)
    private var appearance: AppearancePreference = .system

    /// Known tenant ids the switcher offers. Only "fleet" is seeded in DDB
    /// today — others will show an inline "not yet seeded" message.
    private let knownTenants = ["fleet", "oem"]

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 12) {
                        Button {
                            avatarTaps += 1
                            if avatarTaps >= 4 {
                                devUnlocked = true
                                avatarTaps = 0
                            }
                        } label: {
                            Image(systemName: "person.crop.circle.fill")
                                .resizable().scaledToFit()
                                .frame(width: 48, height: 48)
                                .foregroundStyle(theme.primary)
                        }
                        .buttonStyle(.plain)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Demo User").bold()
                            if case .signedIn(_, let email) = session.authState {
                                Text(email).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }

                Section("Tenant") {
                    labeledRow("Name", value: theme.displayName)
                    if let cfg = session.tenantConfig {
                        labeledRow("Segment", value: cfg.segment.capitalized)
                        labeledRow("Version", value: cfg.version)
                    }
                }

                Section {
                    // Segmented picker reads/writes the same @AppStorage
                    // key the app root observes via `.preferredColorScheme`,
                    // so the change is applied immediately and persists
                    // across launches. `.system` (default) defers to
                    // iOS Settings → Display & Brightness.
                    Picker("Appearance", selection: $appearance) {
                        ForEach(AppearancePreference.allCases) { pref in
                            Label(pref.label, systemImage: pref.systemImage)
                                .tag(pref)
                        }
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Appearance")
                } footer: {
                    Text(appearance == .system
                         ? "Following your iPhone's Display & Brightness setting."
                         : "Overriding system setting on this device.")
                }

                // Demo controls. Visible to everyone (not gated behind
                // devUnlocked) because the demo flow is the primary use
                // case for this app today. When/if the app ships for
                // real use, gate this section the same way Developer is.
                Section {
                    Button(role: .destructive) {
                        // Clear the transcript + tool history without
                        // tearing down the WebSocket. Keeps the demoer
                        // from having to wait through a full session
                        // re-handshake between scenarios on a Zoom
                        // share. Server-side conversation state still
                        // persists; sign out + sign in for a true
                        // clean slate at the model level.
                        //
                        // Also fires a backend cleanup of CMS service-
                        // history rows tagged source=voice-assistant so
                        // demo bookings ("Tuesday 9 AM") don't pile up
                        // in the Service tab across runs. The backend
                        // filters strictly by tag — seeded historical
                        // rows survive. iOS refreshes the Service tab
                        // on next visit because hasLoadedInitialService
                        // doesn't gate refetch on stale data.
                        if let vm = session.voiceSession {
                            vm.resetDemoState()
                        }
                        let vehicleId = session.currentVehicle?.vehicleId
                        if vehicleId != nil {
                            // Optimistic toast — actual count comes back from API.
                            demoToast = "Clearing demo data…"
                            Task {
                                let result = await session.purgeVsaDemoBookings()
                                await MainActor.run {
                                    if let err = result.error {
                                        demoToast = "Cleanup failed: \(err)"
                                    } else if result.deleted == 0 {
                                        demoToast = "Demo cleared (no bookings found)"
                                    } else if result.deleted == 1 {
                                        demoToast = "Demo cleared (1 booking removed)"
                                    } else {
                                        demoToast = "Demo cleared (\(result.deleted) bookings removed)"
                                    }
                                }
                            }
                        } else if session.voiceSession != nil {
                            demoToast = "Demo cleared"
                        } else {
                            demoToast = "No active session"
                        }
                    } label: {
                        Label("Reset demo", systemImage: "arrow.counterclockwise")
                    }
                } header: {
                    Text("Demo controls")
                } footer: {
                    Text("Clears the assistant transcript and tool history. Keeps your session alive so the next demo starts fast.")
                }

                if devUnlocked {
                    Section("Developer") {
                        Button {
                            showPresenter = true
                        } label: {
                            Label("Scenario Presenter", systemImage: "slider.horizontal.3")
                        }

                        Picker("Tenant Skin", selection: Binding(
                            get: { session.activeTenantId },
                            set: { newId in
                                tenantSwitchError = nil
                                Task {
                                    let previousId = session.activeTenantId
                                    await onTenantSwitch(newId)
                                    // If activeTenantId didn't change, the fetch failed.
                                    if session.activeTenantId == previousId && newId != previousId {
                                        tenantSwitchError = "\(newId.capitalized) tenant not yet seeded. Fleet is the only deployed tenant today."
                                    }
                                }
                            }
                        )) {
                            ForEach(knownTenants, id: \.self) { id in
                                Text(id.capitalized).tag(id)
                            }
                        }

                        if session.tenantConfigLoading {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text("Loading tenant…")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        if let tenantSwitchError {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "info.circle")
                                    .foregroundStyle(.orange)
                                Text(tenantSwitchError)
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section {
                    Button(role: .destructive) {
                        // Full reset — tears down the voice session,
                        // clears keychain tokens, wipes every
                        // user-scoped field in AppSession. Without this
                        // the next sign-in inherits the previous user's
                        // cached driver/vehicle/voice-session state.
                        Task { await session.signOut() }
                    } label: {
                        Text("Sign Out")
                    }
                }
            }
            .navigationTitle("Account")
            .navigationBarTitleDisplayMode(.inline)
            // Transient toast for demo-control feedback. Auto-dismiss
            // after 2s using a Task in .overlay so we don't need a
            // separate state-clearing path.
            .overlay(alignment: .bottom) {
                if let msg = demoToast {
                    Text(msg)
                        .font(.subheadline.bold())
                        .foregroundStyle(.white)
                        .padding(.horizontal, 18)
                        .padding(.vertical, 10)
                        .background(
                            Capsule().fill(Color.black.opacity(0.85))
                        )
                        .padding(.bottom, 30)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                        .task(id: msg) {
                            try? await Task.sleep(nanoseconds: 2_000_000_000)
                            withAnimation { demoToast = nil }
                        }
                }
            }
            .animation(.easeInOut(duration: 0.18), value: demoToast)
            .sheet(isPresented: $showPresenter) {
                PresenterControls(
                    currentScenario: telemetry.currentScenario,
                    onScenarioSelected: { s in
                        telemetry.setScenario(s)
                        if let coordinator {
                            Task { await coordinator.reset() }
                        }
                    }
                )
                .presentationDetents([.medium])
            }
        }
    }

    private func labeledRow(_ label: String, value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).foregroundStyle(.primary)
        }
    }
}
