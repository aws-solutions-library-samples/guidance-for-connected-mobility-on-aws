import Foundation
import Observation
import AVFoundation

/// Orchestrates a voice session with the AgentCore bidi runtime.
///
/// Owns the three voice actors (`VSABidiClient`, `AudioCapture`, `AudioPlayer`)
/// and the state machine that AssistantTabView renders. The lifecycle is now
/// tab-scoped, not interaction-scoped: `connect()` runs when the Assistant
/// tab appears, `disconnect()` when it disappears. Text input and mic input
/// are orthogonal to session lifetime — the user can type during
/// `.connecting` (messages are queued and flushed on `.ready`), and the mic
/// is a push-to-talk toggle that doesn't affect the session.
///
/// ## State machine
///
/// ```
///   disconnected ──► connecting ──► ready ⇄ talking
///                        │              │      │
///                        ▼              ▼      │
///                      error(String) ◄─ thinking ⇄ speaking
///                                         ▲         │
///                                         └─────────┘
/// ```
///
/// - `disconnected`: no WebSocket, idle. Fresh state after construction and
///    after `disconnect()`.
/// - `connecting`: WebSocket handshake in progress. Text input is allowed;
///    it goes to `pendingOutgoing` and flushes on `.ready`.
/// - `ready`: WebSocket open, waiting for input. Replaces old `.listening`
///    semantically but without the implication that mic capture is running.
/// - `talking`: mic is capturing and streaming PCM chunks.
/// - `thinking`: user input sent (text or final audio transcript), awaiting
///    first assistant output.
/// - `speaking`: assistant audio chunks are flowing. Reverts to `.ready` when
///    no chunk arrives for ~1.2s.
/// - `error(String)`: terminal for the current session. User or tab lifecycle
///    must call `connect()` again to recover.
///
/// ## Auto-reconnect
///
/// If a connection drops unexpectedly (AgentCore's 8-minute idle timeout,
/// transient network error), the view model auto-reconnects once without
/// user intervention. A second consecutive failure surfaces `.error` so
/// the user sees the problem instead of a silent retry loop.
///
/// ## Barge-in
///
/// On `.interruption` from the backend (Nova Sonic detected the user talking
/// over the assistant) we call `AudioPlayer.flush()` and flip to `.ready`
/// (if not mid-talk).
@Observable
@MainActor
final class VoiceSessionViewModel {

    // MARK: - Constants

    /// Spoken to the driver when `lookup_knowledge` returns an empty or
    /// "Knowledge base not configured." sentinel result. Without this
    /// fallback, Nova goes silent on empty-KB tenants and the silence
    /// watchdog tears down the session mid-turn (Bug B from
    /// `issues/2026-05-26-voice-silence-watchdog-empty-tool-result`).
    /// The string is also dispatched as a `text.input` to Nova so she
    /// narrates it via TTS — the bubble shown locally is the safety net
    /// in case the inject roundtrip is slow or fails.
    private static let kbEmptyFallback =
        "I don't have detailed information on that. Anything else I can help with?"

    // MARK: - Published types

    enum State: Equatable {
        case disconnected
        case connecting
        case ready
        case talking
        case thinking
        case speaking
        case error(String)
    }

    struct TranscriptEntry: Identifiable, Equatable {
        let id: UUID
        let role: String   // "user" | "assistant"
        var text: String
        var isFinal: Bool
        /// True while the message is queued locally and hasn't been sent
        /// because the session is still connecting. Used by the UI to show
        /// a subtle italic/pending style until it goes live.
        var isPending: Bool = false
        /// True for the placeholder "..." bubble shown while waiting for response.
        var isThinking: Bool = false
        /// Non-nil when this entry is a booking confirmation card.
        var booking: BookingConfirmation? = nil
        /// Non-nil when this entry is a screen-side info card emitted
        /// by a sub-agent tool (e.g. tire pressure table from
        /// diy_repair_advisor). Renders as a markdown bubble.
        var infoCard: InfoCard? = nil
    }

    struct InfoCard: Equatable {
        /// Tool that emitted the card (e.g. "diy_repair_advisor").
        let source: String
        /// Markdown body. iOS renders with SwiftUI's native Markdown
        /// support — supports bold, tables, italics out of the box.
        let markdown: String
    }

    struct BookingConfirmation: Equatable {
        let requestNumber: String
        let centerName: String
        let centerAddress: String
        let status: String
    }

    enum DrawerEvent: Equatable {
        case toolCall(name: String, input: [String: AnyCodable])
        case toolResult(name: String, output: [String: AnyCodable])
        case classification(String)
        case sessionStarted
        case sessionEnded
    }

    struct ToolInteraction: Identifiable, Equatable {
        let id: UUID
        let name: String
        let input: [String: AnyCodable]
        var output: [String: AnyCodable]?
        let startedAt: Date
        var completedAt: Date?
    }

    /// State machine for the Connect-agent handoff flow. Orthogonal to
    /// the voice-session State — the voice session stays running while
    /// the handoff happens so Nova Sonic can keep narrating to the
    /// driver. See AssistantTabView for how this drives the banner +
    /// chat UI.
    enum HandoffState: Equatable {
        /// No handoff in progress. Default.
        case none
        /// Supervisor said "I'm escalating you" (escalation event with
        /// status=initiated landed) but the Connect chat isn't open
        /// yet. UI shows an amber "Connecting…" banner.
        case initiated(severity: String, rsaDispatched: Bool)
        /// WebSocket handshake with Connect is in progress.
        case connecting(severity: String, rsaDispatched: Bool, contactId: String)
        /// Chat is live. agentName is populated once an agent JOINED
        /// event arrives with a DisplayName.
        case connected(severity: String, rsaDispatched: Bool, contactId: String, agentName: String?)
        /// Agent ended the chat or the token expired.
        case ended(reason: String)
        /// /escalate failed, or ConnectChatClient hit an error.
        case failed(message: String)
    }

    /// One bubble in the Connect chat UI. Thin wrapper around
    /// ConnectChatClient.Message — we keep our own type so the view
    /// doesn't directly import the chat-client internals.
    struct ChatMessage: Identifiable, Equatable {
        let id: String
        let role: String            // "CUSTOMER" | "AGENT" | "SYSTEM" | ...
        let displayName: String?
        let text: String
        let timestamp: Date

        /// True iff this message was sent by the driver using this app.
        /// The role comes from Connect's broadcast of our own message
        /// back over the WebSocket; CUSTOMER = driver.
        var isFromDriver: Bool { role == "CUSTOMER" }
    }

    // MARK: - Published state

    private(set) var state: State = .disconnected
    private(set) var transcript: [TranscriptEntry] = []
    private(set) var lastError: String?
    private(set) var toolInteractions: [ToolInteraction] = []
    private(set) var latestClassification: String?
    /// Source of the latest classification, when provided by the server.
    /// - nil          : legacy sensor-backed classifier event (assume "classifier")
    /// - "classifier" : deterministic sensor-based triage result
    /// - "driver-confirmed" : driver verbally confirmed an emergency the
    ///                        sensors didn't corroborate; tier is elevated
    /// - "driver-unclear-default" : driver's confirmation answer was
    ///                              ambiguous, server escalated to P1 defensively
    ///
    /// Used by the reasoning drawer to render a "Driver-confirmed" or
    /// similar badge distinguishing these paths from sensor-backed triage.
    private(set) var latestClassificationSource: String?
    /// Emergency category name (e.g. "brake_failure") when the latest
    /// classification came from the driver-confirmed or driver-unclear
    /// paths. Nil otherwise.
    private(set) var latestClassificationCategory: String?

    /// Handoff state for the Connect-agent chat. Driven by the
    /// `escalation` side-channel event from bidi_app.py plus the
    /// ConnectChatClient's own AsyncStream of events.
    private(set) var handoffState: HandoffState = .none

    /// Chat messages between the driver and the Connect agent. Chronological,
    /// oldest first. Populated once handoffState transitions to .connected.
    private(set) var chatMessages: [ChatMessage] = []

    /// Fire-and-forget drawer event hook for external subscribers.
    var onDrawerEvent: (@MainActor (DrawerEvent) -> Void)?

    // MARK: - Dependencies

    private let tenantId: String
    /// Exposed read-only (not `private`) so AppSession.warmVoiceSession()
    /// can detect an identity drift (user switched accounts without a
    /// clean teardown) and rebuild the session with the right VIN.
    let vin: String
    /// CMS-side identifiers for the vehicle + driver. Sent as custom AgentCore
    /// headers so the supervisor can populate SessionContext.vehicle_id /
    /// driver_id used by book() writes and voice-prompt enrichment.
    /// Read-only-but-visible for the same identity-drift reason as `vin`.
    let vehicleId: String?
    let driverId: String?
    private let jwtProvider: @Sendable () -> String?
    /// Returns the latest live coordinates for the current vehicle, if
    /// known. Read at WebSocket-connect time and forwarded to the
    /// backend as Latitude / Longitude headers so the agent runtime
    /// can skip its own `/vehicles/{id}/live-state` HTTP call. Caller
    /// typically wires this to `session.liveState` so the voice
    /// agent's find_service_center distance matches what the iOS
    /// Vehicle tab map shows.
    private let locationProvider: @Sendable () -> (Double, Double)?

    // MARK: - Session actors (recreated per connect)

    private var client: VSABidiClient?
    private var capture: AudioCapture?
    private var player: AudioPlayer?

    /// Connect-agent chat client, non-nil while a handoff is active.
    private var chatClient: ConnectChatClient?
    /// Event loop task consuming the ConnectChatClient stream.
    private var chatEventLoopTask: Task<Void, Never>?

    /// Long-lived — reused across connects so the credential cache survives
    /// reconnects within the same sign-in.
    private let credentialProvider: AwsCredentialProvider

    // MARK: - Background tasks

    private var eventLoopTask: Task<Void, Never>?
    private var captureLoopTask: Task<Void, Never>?
    private var speakingIdleTask: Task<Void, Never>?
    /// Periodic silence-audio task that prevents Nova Sonic's 55-second
    /// idle-timeout from firing while the voice session is pre-warmed but
    /// the user is on a different tab. Fires every 40 seconds while
    /// connected and not currently talking.
    private var keepAliveTask: Task<Void, Never>?

    // MARK: - Internal state

    /// Text messages typed before `.ready`. Flushed in order once ready.
    private var pendingOutgoing: [String] = []

    /// Did we attempt an auto-reconnect after the most recent unexpected
    /// disconnect? Prevents infinite retry loops.
    private var didAutoReconnectOnce = false

    /// Latched true when `disconnect()` is called explicitly (user
    /// sign-out, AppSession identity-drift teardown, tab dismissed,
    /// etc.). Suppresses the auto-reconnect path in
    /// `handleUnexpectedDisconnect`: the `.sessionEnded` event fires
    /// *after* tearDownActors closes the WebSocket, and without this
    /// latch the receive-loop treats our own shutdown as an
    /// unexpected disconnect and reconnects on the SAME VM — which
    /// has the OLD pinned vin/vehicleId/driverId, so switching
    /// drivers ends up talking to Nova with the previous driver's
    /// credentials. Observed 2026-05-08: Stephanie → Samantha
    /// switch and Nova kept addressing the user as "Stephanie"
    /// because the triage call used Stephanie's VIN.
    private var permanentlyDisconnected = false

