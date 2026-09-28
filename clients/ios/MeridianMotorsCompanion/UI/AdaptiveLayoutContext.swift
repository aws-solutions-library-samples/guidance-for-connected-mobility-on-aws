import SwiftUI
import UIKit

// MARK: - AdaptiveLayoutContext

/// Canonical description of the device layout context, derived once at the
/// root of the view hierarchy and injected via SwiftUI environment.
///
/// Views read `@Environment(\.adaptiveLayoutContext)` and branch on this enum
/// rather than raw `horizontalSizeClass` or `userInterfaceIdiom` values.
/// This makes layout decisions:
///   - **Testable**: an XCTest can inject any case and snapshot the result.
///   - **Named**: branches are explicit, not implicit `if .regular`.
///   - **Stable**: all detection logic lives in `detectAdaptiveLayout()`;
///     no view re-derives the context from raw traits.
///
/// Documented in `clients/ios/MeridianMotorsCompanion/docs/tech.md`.
public enum AdaptiveLayoutContext: Equatable {
    /// Compact-width, any height — most iPhones in portrait.
    case phonePortrait
    /// Compact-width, compact-height — iPhone in landscape.
    case phoneLandscape
    /// Regular-width, regular-height, screen ≤ 11" — iPad Air/Mini/11" Pro.
    case iPadPortraitStandard
    /// Regular-width, regular-height, screen ≤ 11" — iPad landscape.
    case iPadLandscapeStandard
    /// Regular-width, regular-height, screen ≥ 12.9" — iPad Pro 12.9".
    case iPadLandscapeLarge
    /// Attached wall projector / external display (detected via UIScreen).
    case externalDisplay16by9

    // MARK: - Convenience queries

    /// `true` for compact-width contexts (iPhone portrait or landscape).
    ///
    /// Views use this rather than branching on `horizontalSizeClass` directly
    /// — the detection logic stays in `detectAdaptiveLayout()`, not spread
    /// across view hierarchies.
    public var isCompact: Bool {
        switch self {
        case .phonePortrait, .phoneLandscape:
            return true
        case .iPadPortraitStandard, .iPadLandscapeStandard, .iPadLandscapeLarge,
             .externalDisplay16by9:
            return false
        }
    }

    /// `true` for regular-width contexts (iPad and external display).
    public var isRegular: Bool { !isCompact }
}

// MARK: - EnvironmentKey + EnvironmentValues extension

private struct AdaptiveLayoutContextKey: EnvironmentKey {
    static let defaultValue: AdaptiveLayoutContext = .phonePortrait
}

public extension EnvironmentValues {
    /// The resolved adaptive layout context for the current device/screen.
    ///
    /// Injected by `.detectAdaptiveLayout()` at the app root.
    /// Default is `.phonePortrait` for views outside the detection hierarchy.
    var adaptiveLayoutContext: AdaptiveLayoutContext {
        get { self[AdaptiveLayoutContextKey.self] }
        set { self[AdaptiveLayoutContextKey.self] = newValue }
    }
}

// MARK: - Detection logic

/// Derive the `AdaptiveLayoutContext` from raw trait signals.
///
/// This is the **single point of detection** in the application. No view
/// outside of `detectAdaptiveLayout()` should read `horizontalSizeClass` and
/// branch on it — that logic belongs here and only here.
///
/// - Parameters:
///   - horizontalSizeClass: The horizontal size class of the current scene.
///   - verticalSizeClass: The vertical size class of the current scene.
///   - idiom: The UI idiom (`.phone`, `.pad`, etc.).
///   - screenWidth: The width of the relevant `UIScreen` in points.
///   - screenHeight: The height of the relevant `UIScreen` in points.
///   - externalScreenConnected: Whether a non-main external display is attached.
/// - Returns: The canonical `AdaptiveLayoutContext` for these inputs.
public func resolvedAdaptiveLayoutContext(
    horizontalSizeClass: UserInterfaceSizeClass?,
    verticalSizeClass: UserInterfaceSizeClass?,
    idiom: UIUserInterfaceIdiom,
    screenWidth: CGFloat,
    screenHeight: CGFloat,
    externalScreenConnected: Bool
) -> AdaptiveLayoutContext {
    if externalScreenConnected {
        return .externalDisplay16by9
    }

    let isRegularWidth = horizontalSizeClass == .regular
    let isCompactHeight = verticalSizeClass == .compact

    guard isRegularWidth else {
        // Compact-width — iPhone portrait or landscape
        return isCompactHeight ? .phoneLandscape : .phonePortrait
    }

    // Regular-width — iPad or large-iPhone (rare; treat as iPad)
    let largerDimension = max(screenWidth, screenHeight)

    if largerDimension >= 1024 {
        // 12.9" iPad Pro: native resolution 2732×2048 pt→1366×1024
        return .iPadLandscapeLarge
    } else if isCompactHeight {
        // Should not occur on iPad (iPads never report compact vertical),
        // but handle defensively.
        return .iPadLandscapeStandard
    } else {
        // Regular width + regular height on standard iPad
        let isLandscape = screenWidth > screenHeight
        return isLandscape ? .iPadLandscapeStandard : .iPadPortraitStandard
    }
}

