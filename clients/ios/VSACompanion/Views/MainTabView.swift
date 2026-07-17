import SwiftUI

struct MainTabView: View {
    @Environment(AppSession.self) private var session
    @State private var telemetry = MockTelemetryClient()
    @State private var currentFrame = TelemetryFrame.green
    @State private var coordinator: TriageCoordinator?
    // Controls which tab is currently active. Driven by TabView
    // .selection below so any view can programmatically jump to a
    // different tab (e.g. HomeTabView's critical-DTC banner tapping
    // into .alerts). Added 2026-05-04 to support the home-to-alerts
    // tap affordance.
    @State private var selectedTab: AppTab = .home

    /// Identifiers for each tab so we can switch programmatically
    /// without relying on tag indices (which break if tabs reorder).
    ///
    /// 2026-05-06: Assistant was removed from the tab bar in favour of a
    /// floating mic button (see `AssistantFAB` below) that presents
    /// `AssistantTabView` as a full-screen cover. This matches the web
    /// CMS UI's floating-assistant pattern and frees the 6th tab slot so
    /// Account can stay on the bar instead of spilling into "More".
    enum AppTab: Hashable {
        case home, vehicle, alerts, service, account
    }

    /// Presented full-screen when the user taps the floating mic. Bound
    /// to `.fullScreenCover` below; the cover hosts the existing
    /// `AssistantTabView` unchanged so its lifecycle, reasoning drawer,
    /// and chat card keep working.
    @State private var isAssistantPresented: Bool = false

    /// Optional text seed for the next assistant presentation. When the
    /// Book Service CTA on Service/Dealer tab opens the assistant, it
    /// stashes a primed prompt here ("I'd like to book a service
    /// appointment") so Nova starts the conversation as if the driver
    /// had spoken it. Cleared after the cover dismisses so a subsequent
    /// FAB tap opens with a clean slate.
    @State private var assistantInitialMessage: String? = nil

    /// Bumped every time the assistant cover is presented. Applied as
    /// `.id(...)` on `AssistantTabView` so SwiftUI tears down and
    /// rebuilds the view from scratch on each open instead of reusing
    /// the cached body. Without this, closing the cover (X button)
    /// and reopening retained stale state (transcript, scroll
    /// position, in-flight tool drawer state, primed initialMessage
    /// echoing into the new session). Bumping the id triggers a
    /// fresh init → AssistantTabView's own .task / @State / scroll
    /// containers all reset cleanly.
    @State private var assistantPresentationToken: UUID = UUID()

    /// Filter selection for the Alerts tab's DTC section. Lifted to
    /// MainTabView so external nav (Home tab's "X high-severity
    /// alerts" banner) can pick the right filter when jumping over —
    /// without this, tapping a "high-severity" banner landed on
    /// Alerts with the default Critical filter and the user saw
    /// "no alerts" because nothing matched. AlertsTabView consumes
    /// this via @Binding.
    @State private var alertsDtcFilter: DtcFilter = .critical

    /// Keyboard visibility — the FAB hides while the on-screen keyboard
    /// is up so it doesn't sit on top of the suggestion bar or cover
    /// input-adjacent controls. Updated via Notification observers in
    /// `.onReceive` handlers below.
    @State private var isKeyboardVisible: Bool = false

    private var theme: TenantTheme { TenantTheme.from(session.tenantConfig) }