    /// True while mic capture is running. Separate from `state` because
    /// state transitions happen on transcript/audio events, while mic
    /// control is driven by user taps. Exposed so the UI can key the mic
    /// button's "listening" glyph off this directly rather than the
    /// `.talking` state, which is vulnerable to being interrupted by
    /// assistant audio arriving mid-turn.
    private(set) var isTalking = false

    /// Whether Nova is expected to produce more output before the
    /// current turn ends. Single source of truth for the chat's
    /// thinking indicator. SEMANTICALLY = "the server is currently
    /// processing this turn and we are still waiting for output."
    ///
    /// Lifecycle:
    ///   user-final transcript        → set true   (waiting for Nova)
    ///   sendText()                   → set true   (waiting for Nova)
    ///   assistant is_final=true      → set false  (Nova marked done)
    ///       UNLESS a tool is in flight, in which case stay true —
    ///       Nova will speak again after the tool result lands.
    ///   tool result                  → set true   (Nova about to narrate)
    ///   audio-idle 1.2s after Nova's last chunk → set false (defensive
    ///       force-clear; if Nova had more to say, we'd still be
    ///       receiving chunks. This catches the case where Nova
    ///       narrates a question and waits for the user — no
    ///       assistant is_final=true ever arrives because Nova doesn't
    ///       always emit one when she's awaiting user reply).
    ///   booking / info card render   → set false  (turn-terminal)
    ///   escalation initiated         → set false  (handed to human)
    ///
    /// Cycle 3 (issues/2026-05-28-ios-chat-thinking-indicator-persists):
    /// The thinking indicator is NO LONGER a transcript entry. It is
    /// a derived view-model property (`isServerThinking`) computed
    /// from this flag plus the speaking state. Cycles 1+2 rendered
    /// the indicator as `TranscriptEntry(isThinking: true)` and tried
    /// to manage its insertion/removal across multiple firing paths
    /// (case .transcript, case .toolCall, scheduleSpeakingIdleTimeout)
    /// using local heuristics; that approach was wrong in principle.
    /// The only authoritative "Nova is processing" signal is the
    /// server-side event lifecycle, which this flag now mirrors.
    private var awaitingMoreFromNova: Bool = false

    /// Computed: should the chat show a "Nova is thinking" indicator?
    ///
    /// True iff the server is currently processing a turn AND no
    /// audio is actively being delivered. The view layer renders this
    /// as a sticky row below the transcript — NOT as a transcript
    /// entry. There is exactly one writer of indicator visibility:
    /// this property. There is exactly one reader: the view.
    ///
    /// `state == .speaking` is treated as "Nova is currently producing
    /// output" — the indicator hides during active audio streaming
    /// because the user can hear Nova talking. The 1.2s post-audio
    /// debounce that flips state from .speaking back to .ready is
    /// owned by `scheduleSpeakingIdleTimeout`; that same timer also
    /// force-clears `awaitingMoreFromNova` so the indicator does NOT
    /// turn on after Nova narrates a question and idles.
    ///
    /// `state == .thinking` plus `awaitingMoreFromNova == true` is the
    /// canonical "indicator on" state — covers the gap between user
    /// input and Nova's first audio chunk, and the gap between a
    /// tool result and Nova's narration of it.
    var isServerThinking: Bool {
        guard awaitingMoreFromNova else { return false }
        switch state {
        case .speaking:
            // Nova is actively producing output — user hears her.
            return false
        case .talking:
            // User is currently speaking — server isn't processing yet.
            return false
        case .disconnected, .error, .connecting:
            // No live session.
            return false
        case .ready, .thinking:
            // Either between user-input and Nova's first output, or
            // post-audio quiescence with the turn not yet finalized.
            return true
        }
    }
    /// Timestamp of last audio chunk received from Nova (for echo suppression).
    private var lastAudioChunkAt: Date?
    /// True while Nova is speaking — gates mic sending to prevent echo.
    private var suppressMic = false
    /// Last service center name offered (for booking confirmation card).
    private var lastOfferedCenterName: String?

    // MARK: - Nova silence watchdog
    //
    // Nova Sonic v2 sometimes accepts a user turn (audio + text) and
    // even calls the right tool, then goes completely silent — no
    // assistant transcript, no audio_stream, nothing — until the
    // server-side 55s session timeout fires. Without this watchdog
    // the user just sits staring at the thinking dots for almost a
    // minute before the WebSocket gets killed.
    //
    // Strategy:
    //   1. Whenever the user takes a turn (text input or demo clip),
    //      arm a 5s watchdog and stash the user's text for retry.
    //   2. Whenever a tool_result lands, re-arm the watchdog —
    //      Nova should narrate the result next.
    //   3. As soon as ANY assistant audio chunk arrives, cancel the
    //      watchdog (Nova is speaking).
    //   4. If the watchdog fires and Nova is still expected to
    //      respond:
    //        attempt 1: silently re-send the last user text via
    //                   client.sendText, then re-arm the watchdog
    //                   one more time.
    //        attempt 2: surface a user-visible error on the
    //                   transcript ("Nova didn't respond. Tap the
    //                   mic to try again.") and stop the spinner.
    //   5. Reset the retry counter when a fresh user turn starts or
    //      Nova produces audio.
    private var novaSilenceTask: Task<Void, Never>?
    private var lastUserTextInput: String?
    private var novaSilenceRetryAttempt: Int = 0
    /// Set when the 2nd silence watchdog fires (Nova didn't respond
    /// even after the auto-retry). UI surfaces this as a tappable
    /// retry banner. Cleared automatically on the next successful
    /// user turn.
    private(set) var novaUnresponsive: Bool = false

    /// Have we seen a user-role final transcript yet in this session?
    /// Used to suppress the priming-response assistant turn and its
    /// audio chunks — the priming text (see bidi_app.py PRIMING_TEXT)
    /// is framed as a user turn, and Nova Sonic replies to it with a
    /// greeting the user didn't ask for. We don't want that greeting
    /// rendered (or spoken) because from the user's perspective they
    /// haven't said anything yet.
    private var hasSeenFirstUserTurn = false

    /// Timestamp of the last user transcript we synthesized locally
    /// (e.g. from a demo clip's `transcriptText`). When Nova's STT
    /// later emits its own user transcript for the same audio
    /// injection, we want to suppress that duplicate so the demo
    /// viewer doesn't see two consecutive user bubbles ("Yes,
    /// Tuesday at one PM." followed by Nova's slightly-rephrased
    /// "yes, tuesday at one pm."). Nova's STT was observed taking
    /// up to 90 seconds to commit (2026-05-20), so we use a long
    /// 120s window — and reset only when the user takes a new turn
    /// (next demo clip, mic press, or typed text).
    private var lastSyntheticUserTranscriptAt: Date? = nil

    /// Same idea for the assistant side. When `_auto_book_on_affirmation`
    /// runs server-side it sends us a `transcript role=assistant` event
    /// with the booking confirmation, AND injects a TTS narration to
    /// Nova. If Nova's TTS injection eventually produces audio (often
    /// 60-90s later), it also produces a streamed assistant transcript
    /// that duplicates the confirmation text we already showed.
    /// Suppress that duplicate when it arrives close to (or even far
    /// after) the synthetic — viewer should never see "Done — you're
    /// booked..." rendered twice.
    private var lastSyntheticAssistantText: String? = nil
    private var lastSyntheticAssistantAt: Date? = nil
    /// Running count of assistant audio chunks received this session. Used
    /// only for rate-limited diagnostic logging so we can confirm from the
    /// iOS console that audio is actually arriving from the backend vs
    /// being suppressed or dropped client-side.
    private var audioChunksReceived: Int = 0
    /// Timestamp when the connect finished (state left .connecting). Used
    /// by isSessionReadyForAudio() to add a ~1.5s buffer before flushing
    /// locally-buffered audio chunks to Nova Sonic, since the priming
    /// cycle needs to complete first or audio is silently dropped.
    private var sessionReadyAt: Date?
    /// Timestamp of the last priming-response event (audio chunk or text
    /// transcript) received from Nova Sonic. Used to detect when the
    /// priming turn has fully ended — Nova Sonic won't accept user audio
    /// until its priming response is complete, and it gives us no
    /// explicit "priming done" signal. We infer it from silence.
    private var primingLastActivityAt: Date?

    // MARK: - Init

    init(tenantId: String = VSAConfig.defaultTenantId,
         vin: String = VSAConfig.demoVin,
         vehicleId: String? = nil,
         driverId: String? = nil,
         jwtProvider: @escaping @Sendable () -> String?,
         locationProvider: @escaping @Sendable () -> (Double, Double)? = { nil }) {
        self.tenantId = tenantId
        self.vin = vin
        self.vehicleId = vehicleId
        self.driverId = driverId
        self.jwtProvider = jwtProvider
        self.locationProvider = locationProvider
        self.credentialProvider = AwsCredentialProvider(
            region: VSAConfig.awsRegion,
            identityPoolId: VSAConfig.defaultPool.identityPoolId,
            userPoolId: VSAConfig.defaultPool.userPoolId,
            idTokenProvider: jwtProvider
        )
    }

    // MARK: - Lifecycle (call from tab onAppear / onDisappear)

