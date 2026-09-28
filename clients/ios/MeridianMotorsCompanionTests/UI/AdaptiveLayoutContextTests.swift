import XCTest
import SwiftUI
@testable import MeridianMotorsCompanion

/// Unit tests for `resolvedAdaptiveLayoutContext(...)`.
///
/// Each test supplies the six raw signals directly to the pure detection
/// function and asserts the expected `AdaptiveLayoutContext` case is returned.
/// No app boot, no simulator UI, no SwiftUI rendering required.
///
/// Covers all six enum cases per spec Group 2 task 2.1.
final class AdaptiveLayoutContextTests: XCTestCase {

    // MARK: - phonePortrait

    /// Compact-width, regular-height, phone idiom, portrait dimensions.
    func testPhonePortrait() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .compact,
            verticalSizeClass: .regular,
            idiom: .phone,
            screenWidth: 390,    // iPhone 15 Pro logical width
            screenHeight: 844,
            externalScreenConnected: false
        )
        XCTAssertEqual(result, .phonePortrait)
    }

    // MARK: - phoneLandscape

    /// Compact-width, compact-height — iPhone in landscape.
    func testPhoneLandscape() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .compact,
            verticalSizeClass: .compact,
            idiom: .phone,
            screenWidth: 844,    // iPhone 15 Pro landscape logical width
            screenHeight: 390,
            externalScreenConnected: false
        )
        XCTAssertEqual(result, .phoneLandscape)
    }

    // MARK: - iPadPortraitStandard

    /// Regular-width, regular-height, portrait orientation, 11" iPad.
    /// `max(screenWidth, screenHeight) = 1194 > 1024`, but we're checking portrait here.
    /// The iPad Air 11" has logical bounds 820×1180 in portrait — max = 1180 ≥ 1024
    /// routes to `iPadLandscapeLarge` per the detection function. Use a ≤ 1023 pt max.
    /// iPad Mini (6th gen) portrait: 744×1133 — max = 1133 ≥ 1024 → also iPadLandscapeLarge.
    /// The `iPadPortraitStandard` branch fires only when
    /// `max(screenWidth, screenHeight) < 1024` AND `!isCompactHeight` AND `!isLandscape`.
    ///
    /// Note: standard iPads in practice have a max dimension ≥ 1024pt, which routes to
    /// `iPadLandscapeLarge`. The `iPadPortraitStandard` case is reachable with custom
    /// trait injection at < 1024pt (e.g. in Split View). We exercise the detection logic
    /// with synthetic values that satisfy the branch.
    func testIPadPortraitStandard() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .regular,
            verticalSizeClass: .regular,
            idiom: .pad,
            screenWidth: 500,    // narrow Split View — width < height, max < 1024
            screenHeight: 768,
            externalScreenConnected: false
        )
        XCTAssertEqual(result, .iPadPortraitStandard)
    }

    // MARK: - iPadLandscapeStandard

    /// Regular-width, regular-height, landscape orientation, < 1024pt max.
    /// Exercises the `isLandscape = true` branch within the regular-width
    /// sub-1024 region.
    func testIPadLandscapeStandard() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .regular,
            verticalSizeClass: .regular,
            idiom: .pad,
            screenWidth: 900,    // landscape: width > height, but max (900) < 1024
            screenHeight: 600,
            externalScreenConnected: false
        )
        XCTAssertEqual(result, .iPadLandscapeStandard)
    }

    // MARK: - iPadLandscapeLarge

    /// Regular-width, regular-height, max dimension ≥ 1024pt.
    /// Represents iPad Pro 12.9" (logical bounds 1366×1024) and any standard
    /// iPad where `max(width, height) ≥ 1024`.
    func testIPadLandscapeLarge() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .regular,
            verticalSizeClass: .regular,
            idiom: .pad,
            screenWidth: 1366,   // iPad Pro 12.9" landscape
            screenHeight: 1024,
            externalScreenConnected: false
        )
        XCTAssertEqual(result, .iPadLandscapeLarge)
    }

    /// iPad Air 11" portrait — max(820, 1180) = 1180 ≥ 1024 → iPadLandscapeLarge.
    /// This is the expected real-device case for an iPad Air 11".
    func testIPadAir11Portrait() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .regular,
            verticalSizeClass: .regular,
            idiom: .pad,
            screenWidth: 820,    // iPad Air 11" portrait logical width
            screenHeight: 1180,
            externalScreenConnected: false
        )
        XCTAssertEqual(result, .iPadLandscapeLarge)
    }

    // MARK: - externalDisplay16by9

    /// `externalScreenConnected = true` always maps to `.externalDisplay16by9`
    /// regardless of all other signals.
    func testExternalDisplayTakesPrecedence() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .compact,
            verticalSizeClass: .regular,
            idiom: .phone,
            screenWidth: 390,
            screenHeight: 844,
            externalScreenConnected: true  // overrides everything else
        )
        XCTAssertEqual(result, .externalDisplay16by9)
    }

    /// External display flag takes precedence over regular-width iPad signals.
    func testExternalDisplayOverridesIPad() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .regular,
            verticalSizeClass: .regular,
            idiom: .pad,
            screenWidth: 1366,
            screenHeight: 1024,
            externalScreenConnected: true
        )
        XCTAssertEqual(result, .externalDisplay16by9)
    }

    // MARK: - Edge cases

    /// Nil size classes default to compact — verify `.phonePortrait` is returned
    /// when both size classes are nil (e.g. before SwiftUI injects them).
    func testNilSizeClassesDefaultToPhonePortrait() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: nil,
            verticalSizeClass: nil,
            idiom: .phone,
            screenWidth: 390,
            screenHeight: 844,
            externalScreenConnected: false
        )
        // nil horizontal → `isRegularWidth = false` → compact path
        // nil vertical → `isCompactHeight = false` → phonePortrait
        XCTAssertEqual(result, .phonePortrait)
    }

    /// Regular-width with compact-height and `< 1024pt` max is the defensive
    /// branch ("Should not occur on iPad, but handle defensively"). Verify it
    /// returns `iPadLandscapeStandard` — same outcome as the normal landscape branch.
    func testRegularWidthCompactHeightDefensivePath() {
        let result = resolvedAdaptiveLayoutContext(
            horizontalSizeClass: .regular,
            verticalSizeClass: .compact,
            idiom: .pad,
            screenWidth: 900,
            screenHeight: 600,
            externalScreenConnected: false
        )
        // `max(900, 600) = 900 < 1024` AND `isCompactHeight = true`
        // → hits the defensive `iPadLandscapeStandard` branch
        XCTAssertEqual(result, .iPadLandscapeStandard)
    }
}
