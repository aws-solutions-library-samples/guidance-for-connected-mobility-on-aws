import AVFoundation
import SwiftUI
import UIKit

// MARK: - Timing + planning (pure, testable)

/// Timings for the launch splash. Collected here so the values the tests
/// assert against are the same ones the views use — a duplicated literal is
/// how a test starts passing while the app does something else.
enum SplashTiming {
    /// How long the static logo shows on the reduced-motion / failure path.
    /// Long enough to register as intentional branding, short enough that it
    /// never feels like the app is stuck.
    /// Master switch for the animated reveal. `false` ships the static wordmark.
    /// Retained so re-enabling motion is one value, not a re-implementation.
    static let animatedRevealEnabled = false

    /// How long the wordmark is held at full opacity, between the fades.
    ///
    /// Raised 1.5 -> 2.0 when the static path became the ONLY path. As a
    /// reduced-motion fallback it only had to avoid feeling broken; as the actual launch
    /// treatment it has to read as deliberate branding. Total on-screen time is
    /// `fadeIn + hold + fadeOut` — see `totalStaticSeconds`.
    static let staticHoldSeconds: TimeInterval = 2.5

    /// Fade OUT duration. There is no fade in — the launch screen is the appearance, and
    /// fading in over it produced a visible second logo.
    static let logoFadeOutSeconds: TimeInterval = 0.45

    /// End-to-end splash duration, so tests and reviewers can reason about one number
    /// instead of adding three.
    static var totalStaticSeconds: TimeInterval {
        staticHoldSeconds + logoFadeOutSeconds
    }

    /// Crossfade from splash to app content.
    static let crossfadeSeconds: TimeInterval = 0.4

    /// Where the reveal is cut, and the splash's deterministic upper bound.
    ///
    /// The asset runs 5.209 s, which is far longer than it needs to be. Frame
    /// analysis of the clip:
    ///
    /// - the wordmark is **fully legible by ~1.0 s**; everything after that is
    ///   decorative light glints sweeping over type that is already formed
    /// - brightness peaks ~1.25 s, dips to near-nothing at ~3.25 s, then peaks
    ///   again at ~4.25 s — the back half is a **repeat of the same cycle**
    /// - 3.0 s is the first frame where the glints have cleared and the
    ///   wordmark sits clean, which is also exactly what the LaunchScreen and
    ///   the static fallback show, so the crossfade is seamless
    ///
    /// This doubles as the hard ceiling. `SplashView` sits above the whole app,
    /// so a clip that never reports an end — a stalled decode, a truncated file
    /// that yields no error, a `readyToPlay` item whose playback never advances
    /// — would otherwise leave the user staring at black with no way forward.
    /// Dismissal is driven by a plain timer rather than by
    /// `AVPlayerItemDidPlayToEndTime`, because that notification is reported not
    /// to fire at all on some iOS versions, and because whether reaching
    /// `forwardPlaybackEndTime` posts it is not something to bet the app's
    /// launch on. The notification is still observed, just not depended upon.
    static let videoCutoffSeconds: TimeInterval = 3.0
}

/// Which splash treatment to run. Resolved before any player is built so the
/// missing-asset case never flashes black on its way to the fallback.
enum SplashPlan: Equatable {
    /// Play the animated reveal.
    case video(URL)
    /// Hold the static wordmark, then finish.
    case staticLogo
}

/// Chooses the splash treatment.
///
/// `reduceMotion` wins over asset availability: when a user has asked the
/// system for less motion, "the video is present" is not a reason to play it.
/// - Parameter locateVideo: injected so tests can exercise both the
///   asset-present and asset-missing branches without a bundle.
func resolveSplashPlan(
    reduceMotion: Bool,
    locateVideo: () -> URL?,
    animatedRevealEnabled: Bool = SplashTiming.animatedRevealEnabled
) -> SplashPlan {
    // Animated reveal retired 2026-08-21 (product decision): the wordmark holding on
    // black, fading in and out, reads as more confident than a motion sequence. The clip
    // and its player are kept — flipping `animatedRevealEnabled` restores them — because
    // the intent is to revisit animation later, not to abandon it.
    if !animatedRevealEnabled { return .staticLogo }
    if reduceMotion { return .staticLogo }
    guard let url = locateVideo() else { return .staticLogo }
    return .video(url)
}

/// Collapses many possible "the splash is over" signals into exactly one
/// callback.
///
/// The video path can legitimately report completion more than once — the
/// end-of-item notification and the watchdog can both fire, and a failure can
/// arrive after a successful start. Every one of those routes through
/// `settle()`, and only the first wins. Without this, `onFinished` runs twice
/// and the second call animates a `showSplash = false` that is already false.
final class SplashSettleGuard {
    private(set) var hasSettled = false

    /// Runs `action` on the first call only. Returns whether it ran.
    @discardableResult
    func settle(_ action: () -> Void) -> Bool {
        guard !hasSettled else { return false }
        hasSettled = true
        action()
        return true
    }
}

// MARK: - Video player

