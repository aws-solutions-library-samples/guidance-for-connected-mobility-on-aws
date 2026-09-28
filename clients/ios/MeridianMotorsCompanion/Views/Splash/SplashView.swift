import SwiftUI

/// Full-screen Meridian Motors launch reveal, shown once per cold start above
/// the app's real content.
///
/// Three paths, all of which end in exactly one `onFinished()`:
///
/// | Condition | Treatment |
/// |---|---|
/// | Normal | animated reveal, then finish |
/// | Reduce Motion on | static wordmark for `staticHoldSeconds` |
/// | Clip missing or undecodable | static wordmark for `staticHoldSeconds` |
///
/// The plan is resolved once in `onAppear` rather than recomputed in `body`.
/// Recomputing it would rebuild `SplashVideoPlayer` on unrelated redraws, and
/// each rebuild restarts the clip from frame zero.
struct SplashView: View {
    /// Invoked exactly once when the splash is done. The parent owns dismissal
    /// so the view — and the `AVPlayer` inside it — can leave the hierarchy.
    let onFinished: () -> Void

    /// SwiftUI's reactive mirror of `UIAccessibility.isReduceMotionEnabled`.
    /// Preferred over reading `UIAccessibility` directly, matching
    /// `VehicleSweepView` and `SupplyChainPlanningView`.
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Seeded to `.staticLogo` rather than `nil`.
    ///
    /// This was the "two logos" defect. `nil` rendered `EmptyView()`, and `.onAppear` does
    /// not run until AFTER the first frame — so the sequence was: launch-screen wordmark,
    /// then a frame of black with NO wordmark, then the wordmark again. Read as the logo
    /// appearing twice with a blink between.
    ///
    /// Seeding is safe because the shipped plan is unconditional: with
    /// `SplashTiming.animatedRevealEnabled == false`, `resolveSplashPlan` returns
    /// `.staticLogo` for every input. `onAppear` still runs and still overwrites this, so
    /// re-enabling the animated reveal restores the old behaviour without further change —
    /// at the cost of that first-frame gap returning, which is inherent to deciding on a
    /// video asset you have not yet located.
    @State private var plan: SplashPlan? = .staticLogo

    /// Drives the fade OUT only.
    ///
    /// Starts at 1, not 0. `LaunchScreen.storyboard` has already drawn this same wordmark
    /// at this same size on the same black, so fading in from 0 does not read as an
    /// entrance — it reads as the logo vanishing and a second one arriving. Starting
    /// opaque makes the storyboard-to-SwiftUI handoff invisible: one logo, which then
    /// dissolves.
    @State private var logoOpacity: Double = 1

    /// Locates the bundled clip. `nil` means the resource did not ship, which
    /// takes the static path without ever showing a black frame.
    static func bundledVideoURL() -> URL? {
        Bundle.main.url(forResource: "meridian_splash", withExtension: "mp4")
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            switch plan {
            case .video(let url):
                SplashVideoPlayer(
                    videoURL: url,
                    onFinished: onFinished,
                    // A clip that fails mid-flight falls back to the static
                    // wordmark rather than cutting straight to the app, so a
                    // decode error still reads as branding.
                    onFailure: { plan = .staticLogo }
                )
                .ignoresSafeArea()

            case .staticLogo:
                staticLogo

            case nil:
                // Unreachable while `plan` is seeded above; retained for the
                // animated-reveal path, which cannot resolve until it has looked for the
                // clip. Black matches the LaunchScreen storyboard and the clip's own
                // background.
                EmptyView()
            }
        }
        .onAppear {
            guard plan == nil else { return }
            plan = resolveSplashPlan(
                reduceMotion: reduceMotion,
                locateVideo: Self.bundledVideoURL
            )
        }
        // Nothing here is interactive and nothing needs announcing; VoiceOver
        // should move straight to the app content underneath.
        .accessibilityHidden(true)
    }

    private var staticLogo: some View {
        Image("MeridianLogo")
            .resizable()
            .scaledToFit()
            .opacity(logoOpacity)
            // Width-only frame: the asset is a content-cropped wordmark at
            // ~14.7:1, so height follows from its intrinsic aspect. An earlier
            // version constrained it into a 200×200 box, which — because the
            // uncropped master was a 1024² canvas holding a 657×39 wordmark —
            // rendered the type at roughly 7pt tall. Keep this in sync with the
            // 260pt width in LaunchScreen.storyboard.
            .frame(width: Self.logoWidth)
            .task {
                // Hold, then dissolve. There is no fade IN: the appearance is the launch
                // screen, which draws the identical wordmark at the identical size, so
                // this view simply continues it. `.task` is cancelled if the view goes
                // away first, so a dismissal that races this cannot fire onFinished late.
                //
                // The fade is an explicit `withAnimation` rather than a `.transition`,
                // because a transition only runs when the view enters or leaves the
                // hierarchy — and this view's removal is what `onFinished` triggers, so
                // the exit would be raced by its own dismissal and usually skipped.
                try? await Task.sleep(for: .seconds(SplashTiming.staticHoldSeconds))
                guard !Task.isCancelled else { return }

                withAnimation(.easeOut(duration: SplashTiming.logoFadeOutSeconds)) {
                    logoOpacity = 0
                }
                // Wait out the fade before handing off, or the app would appear behind a
                // wordmark that is still visibly dissolving.
                try? await Task.sleep(for: .seconds(SplashTiming.logoFadeOutSeconds))
                guard !Task.isCancelled else { return }
                onFinished()
            }
    }

    /// Rendered width of the static wordmark. Mirrored in
    /// `LaunchScreen.storyboard`; a storyboard cannot read a Swift constant, so
    /// the two are kept aligned by comment and by
    /// `SplashPlanTests.testStaticLogoWidthMatchesLaunchScreenStoryboard`.
    /// Rendered width of the wordmark. MUST equal the width constraint in
    /// `LaunchScreen.storyboard` (MRD-cn-0001).
    ///
    /// Briefly set to 320 on 2026-08-21 on the theory that a larger mark here would
    /// "grow into place" from the launch screen's 260. It does not. The storyboard draws
    /// at 260 and cannot animate, so the handoff renders as the first logo disappearing
    /// and a second, larger one fading in — reported as "two different logos". Equality
    /// is what makes it one logo, which is why the original test asserted it.
    ///
    /// To show a LARGER wordmark, change both this and the storyboard constraint together.
    static let logoWidth: CGFloat = 260
}

// Only one preview: `accessibilityReduceMotion` is a read-only environment key
// path and cannot be injected, so the static path is exercised by
// SplashPlanTests and by toggling Settings → Accessibility → Motion in the
// simulator rather than by a second preview.
#Preview {
    SplashView(onFinished: {})
}
