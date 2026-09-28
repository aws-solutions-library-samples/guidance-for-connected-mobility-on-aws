import AVFoundation
import UIKit
import XCTest

@testable import MeridianMotorsCompanion

/// Tests for the launch-splash decision logic and its bundled assets.
///
/// The view layer itself is not exercised here — `AVPlayerLayer` playback needs
/// a rendering host. What is covered is everything that can silently degrade
/// the splash without failing a build: which treatment gets chosen, whether the
/// completion callback can fire more than once, whether the assets actually
/// shipped, and whether the watchdog can cut the clip short.
/// `@MainActor` because `SplashView` is a SwiftUI `View` and therefore
/// MainActor-isolated, which makes its static `bundledVideoURL()` isolated too;
/// calling it from a non-isolated test warns under Swift 5 and is an error under
/// Swift 6.
@MainActor
final class SplashPlanTests: XCTestCase {

    private let dummyURL = URL(fileURLWithPath: "/tmp/meridian_splash.mp4")

    // MARK: - Plan resolution

    /// The animated reveal is retired but not deleted — this covers the machinery that
    /// remains, by opting in explicitly.
    func testPlaysVideoWhenRevealEnabledAndMotionAllowedAndAssetPresent() {
        let plan = resolveSplashPlan(
            reduceMotion: false,
            locateVideo: { self.dummyURL },
            animatedRevealEnabled: true
        )
        XCTAssertEqual(plan, .video(dummyURL))
    }

    /// What SHIPS. The static wordmark is the launch treatment as of 2026-08-21, so this
    /// asserts the default with no arguments overridden — including that a bundled clip
    /// and motion being allowed are BOTH insufficient to animate.
    func testShippedDefaultIsStaticLogoEvenWithClipPresentAndMotionAllowed() {
        let plan = resolveSplashPlan(reduceMotion: false, locateVideo: { self.dummyURL })
        XCTAssertEqual(
            plan, .staticLogo,
            "Splash must be the static wordmark by default. If the animated reveal is "
                + "being brought back, flip SplashTiming.animatedRevealEnabled and update "
                + "this test deliberately — do not let it drift."
        )
    }

    func testAnimatedRevealIsDisabledInShippingConfiguration() {
        XCTAssertFalse(
            SplashTiming.animatedRevealEnabled,
            "The clip and player are retained for a future revisit, but must stay off."
        )
    }

    func testReduceMotionForcesStaticLogoEvenWhenAssetPresent() {
        let plan = resolveSplashPlan(reduceMotion: true, locateVideo: { self.dummyURL })
        XCTAssertEqual(
            plan, .staticLogo,
            "Reduce Motion must win over asset availability — a present clip is not a reason to animate."
        )
    }

    func testMissingAssetFallsBackToStaticLogo() {
        let plan = resolveSplashPlan(reduceMotion: false, locateVideo: { nil })
        XCTAssertEqual(plan, .staticLogo)
    }

    func testMissingAssetAndReduceMotionFallsBackToStaticLogo() {
        let plan = resolveSplashPlan(reduceMotion: true, locateVideo: { nil })
        XCTAssertEqual(plan, .staticLogo)
    }

    /// The resolver must not consult the bundle when Reduce Motion is on. If it
    /// did, a future refactor could make the accessibility path depend on an
    /// asset it never uses.
    func testReduceMotionDoesNotEvaluateAssetLookup() {
        var lookupCount = 0
        _ = resolveSplashPlan(reduceMotion: true, locateVideo: {
            lookupCount += 1
            return self.dummyURL
        })
        XCTAssertEqual(lookupCount, 0)
    }

    // MARK: - Fire-once guard

    func testSettleGuardRunsActionExactlyOnce() {
        let guardian = SplashSettleGuard()
        var calls = 0

        XCTAssertFalse(guardian.hasSettled)
        XCTAssertTrue(guardian.settle { calls += 1 })
        XCTAssertTrue(guardian.hasSettled)
        XCTAssertEqual(calls, 1)

        // Mirrors the real race: end-of-item notification and watchdog both fire.
        XCTAssertFalse(guardian.settle { calls += 1 })
        XCTAssertEqual(calls, 1, "A second settle must not re-run the completion handler.")
    }

    /// Failure arriving after success must not re-fire — the app would animate
    /// a dismissal that already happened.
    func testSettleGuardIgnoresLateFailureAfterSuccess() {
        let guardian = SplashSettleGuard()
        var finished = 0
        var failed = 0

        guardian.settle { finished += 1 }
        guardian.settle { failed += 1 }

        XCTAssertEqual(finished, 1)
        XCTAssertEqual(failed, 0)
    }

    // MARK: - Bundled assets