/// Plays a bundled clip exactly once through an `AVPlayerLayer`.
///
/// Deliberately not SwiftUI's `VideoPlayer`: that surfaces playback controls
/// and does not expose `videoGravity`, both of which matter for a splash.
///
/// The clip has no audio track (verified: 1 video track, 0 audio tracks), so
/// playback does not activate the shared `AVAudioSession`. That matters in
/// this app specifically — `AudioCapture` / `AudioPlayer` own that session for
/// the Nova Sonic voice loop, and a splash that reconfigured it on launch
/// would be a hard bug to trace back here. `isMuted` is set anyway so the
/// property does not silently depend on the asset staying audio-free.
struct SplashVideoPlayer: UIViewRepresentable {
    let videoURL: URL
    /// Called once when the clip reaches its end (or the watchdog expires).
    let onFinished: () -> Void
    /// Called once when the clip cannot be loaded or played at all.
    let onFailure: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onFinished: onFinished, onFailure: onFailure)
    }

    func makeUIView(context: Context) -> PlayerHostView {
        let view = PlayerHostView()
        view.backgroundColor = .black
        context.coordinator.start(in: view, url: videoURL)
        return view
    }

    func updateUIView(_ uiView: PlayerHostView, context: Context) {
        // Nothing to reconcile: the clip is fixed for the lifetime of the view
        // and playback is driven entirely by the coordinator.
    }

    /// Tears the player down when SwiftUI removes the view.
    ///
    /// The splash is removed from the hierarchy once dismissed, so this is
    /// where the `AVPlayer`, its KVO observation, its notification
    /// registrations and the watchdog stop existing. Leaving any of them alive
    /// keeps a decoder and a 248 KB item resident for the whole session.
    static func dismantleUIView(_ uiView: PlayerHostView, coordinator: Coordinator) {
        coordinator.teardown()
    }

    /// Hosts the `AVPlayerLayer` and keeps it at the view's bounds.
    ///
    /// A plain `UIView` will not do: `AVPlayerLayer` is a layer, and without
    /// `layoutSubviews` re-applying the frame it keeps its initial `.zero`
    /// bounds and renders nothing.
    final class PlayerHostView: UIView {
        let playerLayer = AVPlayerLayer()

        override init(frame: CGRect) {
            super.init(frame: frame)
            playerLayer.videoGravity = .resizeAspect
            layer.addSublayer(playerLayer)
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

        override func layoutSubviews() {
            super.layoutSubviews()
            playerLayer.frame = bounds
        }
    }

    /// Owns the player and every observation attached to it.
    ///
    /// `@MainActor` is doing two jobs: playback and layer mutation belong on
    /// the main thread anyway, and a global-actor-isolated class is *implicitly
    /// Sendable*, which is what lets the notification and KVO blocks below
    /// capture `self` without tripping a non-Sendable-capture warning.
    @MainActor
    final class Coordinator {
        private let onFinished: () -> Void
        private let onFailure: () -> Void
        private let settleGuard = SplashSettleGuard()

        private var player: AVPlayer?
        private var statusObservation: NSKeyValueObservation?
        private var endObserver: NSObjectProtocol?
        private var failureObserver: NSObjectProtocol?
        private var cutTimer: DispatchWorkItem?

        init(onFinished: @escaping () -> Void, onFailure: @escaping () -> Void) {
            self.onFinished = onFinished
            self.onFailure = onFailure
        }

        func start(in host: PlayerHostView, url: URL) {
            let item = AVPlayerItem(url: url)
            // Stop playback at the cut so the held frame is the clean, fully
            // formed wordmark rather than the clip rolling on into its second
            // reveal cycle behind the crossfade.
            item.forwardPlaybackEndTime = CMTime(
                seconds: SplashTiming.videoCutoffSeconds, preferredTimescale: 600)

            let player = AVPlayer(playerItem: item)
            player.isMuted = true
            // Hold the final frame rather than snapping back to black; the
            // crossfade to app content runs from that held frame.
            player.actionAtItemEnd = .pause
            self.player = player
            host.playerLayer.player = player

            // `queue: .main` is what makes `assumeIsolated` sound here — the
            // block is guaranteed to run on the main thread.
            endObserver = NotificationCenter.default.addObserver(
                forName: .AVPlayerItemDidPlayToEndTime,
                object: item,          // scoped to OUR item, not any item app-wide
                queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.finish() }
            }

            failureObserver = NotificationCenter.default.addObserver(
                forName: .AVPlayerItemFailedToPlayToEndTime,
                object: item,
                queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.fail() }
            }

            // A file can be present and still undecodable. `status` is the only
            // signal for that case — no notification is posted for an item that
            // fails before playback begins.
            statusObservation = item.observe(\.status, options: [.new]) { [weak self] item, _ in
                guard item.status == .failed else { return }
                DispatchQueue.main.async {
                    MainActor.assumeIsolated { self?.fail() }
                }
            }

            // The primary dismissal path. Deterministic and independent of every
            // AVFoundation signal above, which is the point: it fires on time
            // whether or not the player ever reports anything.
            let cut = DispatchWorkItem { [weak self] in
                MainActor.assumeIsolated { self?.finish() }
            }
            cutTimer = cut
            DispatchQueue.main.asyncAfter(
                deadline: .now() + SplashTiming.videoCutoffSeconds, execute: cut)

            player.play()
        }

        private func finish() {
            settleGuard.settle { [onFinished] in onFinished() }
        }

        private func fail() {
            settleGuard.settle { [onFailure] in onFailure() }
        }

        func teardown() {
            cutTimer?.cancel()
            cutTimer = nil
            statusObservation?.invalidate()
            statusObservation = nil
            if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
            if let failureObserver { NotificationCenter.default.removeObserver(failureObserver) }
            endObserver = nil
            failureObserver = nil
            player?.pause()
            player?.replaceCurrentItem(with: nil)
            player = nil
        }
    }
}