// MARK: - View modifier

/// Injects a resolved `AdaptiveLayoutContext` into the SwiftUI environment for
/// all descendant views.
///
/// Apply at the root of the view hierarchy (in `MeridianMotorsCompanionApp.body`):
/// ```swift
/// RootView()
///     .detectAdaptiveLayout()
///     .environment(session)
/// ```
///
/// Child views read the context via:
/// ```swift
/// @Environment(\.adaptiveLayoutContext) private var adaptiveLayoutContext
/// ```
public struct DetectAdaptiveLayoutModifier: ViewModifier {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    public func body(content: Content) -> some View {
        GeometryReader { geometry in
            content
                .environment(
                    \.adaptiveLayoutContext,
                    resolvedAdaptiveLayoutContext(
                        horizontalSizeClass: horizontalSizeClass,
                        verticalSizeClass: verticalSizeClass,
                        idiom: UIDevice.current.userInterfaceIdiom,
                        screenWidth: geometry.size.width,
                        screenHeight: geometry.size.height,
                        externalScreenConnected: externalScreenIsConnected()
                    )
                )
        }
    }

    /// Returns whether a *dedicated* external-display scene is attached.
    ///
    /// **Currently hard-wired to `false`, deliberately.** The wall display is an
    /// OS-level mirror of the phone, not a separate rendered scene — see
    /// `decisions.md` 2026-08-11 ("Wall is a mirror, not a redrawn scene") — so
    /// Group 6's `ExternalDisplayCoordinator` is dropped and **nothing attaches a
    /// second `UIWindowScene`**. With no consumer, a live detection here can only
    /// mis-route layout on the primary device; returning `false` is the honest
    /// answer to "is a dedicated external scene attached?".
    ///
    /// The previous implementation was:
    ///
    /// ```swift
    /// UIApplication.shared.connectedScenes
    ///     .compactMap { $0 as? UIWindowScene }
    ///     .contains { $0.screen != UIScreen.main }
    /// ```
    ///
    /// Review cycle 2 flagged that as returning `true` under AirPlay mirroring.
    /// **That specific claim is unverified and is probably wrong**: mirroring is
    /// handled by the display system and does not create a second
    /// `UIWindowScene`, so `connectedScenes` should still report one — unlike the
    /// iOS 16-deprecated `UIScreen.screens`, which *does* enumerate a mirrored
    /// screen. It could not be settled here: AirPlay mirroring cannot be
    /// exercised from a simulator or by any agent, so the behaviour is untestable
    /// in this environment.
    ///
    /// It is stubbed for the reason above rather than the reason cycle 2 gave —
    /// an unconsumed code path whose behaviour nobody has observed is the kind of
    /// thing that later gets trusted incorrectly.
    ///
    /// **If Group 6 is ever reinstated**, re-derive this against a real device
    /// with a real display attached, and distinguish "mirrored" from "extended"
    /// explicitly. Do not assume the snippet above was correct.
    @MainActor
    private func externalScreenIsConnected() -> Bool {
        false
    }
}

public extension View {
    /// Detects the current device layout context and injects it into the
    /// SwiftUI environment for all descendants.
    ///
    /// Place at the root of the view hierarchy. Do NOT call this on individual
    /// views — the detection cost should be paid once, not per-subtree.
    func detectAdaptiveLayout() -> some View {
        self.modifier(DetectAdaptiveLayoutModifier())
    }
}