    /// Catches the silent-degradation bug: if `meridian_splash.mp4` is not in
    /// Copy Bundle Resources, or lands in a subdirectory, this lookup returns
    /// nil and every launch quietly shows the static fallback instead of the
    /// reveal. Nothing else in the build fails.
    func testSplashVideoIsBundledAtTheRootWhereTheLookupExpectsIt() {
        XCTAssertNotNil(
            SplashView.bundledVideoURL(),
            "meridian_splash.mp4 must ship flat in the app bundle — "
                + "run `ruby scripts/add-splash-screen.rb` if this fails."
        )
    }

    /// The static path and the LaunchScreen storyboard both reference this
    /// image by name; a missing asset renders as an empty frame, not an error.
    func testMeridianLogoImageAssetResolves() {
        XCTAssertNotNil(
            UIImage(named: "MeridianLogo"),
            "MeridianLogo.imageset must exist in Assets.xcassets."
        )
    }

    // MARK: - Timing invariants

    /// The cut is what makes the splash short, and it is also the deterministic
    /// bound that stops a stalled clip from trapping the user. Measured against
    /// the real asset rather than a hardcoded 5.209 s so that replacing the clip
    /// fails here instead of silently changing launch behaviour.
    func testCutoffLandsInsideTheClipAndActuallyTrimsIt() async throws {
        guard let url = SplashView.bundledVideoURL() else {
            return XCTFail("Splash clip not bundled; see testSplashVideoIsBundled…")
        }
        let duration = try await AVURLAsset(url: url).load(.duration)
        let seconds = CMTimeGetSeconds(duration)
        let cut = SplashTiming.videoCutoffSeconds

        XCTAssertGreaterThan(seconds, 0, "Clip duration should be readable and positive.")
        XCTAssertLessThan(
            cut, seconds,
            "Cutoff (\(cut)s) should be shorter than the clip (\(seconds)s) — otherwise "
                + "nothing is being trimmed and the splash is back to full length."
        )
        XCTAssertGreaterThanOrEqual(
            cut, 1.0,
            "The wordmark is not fully formed until ~1.0s; cutting earlier shows partial type."
        )
    }

    /// Guards the actual user-facing complaint that motivated the cut: the
    /// splash was too long. Total = reveal + crossfade.
    func testTotalSplashDurationStaysWithinBudget() {
        let total = SplashTiming.videoCutoffSeconds + SplashTiming.crossfadeSeconds
        XCTAssertLessThanOrEqual(
            total, 3.5,
            "Splash budget exceeded (\(total)s). The untrimmed clip put this at 5.6s, "
                + "which was rejected as too long."
        )
    }

    /// The clip has no audio track, which is why splash playback does not
    /// disturb the shared AVAudioSession that the Nova Sonic voice loop owns.
    /// If a future asset adds an audio track, that reasoning stops holding and
    /// the session handling has to be revisited — so assert it.
    func testSplashClipHasNoAudioTrack() async throws {
        guard let url = SplashView.bundledVideoURL() else {
            return XCTFail("Splash clip not bundled; see testSplashVideoIsBundled…")
        }
        let audioTracks = try await AVURLAsset(url: url).loadTracks(withMediaType: .audio)
        XCTAssertTrue(
            audioTracks.isEmpty,
            "Splash clip gained an audio track — revisit AVAudioSession handling "
                + "before shipping, since AudioCapture/AudioPlayer own that session."
        )
    }

    /// Bound raised 2.0 -> 3.0 on 2026-08-21, deliberately rather than to make a red test
    /// pass.
    ///
    /// The original limit was written when this hold was the REDUCED-MOTION FALLBACK: it
    /// only had to avoid feeling broken for the minority of users who never saw the video.
    /// It is now the launch treatment for everyone, and the product decision was expressed
    /// as "2-3 seconds of black with the logo" — a 2.0s ceiling made that unreachable.
    ///
    /// The tighter constraint now lives on the TOTAL, in
    /// `testStaticSplashTotalDurationIsTwoToThreeSeconds`, which is the number a viewer
    /// actually experiences. This one just keeps the hold from becoming a hang.
    func testStaticHoldIsShortEnoughToNotFeelStuck() {
        XCTAssertGreaterThan(SplashTiming.staticHoldSeconds, 0)
        XCTAssertLessThanOrEqual(
            SplashTiming.staticHoldSeconds, 3.0,
            "Past ~3s a launch hold stops reading as branding and starts reading as a hang."
        )
    }

    // MARK: - Brand asset geometry

    /// The bug this pins: the wordmark master is a 1024² canvas containing a
    /// 657×39 band of type. Shipped uncropped and fitted into a 200pt box, the
    /// type rendered ~7pt tall on both the launch screen and the static
    /// fallback. A wide aspect here is the evidence that the shipped asset is
    /// the content crop and not the raw master.
    func testLogoAssetIsContentCroppedNotTheRawSquareMaster() throws {
        let logo = try XCTUnwrap(UIImage(named: "MeridianLogo"))
        let aspect = logo.size.width / logo.size.height

        XCTAssertGreaterThan(
            aspect, 8.0,
            "MeridianLogo aspect is \(aspect):1 — expected a wide (~14.7:1) content crop. "
                + "A near-1:1 value means the uncropped master shipped; "
                + "re-run `swift scripts/make-brand-assets.swift`."
        )
    }