    /// Open the WebSocket session. Safe to call from `.disconnected` or
    /// `.error`. No-op if already connecting or ready.
    ///
    /// Defensive: if state claims an "active" value (.connecting/.ready/etc.)
    /// but `client == nil`, we're in a stale wedge — most likely a prior
    /// failed pre-warm where state didn't get fully reset. Recover by
    /// forcing state to `.disconnected` and proceeding through the connect
    /// path instead of silently early-returning. Without this guard,
    /// AssistantTabView's `.task` fires `vm.connect()` (no-op) followed by
    /// `vm.sendText(seed)` which then sees a non-nil VM with a nil bidi
    /// client and surfaces "Send: WebSocket not connected" — Bug A from
    /// `cvx/issues/2026-05-27-ios-bidi-websocket-not-connected`.
    func connect() async {
        let entryState = state
        let entryHasClient = client != nil
        NSLog("🎤 VOICE: connect() entry state=%@ client=%@",
              "\(entryState)", entryHasClient ? "non-nil" : "nil")

        switch state {
        case .disconnected, .error:
            break
        case .connecting, .ready, .talking, .thinking, .speaking:
            if client == nil {
                NSLog("🎤 VOICE: connect() detected stale active-state with nil client — forcing reset state=%@",
                      "\(state)")
                state = .disconnected
                // Fall through to the connect path below.
            } else {
                NSLog("🎤 VOICE: connect() early-return — already in active state=%@", "\(state)")
                return
            }
        }

        guard let jwt = jwtProvider(), !jwt.isEmpty else {
            NSLog("🎤 VOICE: connect() jwt missing or empty — aborting")
            state = .error("Not signed in")
            return
        }
        NSLog("🎤 VOICE: connect() jwt acquired len=%d", jwt.count)

        lastError = nil
        permanentlyDisconnected = false
        transcript.removeAll()
        toolInteractions.removeAll()
        latestClassification = nil
        latestClassificationSource = nil
        latestClassificationCategory = nil
        hasSeenFirstUserTurn = false
        audioChunksReceived = 0
        primingLastActivityAt = nil
        sessionReadyAt = nil
        // Only reset handoff state if we're not mid-handoff. The voice
        // session auto-reconnect path shouldn't drop the user's live
        // chat with Kevin; that chat lives on the ConnectChatClient's
        // independent WebSocket and doesn't care about the voice socket.
        switch handoffState {
        case .connected, .connecting, .initiated:
            // Keep chat alive across voice-session reconnects.
            break
        default:
            handoffState = .none
            chatMessages.removeAll()
        }
        state = .connecting

        let client = VSABidiClient(backend: .agentcoreBidi(
            agentRuntimeArn: VSAConfig.agentCoreBidiRuntimeArn,
            region: VSAConfig.awsRegion
        ))
        let capture = AudioCapture()
        let player = AudioPlayer()
        self.client = client
        self.capture = capture
        self.player = player

        // Configure audio output session early so barge-in playback is
        // ready the instant the first assistant audio.chunk arrives.
        // Non-fatal: simulator CoreAudio HAL may be unavailable (no host
        // audio device). Session continues in text-only mode — AudioPlayer
        // guards all play() calls with `guard started` so they're safe no-ops.
        do {
            try await player.start()
        } catch {
            NSLog("🎤 VOICE: connect() audio output start failed (continuing text-only): %@ (%@)",
                  "\(error.localizedDescription)", String(describing: error))
        }

        // Permission pre-check for the mic. Not starting capture yet — just
        // confirming we can later, so the user doesn't get a surprise dialog
        // mid-conversation. If denied, session still opens; mic toggle will
        // show an error if they try to use it.
        //
        // We deliberately do NOT treat denial as fatal for the session,
        // because typing-only use is a first-class path now.
        try? await capture.requestPermissionOnly()

        // Connect the signed WebSocket.
        let eventStream: AsyncStream<VSABidiClient.Event>
        do {
            // Snapshot live coords so the connect call sees a stable
            // value. session.liveState updates in place; reading once
            // here avoids any chance of the closure firing twice with
            // mid-flight changes.
            let liveCoords = locationProvider()
            NSLog("🎤 VOICE: connect() calling client.connect tenant=%@ vin=%@ vehicleId=%@ driverId=%@ coords=%@",
                  tenantId, vin, vehicleId ?? "-", driverId ?? "-",
                  liveCoords.map { "(\($0.0),\($0.1))" } ?? "-")
            eventStream = try await client.connect(
                tenantId: tenantId,
                vin: vin,
                jwt: jwt,
                vehicleId: vehicleId,
                driverId: driverId,
                latitude: liveCoords?.0,
                longitude: liveCoords?.1,
                credentialProvider: credentialProvider
            )
            NSLog("🎤 VOICE: connect() client.connect returned eventStream — handshake completed")
        } catch {
            NSLog("🎤 VOICE: connect() client.connect threw: %@ (%@)",
                  "\(error.localizedDescription)", String(describing: error))
            state = .error("Connect: \(error.localizedDescription)")
            await tearDownActors()
            return
        }

        state = .ready
        onDrawerEvent?(.sessionStarted)
        print("🎤 VOICE: session ready (tenant=\(tenantId) vin=\(vin) vehicleId=\(vehicleId ?? "-") driverId=\(driverId ?? "-"))")

        // Send an immediate silent audio chunk to wake Nova Sonic v2.
        // Without this, Nova Sonic v2 sometimes ignores the FIRST
        // text.input of a session entirely (no transcript, no audio,
        // no tool call — just silent until the 55s session timeout).
        // CRITICAL: this MUST be awaited before flushPendingOutgoing
        // below — otherwise the text.input lands at Nova first and
        // the wake-up arrives too late.
        // 200ms gives Nova's VAD enough audio to register as a
        // user-speaking-then-paused turn before the text arrives;
        // 50ms was too brief and Nova still ignored some sessions.
        let silenceSamples = 3200  // 200ms × 16000Hz
        let silenceBytes = Data(count: silenceSamples * 2)
        let b64 = silenceBytes.base64EncodedString()
        do {
            try await client.sendAudioChunk(b64)
            NSLog("🎤 VOICE: wake-up silent chunk sent (200ms) BEFORE flush")
        } catch {
            NSLog("🎤 VOICE: wake-up chunk failed: %@", "\(error)")
        }

        // Consume backend events.
        eventLoopTask = Task { [weak self] in
            for await event in eventStream {
                await self?.handle(event: event)
            }
            await self?.handleStreamClosed()
        }

        // Keep-alive: Nova Sonic v2 closes sessions after 55 seconds of
        // silence between audio/interactive inputs ("InternalErrorCode=532:
        // Timed out waiting for audio bytes or interactive content"). When
        // the voice session is pre-warmed but the user is on a different
        // tab, we trip that timeout unless we send something periodically.
        // Solution: send a tiny silent audio chunk every 40s while the
        // session is idle. Stops firing when the user starts talking.
        keepAliveTask = Task { [weak self, weak client] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)  // 5s
                guard let self, let client = client else { return }
                if Task.isCancelled { return }
                // Skip when audio is actively flowing from the mic, or
                // when Nova is processing/speaking — sending a 50ms
                // silence chunk during these phases makes Nova's VAD
                // think the user is still talking, which delays the
                // model's response by minutes (observed 2026-05-20:
                // 1m 18s gap between auto-book IMMEDIATE injection
                // and Nova's TTS actually playing the confirmation,
                // because keep-alive pings every 5s kept resetting
                // Nova's end-of-utterance timer). Only send when the
                // session is truly idle (state=.ready) so we hit
                // Nova's 55s session timeout, not its turn-end timer.
                switch self.state {
                case .ready, .disconnected:
                    break  // proceed to send keep-alive
                case .talking, .thinking, .speaking, .connecting, .error:
                    continue  // skip — would interfere with current turn
                }
                if self.isTalking {
                    continue
                }
                // 50ms of PCM silence at 16kHz mono 16-bit = 1600 bytes
                // = 2133 base64 chars. Negligible bandwidth.
                let silenceSamples = 800  // 50ms * 16000Hz
                let silenceBytes = Data(count: silenceSamples * 2)
                let b64 = silenceBytes.base64EncodedString()
                do {
                    try await client.sendAudioChunk(b64)
                    NSLog("🎤 VOICE: keep-alive ping sent")
                } catch {
                    NSLog("🎤 VOICE: keep-alive ping failed: %@", "\(error)")
                    // Don't tear down — next ping will retry.
                }
            }
        }

        // Flush anything the user typed during .connecting.
        await flushPendingOutgoing()
    }

    /// Close the session cleanly. Used on tab disappear or explicit user
    /// action. Idempotent.
    func disconnect() async {
        NSLog("🎤 VOICE: disconnect() called state=%@ permanentlyDisconnected=%@",
              "\(state)", "\(permanentlyDisconnected)")
        // Latch the permanent-disconnect flag BEFORE tearing down. The
        // WebSocket close triggers a `.sessionEnded` event that arrives
        // at the receive-loop after tearDownActors finishes. Without
        // this latch, the receive-loop would treat our own shutdown as
        // an unexpected disconnect and reconnect on this same VM with
        // its pinned (now-stale) credentials.
        permanentlyDisconnected = true

        // Also tear down any live Connect chat; this path fires on sign-out
        // and we don't want a stale chat client clinging to a dead auth.
        await teardownChat(reason: "session-disconnect")
        handoffState = .none
        chatMessages.removeAll()

        await tearDownActors()
        state = .disconnected
        didAutoReconnectOnce = false
        pendingOutgoing.removeAll()
        onDrawerEvent?(.sessionEnded)
        print("🎤 VOICE: session disconnected")
    }

    // MARK: - Input (text and mic)

    /// Play a bundled demo audio clip as if the driver had spoken it.
    /// Used by the Demo Phrases drawer on the Assistant screen — lets
    /// the iOS Simulator (no reliable mic access) run the full voice
    /// flow during a Zoom-shared demo. The caller resolves the
    /// persona-specific filename via `DemoClip.fileName(for:)` and
    /// passes it here as a plain string; the VM stays
    /// persona-agnostic and just plays whatever clip name it gets.
    ///
    /// Two parallel audio paths:
    ///   1. Local playback through the device speakers (AVAudioPlayer).
    ///      That's what the demo audience hears on a Zoom share — the
    ///      prompt sounds like a person speaking, not silent text
    ///      appearing on screen.
    ///   2. Server injection through the existing WebSocket
    ///      (DemoAudioInjector). Backend ASR transcribes and Nova
    ///      replies via TTS as usual.
    /// Both run concurrently; the injector adds ~800ms of trailing
    /// silence that the server uses to detect end-of-utterance.
    /// A `text.input` is sent right after the audio injection
    /// completes so the backend has a deterministic transcript even
    /// if Nova's STT is slow or drops the audio.
    ///
    /// Implementation mirrors talkStart/talkStop's important bits —
    /// barge-in flush, mic mute, isTalking flag — so the rest of the
    /// session's state machine can't tell it apart from a live mic
    /// turn.
    ///
    /// No-op on `.error` / `.disconnected` / `.connecting` (no live
    /// session to feed) and on a broken-out-of-band attempt while
    /// already speaking the clip (avoid stomping on an in-flight
    /// playback).
    /// Reset visible demo state without tearing the session down.
    /// Used by the Account tab's "Reset demo" button so the next demo
    /// run starts on a clean transcript without re-establishing the
    /// WebSocket / agent / audio session (which adds ~2-4 seconds of
    /// startup latency we don't want during a live demo).
    ///
    /// Clears:
    ///   - the visible transcript and reasoning-drawer tool history
    ///   - any pending text-input queue
    ///   - the post-handoff Connect chat messages (the chat itself
    ///     stays alive — clearing the local mirror keeps the demo
    ///     visually clean even though Kevin can still type)
    ///   - the latest triage classification badge
    ///
    /// Does NOT clear:
    ///   - WebSocket / agent state on the server side. Nova still
    ///     remembers the conversation. For a clean slate at the
    ///     model level, sign out and back in.
    ///   - handoff state if a Connect chat is currently active —
    ///     keeping that flag intact prevents the chat banner from
    ///     ghosting between resets while a real human is on the
    ///     other side.
    func resetDemoState() {
        NSLog("🎤 VOICE: resetDemoState — clearing transcript and tool history")
        transcript.removeAll()
        toolInteractions.removeAll()
        pendingOutgoing.removeAll()
        latestClassification = nil
        latestClassificationSource = nil
        latestClassificationCategory = nil
        audioChunksReceived = 0
        awaitingMoreFromNova = false
        // Keep chatMessages / handoffState intact when a chat is
        // currently connected — wiping mid-handoff would orphan the
        // banner. When idle, also clear the chat mirror so the next
        // demo doesn't show a stale "previously connected" hint.
        switch handoffState {
        case .connected, .connecting, .initiated:
            break
        default:
            chatMessages.removeAll()
            handoffState = .none
        }
    }

    func playDemoClip(named clipFileName: String, transcriptText: String? = nil) async {
        NSLog(
            "🎤 VOICE: playDemoClip name=%@ state=%@ isTalking=%@",
            clipFileName, "\(state)", "\(isTalking)"
        )
        switch state {
        case .error, .disconnected, .connecting:
            NSLog("🎤 VOICE: playDemoClip bailing — state=%@", "\(state)")
            return
        default:
            break
        }
        guard let client = client else {
            NSLog("🎤 VOICE: playDemoClip bailing — client nil")
            return
        }
        // Barge-in: if Nova is still speaking when the demoer taps a
        // clip, flush the player and reset state to .ready first. Same
        // logic as talkStart's mid-response handling — we don't want
        // Nova's in-flight TTS overlapping the new clip's audio.
        if state == .speaking || state == .thinking {
            if let player = player {
                await player.flush()
            }
            speakingIdleTask?.cancel()
            suppressMic = false
            state = .ready
        }
        // Mute the real mic for the duration of clip playback so the
        // simulator's bonus noise (or real-device feedback) doesn't
        // mix into the injected audio. The capture stays paused;
        // we'll let the natural turn-end logic flip state back to
        // .ready when Nova finishes responding.
        await capture?.mute()
        suppressMic = true
        isTalking = true
        state = .talking

        // Kick off local playback through the speakers in parallel
        // with the server injection. AVAudioPlayer.play() returns
        // immediately — it streams audio to the HAL on its own
        // thread — so we don't await or wrap in Task. The
        // PlayAndRecord audio session with .defaultToSpeaker routes
        // this to the simulator's host audio output, which Zoom
        // screen-share captures alongside Nova's TTS reply.
        // DemoLocalPlaybackCache retains the player until playback
        // finishes so it survives the function return.
        playLocalDemoClip(named: clipFileName)

        let injector = DemoAudioInjector(client: client)
        // Append the local user transcript bubble BEFORE awaiting
        // injection so the demo viewer sees the user's phrase
        // immediately (and so hasSeenFirstUserTurn flips for the
        // priming-suppression guard). The actual user turn
        // delivered to Nova is the AUDIO injection — Nova STTs the
        // WAV bytes itself and treats it as a normal voice turn
        // with prosody, end-of-utterance VAD, etc.
        if let transcriptText, !transcriptText.isEmpty {
            appendTranscript(role: "user", text: transcriptText, isFinal: true)
            hasSeenFirstUserTurn = true
            // Nova's later STT-derived transcript should match
            // this text. Suppress the duplicate when Nova echoes
            // it back (lastSyntheticUserTranscriptAt check in the
            // transcript handler).
            lastSyntheticUserTranscriptAt = Date()
        }
        // AUDIO-ONLY demo clip injection.
        //
        // Why we removed the parallel text.input (2026-05-22):
        // Nova Sonic v2 was designed around real voice input — the
        // model uses prosody, intonation, and end-of-utterance VAD
        // from the audio stream. Adding a parallel text.input for
        // the same user turn creates two overlapping "user
        // contents" with different fidelities (audio + prosody
        // vs. flat text). Nova marks one of them
        // stopReason=PARTIAL_TURN and goes silent post-tool-result
        // because its turn-tracking state is corrupted. We caught
        // this in the act in agent log session vsa-ios-CFB1E468-ADA
        // (full trace in agents/supervisor/bidi_app_canonical.py
        // commit notes).
        //
        // Demo clip phrases were updated to 1.5s+ with clear
        // sentence breaks (see scripts/generate-demo-clips.py
        // CLIPS) so Nova's VAD reliably commits end-of-utterance.
        // Short phrases like "Yes." (~600ms) were the original
        // problem — Nova's STT didn't always commit, which is
        // why we'd added the text.input fallback in the first
        // place. Longer phrases avoid that.
        do {
            try await injector.play(clipName: clipFileName)
            NSLog("🎤 VOICE: demo clip audio injected name=%@ (audio-only)", clipFileName)
            if let transcriptText, !transcriptText.isEmpty {
                lastUserTextInput = transcriptText
                resetNovaSilenceState()
                awaitingMoreFromNova = true
                armNovaSilenceWatchdog(reason: "after-demo-clip-audio-only")
            }
        } catch {
            NSLog("🎤 VOICE: playDemoClip audio inject error %@", "\(error)")
        }
        // Mark the local "user is talking" turn as ended. The server
        // will deliver the user-final transcript a moment later via
        // ASR, which routes through the existing transcript path —
        // we do NOT pre-populate the transcript here, because then
        // appendTranscript would have to merge two sources.
        isTalking = false
        state = .thinking
    }

    /// Play a bundled demo WAV through the device speakers. The
    /// retained AVAudioPlayer is held in a process-global cache so
    /// it isn't deallocated mid-playback — without that, ARC would
    /// tear the player down as soon as this method returns.
    private func playLocalDemoClip(named clipName: String) {
        guard let url = Bundle.main.url(
            forResource: clipName, withExtension: "wav"
        ) else {
            NSLog("🎤 VOICE: playLocalDemoClip clip not found: %@", clipName)
            return
        }
        do {
            let player = try AVAudioPlayer(contentsOf: url)
            player.prepareToPlay()
            // Stash on the per-VM bag so ARC keeps the player alive
            // until playback finishes. `delegate` isn't set; we don't
            // need a finish callback because state is driven by the
            // injector's completion, not the local audio.
            DemoLocalPlaybackCache.shared.retain(player)
            player.play()
        } catch {
            NSLog("🎤 VOICE: playLocalDemoClip failed: %@", "\(error)")
        }
    }

    /// Send a typed text message. If the session isn't `.ready` yet, the
    /// message is queued and sent when ready.
    ///
    /// Defensive (Bug A): if state is "active" (e.g. `.ready`) but
    /// `client == nil`, the only sane recovery is to queue the message,
    /// reset state, and reconnect — same path as the `.disconnected`
    /// branch. The original code silently appended the transcript and
    /// returned, leaving the user staring at their seed message with no
    /// indication that a connect was needed. With the recovery in place,
    /// the (i) button still works even if the pre-warm wedged.
    func sendText(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let entryState = state
        let entryHasClient = client != nil
        NSLog("🎤 VOICE: sendText() entry state=%@ client=%@ chars=%d",
              "\(entryState)", entryHasClient ? "non-nil" : "nil", trimmed.count)

        // Queue-while-connecting: user can type before handshake completes
        // or reconnect finishes; don't lose their input.
        if state == .connecting || state == .disconnected {
            NSLog("🎤 VOICE: sendText() queueing — state=%@ pending=%d",
                  "\(state)", pendingOutgoing.count + 1)
            pendingOutgoing.append(trimmed)
            appendTranscript(role: "user", text: trimmed, isFinal: true, isPending: true)
            // If we're disconnected (e.g. tab just disappeared/reappeared),
            // kick off a connect now so the queue eventually flushes.
            if state == .disconnected {
                await connect()
            }
            return
        }

        // Bug A defense: if state claims "active" but client is nil, this
        // is the stale-wedge case (pre-warm failure that didn't cleanly
        // reset state). Queue the message and force a reconnect rather
        // than silently appending the transcript or hitting the catch
        // path with a misleading "Send: WebSocket not connected" error.
        if client == nil {
            NSLog("🎤 VOICE: sendText() detected stale active-state with nil client — queueing and forcing reconnect state=%@",
                  "\(state)")
            pendingOutgoing.append(trimmed)
            appendTranscript(role: "user", text: trimmed, isFinal: true, isPending: true)
            state = .disconnected
            await connect()
            return
        }

        // Bug fix: the original guard `state != .error(.init())` only matched
        // `.error("")` (empty string). Any real error like `.error("Audio output: …")`
        // or `.error("Connect: …")` slipped through, reached `client.sendText()`,
        // and threw `notConnected` because `task` was nil (connect() had failed
        // before the WebSocket handshake ran). Use a pattern-match instead so
        // ALL error states bail out here.
        if case .error = state {
            NSLog("🎤 VOICE: sendText() error-state branch — appending transcript only state=%@",
                  "\(state)")
            appendTranscript(role: "user", text: trimmed, isFinal: true)
            return
        }
        guard let client = client else {
            // client nil with non-error state — shouldn't happen after the
            // stale-wedge check above, but guard defensively.
            NSLog("🎤 VOICE: sendText() client nil in non-error state=%@ — appending only",
                  "\(state)")
            appendTranscript(role: "user", text: trimmed, isFinal: true)
            return
        }
        appendTranscript(role: "user", text: trimmed, isFinal: true)
        // The user just typed/seeded a message. That counts as the
        // first user turn — without flipping this flag, the
        // "suppress priming response" guard in the audio/transcript
        // event handler stays armed and silently drops Nova's reply
        // (no audio plays, no assistant transcript renders). The
        // priming-suppression logic was originally written assuming
        // the first user turn would always be SPEECH (which produces
        // a `transcript role=user, isFinal=true` event from Nova
        // that flips the flag). Text-seeded sessions — primed by
        // the DTC info button or the Service Book Service CTA —
        // never produce that user transcript, so we have to flip
        // the flag here. Bug observed 2026-05-20: tapping the (i)
        // on a P0299 row produced a tool.call but no audible /
        // visible response.
        hasSeenFirstUserTurn = true
        // Stash for the silence-watchdog auto-retry path. If Nova
        // doesn't produce audio within 5s of this turn, the
        // watchdog will silently re-send this same text once
        // before showing the user-visible "didn't catch a
        // response" fallback.
        lastUserTextInput = trimmed
        resetNovaSilenceState()
        awaitingMoreFromNova = true
        state = .thinking
        do {
            try await client.sendText(trimmed)
            armNovaSilenceWatchdog(reason: "after-sendText")
        } catch {
            NSLog("🎤 VOICE: sendText() client.sendText threw: %@ (%@)",
                  "\(error.localizedDescription)", String(describing: error))
            state = .error("Send: \(error.localizedDescription)")
        }
    }

    /// Start capturing and streaming mic audio (push-to-talk begin).
    /// If the session is still `.connecting`, waits up to ~3s for it to
    /// reach `.ready` before proceeding — so a tap-during-connect feels
    /// responsive instead of silently no-oping. If the session never
    /// reaches `.ready` (connect failed), bails without error; the UI's
    /// error card surfaces the failure separately.
    ///
    /// Flow (2026-04-30, simplified after optimistic-buffering proved
    /// lossy): wait for the Nova Sonic session to be fully ready before
    /// starting mic capture. We keep the UI spinner approach to give
    /// immediate feedback on the tap, but the actual mic only opens
    /// once the session can ingest audio. This avoids a class of bugs
    /// where buffered pre-session-ready audio chunks confused Nova
    /// Sonic's ASR.
    func talkStart() async {
        NSLog("🎤 VOICE: talkStart called. state=%@ isTalking=%@", "\(state)", "\(isTalking)")
        // Normally we guard against double-start. But if isTalking got
        // stuck true (e.g. the server never sent the isFinal=true user
        // transcript that triggers talkStop(), or capture errored out
        // without flipping the flag), the mic button ends up wedged in
        // "stop" mode and tapping it stops a dead session — user presses
        // mic again expecting a new turn, nothing records, silence.
        // Recovery: force a clean talkStop here so the subsequent
        // capture.start() is reliable.
        if isTalking {
            NSLog("🎤 VOICE: talkStart found stale isTalking=true — forcing talkStop before retry")
            await talkStop()
            // Small yield so the capture actor's stop completes before
            // we ask it to start again. Actor re-entry guarantees make
            // this largely theoretical, but the belt-and-braces is
            // cheap.
            try? await Task.sleep(nanoseconds: 30_000_000)
        }

        switch state {
        case .error, .disconnected:
            NSLog("🎤 VOICE: talkStart bailing — state=%@ is unrecoverable", "\(state)")
            return
        default:
            break
        }
        guard let capture = capture, let client = client else {
            NSLog("🎤 VOICE: talkStart bailing — capture or client nil")
            return
        }

        // Client-initiated barge-in: if the assistant is still speaking
        // or thinking when the user taps the mic, flush the player and
        // flip state back to .ready BEFORE opening the capture.
        //
        // Why this matters: on simulator (and on device without AEC
        // headphones) the mic will pick up the speaker's own output.
        // If we leave assistant audio playing while capture opens, the
        // feedback loop confuses Nova Sonic's server-side VAD — the
        // model ends up "hearing" its own voice, doesn't detect the
        // user's interruption, and never emits a user-turn transcript.
        // Symptom (reported 2026-05-07): user taps mic to correct, speaks,
        // but nothing appears in the transcript. Flushing here gives the
        // mic a clean acoustic environment and tells our own state machine
        // the assistant is done — the server will re-sync when it gets
        // the user's chunks.
        if state == .speaking || state == .thinking {
            NSLog("🎤 VOICE: talkStart barge-in — flushing player, state=%@ -> .ready", "\(state)")
            if let player = player {
                await player.flush()
            }
            speakingIdleTask?.cancel()
            suppressMic = false
            await capture.unmute()
            state = .ready
        }

        // Wait for connect to complete. 10s ceiling.
        if state == .connecting {
            NSLog("🎤 VOICE: talkStart waiting for .connecting -> .ready")
            let deadline = Date().addingTimeInterval(10.0)
            while state == .connecting && Date() < deadline {
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
            NSLog("🎤 VOICE: talkStart connect-wait done. state=%@", "\(state)")
            if state == .connecting {
                state = .error("Still connecting — try again.")
                return
            }
        }

        // Priming wait — disabled in the current supervisor config
        // (PRIMING_TEXT is empty server-side because Nova Sonic v2 accepts
        // audio directly now, so no session warm-up is required). If
        // priming is re-enabled server-side, restore a wait loop here
        // that checks primingLastActivityAt for a quiet window.
        NSLog("🎤 VOICE: talkStart skipping priming wait (priming disabled server-side)")

        // Now actually start mic capture — session is guaranteed ready.
        let audioStream: AsyncStream<String>
        do {
            audioStream = try await capture.start()
        } catch AudioCapture.CaptureError.permissionDenied {
            state = .error("Microphone permission denied. Enable it in Settings.")
            return
        } catch {
            state = .error("Microphone: \(error.localizedDescription)")
            return
        }

        isTalking = true
        state = .talking
        NSLog("🎤 VOICE: talk started (post-ready) state=%@", "\(state)")

        captureLoopTask = Task { [weak self, weak client] in
            var chunkCount = 0
            var totalBytes = 0
            var lastReportAt = Date()
            for await chunk in audioStream {
                guard let self, let client = client else { return }
                // Half-duplex: don't send mic audio while Nova is speaking.
                if self.suppressMic { continue }
                chunkCount += 1
                totalBytes += chunk.count
                if Date().timeIntervalSince(lastReportAt) > 2 {
                    NSLog("🎤 VOICE: %d chunks, %d bytes in last 2s", chunkCount, totalBytes)
                    chunkCount = 0
                    totalBytes = 0
                    lastReportAt = Date()
                }
                do {
                    try await client.sendAudioChunk(chunk)
                } catch {
                    await self.handleSendFailure(error)
                    return
                }
            }
            NSLog("🎤 VOICE: audio stream ended")
        }
    }

    /// No longer used — preserved as an anchor for future optimistic
    /// audio flow if Nova Sonic ever exposes a session.started ACK.
    private func isSessionReadyForAudio() async -> Bool {
        return state == .ready && hasSeenFirstUserTurn
    }

    /// Stop capturing mic audio (push-to-talk end). Server-side VAD decides
    /// the utterance boundary; we just stop feeding it new chunks.
    func talkStop() async {
        guard isTalking else { return }
        isTalking = false
        captureLoopTask?.cancel()
        captureLoopTask = nil
        if let capture = capture {
            await capture.stop()
        }
        // Transition to .thinking so the UI reflects "waiting for response"
        // until the assistant starts speaking. If the server never
        // transcribes anything, the VAD silence-continuation logic will
        // still produce an empty-turn response eventually.
        if state == .talking {
            state = .thinking
        }
        print("🎤 VOICE: talk stopped")
    }

    // MARK: - Event handling

    private func handle(event: VSABidiClient.Event) async {
        switch event {
        case .audioChunk(let base64, _):
            // Suppress the priming-response audio. See hasSeenFirstUserTurn.
            guard hasSeenFirstUserTurn else {
                // Track that priming activity is still happening so
                // talkStart() can wait for it to end before opening the mic.
                primingLastActivityAt = Date()
                NSLog("🎤 VOICE: audioChunk SUPPRESSED (hasSeenFirstUserTurn=false) bytes=%d", base64.count)
                return
            }
            // Rate-limit to one log every ~20 chunks so we can see flow
            // without drowning the console.
            audioChunksReceived += 1
            lastAudioChunkAt = Date()
            if audioChunksReceived == 1 || audioChunksReceived % 20 == 0 {
                NSLog("🎤 VOICE: audioChunk received #%d base64_chars=%d player=%@ state=%@",
                      audioChunksReceived, base64.count,
                      player == nil ? "nil" : "ok", "\(state)")
            }
            if let player = player {
                await player.play(base64Chunk: base64)
            } else {
                NSLog("🎤 VOICE: audioChunk DROPPED — player is nil!")
            }
            if state != .speaking {
                state = .speaking
                suppressMic = true
                await capture?.mute()
            }
            // Nova produced audio — the silence watchdog (and any
            // pending auto-retry) is no longer needed for this turn.
            // Reset the retry counter too: a single successful audio
            // chunk means this turn worked, future turns start fresh.
            cancelNovaSilenceWatchdog(reason: "assistant-audio-arrived")
            if novaSilenceRetryAttempt != 0 {
                novaSilenceRetryAttempt = 0
            }
            scheduleSpeakingIdleTimeout()

        case .transcript(let role, let text, let isFinal, let isSynthetic):
            print("🎤 VOICE: transcript[\(role), final=\(isFinal)\(isSynthetic ? ", synthetic" : "")] len=\(text.count): \(text.prefix(200))")
            NSLog("🎤 VOICE: transcript role=%@ final=%@ synthetic=%@ len=%d preview=%@",
                  role, "\(isFinal)", "\(isSynthetic)", text.count, String(text.prefix(60)))
            // Suppress the priming-response assistant transcript. First
            // assistant turn of the session is always a reply to the
            // priming text we secretly injected; the user didn't ask for
            // it, so we don't display it or count it in state transitions.
            if role == "assistant" && !hasSeenFirstUserTurn {
                primingLastActivityAt = Date()
                return
            }
            // Suppress Nova's late STT echo of a user audio injection
            // we already locally transcribed (demo clip path). Without
            // this, demo viewers see two consecutive user bubbles —
            // first our deterministic clip text ("Yes, Tuesday at one
            // PM."), then Nova's STT-derived rephrasing ("yes, tuesday
            // at one pm.") a few seconds later. Reset
            // lastSyntheticUserTranscriptAt so a real follow-up the
            // viewer types doesn't get accidentally swallowed.
            //
            // 120s window because Nova STT was observed taking up to
            // 90s to commit (2026-05-20); narrower windows let the
            // duplicate slip through. The viewer is unlikely to fire
            // a new demo clip and ALSO a real follow-up within 2 min.
            if role == "user" && isFinal,
               let synthAt = lastSyntheticUserTranscriptAt,
               Date().timeIntervalSince(synthAt) < 120.0 {
                NSLog(
                    "🎤 VOICE: transcript SUPPRESSED — Nova STT echo of "
                    + "synthetic clip transcript (delta=%.2fs) text=%@",
                    Date().timeIntervalSince(synthAt), String(text.prefix(60))
                )
                lastSyntheticUserTranscriptAt = nil
                hasSeenFirstUserTurn = true
                awaitingMoreFromNova = true
                return
            }
            // Record the backend-tagged synthetic so we can dedupe
            // Nova's late TTS-streamed echo of the same text. The
            // backend sends this for the auto-book confirmation
            // path; future synthetic-from-server messages can use
            // the same flag to opt into dedup.
            if role == "assistant" && isSynthetic && isFinal {
                lastSyntheticAssistantText = text
                lastSyntheticAssistantAt = Date()
            }
            // Suppress Nova's late TTS-streamed transcript when it
            // duplicates an assistant message we already synthesized
            // server-side (typically the auto-book "Done — you're
            // booked..." confirmation). Same problem as the user side:
            // viewer would see the confirmation twice — once
            // immediately as our synthetic, again 60-90s later as
            // Nova's belated TTS streaming repeats the text. Match
            // is structural (same prefix or contains check) so
            // partial Nova streams ("Done — you're book...") still
            // suppress correctly.
            if role == "assistant",
               let synth = lastSyntheticAssistantText,
               let synthAt = lastSyntheticAssistantAt,
               Date().timeIntervalSince(synthAt) < 120.0,
               (text == synth
                || text.hasPrefix(synth.prefix(20))
                || synth.hasPrefix(text.prefix(20))) {
                NSLog(
                    "🎤 VOICE: transcript SUPPRESSED — Nova TTS echo of "
                    + "synthetic auto-book confirmation (delta=%.2fs, "
                    + "isFinal=%@) text=%@",
                    Date().timeIntervalSince(synthAt), "\(isFinal)",
                    String(text.prefix(60))
                )
                if isFinal {
                    lastSyntheticAssistantText = nil
                    lastSyntheticAssistantAt = nil
                }
                return
            }
            if role == "user" && isFinal {
                hasSeenFirstUserTurn = true
                // Driver finished saying something → Nova owes us a
                // response. The speakingIdleTimeout uses this to
                // decide whether dots should show during the gap.
                awaitingMoreFromNova = true
                // Capture the STT'd text so the silence watchdog
                // can re-send it as text.input if Nova goes
                // silent. Push-to-talk doesn't have a local
                // transcript to retry from, but Nova's STT does.
                lastUserTextInput = text
                resetNovaSilenceState()
                armNovaSilenceWatchdog(reason: "after-user-final-stt")
            }
            // When Nova finalizes her assistant turn, she's signaling
            // "I'm done speaking for now." Clear the awaiting flag so
            // the idle timeout doesn't render dots afterwards. The
            // exception is when a tool is still in flight — Nova will
            // speak again once tool_result lands, so keep the flag true.
            if role == "assistant" && isFinal {
                let hasInFlightTool = toolInteractions.contains { $0.output == nil }
                if !hasInFlightTool {
                    awaitingMoreFromNova = false
                    // Nova's response landed cleanly. The watchdog's
                    // job is done; reset retry counter so the next
                    // turn starts from attempt 0.
                    cancelNovaSilenceWatchdog(reason: "assistant-final")
                    novaSilenceRetryAttempt = 0
                }
            }
            appendTranscript(role: role, text: text, isFinal: isFinal)
            // Mute capture as soon as assistant starts speaking — this
            // arrives before audio chunks, closing the echo window.
            if role == "assistant" && !isFinal {
                await capture?.mute()
                suppressMic = true
            }
            if role == "user" && isFinal && (state == .ready || state == .talking) {
                // Push-to-talk: stop mic after user's turn ends.
                if isTalking {
                    await talkStop()
                }
                state = .thinking
                // Cycle 3: indicator is now a derived `isServerThinking`
                // property, not a transcript entry. The view renders it
                // as a sticky row below the transcript when
                // awaitingMoreFromNova is true and we're not actively
                // speaking. No insertion needed here.
            }

        case .toolCall(let name, let input):
            print("🎤 VOICE: tool.call \(name) input=\(input)")
            NSLog("🎤 VOICE: tool.call name=%@ inputKeys=%@",
                  name, "\(input.keys.sorted())")
            toolInteractions.append(ToolInteraction(
                id: UUID(), name: name, input: input,
                output: nil, startedAt: Date(), completedAt: nil
            ))
            onDrawerEvent?(.toolCall(name: name, input: input))
            // Cycle 3: indicator is `isServerThinking`, computed from
            // `awaitingMoreFromNova` (which is already true here — a
            // tool.call only arrives during an in-flight turn). No
            // transcript entry is inserted. Cycle 1's `awaitingMoreFromNova
            // && state != .speaking && !contains-isThinking` guard is
            // subsumed by the derived property's own state check.

        case .toolResult(let name, let output):
            print("🎤 VOICE: tool.result \(name) output=\(output)")
            NSLog("🎤 VOICE: tool.result name=%@ keys=%@",
                  name, "\(output.keys.sorted())")
            if let idx = toolInteractions.lastIndex(where: { $0.name == name && $0.output == nil }) {
                toolInteractions[idx].output = output
                toolInteractions[idx].completedAt = Date()
            }
            // Tool result landed → Nova will speak the result next.
            // Re-arm the awaiting flag so the speakingIdleTimeout
            // shows dots during the gap before she does.
            awaitingMoreFromNova = true

            // Bug B fix: detect empty/sentinel lookup_knowledge result
            // and inject a fallback narration so Nova has something to
            // say. Without this, Nova goes silent on empty-KB tenants
            // (e.g. tenant=fleet where the corpora directory is
            // missing on the backend) and the silence watchdog tears
            // the session down mid-turn. The transcript bubble is
            // shown synchronously as the safety net; the Nova narration
            // is best-effort fire-and-forget. See
            // `issues/2026-05-26-voice-silence-watchdog-empty-tool-result`.
            if name == "lookup_knowledge" {
                let foundCount = (output["found"]?.value as? Int) ?? -1
                let answerStr = (output["answer"]?.value as? String) ?? ""
                let isEmpty = foundCount == 0
                    || answerStr == "Knowledge base not configured."
                    || answerStr.isEmpty
                if isEmpty {
                    NSLog("🎤 VOICE: lookup_knowledge returned empty/sentinel found=%d answerLen=%d — injecting fallback narration",
                          foundCount, answerStr.count)
                    onDrawerEvent?(.toolResult(name: name, output: output))
                    let fallback = Self.kbEmptyFallback
                    appendTranscript(role: "assistant", text: fallback, isFinal: true)
                    if let client = client {
                        Task { [weak self] in
                            do {
                                try await client.sendText(fallback)
                                NSLog("🎤 VOICE: kbEmptyFallback narration injected to Nova")
                                // Re-arm the watchdog so Nova has a fresh
                                // window to respond before the existing
                                // after-sendText watchdog fires. Reset
                                // the retry counter for the same reason
                                // — this fallback is the new turn baseline.
                                await self?.armNovaSilenceWatchdog(reason: "after-empty-kb-fallback")
                            } catch {
                                NSLog("🎤 VOICE: kbEmptyFallback narration inject failed: %@",
                                      "\(error)")
                            }
                        }
                    } else {
                        NSLog("🎤 VOICE: kbEmptyFallback skip Nova narration — client nil")
                    }
                    // Skip the Nova-driven tool re-arm branch below; this
                    // branch already armed its own watchdog above.
                    return
                }
            }

            // Re-arm the silence watchdog ONLY for Nova-driven
            // tools. The server-side `triage` classifier emits a
            // tool.result event for visibility, but Nova didn't
            // call it — so re-arming on that would restart the
            // silence clock based on a non-Nova event and make
            // the auto-retry fire prematurely while Nova is
            // still processing the original user turn.
            if name == "find_service_center" || name == "book" || name == "escalate_to_human" {
                armNovaSilenceWatchdog(reason: "after-tool-result-\(name)")
            }
            onDrawerEvent?(.toolResult(name: name, output: output))
            // Track last offered service center name for booking card
            if name == "find_service_center" {
                if let centers = output["centers"]?.value as? [[String: Any]],
                   let first = centers.first,
                   let centerName = first["name"] as? String {
                    lastOfferedCenterName = centerName
                } else if let centers = output["centers"]?.value as? NSArray,
                          let first = centers.firstObject as? NSDictionary,
                          let centerName = first["name"] as? String {
                    lastOfferedCenterName = centerName
                }
            }
            // Show booking confirmation card in transcript
            if name == "book", let reqNum = output["requestNumber"]?.value as? String {
                // Deduplicate — only one card per request number
                let alreadyShown = transcript.contains { $0.booking?.requestNumber == reqNum }
                if !alreadyShown {
                    let status = (output["status"]?.value as? String) ?? "confirmed"
                    let centerName = lastOfferedCenterName ?? "Service Center"
                    // Defense-in-depth: this direct `transcript.append`
                    // bypasses `appendTranscript`, which is the only
                    // place that strips stale thinking entries. If any
                    // path (e.g. a late toolCall) left a phantom "..."
                    // bubble in the transcript, the booking card would
                    // land below it and the user would see the
                    // indicator wedged between the assistant's final
                    // message and the confirmation card. Booking is a
                    // turn-terminal event — sweep dots before rendering.
                    transcript.removeAll { $0.isThinking }
                    transcript.append(TranscriptEntry(
                        id: UUID(), role: "assistant", text: "",
                        isFinal: true,
                        booking: BookingConfirmation(
                            requestNumber: reqNum,
                            centerName: centerName,
                            centerAddress: "",
                            status: status
                        )
                    ))
                    // Cycle 2 fix: the booking confirmation card is a
                    // turn-terminal event. Clear `awaitingMoreFromNova`
                    // and cancel the silence watchdog so that:
                    //   1. `scheduleSpeakingIdleTimeout` does NOT
                    //      re-insert "..." dots after Nova's optional
                    //      booking-narration audio idles. Without this,
                    //      `awaitingMoreFromNova` stays true (re-armed
                    //      ~30 lines above on every tool.result), the
                    //      audio idle timer fires 1.2s after the last
                    //      chunk, sees the flag still true, and inserts
                    //      a phantom dot bubble that nothing ever
                    //      clears (Nova's late TTS echo of the
                    //      synthetic auto-book final is suppressed and
                    //      returns early, bypassing the only code path
                    //      that clears `awaitingMoreFromNova`).
                    //   2. `handleNovaUnresponsive` does NOT fire 12s
                    //      after a successful auto-book, surfacing
                    //      "I didn't catch a response. Tap the mic
                    //      and try again." as a phantom error banner.
                    //
                    // The cycle 1 fix (gating `case .toolCall` dot-add
                    // on `awaitingMoreFromNova`) was correct but
                    // incomplete — it closed the path that fires
                    // BEFORE the booking card lands; this closes the
                    // path that fires AFTER, when narration audio
                    // idles. See `~/guidance-for-connected-vehicle-experience-on-aws/issues/2026-05-28-ios-chat-thinking-indicator-persists/`
                    // cycle 2.
                    NSLog("🎤 VOICE: booking-card rendered — clearing awaitingMoreFromNova and cancelling watchdog (turn-terminal)")
                    awaitingMoreFromNova = false
                    cancelNovaSilenceWatchdog(reason: "booking-card-rendered-turn-terminal")
                }
            }

        case .classification(let level, let source, let category):
            print("🎤 VOICE: classification \(level) source=\(source ?? "classifier") category=\(category ?? "-")")
            latestClassification = level
            latestClassificationSource = source
            latestClassificationCategory = category
            onDrawerEvent?(.classification(level))

        case .interruption:
            // Log every interruption so we can tell real barge-in from
            // spurious ones (e.g. simulator echo feedback). Include
            // isTalking so we know whether the mic was even open — if
            // the mic is closed, any interruption is spurious because
            // the user physically can't be talking over the assistant.
            NSLog("🎤 VOICE: interruption received. isTalking=%@ state=%@",
                  "\(isTalking)", "\(state)")
            if let player = player {
                await player.flush()
            }
            if state == .speaking {
                state = .ready
            }

        case .escalation(let payload):
            await handleEscalation(payload)

        case .infoMessage(let source, let markdown):
            // Sub-agent (e.g. diy_repair_advisor) emitted a screen-side
            // info card with detailed reference data. Render as a new
            // transcript entry so it sits inline with the conversation.
            // Nova's brief voice response references it ("I've put the
            // pressures on your screen…") so the driver knows where
            // to look.
            //
            // Dedupe: only one card per (source, markdown) pair per
            // session. Otherwise, repeated tool calls (e.g. Nova
            // mistakenly calls diy_repair_advisor twice) stack
            // identical cards on top of each other.
            let alreadyShown = transcript.contains { entry in
                entry.infoCard?.source == source
                    && entry.infoCard?.markdown == markdown
            }
            if alreadyShown {
                NSLog("🎤 VOICE: info.message dedup'd (already in transcript) source=%@", source)
            } else {
                NSLog("🎤 VOICE: info.message source=%@ chars=%d",
                      source, markdown.count)
                // Defense-in-depth: same reasoning as the booking card
                // append above. Info cards are turn-terminal — sweep
                // any stale thinking entry before rendering so the
                // card never lands below a phantom "..." bubble.
                transcript.removeAll { $0.isThinking }
                transcript.append(TranscriptEntry(
                    id: UUID(), role: "assistant", text: "",
                    isFinal: true,
                    infoCard: InfoCard(source: source, markdown: markdown)
                ))
                // Cycle 2 fix (symmetric with booking-card path above):
                // info cards are turn-terminal — same idle-timeout
                // dot-resurrection risk applies if Nova narrates
                // afterwards and the audio idles. Clear the awaiting
                // flag and cancel the watchdog so the post-narration
                // idle timeout doesn't insert phantom dots.
                NSLog("🎤 VOICE: info-card rendered — clearing awaitingMoreFromNova and cancelling watchdog (turn-terminal) source=%@", source)
                awaitingMoreFromNova = false
                cancelNovaSilenceWatchdog(reason: "info-card-rendered-turn-terminal")
            }

        case .sessionEnded(let reason):
            print("🎤 VOICE: session ended reason=\(reason)")
            await handleUnexpectedDisconnect(reason: reason)

        case .error(let message):
            lastError = message
            state = .error(message)
            await tearDownActors()

        case .debug:
            break
        }
    }

    private func handleSendFailure(_ error: Error) {
        state = .error("Mic upload: \(error.localizedDescription)")
        Task { await tearDownActors() }
    }

    private func handleStreamClosed() async {
        // Stream ended. If we're still in an active state, the server
        // dropped us (idle timeout, transient network, etc.). Treat as
        // an unexpected disconnect and try once to recover.
        switch state {
        case .disconnected, .error:
            return
        default:
            await handleUnexpectedDisconnect(reason: "stream closed")
        }
    }

    /// Tear down the current session; if this is the first unexpected
    /// disconnect since the last successful connect, retry once. If we've
    /// already retried, surface the error.
    private func handleUnexpectedDisconnect(reason: String) async {
        await tearDownActors()
        // An explicit `disconnect()` call latched this flag. The
        // resulting `.sessionEnded` reaches us here through the receive
        // loop after our own tearDownActors, and we must NOT
        // auto-reconnect — the AppSession is either signing out or
        // handing the session off to a fresh VM with new credentials.
        if permanentlyDisconnected {
            state = .disconnected
            print("🎤 VOICE: session ended after explicit disconnect (\(reason)) — no reconnect")
            return
        }
        if didAutoReconnectOnce {
            state = .error("Session ended: \(reason). Reconnect by leaving and returning to the tab.")
            didAutoReconnectOnce = false
            return
        }
        didAutoReconnectOnce = true
        state = .disconnected
        print("🎤 VOICE: auto-reconnecting after unexpected disconnect (\(reason))")
        await connect()
    }

    // MARK: - Pending queue

    private func flushPendingOutgoing() async {
        guard state == .ready, let client = client else { return }
        let queued = pendingOutgoing
        pendingOutgoing.removeAll()
        // Mark corresponding transcript entries as no-longer-pending.
        for i in transcript.indices where transcript[i].isPending {
            transcript[i].isPending = false
        }
        for text in queued {
            do {
                try await client.sendText(text)
            } catch {
                state = .error("Send: \(error.localizedDescription)")
                return
            }
        }
        if !queued.isEmpty {
            state = .thinking
            // Same rationale as in sendText() — flushing a queued
            // text message counts as the first user turn even
            // though it never produced a user-speech transcript.
            // Without this, Nova's response audio + transcript
            // get suppressed by the priming guard.
            hasSeenFirstUserTurn = true
            print("🎤 VOICE: flushed \(queued.count) pending message(s)")
        }
    }

    // MARK: - Transcript helpers

    private func appendTranscript(role: String, text: String, isFinal: Bool, isPending: Bool = false) {
        // Cycle 3: defense-in-depth dot sweep. The thinking indicator
        // is no longer a transcript entry (it is the derived
        // `isServerThinking` view-model property), so no path should
        // ever insert an isThinking entry into `transcript`. This
        // sweep stays as a safety net — if some future code path or
        // a stale build artifact ever inserts one, this guarantees
        // it gets cleared on the next transcript update.
        transcript.removeAll { $0.isThinking }
        // Find the most recent transcript entry with matching role.
        // Used by both dedup and streaming-concat branches below. We
        // scan backwards instead of just looking at `transcript.last`
        // because user messages can interleave with an assistant
        // turn's streaming-then-final-true sequence:
        //
        //   1. assistant streaming (final=false): "Your turbocharger..."
        //      ...four streamed segments concatenated into one bubble...
        //   2. user final=true: "Yes."  ← inserts a user bubble between
        //   3. assistant final=true: "Your turbocharger..."  ← needs to
        //      dedupe against the assistant bubble from step 1, not
        //      against "Yes."
        //
        // Without this lookback, step 3 fell through to the "append
        // new bubble" branch and the user saw the first assistant
        // sentence rendered twice — once at the top of the
        // conversation (streamed concat) and again below "Yes."
        // (the late-arriving final). Bug observed 2026-05-20.
        let lastSameRoleIndex: Int? = transcript.lastIndex { $0.role == role }
        // Deduplicate: if the final transcript matches the last entry
        // for this role, just mark it final.
        if isFinal,
           let idx = lastSameRoleIndex,
           let last = Optional(transcript[idx]),
           (last.text == text || text.hasPrefix(last.text) || last.text.hasPrefix(text)) {
            var updated = last
            updated.isFinal = true
            updated.isPending = isPending
            if text.count > updated.text.count { updated.text = text }
            transcript[idx] = updated
            return
        }
        if let idx = lastSameRoleIndex,
           transcript[idx].isFinal == false {
            let last = transcript[idx]
            var updated = last
            // Append the new delta. If the backend ever sends a cumulative
            // snapshot instead of a delta (some event paths do), detect and
            // use-as-is.
            if text.hasPrefix(last.text) {
                // Cumulative snapshot — replace.
                updated.text = text
            } else if !text.isEmpty {
                // Delta — concatenate.
                updated.text = last.text + text
            }
            updated.isFinal = isFinal
            updated.isPending = isPending
            transcript[idx] = updated
        } else {
            transcript.append(TranscriptEntry(
                id: UUID(),
                role: role,
                text: text,
                isFinal: isFinal,
                isPending: isPending
            ))
        }
    }

    private func scheduleSpeakingIdleTimeout() {
        speakingIdleTask?.cancel()
        speakingIdleTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_200_000_000)
            guard !Task.isCancelled, let self else { return }
            if self.state == .speaking {
                self.state = .ready
                self.suppressMic = false
                await self.capture?.unmute()
                // Cycle 3 (issues/2026-05-28-ios-chat-thinking-indicator-persists):
                // The most authoritative "Nova has stopped processing" signal
                // available client-side is "audio chunks have stopped
                // arriving." Once the audio stream has been quiet for 1.2s,
                // Nova is no longer producing output for this turn. If
                // `awaitingMoreFromNova` is still true at this point,
                // either:
                //   (a) Nova emitted assistant is_final=true and that
                //       cleared the flag already (no-op below); OR
                //   (b) Nova ended the turn audibly but never emitted
                //       is_final=true (e.g. she narrated "Want me to book
                //       it?" and is awaiting the user's answer — Nova
                //       Sonic doesn't always finalize when waiting on
                //       the user). In that case the post-narration audio
                //       quiescence IS the turn-end signal. Force-clear
                //       so the indicator (`isServerThinking`) does not
                //       turn on after Nova has clearly stopped talking.
                //
                // This single force-clear replaces the cycle-1 + cycle-2
                // guard stack: there is now no path that "resurrects"
                // the indicator after Nova quiesces. The booking-card /
                // info-card cycle-2 force-clears remain for the silence
                // watchdog (those events ARE turn-terminal even if Nova
                // continues narrating afterwards), but they are no
                // longer load-bearing for indicator suppression.
                if self.awaitingMoreFromNova {
                    NSLog("🎤 VOICE: speakingIdleTimeout force-clearing awaitingMoreFromNova (Nova quiesced)")
                    self.awaitingMoreFromNova = false
                    self.cancelNovaSilenceWatchdog(reason: "speaking-idle-timeout")
                }
            }
        }
    }

    // MARK: - Nova silence watchdog helpers

    /// Arm (or re-arm) the "Nova should respond by now" watchdog.
    ///
    /// Timeout is 12s — long enough that we don't fire while Nova
    /// is just being slow (we've observed Nova taking up to ~21s
    /// to call find_service_center under load), but short enough
    /// that the user doesn't sit through the full server-side 55s
    /// session timeout.
    ///
    /// The auto-retry idea was tried and reverted: re-sending the
    /// user's text at 5s while Nova was still processing the first
    /// input piled multiple text inputs into Nova's queue and made
    /// things worse. Now we just show a user-visible "tap to try
    /// again" message — manual retry only, no auto-retry.
    ///
    /// `reason` is just a log tag — useful when chasing flaky
    /// sessions.
    private func armNovaSilenceWatchdog(reason: String) {
        novaSilenceTask?.cancel()
        novaSilenceTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 12_000_000_000)  // 12s
            guard !Task.isCancelled, let self else { return }
            // If Nova already finalized her turn while we slept,
            // no need to do anything.
            guard self.awaitingMoreFromNova else { return }
            NSLog("🎤 VOICE: nova-silence-watchdog fired reason=%@", reason)
            self.handleNovaUnresponsive()
        }
    }

    /// Cancel the pending watchdog. Called when Nova produces any
    /// audio (turn is in progress) or finalizes the turn.
    private func cancelNovaSilenceWatchdog(reason: String) {
        if novaSilenceTask != nil {
            NSLog("🎤 VOICE: nova-silence-watchdog cancelled reason=%@", reason)
        }
        novaSilenceTask?.cancel()
        novaSilenceTask = nil
    }

    /// Reset the watchdog state for a fresh user turn — cancels the
    /// timer and clears any prior "unresponsive" banner.
    private func resetNovaSilenceState() {
        cancelNovaSilenceWatchdog(reason: "new-user-turn")
        novaSilenceRetryAttempt = 0
        if novaUnresponsive {
            novaUnresponsive = false
        }
    }

    /// Final fallback when Nova was silent past the watchdog window.
    /// Stop spinning and tell the user.
    private func handleNovaUnresponsive() {
        NSLog("🎤 VOICE: nova-silence: marking session as unresponsive")
        awaitingMoreFromNova = false
        novaUnresponsive = true
        // Drop the thinking dots — they're misleading at this point.
        transcript.removeAll { $0.isThinking }
        // Surface a transcript entry the user can read; the UI banner
        // (driven by `novaUnresponsive`) is the actionable element.
        transcript.append(TranscriptEntry(
            id: UUID(),
            role: "assistant",
            text: "I didn't catch a response. Tap the mic and try again.",
            isFinal: true
        ))
    }

    private func tearDownActors() async {
        NSLog("🎤 VOICE: tearDownActors() entry state=%@ client=%@ capture=%@ player=%@",
              "\(state)",
              client == nil ? "nil" : "non-nil",
              capture == nil ? "nil" : "non-nil",
              player == nil ? "nil" : "non-nil")
        captureLoopTask?.cancel()
        eventLoopTask?.cancel()
        speakingIdleTask?.cancel()
        keepAliveTask?.cancel()
        novaSilenceTask?.cancel()
        captureLoopTask = nil
        eventLoopTask = nil
        speakingIdleTask = nil
        keepAliveTask = nil
        novaSilenceTask = nil
        novaSilenceRetryAttempt = 0
        isTalking = false

        if let capture = capture {
            await capture.stop()
        }
        if let client = client {
            await client.disconnect()
        }
        if let player = player {
            await player.stop()
        }
        capture = nil
        client = nil
        player = nil
        NSLog("🎤 VOICE: tearDownActors() complete — client/capture/player all niled")
    }

    // MARK: - Handoff (Connect chat with human agent)

    /// Handle an `escalation` event from the supervisor. On success
    /// payload, flips handoffState to .initiated and kicks off the
    /// ConnectChatClient; on failure payload, flips to .failed so the
    /// UI can surface the error.
    ///
    /// Safe to call multiple times; second invocation tears down the
    /// existing chat and starts over. In practice this only happens
    /// if the supervisor re-escalates after a failure.
    private func handleEscalation(_ payload: VSABidiClient.Escalation) async {
        if payload.status == "failed" {
            let msg = payload.message ?? "Escalation failed"
            NSLog("💬 HANDOFF: escalation failed — %@", msg)
            await teardownChat(reason: "escalate-failed")
            handoffState = .failed(message: msg)
            return
        }

        // status == "initiated" (the supervisor only emits those two
        // values; anything else we treat as initiated).
        guard let contactId = payload.contactId,
              let participantId = payload.participantId,
              let participantToken = payload.participantToken else {
            NSLog("💬 HANDOFF: escalation missing required fields, ignoring")
            handoffState = .failed(message: "Escalation response was incomplete")
            return
        }

        // Clean up the voice-side "thinking" UI: control is moving to
        // a human agent, so Nova won't speak further on this turn. The
        // thinking dots from `case .toolCall` and the awaitingMoreFromNova
        // flag set by `case .toolResult` would otherwise persist
        // indefinitely (no transcript update arrives to clear them).
        // Cancel the silence watchdog for the same reason — there's
        // no Nova response coming, so its 12s timeout would fire on
        // a successfully-handed-off session and falsely mark it
        // unresponsive.
        transcript.removeAll { $0.isThinking }
        awaitingMoreFromNova = false
        cancelNovaSilenceWatchdog(reason: "escalation-handed-off")

        // Tear down any prior chat — shouldn't normally be needed, but
        // defensive: the supervisor may have fired escalation twice
        // during a retry.
        await teardownChat(reason: "restart")

        handoffState = .initiated(
            severity: payload.severity,
            rsaDispatched: payload.rsaDispatched
        )
        chatMessages.removeAll()

        // Seed a one-shot SYSTEM bubble with the supervisor-supplied
        // fault diagnosis (if present). This is rendered locally only;
        // never sent into the Connect chat transport. It gives the
        // driver fault context during the 3-20s window between the
        // escalation firing and the live agent posting their first
        // message, and it sits in-line above Kevin's messages so the
        // whole thread reads top-to-bottom. See
        // dtc_response_catalog.py for where the text comes from and
        // bidi_app.py's _run_escalate_in_background for the wire
        // field. Added 2026-05-11.
        if let dx = payload.chatDiagnosis, !dx.isEmpty {
            NSLog("💬 HANDOFF: inserting VSA diagnosis bubble (len=%d)", dx.count)
            let bubble = ChatMessage(
                id: "vsa-diagnosis-\(UUID().uuidString)",
                role: "SYSTEM",
                displayName: "VSA",
                text: dx,
                timestamp: Date()
            )
            chatMessages.append(bubble)
        } else {
            NSLog("💬 HANDOFF: no VSA diagnosis bubble (payload.chatDiagnosis was nil or empty)")
        }

        NSLog("💬 HANDOFF: initiating Connect chat contact=%@ severity=%@",
              contactId, payload.severity)

        let client = ConnectChatClient(config: .init(
            region: VSAConfig.connectRegion,
            contactId: contactId,
            participantId: participantId,
            participantToken: participantToken
        ))
        chatClient = client
        handoffState = .connecting(
            severity: payload.severity,
            rsaDispatched: payload.rsaDispatched,
            contactId: contactId
        )

        let stream: AsyncStream<ConnectChatClient.Event>
        do {
            stream = try await client.connect()
        } catch {
            NSLog("💬 HANDOFF: ConnectChatClient.connect failed: %@", "\(error)")
            handoffState = .failed(message: "Could not join chat: \(error.localizedDescription)")
            chatClient = nil
            return
        }

        // Pipe chat events into @Observable state.
        chatEventLoopTask = Task { [weak self] in
            for await event in stream {
                await self?.handleChatEvent(event)
            }
        }
    }

    /// Project a ConnectChatClient event into ViewModel state.
    private func handleChatEvent(_ event: ConnectChatClient.Event) async {
        switch event {
        case .connected:
            // Flip from .connecting to .connected. Agent name is still
            // unknown until the JOINED event lands.
            if case .connecting(let severity, let rsa, let contactId) = handoffState {
                handoffState = .connected(
                    severity: severity,
                    rsaDispatched: rsa,
                    contactId: contactId,
                    agentName: nil
                )
            }

        case .participantJoined(let displayName, let role):
            // Only update agentName for actual agents; ignore our own
            // CUSTOMER join event broadcast back at us.
            guard role == "AGENT" || role == "SUPERVISOR" else { return }
            if case .connected(let severity, let rsa, let contactId, _) = handoffState {
                handoffState = .connected(
                    severity: severity,
                    rsaDispatched: rsa,
                    contactId: contactId,
                    agentName: displayName
                )
            }
            // Kill any in-flight Nova Sonic audio + stop its state
            // machine from generating further. Driver is talking to
            // Kevin now; Nova Sonic shouldn't be interjecting (observed
            // 2026-05-08: Nova kept narrating "a support agent will be
            // with you in a moment" while Kevin was trying to chat).
            // Belt-and-braces: also move voice state to .ready so
            // speakingIdleTimeout doesn't re-fire speaking.
            if let player = player {
                await player.flush()
            }
            speakingIdleTask?.cancel()
            if state == .speaking || state == .thinking || state == .talking {
                state = .ready
            }
            isTalking = false
            captureLoopTask?.cancel()
            captureLoopTask = nil
            if let capture = capture {
                await capture.stop()
            }

        case .participantLeft:
            // Agent left → chat ends. Connect often sends a chat.ended
            // right after this, but we shouldn't rely on ordering.
            break

        case .message(let message):
            // Render the bubble. parseAbsoluteTime returns .now if the
            // AbsoluteTime field is missing or malformed — good enough
            // for a chronological sort.
            let ts = parseChatTimestamp(message.absoluteTime)
            let bubble = ChatMessage(
                id: message.id,
                role: message.participantRole,
                displayName: message.displayName,
                text: message.content,
                timestamp: ts
            )
            // Dedupe in case a message arrives twice (can happen on
            // reconnect scenarios — Connect replays recent history).
            if !chatMessages.contains(where: { $0.id == bubble.id }) {
                chatMessages.append(bubble)
            }

        case .ended(let reason):
            NSLog("💬 HANDOFF: chat ended reason=%@", reason)
            handoffState = .ended(reason: reason)
            await teardownChat(reason: reason)

        case .error(let msg):
            NSLog("💬 HANDOFF: chat error: %@", msg)
            handoffState = .failed(message: msg)
            await teardownChat(reason: "error")
        }
    }

    /// Send a driver-typed message into the Connect chat.
    func sendChatMessage(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard let client = chatClient else {
            NSLog("💬 HANDOFF: sendChatMessage bailing — no chat client")
            return
        }
        do {
            try await client.sendMessage(trimmed)
        } catch {
            NSLog("💬 HANDOFF: sendMessage failed: %@", "\(error)")
            // Don't flip handoffState to .failed for a single send
            // failure — the user may just be rate-limited or hit a
            // transient network blip. Surface via lastError instead.
            lastError = "Chat send failed: \(error.localizedDescription)"
        }
    }

    /// Explicit user action (e.g. tapping "End chat") tears down the
    /// Connect chat. Voice session is unaffected.
    func endHandoff() async {
        await teardownChat(reason: "driver-ended")
        handoffState = .ended(reason: "driver-ended")
    }

    /// Parse Connect's AbsoluteTime (ISO-8601 with millis) into a Date.
    /// Falls back to now() on failure.
    private func parseChatTimestamp(_ iso: String?) -> Date {
        guard let iso else { return Date() }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: iso) { return d }
        let f2 = ISO8601DateFormatter()
        f2.formatOptions = [.withInternetDateTime]
        return f2.date(from: iso) ?? Date()
    }

    /// Cancel the chat event loop and disconnect the client. Idempotent.
    private func teardownChat(reason: String) async {
        chatEventLoopTask?.cancel()
        chatEventLoopTask = nil
        if let c = chatClient {
            await c.disconnect(reason: reason)
        }
        chatClient = nil
    }
}