    var body: some View {
        TabView(selection: $selectedTab) {
            HomeTabView(
                frame: currentFrame,
                theme: theme,
                onJumpToAlerts: { filter in
                    // Set the desired filter BEFORE switching tabs so
                    // the Alerts tab renders with the right rows from
                    // the first frame, not after a flicker. The
                    // banner-tap UX promises "see these alerts now",
                    // and Critical-as-default would silently filter
                    // them out otherwise.
                    if let filter { alertsDtcFilter = filter }
                    selectedTab = .alerts
                }
            )
                .tabItem { Label("Home", systemImage: "house.fill") }
                .tag(AppTab.home)
            VehicleTabView(frame: currentFrame, theme: theme)
                .tabItem { Label("Vehicle", systemImage: "car.fill") }
                .tag(AppTab.vehicle)
            AlertsTabView(
                theme: theme,
                onAskAboutDtc: { primedMessage in
                    // Tapping the (i) button on a DTC row jumps to
                    // the assistant cover with a question pre-armed
                    // about that specific fault — same mechanism the
                    // Service tab's Book Service CTA uses.
                    //
                    // `🎤 MTV:` (MainTabView) is the diagnostic prefix
                    // for cover-presentation events; added 2026-05-27
                    // while diagnosing Bug A
                    // (cvx/issues/2026-05-27-ios-bidi-websocket-not-connected).
                    NSLog("🎤 MTV: onAskAboutDtc primedLen=%d voiceSessionExists=%@",
                          primedMessage.count,
                          session.voiceSession == nil ? "no" : "yes")
                    assistantInitialMessage = primedMessage
                    assistantPresentationToken = UUID()
                    isAssistantPresented = true
                },
                dtcFilter: $alertsDtcFilter
            )
                .tabItem { Label("Alerts", systemImage: "bell.fill") }
                // Badge = critical DTCs + unreviewed safety events (last 7d).
                // iOS hides the badge automatically when the value is 0, so we
                // don't need to conditionally apply the modifier.
                .badge(session.alertsBadgeCount)
                .tag(AppTab.alerts)
            // Service tab split out of Alerts on 2026-05-06 — upcoming
            // + historical service rows live here so Alerts can focus on
            // real-time driver signals (faults, safety events, triage).
            //
            // Per-persona label: OEM tenants ("OEM Owner Connect", etc.)
            // see this tab as "Dealer" with a building icon, since their
            // drivers are routed to authorized dealerships rather than a
            // generic service center. Fleet/rental keep the original
            // wrench label. Driven by AppSession.layoutSegment, which
            // reads tenantConfig.segment.
            ServiceTabView(
                theme: theme,
                onBookService: { primedMessage in
                    // Service/Dealer tab "Book Service" CTA opens the
                    // assistant with a primed prompt so Nova begins the
                    // conversation as if the driver had spoken it.
                    // The cover handler reads `assistantInitialMessage`
                    // and clears it on dismiss.
                    NSLog("🎤 MTV: onBookService primedLen=%d voiceSessionExists=%@",
                          primedMessage.count,
                          session.voiceSession == nil ? "no" : "yes")
                    assistantInitialMessage = primedMessage
                    assistantPresentationToken = UUID()
                    isAssistantPresented = true
                }
            )
                .tabItem {
                    Label(
                        session.layoutSegment.serviceTabLabel,
                        systemImage: session.layoutSegment.serviceTabSymbol
                    )
                }
                .tag(AppTab.service)
            AccountTabView(
                telemetry: telemetry,
                theme: theme,
                coordinator: coordinator,
                onTenantSwitch: { newTenantId in
                    await loadTenantConfig(tenantId: newTenantId)
                }
            )
            .tabItem { Label("Account", systemImage: "person.crop.circle.fill") }
            .tag(AppTab.account)
        }
        .tint(theme.primary)
        // Floating mic-button overlay. Sits above the tab bar on every
        // signed-in screen so the assistant is always one tap away.
        // Hidden when the keyboard is up (to avoid overlapping the
        // system suggestion bar) and while the full-screen assistant
        // cover is already showing (redundant + distracting).
        .overlay(alignment: .bottomTrailing) {
            if !isKeyboardVisible && !isAssistantPresented {
                AssistantFAB(
                    theme: theme,
                    isActive: assistantSessionActive,
                    onTap: {
                        // Each FAB tap rebuilds the cover from scratch
                        // — see assistantPresentationToken docstring.
                        NSLog("🎤 MTV: FAB onTap voiceSessionExists=%@ alreadyPresented=%@",
                              session.voiceSession == nil ? "no" : "yes",
                              "\(isAssistantPresented)")
                        assistantPresentationToken = UUID()
                        isAssistantPresented = true
                    }
                )
                .padding(.trailing, 20)
                // Clear the tab bar. Standard iOS tab bar height is 49pt
                // + safe-area inset; 80pt total keeps the FAB above it
                // on every device without a hard-coded safe-area math.
                .padding(.bottom, 80)
                .transition(.scale.combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.18), value: isKeyboardVisible)
        .animation(.easeInOut(duration: 0.18), value: isAssistantPresented)
        .fullScreenCover(isPresented: $isAssistantPresented, onDismiss: {
            NSLog("🎤 MTV: cover onDismiss voiceSessionExists=%@",
                  session.voiceSession == nil ? "no" : "yes")
            // Clear the seed so the next FAB tap opens a clean session.
            // (Without this, a fresh tap would re-prime the same prompt.)
            assistantInitialMessage = nil
            // Tear down the live voice session so the next time the
            // user opens the assistant they get a brand-new
            // conversation — fresh transcript, fresh tool history,
            // fresh Nova session.
            //
            // Race-proofing this is fiddly because the user can tap
            // the FAB to re-open the cover at any moment, including
            // while the old session is still tearing down or while
            // a re-warm is in progress. Without care, two
            // VoiceSessionViewModels end up racing for the same
            // AgentCore bidi session and both fail with
            // "Connection X: received failure notification" +
            // "Send failed with error 'Socket is not connected'".
            //
            // Three steps to avoid that:
            //
            //   1. Snap session.voiceSession to nil RIGHT NOW
            //      (synchronously on the main actor) so any racing
            //      FAB tap that opens AssistantTabView sees nil and
            //      falls into the "create my own fresh VM" branch
            //      instead of using the dying VM.
            //   2. Disconnect the old VM in a background Task so
            //      we don't block UI dismissal. iOS'
            //      URLSessionWebSocketTask close handshake completes
            //      on its own thread.
            //   3. After a small grace period for the OS-level
            //      socket cleanup, re-warm a fresh global
            //      voiceSession ONLY IF the user hasn't already
            //      re-opened the cover. If they have, the cover's
            //      own AssistantTabView already owns a fresh VM
            //      and we don't want a second one fighting it.
            let oldVM = session.voiceSession
            session.voiceSession = nil
            Task { @MainActor in
                NSLog("🎤 MTV: cover-dismiss tear-down task — disconnecting oldVM")
                await oldVM?.disconnect()
                NSLog("🎤 MTV: cover-dismiss oldVM disconnected, sleeping 300ms")
                // 300ms is empirically enough on simulator + real
                // hardware for the OS to fully release the
                // outgoing TCP socket before we open a new one.
                // Without this, "Connection X failure" pops up
                // because the new WebSocket connects while the
                // old socket is still in TCP CLOSE_WAIT.
                try? await Task.sleep(nanoseconds: 300_000_000)
                if !isAssistantPresented {
                    NSLog("🎤 MTV: cover-dismiss re-warming fresh voice session")
                    await session.warmVoiceSession()
                } else {
                    NSLog("🎤 MTV: cover-dismiss skip re-warm — cover already re-presented")
                }
            }
        }) {
            // AssistantTabView brings its own NavigationStack + nav
            // title, so we pass an `onClose` callback and let it render
            // the X button in its own toolbar rather than wrapping in
            // another nav stack or overlaying on top of the nav bar.
            AssistantTabView(
                theme: theme,
                onClose: { isAssistantPresented = false },
                onNavigateToService: {
                    selectedTab = .service
                },
                initialMessage: assistantInitialMessage
            )
            // Force a fresh init each time the cover opens. SwiftUI
            // would otherwise keep the previous AssistantTabView state
            // (transcript, scroll, drawer, primed message echo) alive
            // across X-close → reopen, which produced "ghost" content
            // appearing in the second session. The token is bumped at
            // every open site (FAB, DTC ⓘ, Service Book CTA).
            .id(assistantPresentationToken)
        }
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillShowNotification)) { _ in
            isKeyboardVisible = true
        }
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)) { _ in
            isKeyboardVisible = false
        }
        .task {
            await loadTenantConfig(tenantId: session.activeTenantId)
            bootCoordinator()
            for await frame in telemetry.frames {
                await MainActor.run { self.currentFrame = frame }
                if let coordinator {
                    await coordinator.consider(frame: frame)
                }
            }
        }
    }

    private func bootCoordinator() {
        guard coordinator == nil,
              case .signedIn(let token, _) = session.authState else { return }
        let client = VSAClient(idTokenProvider: { token })
        // Capture the session so the vinSupplier closure always reads the
        // currently-resolved driver's VIN. If the user's vehicle changes
        // mid-session (edge case), the next triage call automatically
        // targets the new VIN.
        let sessionRef = session
        coordinator = TriageCoordinator(
            client: client,
            vinSupplier: { sessionRef.effectiveVin }
        ) { resp in
            await MainActor.run { session.recordTriage(resp) }
        }
        // Backstop: if the app launched with a cached token (no SignInView
        // flow), ensure currentDriver gets resolved. loadCurrentDriver is
        // idempotent via its 5-minute cache so a duplicate call here is a
        // no-op when SignInView already ran it.
        Task { @MainActor in
            await session.loadCurrentDriver(client: client)
            // Pre-warm the voice session on cached-token launches too —
            // same rationale as the SignInView warm call.
            await session.warmVoiceSession()
        }

        // DEBUG-only: run the voice integration test if the compile flag
        // VSA_RUN_INTEGRATION_TEST is set. No-op in release and when the
        // flag isn't set. See Voice/VoiceIntegrationTest.swift for details.
        #if DEBUG
        VoiceIntegrationTestHook.fireAfterSignIn(idTokenProvider: { token })
        #endif
    }

    private func loadTenantConfig(tenantId: String) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        await MainActor.run { session.tenantConfigLoading = true }
        let client = VSAClient(idTokenProvider: { token })
        do {
            let cfg = try await client.getTenantConfig(tenantId)
            await MainActor.run {
                session.tenantConfig = cfg
                session.activeTenantId = tenantId
                session.tenantConfigLoading = false
            }
        } catch {
            await MainActor.run { session.tenantConfigLoading = false }
            print("Tenant config load failed for \(tenantId): \(error.localizedDescription)")
        }
    }

    /// True when there's a live voice session that isn't fully idle. Drives
    /// the pulsing indicator on the FAB so the user can tell at a glance
    /// that the assistant is mid-conversation, even on other tabs. Any
    /// state other than `.disconnected` counts as active (connecting +
    /// ready + talking). If the shape of `VoiceSessionViewModel.State`
    /// grows, this line is the only thing to touch.
    private var assistantSessionActive: Bool {
        guard let vm = session.voiceSession else { return false }
        return vm.state != .disconnected
    }
}

