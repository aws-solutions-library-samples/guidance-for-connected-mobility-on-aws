import SwiftUI

/// Assistant tab view. Session lifecycle is now tab-scoped:
/// - `.onAppear` (or rather `.task`) opens the WebSocket session.
/// - `.onDisappear` closes it.
/// - The mic button is a push-to-talk toggle, orthogonal to the session.
/// - The text field is always enabled while signed in. Messages typed
///   before the session is ready are queued and flushed automatically.
struct AssistantTabView: View {
    @Environment(AppSession.self) private var session
    let theme: TenantTheme
    /// Optional dismiss callback. When non-nil, the nav bar renders a
    /// close (X) button in the top-trailing slot that invokes this.
    /// MainTabView passes this in when presenting via `.fullScreenCover`
    /// (the floating-mic flow, 2026-05-06). Tab-based entry points pass
    /// nil so the button doesn't appear when the Assistant is being
    /// used inline. Default nil keeps old call sites compiling.
    var onClose: (() -> Void)? = nil
    var onNavigateToService: (() -> Void)? = nil

    /// Optional text seed sent to Nova as soon as the voice session is
    /// `.ready`. When the Service/Dealer tab's "Book Service" CTA
    /// presents this view, it passes "I'd like to book a service
    /// appointment" so the conversation starts on-topic without
    /// requiring the driver to speak the priming utterance themselves.
    /// `nil` is the existing FAB-tap path: opens an empty assistant
    /// and waits for the driver to talk.
    var initialMessage: String? = nil

    @State private var viewModel: VoiceSessionViewModel?
    @State private var typedMessage: String = ""
    @State private var isReasoningTrayPresented: Bool = false
    @State private var lastSeenInteractionCount: Int = 0
    /// Flipped true the instant the user taps the mic button, cleared
    /// when viewmodel transitions to .talking. Spinner shows during the
    /// connect+priming wait (~2-4s).
    @State private var micActionPending: Bool = false
    /// One-shot guard so a re-render of this view (state changes ripple)
    /// doesn't re-send the priming message to Nova. We only inject it
    /// once per presentation lifecycle.
    @State private var didSendInitialMessage: Bool = false