// MARK: - Equatable helper for error check in sendText

private extension VoiceSessionViewModel.State {
    init(_ message: String) { self = .error(message) }
}


// MARK: - Demo local playback cache

/// Tiny holder so `AVAudioPlayer` instances created for demo-clip
/// local playback aren't deallocated mid-playback. SwiftUI Task
/// lifetimes are short; without this, ARC would tear the player
/// down before the audio actually finishes streaming through the
/// HAL. The cache is a process-global singleton — overhead is
/// negligible (one object per active demo clip, cleared as each
/// finishes) and avoids leaking the responsibility into every view
/// that wants to fire a clip.
final class DemoLocalPlaybackCache: NSObject, AVAudioPlayerDelegate {
    static let shared = DemoLocalPlaybackCache()
    private var players: [ObjectIdentifier: AVAudioPlayer] = [:]
    private let lock = NSLock()

    /// Hold a reference to a player until it stops. The player is
    /// released in `audioPlayerDidFinishPlaying`. We assume the
    /// caller has already invoked `play()` (or is about to) — this
    /// just keeps the object alive across the Task boundary.
    func retain(_ player: AVAudioPlayer) {
        lock.lock(); defer { lock.unlock() }
        player.delegate = self
        players[ObjectIdentifier(player)] = player
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully _: Bool) {
        lock.lock(); defer { lock.unlock() }
        players.removeValue(forKey: ObjectIdentifier(player))
    }
}
