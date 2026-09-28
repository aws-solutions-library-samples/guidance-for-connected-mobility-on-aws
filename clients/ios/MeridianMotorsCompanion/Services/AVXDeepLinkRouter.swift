import Foundation
import Observation
import os

// MARK: - AVXDeepLinkRouter
//
// Routes APNs tap-to-open notifications into the AVX Cards tab.
//
// **Design**
// `AppDelegate.userNotificationCenter(_:didReceive:withCompletionHandler:)` has no
// access to the SwiftUI `.environment(session)` subtree — it is a UIKit callback
// predating the App lifecycle. Rather than threading `AppSession` through `AppDelegate`,
// `AVXDeepLinkRouter` is an `@Observable` singleton that:
//
//  1. `AppDelegate.didReceive` calls `AVXDeepLinkRouter.shared.open(findingId:)` with
//     the `finding_id` from the notification payload.
//  2. `MainTabView` (or `AlertsTabView`) observes `AVXDeepLinkRouter.shared` and
//     responds to `openFindingId` by switching the active tab to `.alerts` and
//     highlighting the matching card.
//  3. After the navigation fires, the caller clears `openFindingId` via
//     `AVXDeepLinkRouter.shared.consumeOpenFindingId()` so back-navigation does
//     not re-trigger the deep link.
//
// **Group 4 hook (OUT OF SCOPE for Task 3.4)**
// The router also exposes a `drillDownFindingId: String?` property for the
// `AssistantTabView` pre-seeding path (Task 4.1). This property is declared here
// so the interface is visible to `AVXCardsViewModel` and `MainTabView` without
// requiring a later structural change, but it is NOT wired to `AssistantTabView`
// in Task 3.4 per the hard constraint: `Group 4 is OUT OF SCOPE`.
//
// Source: spec.md § Design → APNs client path; tasks.md Task 3.4 Accept #4, #5.

@Observable @MainActor
final class AVXDeepLinkRouter {

    // MARK: Shared instance (production path)
    static let shared = AVXDeepLinkRouter()

    // MARK: Published state

    /// Non-nil when a notification tap should navigate to the given Finding.
    /// Consumers: `AlertsTabView` (switches to `.alerts`, scrolls to card).
    /// Lifecycle: set by `open(findingId:)`, cleared by `consumeOpenFindingId()`.
    private(set) var openFindingId: String? = nil

    /// Hook for `AssistantTabView` pre-seeding (Task 4.1, OUT OF SCOPE for Task 3.4).
    /// Set when the user taps "Drill-down" on an `AVXFindingCard`. Cleared after the
    /// first `POST /assistant/chat` body carries `avx_finding_id` (Task 4.2).
    private(set) var drillDownFindingId: String? = nil

    // MARK: Private

    private let log = Logger(
        subsystem: "com.aws.meridianmotors.companion",
        category: "AVXDeepLinkRouter"
    )

    // MARK: Init (injectable for tests — not a singleton when injected)
    init() {}

    // MARK: - Open (APNs tap path)

    /// Called from `AppDelegate.userNotificationCenter(_:didReceive:withCompletionHandler:)`
    /// when a notification payload carries a `finding_id`.
    ///
    /// Sets `openFindingId`, which `AlertsTabView` observes. The observer switches the
    /// active tab to `.alerts` and highlights the matching card.
    func open(findingId: String) {
        log.info("🔔 AVXDeepLinkRouter: routing to finding_id=\(findingId, privacy: .public)")
        openFindingId = findingId
    }

    /// Clears `openFindingId` once the navigation has been performed.
    /// Called from the consuming view after it has switched to the AVX Cards tab.
    func consumeOpenFindingId() {
        openFindingId = nil
    }

    // MARK: - Drill-down (Group 4 hook, Task 4.1 — OUT OF SCOPE for Task 3.4)

    /// Called from `AVXFindingCard` when the user taps "Drill-down".
    /// `AssistantTabView` observes this and pre-seeds the next chat POST body.
    ///
    /// - Note: This method exists to expose the interface for Task 4.1 without
    ///   wiring `AssistantTabView` here. AppDelegate does NOT call this method.
    func openDrillDown(findingId: String) {
        log.info("🔔 AVXDeepLinkRouter: drill-down finding_id=\(findingId, privacy: .public)")
        drillDownFindingId = findingId
    }

    /// Clears `drillDownFindingId` after the first pre-seeded chat POST.
    /// Called from `AssistantTabView` (Task 4.2).
    func consumeDrillDownFindingId() {
        drillDownFindingId = nil
    }
}
