import Foundation
import UIKit

// MARK: - KioskGuidedAccessMonitor

/// Observes the iOS Guided Access state and exposes it to the kiosk UI.
///
/// ## What this does
/// When kiosk mode is active but Guided Access is OFF, a hint is surfaced to
/// operators ("station unlocked") so they can re-engage it before the next
/// visitor. The hint is designed to be unobtrusive enough that a visitor glances
/// past it while being unmistakable to staff who know to look.
///
/// Since the wall mirrors the phone exactly (Group 6 is dropped — see
/// `decisions.md` 2026-08-11), the hint appears on both phone and wall. That
/// is acceptable: it reads as ambient to a visitor but noticeable to an operator
/// scanning for yellow status indicators.
///
/// ## What this does NOT do
/// - Does NOT enable Guided Access programmatically.
///   That is always an operator action on the device Settings or via a side-button
///   triple-press. Autonomous Single App Mode requires MDM supervision, which is
///   out of scope.
/// - Does NOT block or slow the visitor journey when Guided Access is off.
///   An unlocked station that runs is better for the show than a locked-down
///   one that refuses.
///
/// ## Testing
/// Inject a custom `NotificationCenter` and `guidedAccessChecker` closure so
/// tests can control GA state without touching system state.
@Observable
@MainActor
final class KioskGuidedAccessMonitor {

    // MARK: - State

    /// `true` when `UIAccessibility.isGuidedAccessEnabled` returns `true`.
    private(set) var isGuidedAccessEnabled: Bool

    /// `true` when the kiosk is in active kiosk mode AND Guided Access is OFF.
    /// This is the signal that causes the operator hint to appear.
    var showsUnlockedHint: Bool {
        isKioskModeActive && !isGuidedAccessEnabled
    }

    // MARK: - Kiosk mode flag

    /// Set to `true` when the app enters kiosk mode, `false` when it exits.
    /// Only shows the hint when kiosk mode is active; suppresses it in normal use.
    var isKioskModeActive: Bool = false

    // MARK: - Observer lifetime

    /// Holds the observation token for the GA notification.
    /// Stored as `Any?` to keep the `deinit` nonisolated-safe; the actual type is
    /// `NSObjectProtocol` but we only need `removeObserver(_:)` which takes `Any`.
    private let notificationCenter: NotificationCenter
    private let guidedAccessChecker: () -> Bool

    // Token stored on a nonisolated helper so deinit can clean up without crossing
    // actor isolation boundaries.
    private let tokenBox: TokenBox = TokenBox()

    // MARK: - Init

    /// - Parameters:
    ///   - notificationCenter: Defaults to `.default`. Tests inject a separate
    ///     instance so they can post notifications without system side-effects.
    ///   - guidedAccessChecker: Closure that returns the current GA state.
    ///     Defaults to `UIAccessibility.isGuidedAccessEnabled`. Tests inject
    ///     a controllable closure.
    init(
        notificationCenter: NotificationCenter = .default,
        guidedAccessChecker: @escaping () -> Bool = { UIAccessibility.isGuidedAccessEnabled }
    ) {
        self.notificationCenter = notificationCenter
        self.guidedAccessChecker = guidedAccessChecker
        self.isGuidedAccessEnabled = guidedAccessChecker()
        startObserving()
    }

    // MARK: - Private

    private func startObserving() {
        let token = notificationCenter.addObserver(
            forName: UIAccessibility.guidedAccessStatusDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.isGuidedAccessEnabled = self.guidedAccessChecker()
            }
        }
        tokenBox.token = token
        tokenBox.center = notificationCenter
    }
}

// MARK: - TokenBox

/// Reference-type wrapper that removes the observation token on deinit.
/// Lives outside the @MainActor boundary so deinit is nonisolated.
private final class TokenBox {
    var token: NSObjectProtocol?
    var center: NotificationCenter?

    deinit {
        if let token = token {
            center?.removeObserver(token)
        }
    }
}
