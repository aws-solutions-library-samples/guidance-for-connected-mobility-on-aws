import Foundation

/// Whether the microphone should be suppressed right now, derived from one signal.
///
/// ## The bug this replaces
///
/// `suppressMic` was a `Bool` set to `true` in three places and cleared in one, and
/// the one clearer was guarded by `state == .speaking`:
///
/// * an assistant **transcript** (`role == "assistant" && !isFinal`) set
///   `suppressMic = true` and muted capture, but did NOT set `state = .speaking`;
/// * an assistant **audio chunk** set both, and was the only caller of
///   `scheduleSpeakingIdleTimeout()`, whose body cleared the flag only
///   `if state == .speaking`.
///
/// So when Nova emitted a transcript but no audio, the mic was muted, `state` never
/// became `.speaking`, the timeout was never scheduled — and would have bailed on the
/// guard anyway. The mic stayed shut for the rest of the session.
///
/// That is self-sustaining: Nova cannot produce audio because it is receiving almost
/// no audio, and the mic cannot reopen because it is waiting for Nova to finish
/// speaking. Observed on staging 2026-08-21 in the server-side audio-pipeline
/// summary: `realtime_ratio=0.011`, `gated=0` (the client never sent, so the server
/// had nothing to drop), `outbound=0` (Nova never spoke).
///
/// ## Why a timestamp instead of a flag
///
/// Suppression is not a state the code should have to remember to leave. It is a
/// question with an answer: *did the assistant produce output within the last
/// `window` seconds?* Expressed that way it cannot latch, because nothing has to
/// arrive in order to clear it — the passage of time clears it. Any assistant output
/// of any kind pushes the deadline forward, so a continuous response keeps the mic
/// shut for exactly as long as the response lasts.
///
/// This is the same correction applied to the chat "thinking" indicator in
/// `issues/2026-05-28-ios-chat-thinking-indicator-persists`: model the UI concern as
/// a derived property of one authoritative signal rather than a parallel flag
/// maintained by N call sites.
struct MicSuppression: Equatable {
    /// How long after the assistant's last output the mic stays shut.
    ///
    /// 1.2s matches the delay the old `speakingIdleTask` used, so barge-in feel is
    /// unchanged: it is long enough to cover the gap between consecutive audio
    /// chunks of one response, and short enough that a driver is not locked out.
    static let window: TimeInterval = 1.2

    /// When the assistant last produced output — transcript or audio, whichever
    /// arrived. `nil` means the assistant is not speaking and the mic is open.
    private(set) var lastAssistantOutputAt: Date?

    init(lastAssistantOutputAt: Date? = nil) {
        self.lastAssistantOutputAt = lastAssistantOutputAt
    }

    /// Record assistant output. Any kind counts — a transcript with no audio must
    /// suppress just as a chunk does, and must expire just the same.
    mutating func assistantProducedOutput(at now: Date = Date()) {
        lastAssistantOutputAt = now
    }

    /// The assistant's turn is definitively over (barge-in, user pressed talk, an
    /// injected clip finished). Opens the mic immediately rather than waiting out
    /// the window.
    mutating func assistantTurnEnded() {
        lastAssistantOutputAt = nil
    }

    /// The whole decision. No `state` involved, deliberately: coupling it to a view
    /// state is what allowed the latch, because the state that gated the clear was
    /// set on a different event than the one that set the flag.
    func isSuppressed(now: Date = Date()) -> Bool {
        guard let last = lastAssistantOutputAt else { return false }
        return now.timeIntervalSince(last) < Self.window
    }
}
