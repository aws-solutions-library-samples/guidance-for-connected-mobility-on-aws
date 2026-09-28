import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the mic-suppression rework.
///
/// The bug being pinned: a transcript-only assistant turn latched the mic shut for
/// the rest of the session, because the flag was set on a transcript but cleared only
/// under `state == .speaking`, which was set on audio. Observed on staging
/// 2026-08-21 as `realtime_ratio=0.011`, `gated=0`, `outbound=0`.
///
/// `test_transcriptWithNoAudio_doesNotLatch` is the load-bearing one — it is the exact
/// sequence that deadlocked, and it fails against a flag-based implementation.
final class MicSuppressionTests: XCTestCase {

    private let t0 = Date(timeIntervalSince1970: 1_000_000)

    func test_startsOpen() {
        XCTAssertFalse(MicSuppression().isSuppressed(now: t0))
    }

    func test_assistantOutputSuppresses() {
        var s = MicSuppression()
        s.assistantProducedOutput(at: t0)
        XCTAssertTrue(s.isSuppressed(now: t0))
    }

    /// THE REGRESSION TEST.
    ///
    /// A transcript arrives, no audio ever follows. The old code muted here and had
    /// no path back — the clearer required a state that only audio set. Time alone
    /// must reopen the mic.
    func test_transcriptWithNoAudio_doesNotLatch() {
        var s = MicSuppression()
        s.assistantProducedOutput(at: t0)          // assistant transcript, no audio

        XCTAssertTrue(s.isSuppressed(now: t0.addingTimeInterval(0.5)),
                      "should still be suppressed inside the window")
        XCTAssertFalse(s.isSuppressed(now: t0.addingTimeInterval(MicSuppression.window + 0.01)),
                       "must reopen on its own — nothing else is coming")
        XCTAssertFalse(s.isSuppressed(now: t0.addingTimeInterval(600)),
                       "and must not be shut ten minutes later, which is what shipped")
    }

    /// A continuous response keeps the mic shut for as long as it lasts, so the fix
    /// does not reintroduce echo. Chunks arrive inside the window and each pushes the
    /// deadline forward.
    func test_continuousOutputKeepsMicShut() {
        var s = MicSuppression()
        var now = t0
        for _ in 0..<20 {
            s.assistantProducedOutput(at: now)
            now = now.addingTimeInterval(0.3)      // < window, i.e. a steady stream
            XCTAssertTrue(s.isSuppressed(now: now))
        }
        // Stream stops; the mic reopens one window later, not on an event.
        XCTAssertFalse(s.isSuppressed(now: now.addingTimeInterval(MicSuppression.window + 0.01)))
    }

    func test_turnEndedOpensImmediately() {
        var s = MicSuppression()
        s.assistantProducedOutput(at: t0)
        s.assistantTurnEnded()
        XCTAssertFalse(s.isSuppressed(now: t0), "barge-in must not wait out the window")
    }

    /// Suppression must not depend on any view state. Coupling it to `state` is what
    /// allowed the latch, so the type deliberately has no state input at all — this
    /// test documents that as intent rather than omission.
    func test_decisionDependsOnlyOnTime() {
        var a = MicSuppression()
        var b = MicSuppression()
        a.assistantProducedOutput(at: t0)
        b.assistantProducedOutput(at: t0)
        XCTAssertEqual(a, b, "identical output history must give identical suppression")
        XCTAssertEqual(a.isSuppressed(now: t0.addingTimeInterval(2)),
                       b.isSuppressed(now: t0.addingTimeInterval(2)))
    }

    func test_windowMatchesTheOldIdleDelay() {
        // 1.2s was the old speakingIdleTask sleep. Keeping it means barge-in feel is
        // unchanged; changing it is a UX decision, not an implementation detail.
        XCTAssertEqual(MicSuppression.window, 1.2, accuracy: 0.001)
    }
}