    /// At 260pt wide the wordmark's cap height must stay legible. This is the
    /// number the 200pt square box got wrong.
    func testStaticLogoRendersLegibleCapHeight() throws {
        let logo = try XCTUnwrap(UIImage(named: "MeridianLogo"))
        let renderedHeight = SplashView.logoWidth * (logo.size.height / logo.size.width)
        XCTAssertGreaterThan(
            renderedHeight, 12.0,
            "Wordmark renders only \(renderedHeight)pt tall at \(SplashView.logoWidth)pt wide."
        )
    }

    /// The storyboard cannot reference `SplashView.logoWidth`, so the two are aligned by
    /// hand and asserted equal here.
    ///
    /// Equality is not tidiness — it is what makes the splash read as ONE logo.
    /// `LaunchScreen.storyboard` draws the wordmark first and cannot animate; if this view
    /// draws it at a different size, the handoff shows the first mark vanishing and a
    /// second, differently-sized one appearing. That regression was shipped briefly on
    /// 2026-08-21 (320 here vs 260 there) and reported as "two different logos".
    ///
    /// To change the size, change BOTH this constant and the storyboard constraint.
    func testStaticLogoWidthMatchesLaunchScreenStoryboard() throws {
        XCTAssertEqual(
            SplashView.logoWidth, 260,
            "SplashView.logoWidth changed — update the width constraint in "
                + "LaunchScreen.storyboard (id MRD-cn-0001) to match, or the splash will "
                + "render as two logos."
        )
    }

    /// Total on-screen time, which is the number the product decision was expressed in
    /// ("2-3 seconds of black with the logo").
    func testStaticSplashTotalDurationIsTwoToThreeSeconds() {
        let total = SplashTiming.totalStaticSeconds
        XCTAssertGreaterThanOrEqual(total, 2.0, "Too brief to register as branding (\(total)s).")
        XCTAssertLessThanOrEqual(total, 3.0, "Long enough to feel like a hang (\(total)s).")
    }

    /// The fade out must be non-zero, or the splash degrades to a hard cut — the thing
    /// the fade exists to avoid.
    ///
    /// There is deliberately no fade IN to assert: the launch screen draws the same
    /// wordmark at the same size, so fading in over it produced a visible second logo.
    func testFadeOutIsPresent() {
        XCTAssertGreaterThan(SplashTiming.logoFadeOutSeconds, 0)
    }

    /// A missing or non-square app icon is an App Store submission failure
    /// rather than a build error, so it is worth asserting.
    ///
    /// Note `UIImage(named: "AppIcon")` does NOT work for this: the app icon is
    /// not addressable as an ordinary catalog image. The build instead extracts
    /// rasterised copies to the bundle root and records the catalog name under
    /// `CFBundleIcons`, so those are what get checked.
    func testAppIconIsPresentAndSquare() throws {
        let icons = Bundle.main.object(forInfoDictionaryKey: "CFBundleIcons") as? [String: Any]
        let primary = icons?["CFBundlePrimaryIcon"] as? [String: Any]
        XCTAssertEqual(
            primary?["CFBundleIconName"] as? String, "AppIcon",
            "CFBundleIcons should name the AppIcon asset (from ASSETCATALOG_COMPILER_APPICON_NAME)."
        )

        let path = try XCTUnwrap(
            Bundle.main.path(forResource: "AppIcon60x60@2x", ofType: "png"),
            "Rasterised app icon missing from the bundle — the AppIcon asset did not compile."
        )
        let icon = try XCTUnwrap(UIImage(contentsOfFile: path))
        XCTAssertEqual(
            icon.size.width, icon.size.height,
            "App icon must be square, got \(icon.size)."
        )
    }

    /// The home-screen label. Absent this key, springboard falls back to
    /// `CFBundleName` and the icon reads "MeridianMotorsCompanion".
    func testAppDisplayNameIsBranded() {
        let name = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String
        XCTAssertEqual(name, "Meridian")
        XCTAssertLessThanOrEqual(
            (name ?? "").count, 12,
            "Home-screen labels truncate at roughly 12 characters."
        )
    }

    /// A launch storyboard only takes effect if `UILaunchScreen` is absent —
    /// when both keys exist the system ignores the storyboard's counterpart.
    func testLaunchStoryboardIsConfiguredAndNotShadowed() {
        XCTAssertEqual(
            Bundle.main.object(forInfoDictionaryKey: "UILaunchStoryboardName") as? String,
            "LaunchScreen"
        )
        XCTAssertNil(
            Bundle.main.object(forInfoDictionaryKey: "UILaunchScreen"),
            "UILaunchScreen must stay absent: present alongside UILaunchStoryboardName it "
                + "makes the launch configuration ambiguous."
        )
    }
}