// MARK: - Floating assistant button

/// Floating mic button. Replaces the Assistant tab (2026-05-06) — same
/// single-tap reach, no tab-bar real estate cost, visible from every
/// screen. When an active voice session is in progress, a small pulsing
/// dot sits on the top-trailing edge of the button so users can tell the
/// session is still live when they've tabbed away.
///
/// Visual: 56×56 filled circle in the tenant primary colour, `mic.fill`
/// glyph, soft shadow, springy press animation. Matches the mobile
/// pattern used by voice-first assistants (ChatGPT, Gemini, Copilot)
/// more than the iOS-stock style, which is fine — the role is a call-to-
/// action, not a system control.
private struct AssistantFAB: View {
    let theme: TenantTheme
    let isActive: Bool
    let onTap: () -> Void

    /// Drives the active-indicator pulse. Slow so it doesn't distract;
    /// fast enough to read as "alive" at a glance.
    @State private var pulse: Bool = false

    var body: some View {
        Button(action: onTap) {
            ZStack(alignment: .topTrailing) {
                Circle()
                    .fill(theme.primary)
                    .frame(width: 56, height: 56)
                    .shadow(color: .black.opacity(0.22), radius: 6, x: 0, y: 3)
                    .overlay(
                        Image(systemName: "mic.fill")
                            .font(.system(size: 22, weight: .semibold))
                            .foregroundStyle(.white)
                    )
                if isActive {
                    Circle()
                        .fill(Color.green)
                        .frame(width: 12, height: 12)
                        .overlay(Circle().stroke(Color.white, lineWidth: 2))
                        .scaleEffect(pulse ? 1.15 : 0.95)
                        .opacity(pulse ? 0.9 : 1.0)
                        .offset(x: 4, y: -4)
                        .onAppear {
                            withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) {
                                pulse.toggle()
                            }
                        }
                }
            }
        }
        .buttonStyle(PressableFABStyle())
        .accessibilityLabel(isActive ? "Open Assistant, session active" : "Open Assistant")
    }
}

/// Small press-animation for the FAB — shrinks slightly on press so the
/// button feels tactile. Keeps the rest of the animation story consistent
/// with the rest of the app (which uses subtle scale/opacity transitions).
private struct PressableFABStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.92 : 1.0)
            .animation(.easeInOut(duration: 0.12), value: configuration.isPressed)
    }
}