    /// Whether the demo phrases drawer is expanded. Default false so
    /// the assistant looks normal during real use; flipping the
    /// header chevron toggles it. Persists for the lifetime of the
    /// presentation only — re-entering the assistant collapses it.
    @State private var isDemoDrawerExpanded: Bool = false

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        header
                        handoffBanner
                        rsaStrip
                        statusCard
                        demoPhrasesDrawer
                        transcriptCard(proxy: proxy)
                        chatCard(proxy: proxy)
                        errorCard
                    }
                    .padding()
                }
            }
            .safeAreaInset(edge: .bottom) {
                controlBar
                    .padding()
                    .background(.ultraThinMaterial)
            }
            .navigationTitle("Assistant")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if let onClose {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button(action: onClose) {
                            Image(systemName: "xmark")
                                .font(.system(size: 14, weight: .semibold))
                        }
                        .accessibilityLabel("Close Assistant")
                    }
                }
            }
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
            .sheet(isPresented: $isReasoningTrayPresented, onDismiss: {
                if let vm = viewModel {
                    lastSeenInteractionCount = vm.toolInteractions.count
                }
            }) {
                if let vm = viewModel {
                    VoiceReasoningDrawer(
                        interactions: vm.toolInteractions,
                        classification: vm.latestClassification,
                        classificationSource: vm.latestClassificationSource,
                        classificationCategory: vm.latestClassificationCategory,
                        theme: theme
                    )
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
                }
            }
            .task {
                // Diagnostic prefix `🎤 ATV:` (AssistantTabView). Filter the
                // simulator's unified log via:
                //   xcrun simctl spawn booted log stream --predicate \
                //     'eventMessage CONTAINS "🎤"' --style compact
                // Added 2026-05-27 while diagnosing Bug A
                // (cvx/issues/2026-05-27-ios-bidi-websocket-not-connected).
                NSLog("🎤 ATV: task fired vmExists=%@ sharedExists=%@ initialMessageLen=%d",
                      viewModel == nil ? "no" : "yes",
                      session.voiceSession == nil ? "no" : "yes",
                      initialMessage?.count ?? 0)
                // Prefer the app-level pre-warmed voice session (created at
                // sign-in so the mic is instantly responsive when the user
                // reaches this tab). Fall back to creating one lazily if the
                // pre-warm hasn't run yet — same behavior as before.
                if viewModel == nil {
                    if let shared = session.voiceSession {
                        NSLog("🎤 ATV: using shared session.voiceSession state=%@",
                              "\(shared.state)")
                        viewModel = shared
                    } else {
                        NSLog("🎤 ATV: session.voiceSession nil — creating fresh VM lazily")
                        viewModel = VoiceSessionViewModel(
                            tenantId: session.activeTenantId,
                            vin: session.effectiveVin,
                            vehicleId: session.effectiveVehicleId,
                            driverId: session.effectiveDriverId,
                            jwtProvider: { [weak session] in
                                guard let session else { return nil }
                                if case .signedIn(let token, _) = session.authState {
                                    return token
                                }
                                return nil
                            },
                            locationProvider: { [weak session] in
                                guard let session,
                                      let lat = session.liveState?.latitude,
                                      let lng = session.liveState?.longitude
                                else { return nil }
                                return (lat, lng)
                            }
                        )
                    }
                }
                // Bug A defense: if the shared VM is in `.error` from a
                // prior failed warm/connect attempt, fully tear it down
                // first so the subsequent `connect()` runs from a clean
                // `.disconnected` slate. `connect()` itself accepts
                // `.error` as an entry state, but a failed connect
                // leaves stale `client`/`capture`/`player` references
                // on the shared VM and a fresh `await disconnect()`
                // is the cleanest way to reset everything.
                if let vm = viewModel, case .error(let msg) = vm.state {
                    NSLog("🎤 ATV: viewModel in .error(%@) on entry — forcing disconnect+reset before reconnect",
                          msg)
                    await vm.disconnect()
                }
                // Safe to call repeatedly; no-op if already connected.
                if let vm = viewModel {
                    let preState = "\(vm.state)"
                    NSLog("🎤 ATV: pre-connect state=%@", preState)
                    await vm.connect()
                    NSLog("🎤 ATV: post-connect state=%@ (was=%@)",
                          "\(vm.state)", preState)
                }
                // Inject the priming message exactly once per presentation.
                // VoiceSessionViewModel.sendText queues if the session isn't
                // .ready yet, so we don't have to wait — it'll flush as soon
                // as the WebSocket handshake completes. Guarded by
                // didSendInitialMessage so a body re-render doesn't re-send.
                if let seed = initialMessage,
                   !didSendInitialMessage,
                   let vm = viewModel {
                    NSLog("🎤 ATV: sending initial seed message len=%d state=%@",
                          seed.count, "\(vm.state)")
                    didSendInitialMessage = true
                    await vm.sendText(seed)
                    NSLog("🎤 ATV: post-seed state=%@", "\(vm.state)")
                }
                // Fetch vehicle + driver nameplate data in parallel (fire-and-forget).
                if case .signedIn(let token, _) = session.authState {
                    let client = VSAClient(idTokenProvider: { token })
                    await session.loadVehicleContext(client: client)
                }
            }
            .onDisappear {
                // Don't disconnect on disappear — SwiftUI may fire this
                // spuriously on re-renders or brief background transitions.
                // Session cleanup happens on sign-out via AppSession.
            }
        }
    }

    // MARK: - Subviews

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("Fleet Assistant").font(.title3).bold()
                Text("DEMO MODE")
                    .font(.caption2).bold()
                    .foregroundStyle(theme.primary)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(
                        Capsule().strokeBorder(theme.primary.opacity(0.5), lineWidth: 1)
                    )
            }
            Text(theme.greeting).foregroundStyle(.secondary).font(.subheadline)
            // Nameplate: "Stephanie Johnson · 2022 Chevrolet Equinox · 35,031 mi"
            // When no context loaded yet, fall back to the configured VIN so
            // the area isn't empty during the first ~200ms of load.
            nameplate
        }
    }

    @ViewBuilder
    private var nameplate: some View {
        if let ctx = session.vehicleContext {
            HStack(spacing: 6) {
                Image(systemName: "person.fill").font(.caption2)
                    .foregroundStyle(theme.primary.opacity(0.7))
                if let driver = ctx.driver, !driver.fullName.isEmpty {
                    Text(driver.fullName).font(.caption).bold()
                    Text("·").font(.caption).foregroundStyle(.tertiary)
                }
                Image(systemName: "car.fill").font(.caption2)
                    .foregroundStyle(theme.primary.opacity(0.7))
                Text(ctx.vehicle.displayTitle).font(.caption)
                if let odo = ctx.vehicle.odometer ?? ctx.vehicle.mileage {
                    Text("·").font(.caption).foregroundStyle(.tertiary)
                    Text("\(odo) mi").font(.caption2).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            .padding(.top, 2)
        } else if session.vehicleContextLoading {
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini)
                Text("Loading vehicle…").font(.caption).foregroundStyle(.secondary)
            }
            .padding(.top, 2)
        } else if let err = session.vehicleContextError {
            Text("Vehicle context unavailable (\(err))")
                .font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                .padding(.top, 2)
        } else {
            // effectiveVin prefers the signed-in driver's resolved vehicle
            // and falls back to VSAConfig.demoVin only if /drivers/me hasn't
            // loaded yet. Using demoVin directly (as we did before 2026-05-04)
            // showed the wrong VIN for any non-Stephanie driver.
            Text("VIN \(session.effectiveVin)")
                .font(.caption2).foregroundStyle(.tertiary)
                .padding(.top, 2)
        }
    }

    private var statusCard: some View {
        SectionCard(theme: theme) {
            HStack(spacing: 12) {
                statusIcon
                VStack(alignment: .leading, spacing: 2) {
                    Text(statusTitle).font(.subheadline).bold()
                    Text(statusSubtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                reasoningButton
            }
        }
    }

    @ViewBuilder
    private var reasoningButton: some View {
        if let vm = viewModel, !vm.toolInteractions.isEmpty || vm.latestClassification != nil {
            Button {
                isReasoningTrayPresented = true
            } label: {
                ZStack(alignment: .topTrailing) {
                    Image(systemName: "brain")
                        .font(.title3)
                        .foregroundStyle(theme.primary)
                        .frame(width: 36, height: 36)
                        .background(Circle().fill(theme.primary.opacity(0.1)))

                    let unseen = max(0, vm.toolInteractions.count - lastSeenInteractionCount)
                    if unseen > 0 {
                        Text("\(unseen)")
                            .font(.caption2).bold()
                            .foregroundStyle(.white)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Capsule().fill(Color.red))
                            .offset(x: 4, y: -2)
                            .transition(.scale)
                    }
                }
            }
            .accessibilityLabel("Reasoning")
            .accessibilityValue(vm.toolInteractions.isEmpty ? "no tool calls yet"
                                : "\(vm.toolInteractions.count) tool calls")
        }
    }

    /// Collapsible "Demo phrases" drawer that lets demoers run the
    /// full voice flow on the iOS Simulator (no reliable mic access)
    /// or on Zoom screen-sharing. Tapping a pill calls
    /// `vm.playDemoClip(_:)`, which streams a bundled WAV through the
    /// same WebSocket the live mic uses — the rest of the session
    /// can't tell the difference. Phrases are persona-aware
    /// (`DemoClip.script(for:)`); rental gets a shorter list, OEM
    /// drops `findServiceCenter` since OEM flow proposes the dealer
    /// directly.
    ///
    /// The drawer is collapsed by default so a real-use session looks
    /// normal. Tap the header chevron to expand. Hidden entirely
    /// while no viewModel is mounted (pre-connect).
    @ViewBuilder
    private var demoPhrasesDrawer: some View {
        if let vm = viewModel {
            VStack(alignment: .leading, spacing: 0) {
                Button {
                    isDemoDrawerExpanded.toggle()
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "play.rectangle.fill")
                            .foregroundStyle(theme.primary)
                        Text("Demo phrases")
                            .font(.subheadline.bold())
                            .foregroundStyle(.primary)
                        Text("for screen-share demos")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                        Spacer()
                        Image(systemName: isDemoDrawerExpanded ? "chevron.up" : "chevron.down")
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 12)
                    .padding(.horizontal, 14)
                }
                .buttonStyle(.plain)

                if isDemoDrawerExpanded {
                    Divider()
                    let scenarios = DemoScenario.scenarios(for: session.layoutSegment)
                    VStack(alignment: .leading, spacing: 14) {
                        ForEach(scenarios) { scenario in
                            scenarioCard(scenario, vm: vm)
                        }
                    }
                    .padding(.vertical, 12)
                    .padding(.horizontal, 14)
                }
            }
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color(.secondarySystemBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(theme.primary.opacity(0.18), lineWidth: 1)
                    )
            )
        }
    }

    /// Renders one scenario as an expandable card: header (icon +
    /// title + description) and a numbered row of step pills. Tapping
    /// a step plays the matching demo clip; the demoer follows the
    /// numbers in order.
    @ViewBuilder
    private func scenarioCard(_ scenario: DemoScenario, vm: VoiceSessionViewModel) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: scenario.symbolName)
                    .foregroundStyle(theme.primary)
                    .font(.subheadline.bold())
                VStack(alignment: .leading, spacing: 2) {
                    Text(scenario.name)
                        .font(.subheadline.bold())
                        .foregroundStyle(.primary)
                    Text(scenario.description)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Array(scenario.steps.enumerated()), id: \.offset) { idx, clip in
                        Button {
                            // Collapse the demo drawer on tap so the
                            // Nova Sonic conversation/transcript is
                            // immediately visible instead of leaving the
                            // drawer covering it for the user to close.
                            withAnimation(.easeInOut(duration: 0.25)) {
                                isDemoDrawerExpanded = false
                            }
                            Task {
                                await vm.playDemoClip(
                                    named: clip.fileName(for: session.layoutSegment),
                                    transcriptText: clip.transcriptText
                                )
                            }
                        } label: {
                            HStack(spacing: 6) {
                                // Step number badge — small monospaced
                                // numeral so demoers can find their
                                // place at a glance.
                                Text("\(idx + 1)")
                                    .font(.caption2.bold().monospacedDigit())
                                    .foregroundStyle(.white)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Capsule().fill(theme.primary))
                                Image(systemName: clip.symbolName)
                                    .font(.caption.bold())
                                Text(clip.label).font(.caption.bold())
                            }
                            .padding(.vertical, 8)
                            .padding(.horizontal, 10)
                            .foregroundStyle(theme.primary)
                            .background(
                                Capsule().fill(theme.primary.opacity(0.12))
                            )
                            .overlay(
                                Capsule()
                                    .strokeBorder(theme.primary.opacity(0.3), lineWidth: 1)
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color(.tertiarySystemBackground))
        )
    }

    private var statusIcon: some View {
        let (icon, color, pulse) = statusIconSpec
        return ZStack {
            Circle().fill(color.opacity(0.12)).frame(width: 44, height: 44)
            Image(systemName: icon).foregroundStyle(color)
        }
        .modifier(PulseIfActive(active: pulse, color: color))
    }

    @ViewBuilder
    private func transcriptCard(proxy: ScrollViewProxy) -> some View {
        if let vm = viewModel, !vm.transcript.isEmpty {
            SectionCard("Live session", theme: theme) {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(vm.transcript) { entry in
                        // Cycle 3 defense (issues/2026-05-28-ios-chat-thinking-indicator-persists):
                        // The thinking indicator is now the
                        // sticky `thinkingIndicatorRow` below — NOT a
                        // transcript entry. If a stale build inserted
                        // an isThinking entry (shouldn't happen post-cycle-3),
                        // skip it to prevent dot duplication.
                        if entry.isThinking {
                            EmptyView()
                        } else if let booking = entry.booking {
                            bookingCard(booking: booking)
                                .id(entry.id)
                        } else if let info = entry.infoCard {
                            infoCard(info: info)
                                .id(entry.id)
                        } else {
                            bubble(role: entry.role, text: entry.text,
                                   isFinal: entry.isFinal, isPending: entry.isPending,
                                   isThinking: false)
                                .id(entry.id)
                        }
                    }
                    // Sticky thinking indicator. ONE place it shows.
                    // Driven by the view-model's derived
                    // `isServerThinking` property — server-side
                    // processing state, not local heuristic.
                    if vm.isServerThinking {
                        thinkingIndicatorRow
                            .id("thinking-indicator")
                    }
                }
                .onChange(of: vm.transcript.count) { _, _ in
                    if let last = vm.transcript.last {
                        withAnimation(.easeOut(duration: 0.2)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
                .onChange(of: vm.isServerThinking) { _, newValue in
                    // Scroll to the indicator when it appears so the
                    // user sees the dots even if the transcript is
                    // long.
                    if newValue {
                        withAnimation(.easeOut(duration: 0.2)) {
                            proxy.scrollTo("thinking-indicator", anchor: .bottom)
                        }
                    }
                }
            }
        }
    }

    /// Sticky "Nova is thinking" row rendered when
    /// `vm.isServerThinking == true`. Visually identical to the old
    /// transcript-entry dots bubble; behaviorally distinct because
    /// it has zero state of its own — pure derived render.
    @ViewBuilder
    private var thinkingIndicatorRow: some View {
        HStack {
            HStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .fill(theme.primary.opacity(0.6))
                        .frame(width: 8, height: 8)
                        .scaleEffect(1.0)
                        .animation(
                            .easeInOut(duration: 0.6)
                            .repeatForever(autoreverses: true)
                            .delay(Double(i) * 0.2),
                            value: true
                        )
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 12)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.tertiarySystemGroupedBackground))
            )
            Spacer(minLength: 32)
        }
    }

    // MARK: - Handoff UI

    /// Top-of-screen banner that appears whenever a handoff is active.
    /// Color + copy track the HandoffState progression:
    /// initiated/connecting = amber ("Connecting you…"), connected = green
    /// ("Connected to {agent}"), ended/failed = grey.
    @ViewBuilder
    private var handoffBanner: some View {
        if let vm = viewModel, vm.handoffState != .none {
            handoffBannerContent(state: vm.handoffState)
        }
    }

    @ViewBuilder
    private func handoffBannerContent(state: VoiceSessionViewModel.HandoffState) -> some View {
        let spec = handoffBannerSpec(state: state)
        HStack(spacing: 12) {
            Image(systemName: spec.icon)
                .font(.title3)
                .foregroundStyle(spec.accent)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(spec.title).font(.subheadline).bold()
                Text(spec.subtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            // For ended / failed states, offer a quick dismiss so the
            // banner doesn't linger forever once the chat is over.
            if case .ended = state {
                dismissHandoffButton
            } else if case .failed = state {
                dismissHandoffButton
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(spec.accent.opacity(0.12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(spec.accent.opacity(0.35), lineWidth: 1)
                )
        )
    }

    /// Small roadside-assistance strip that appears on P0 when the
    /// /escalate Lambda queued a roadside dispatch. Separate from the
    /// main banner because it carries different information (the
    /// dispatch is a side-effect of the escalation, not the escalation
    /// itself). Sits directly under the banner so the visual grouping
    /// reads as "this whole cluster is about the current incident".
    @ViewBuilder
    private var rsaStrip: some View {
        if let vm = viewModel, handoffHasRsa(vm.handoffState) {
            HStack(spacing: 8) {
                Text("🚨")
                Text("Roadside assistance has been dispatched to your location.")
                    .font(.caption).bold()
                    .foregroundStyle(.white)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.red.opacity(0.85))
            )
        }
    }

    /// Chat bubbles for the Connect-agent conversation. Only rendered
    /// once we're actually connected (so the strip doesn't flash during
    /// the 2-3s of "Connecting…").
    @ViewBuilder
    private func chatCard(proxy: ScrollViewProxy) -> some View {
        if let vm = viewModel, handoffIsChatActive(vm.handoffState) {
            SectionCard(chatCardTitle(state: vm.handoffState), theme: theme) {
                if vm.chatMessages.isEmpty {
                    Text("Say hi — your support agent can see this conversation.")
                        .font(.caption).foregroundStyle(.secondary)
                        .padding(.vertical, 6)
                } else {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(vm.chatMessages) { msg in
                            chatBubble(msg)
                                .id("chat-\(msg.id)")
                        }
                    }
                    .onChange(of: vm.chatMessages.count) { _, _ in
                        if let last = vm.chatMessages.last {
                            withAnimation(.easeOut(duration: 0.2)) {
                                proxy.scrollTo("chat-\(last.id)", anchor: .bottom)
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func chatBubble(_ message: VoiceSessionViewModel.ChatMessage) -> some View {
        if message.role == "SYSTEM" {
            // Supervisor-provided fault diagnosis. Rendered inline in
            // the chat thread but visually distinct from driver/agent
            // bubbles — full-width, outlined, tinted panel with an
            // info icon. Not pinned: the message participates in the
            // scroll flow so Kevin's follow-ups naturally land below
            // it. Added 2026-05-11.
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "info.circle.fill")
                    .foregroundStyle(.orange)
                    .font(.body)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 4) {
                    if let name = message.displayName, !name.isEmpty {
                        Text(name)
                            .font(.caption2).bold()
                            .foregroundStyle(.secondary)
                    }
                    Text(message.text)
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.orange.opacity(0.08))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.orange.opacity(0.35), lineWidth: 1)
            )
        } else {
            HStack {
                if message.isFromDriver { Spacer(minLength: 32) }
                VStack(alignment: .leading, spacing: 2) {
                    if !message.isFromDriver, let name = message.displayName, !name.isEmpty {
                        Text(name).font(.caption2).bold()
                            .foregroundStyle(.secondary)
                    }
                    Text(message.text)
                        .font(.subheadline)
                        .foregroundStyle(message.isFromDriver ? .white : .primary)
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 14)
                        .fill(message.isFromDriver
                              ? theme.primary
                              : Color(.tertiarySystemGroupedBackground))
                )
                if !message.isFromDriver { Spacer(minLength: 32) }
            }
        }
    }

    @ViewBuilder
    private var dismissHandoffButton: some View {
        Button {
            Task { await viewModel?.endHandoff() }
        } label: {
            Image(systemName: "xmark.circle.fill")
                .font(.title3).foregroundStyle(.secondary)
        }
        .accessibilityLabel("Dismiss")
    }

    // MARK: - Handoff helpers (pure)

    private struct HandoffBannerSpec {
        let icon: String
        let title: String
        let subtitle: String
        let accent: Color
    }

    private func handoffBannerSpec(
        state: VoiceSessionViewModel.HandoffState
    ) -> HandoffBannerSpec {
        switch state {
        case .none:
            // Caller guards against this; return a neutral spec just in case.
            return .init(icon: "person.fill", title: "", subtitle: "", accent: .gray)
        case .initiated(let severity, _):
            return .init(
                icon: "person.wave.2.fill",
                title: "Connecting you to a human agent…",
                subtitle: "Priority \(severityLabel(severity)) — hang tight.",
                accent: .orange
            )
        case .connecting(let severity, _, _):
            return .init(
                icon: "person.wave.2.fill",
                title: "Connecting you to a human agent…",
                subtitle: "Priority \(severityLabel(severity)) — hang tight.",
                accent: .orange
            )
        case .connected(let severity, _, _, let agentName):
            let who = agentName ?? "fleet support"
            return .init(
                icon: "person.fill.checkmark",
                title: "Connected to \(who)",
                subtitle: "Priority \(severityLabel(severity)) chat is live.",
                accent: .green
            )
        case .ended(let reason):
            return .init(
                icon: "person.fill.xmark",
                title: "Chat ended",
                subtitle: reasonCopy(reason),
                accent: .gray
            )
        case .failed(let message):
            return .init(
                icon: "exclamationmark.triangle.fill",
                title: "Couldn't connect you to an agent",
                subtitle: message,
                accent: .red
            )
        }
    }

    private func severityLabel(_ severity: String) -> String {
        switch severity {
        case "P0": return "zero"
        case "P1": return "one"
        case "P2": return "two"
        case "P3": return "three"
        default: return severity
        }
    }

    private func reasonCopy(_ raw: String) -> String {
        switch raw {
        case "driver-ended": return "You ended the chat."
        case "chat-ended-by-agent": return "Your agent closed the chat."
        default: return raw.isEmpty ? "Chat closed." : raw
        }
    }

    private func handoffHasRsa(_ state: VoiceSessionViewModel.HandoffState) -> Bool {
        switch state {
        case .initiated(_, let rsa),
             .connecting(_, let rsa, _),
             .connected(_, let rsa, _, _):
            return rsa
        default: return false
        }
    }

    private func handoffIsChatActive(_ state: VoiceSessionViewModel.HandoffState) -> Bool {
        switch state {
        case .connecting, .connected: return true
        default: return false
        }
    }

    private func chatCardTitle(state: VoiceSessionViewModel.HandoffState) -> String {
        if case .connected(_, _, _, let agentName) = state, let name = agentName {
            return "Chat with \(name)"
        }
        return "Support chat"
    }

    @ViewBuilder
    private var errorCard: some View {
        if let vm = viewModel, case .error(let message) = vm.state {
            SectionCard(theme: theme) {
                HStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange).font(.title3)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Session error").font(.subheadline).bold()
                        Text(message).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Retry") {
                        Task { await vm.connect() }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
        }
    }

    private var controlBar: some View {
        HStack(spacing: 12) {
            TextField(textFieldPlaceholder, text: $typedMessage)
                .textFieldStyle(.roundedBorder)
                .disabled(!isSignedIn || isTalking)
                .onSubmit(submitTypedMessage)

            actionButton
        }
    }

    /// Mode-switching button:
    /// - text field has content → up-arrow (send typed message)
    /// - empty + session .ready → mic (start talking)
    /// - session .talking → stop (end talking)
    /// - all other states → disabled gray mic
    @ViewBuilder
    private var actionButton: some View {
        let mode = currentActionMode
        Button {
            if mode == .startTalking {
                micActionPending = true
            }
            Task {
                await performAction(for: mode)
                // Keep spinner visible until state transitions to .talking.
                if mode == .startTalking {
                    let deadline = Date().addingTimeInterval(8.0)
                    while Date() < deadline {
                        if viewModel?.isTalking == true { break }
                        if let vm = viewModel, case .error = vm.state { break }
                        try? await Task.sleep(nanoseconds: 50_000_000)
                    }
                }
                micActionPending = false
            }
        } label: {
            Group {
                if micActionPending && mode == .startTalking {
                    ProgressView()
                        .tint(.white)
                        .controlSize(.small)
                } else {
                    Image(systemName: mode.icon)
                        .foregroundStyle(.white)
                }
            }
            .frame(width: 44, height: 44)
            .background(Circle().fill(mode.color(theme: theme)))
        }
        .accessibilityLabel(mode.accessibilityLabel)
        .disabled(mode == .disabled || micActionPending)
    }

    private enum ActionMode: Equatable {
        case send, startTalking, stopTalking, disabled

        var icon: String {
            switch self {
            case .send: return "arrow.up"
            case .startTalking: return "mic.fill"
            case .stopTalking: return "waveform"  // live listening indicator
            case .disabled: return "mic.slash.fill"
            }
        }

        func color(theme: TenantTheme) -> Color {
            switch self {
            case .send, .startTalking: return theme.primary
            case .stopTalking: return .green  // green = live mic
            case .disabled: return .gray
            }
        }

        var accessibilityLabel: String {
            switch self {
            case .send: return "Send message"
            case .startTalking: return "Start talking"
            case .stopTalking: return "Listening — tap to cancel"
            case .disabled: return "Unavailable"
            }
        }
    }

    private var currentActionMode: ActionMode {
        guard isSignedIn else { return .disabled }
        // Chat handover: once Connect is connecting or live, we're in
        // text-with-agent mode. Hide the mic entirely — Nova Sonic
        // shouldn't be interrupting Kevin mid-chat. Send button stays
        // (text → Connect), otherwise disabled.
        //
        // Driver can still end the chat via the handoff banner's end
        // button, which tears down the Connect chat and flips the
        // session back to the voice-assistant path.
        if isChatActive {
            if !typedMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return .send
            }
            return .disabled
        }
        if isTalking { return .stopTalking }
        if !typedMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return .send
        }
        // Mic available in .ready, .connecting, .speaking (barge-in to
        // interrupt the assistant), and .thinking (user wants to change
        // mind). The viewmodel's talkStart() handles the state transition
        // in all these cases. Tap-during-connect will no-op at the
        // viewmodel layer if the WebSocket isn't open yet — connect is
        // usually <1s so the user doesn't notice. This avoids the
        // "I tapped mic and nothing happened" frustration when the
        // assistant is mid-response.
        if let vm = viewModel,
           vm.state == .ready
            || vm.state == .connecting
            || vm.state == .speaking
            || vm.state == .thinking {
            return .startTalking
        }
        return .disabled
    }

    private func performAction(for mode: ActionMode) async {
        guard let vm = viewModel else { return }
        switch mode {
        case .send:
            let text = typedMessage
            typedMessage = ""
            // Route text into the Connect chat while a handoff is live.
            // Kevin sees the driver's typed messages appear in his CCP
            // instead of Nova Sonic getting them. The voice session
            // stays up so Nova can keep narrating context.
            if isChatActive {
                await vm.sendChatMessage(text)
            } else {
                await vm.sendText(text)
            }
        case .startTalking:
            await vm.talkStart()
        case .stopTalking:
            await vm.talkStop()
        case .disabled:
            break
        }
    }

    private var textFieldPlaceholder: String {
        if isTalking { return "Listening…" }
        if isChatActive { return "Message fleet support…" }
        return "Type a message…"
    }

    private var isTalking: Bool {
        viewModel?.isTalking == true
    }

    /// True when a Connect chat handoff is either connecting or connected.
    /// Drives the send button's routing (chat vs voice) and the text-field
    /// placeholder copy.
    private var isChatActive: Bool {
        guard let vm = viewModel else { return false }
        switch vm.handoffState {
        case .connecting, .connected: return true
        default: return false
        }
    }

    @ViewBuilder
    private func bookingCard(booking: VoiceSessionViewModel.BookingConfirmation) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text("Appointment Confirmed")
                        .font(.subheadline.bold())
                }
                Text(booking.centerName)
                    .font(.subheadline)
                Text(booking.requestNumber)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button(action: {
                    onClose?()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                        onNavigateToService?()
                    }
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "calendar")
                        Text("View in Service")
                    }
                    .font(.caption.bold())
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(theme.primary)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.tertiarySystemGroupedBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14)
                            .stroke(Color.green.opacity(0.3), lineWidth: 1)
                    )
            )
            Spacer(minLength: 32)
        }
    }

    @ViewBuilder
    private func infoCard(info: VoiceSessionViewModel.InfoCard) -> some View {
        // Lightweight markdown bubble for sub-agent reference data
        // (e.g. tire pressure spec). SwiftUI's Text(LocalizedStringKey)
        // renders **bold** and _italic_ inline but collapses single
        // newlines into spaces, so we split on newlines and render
        // each line as its own Text. Empty lines become small
        // vertical gaps so the visual structure (title, blank,
        // bullets, blank, italic note) is preserved.
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(info.markdown.split(
                    separator: "\n",
                    omittingEmptySubsequences: false
                ).enumerated()), id: \.offset) { _, line in
                    if line.isEmpty {
                        Color.clear.frame(height: 4)
                    } else {
                        Text(LocalizedStringKey(String(line)))
                            .font(.subheadline)
                            .textSelection(.enabled)
                    }
                }
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.tertiarySystemGroupedBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14)
                            .stroke(theme.primary.opacity(0.25), lineWidth: 1)
                    )
            )
            Spacer(minLength: 32)
        }
    }

    @ViewBuilder
    private func bubble(role: String, text: String, isFinal: Bool, isPending: Bool, isThinking: Bool = false) -> some View {
        HStack {
            if role == "user" { Spacer(minLength: 32) }
            if isThinking {
                HStack(spacing: 4) {
                    ForEach(0..<3, id: \.self) { i in
                        Circle()
                            .fill(theme.primary.opacity(0.6))
                            .frame(width: 8, height: 8)
                            .scaleEffect(1.0)
                            .animation(
                                .easeInOut(duration: 0.6)
                                .repeatForever(autoreverses: true)
                                .delay(Double(i) * 0.2),
                                value: true
                            )
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
                .background(
                    RoundedRectangle(cornerRadius: 14)
                        .fill(Color(.tertiarySystemGroupedBackground))
                )
            } else {
                Text(displayText(text, isFinal: isFinal))
                    .font(.subheadline)
                    .italic(isPending)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .foregroundStyle(role == "user" ? .white : .primary)
                    .background(
                        RoundedRectangle(cornerRadius: 14)
                            .fill(role == "user"
                                  ? theme.primary.opacity(isPending ? 0.55 : 1.0)
                                  : Color(.tertiarySystemGroupedBackground))
                    )
                    .opacity(isFinal ? 1.0 : 0.7)
            }
            if role == "assistant" { Spacer(minLength: 32) }
        }
    }

    private func displayText(_ text: String, isFinal: Bool) -> String {
        if text.isEmpty { return "…" }
        return isFinal ? text : text + " …"
    }

    // MARK: - State-derived helpers

    private var isSignedIn: Bool {
        if case .signedIn = session.authState { return true }
        return false
    }

    private var statusTitle: String {
        guard let vm = viewModel else { return "Ready" }
        switch vm.state {
        case .disconnected: return "Ready"
        case .connecting: return "Ready"     // connect is async; don't bother the user
        case .ready: return "Ready"
        case .talking: return "Listening"
        case .thinking: return "Thinking…"
        case .speaking: return "Speaking"
        case .error: return "Error"
        }
    }

    private var statusSubtitle: String {
        guard let vm = viewModel else {
            return "Type a message or tap the mic to begin."
        }
        switch vm.state {
        case .disconnected:
            return isSignedIn
                ? "Type a message or tap the mic to begin."
                : "Sign in to begin."
        case .connecting:
            // Same copy as .ready — connection races with the user's first
            // action and if they type/talk during connect, we queue silently.
            return "Type a message or tap the mic to begin."
        case .ready:
            return "Type a message or tap the mic to begin."
        case .talking:
            return "Speak naturally — I'll stop listening when you pause."
        case .thinking:
            return "Got it — one moment."
        case .speaking:
            return "Tap mic to interrupt."
        case .error:
            return "Tap Retry to reconnect."
        }
    }

    private var statusIconSpec: (String, Color, Bool) {
        guard let vm = viewModel else { return ("waveform", theme.primary, false) }
        switch vm.state {
        case .disconnected: return ("waveform", theme.primary, false)
        case .connecting: return ("waveform", theme.primary, false)  // hidden from user
        case .ready: return ("waveform", theme.primary, false)
        case .talking: return ("waveform.and.mic", .green, true)
        case .thinking: return ("brain", theme.primary, true)
        case .speaking: return ("waveform", theme.primary, true)
        case .error: return ("exclamationmark.triangle.fill", .orange, false)
        }
    }

    // MARK: - Actions

    /// Handler for the text field's return/submit key. Routes through
    /// the unified performAction so there's a single send code path.
    private func submitTypedMessage() {
        Task { await performAction(for: .send) }
    }
}

// MARK: - Animation helpers

private struct PulseIfActive: ViewModifier {
    let active: Bool
    let color: Color
    @State private var pulse = false

    func body(content: Content) -> some View {
        content
            .overlay(
                Group {
                    if active {
                        Circle()
                            .stroke(color.opacity(0.4), lineWidth: 2)
                            .scaleEffect(pulse ? 1.4 : 1.0)
                            .opacity(pulse ? 0.0 : 1.0)
                            .frame(width: 44, height: 44)
                    }
                }
            )
            .onChange(of: active) { _, newValue in
                if newValue {
                    withAnimation(.easeOut(duration: 1.0).repeatForever(autoreverses: false)) {
                        pulse = true
                    }
                } else {
                    pulse = false
                }
            }
    }
}
