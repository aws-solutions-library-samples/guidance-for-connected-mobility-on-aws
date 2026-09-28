import Foundation
import UserNotifications
import os

// MARK: - Protocol for testability
//
// UNUserNotificationCenter is a concrete class with no public protocol, so we
// wrap the single method we need in a thin protocol.  The real center conforms
// via the extension below; tests inject a mock.
// Source: clients/ios/docs/tech.md § "setBadgeCount(_:withCompletionHandler:)"
protocol BadgeSettable: AnyObject {
    func setBadgeCount(_ newBadgeCount: Int) async throws
}

extension UNUserNotificationCenter: BadgeSettable {}

// MARK: - Protocol for VSAClient testability in BadgeService
//
// We only need one method from VSAClient inside BadgeService.
// Wrapping it in a protocol lets tests inject a stub without needing a full
// URLSession mock.
protocol FindingsSummaryProvider: AnyObject {
    func getFindingsSummary(ownerId: String) async throws -> VSAClient.FindingsSummary
}

extension VSAClient: FindingsSummaryProvider {}

// MARK: - BadgeService
//
// Fetches the open-findings count for the current owner and updates the app's
// icon badge using the iOS 16+ replacement for the deprecated
// `applicationIconBadgeNumber` API.
//
// Deployment target: iOS 18.0. Using `UNUserNotificationCenter.setBadgeCount(_:)`
// (async/throws form, available iOS 16.0+).
// Source: clients/ios/docs/tech.md §(f)  — "Use -[UNUserNotificationCenter
// setBadgeCount:withCompletionHandler:] instead."
//
// ## Cancellation
// Only one refresh is allowed in-flight at a time.  A call to `refresh()` while
// a previous one is running cancels the in-flight task via Swift structured-
// concurrency cooperative cancellation before starting a new one.
//
// ## Background-execution budget
// This service is called from the silent-push handler, which has ~30 seconds of
// wall-clock time (Apple docs, cited in tech.md §(d)).  The network call and
// badge update must complete well within that window.  The unit tests assert the
// cancellation path specifically so a future mutation inserting a long sleep is
// caught rather than silently missing the budget.
@MainActor
final class BadgeService {

    // MARK: Shared instance (production path)
    static let shared = BadgeService(
        vsaClient: nil,       // lazily replaced by the App-level client on first use
        badgeCenter: UNUserNotificationCenter.current()
    )

    // MARK: Private state
    private var vsaClient: FindingsSummaryProvider?
    private let badgeCenter: BadgeSettable
    private var inflight: Task<Void, Never>?

    private let log = Logger(
        subsystem: "com.aws.meridianmotors.companion",
        category: "BadgeService"
    )

    // MARK: Init (injectable for tests)
    init(vsaClient: FindingsSummaryProvider?, badgeCenter: BadgeSettable) {
        self.vsaClient = vsaClient
        self.badgeCenter = badgeCenter
    }

    // MARK: - Client wiring (called by App on startup or sign-in)
    /// Sets the VSAClient instance that BadgeService will use for summary fetches.
    /// Must be called before the first `refresh()` in the App lifecycle (but is
    /// harmless to call multiple times — the latest value wins).
    func configure(client: FindingsSummaryProvider) {
        self.vsaClient = client
    }

    // MARK: - Refresh
    /// Fetches `GET /findings/owner/{ownerId}/summary` and updates the icon badge.
    ///
    /// - If a previous refresh is in-flight, it is cancelled before a new one starts.
    /// - Errors are logged and swallowed — they are not propagated to the caller, so
    ///   the silent-push completion handler can always call `.newData` or `.noData`
    ///   without dealing with thrown errors (Accept #4c).
    /// - The `ownerId` parameter comes from `AppSession.currentOwnerId` (the
    ///   Cognito `sub` claim). If nil, the refresh is a no-op.
    func refresh(ownerId: String?) async {
        guard let ownerId = ownerId, !ownerId.isEmpty else {
            log.info("🔔 APNs: BadgeService.refresh skipped — no ownerId (user not signed in)")
            return
        }

        // Cancel any in-flight refresh (cooperative cancellation)
        inflight?.cancel()

        let task = Task { [weak self] in
            guard let self else { return }
            do {
                // Check for cancellation before the network call so a rapid
                // second call terminates before touching the network.
                try Task.checkCancellation()

                guard let client = self.vsaClient else {
                    self.log.warning("🔔 APNs: BadgeService.refresh — vsaClient not configured yet")
                    return
                }

                let summary = try await client.getFindingsSummary(ownerId: ownerId)

                // Re-check cancellation after the await so a slow network call
                // that completes after the task was cancelled still exits early.
                try Task.checkCancellation()

                try await self.badgeCenter.setBadgeCount(summary.openCount)
                self.log.info("🔔 APNs: silent refresh → badge=\(summary.openCount, privacy: .public)")

            } catch is CancellationError {
                // Normal — a subsequent call cancelled this one.
                self.log.info("🔔 APNs: BadgeService.refresh cancelled (superseded by newer call)")
            } catch {
                // Log and swallow — errors must not surface to the caller (Accept #4c).
                self.log.error("🔔 APNs: BadgeService.refresh error: \(error.localizedDescription, privacy: .public)")
            }
        }

        inflight = task
        await task.value
    }
}
