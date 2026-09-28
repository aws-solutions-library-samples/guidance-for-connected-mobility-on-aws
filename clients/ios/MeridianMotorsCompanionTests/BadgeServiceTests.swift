import XCTest
import UserNotifications
@testable import MeridianMotorsCompanion

// MARK: - Stub VSAClient (FindingsSummaryProvider)

/// Provides a controllable stub for the `FindingsSummaryProvider` protocol.
/// No network — tests stay deterministic and fast.
final class StubFindingsSummaryProvider: FindingsSummaryProvider {
    // Configurable response
    var summaryToReturn: VSAClient.FindingsSummary?
    var errorToThrow: Error?
    var callCount = 0

    func getFindingsSummary(ownerId: String) async throws -> VSAClient.FindingsSummary {
        callCount += 1
        if let error = errorToThrow { throw error }
        return summaryToReturn ?? VSAClient.FindingsSummary(openCount: 0, updatedAt: "2026-01-01T00:00:00Z")
    }
}

// MARK: - Stub badge center

/// Provides a controllable stub for `BadgeSettable`.
/// Records the badge counts set and lets tests inject an error if needed.
final class StubBadgeCenter: BadgeSettable {
    var badgeCountsSet: [Int] = []
    var errorToThrow: Error? = nil

    func setBadgeCount(_ newBadgeCount: Int) async throws {
        if let error = errorToThrow { throw error }
        badgeCountsSet.append(newBadgeCount)
    }
}

// MARK: - BadgeServiceTests

@MainActor
final class BadgeServiceTests: XCTestCase {

    // MARK: - Helpers

    private func makeService(
        client: StubFindingsSummaryProvider = StubFindingsSummaryProvider(),
        center: StubBadgeCenter = StubBadgeCenter()
    ) -> BadgeService {
        BadgeService(vsaClient: client, badgeCenter: center)
    }

    // MARK: - Accept #1: badge value flows from mocked VSAClient response

    /// `refresh()` fetches the summary and sets the badge count to `openCount`.
    func test_badge_value_flows_from_mocked_summary_response() async {
        let client = StubFindingsSummaryProvider()
        client.summaryToReturn = VSAClient.FindingsSummary(openCount: 7, updatedAt: "2026-09-18T10:00:00Z")
        let center = StubBadgeCenter()
        let svc = makeService(client: client, center: center)

        await svc.refresh(ownerId: "test-owner-sub-123")

        XCTAssertEqual(center.badgeCountsSet, [7],
                       "badge must be set to summary.openCount = 7")
    }

    /// Multiple refreshes produce badge updates matching each response's `openCount`.
    func test_badge_updates_on_each_refresh() async {
        let client = StubFindingsSummaryProvider()
        let center = StubBadgeCenter()
        let svc = makeService(client: client, center: center)

        client.summaryToReturn = VSAClient.FindingsSummary(openCount: 3, updatedAt: "2026-09-18T10:00:00Z")
        await svc.refresh(ownerId: "owner-abc")

        client.summaryToReturn = VSAClient.FindingsSummary(openCount: 0, updatedAt: "2026-09-18T11:00:00Z")
        await svc.refresh(ownerId: "owner-abc")

        XCTAssertEqual(center.badgeCountsSet, [3, 0],
                       "badge must reflect openCount on each successive call")
    }

    /// Zero openCount is a valid badge value (clears the badge).
    func test_zero_open_count_clears_badge() async {
        let client = StubFindingsSummaryProvider()
        client.summaryToReturn = VSAClient.FindingsSummary(openCount: 0, updatedAt: "2026-09-18T10:00:00Z")
        let center = StubBadgeCenter()
        let svc = makeService(client: client, center: center)

        await svc.refresh(ownerId: "owner-xyz")

        XCTAssertEqual(center.badgeCountsSet, [0],
                       "openCount=0 must still call setBadgeCount to clear the badge")
    }

    // MARK: - Accept #2: nil or empty ownerId is a no-op

    /// Refresh with no ownerId must skip the network call entirely.
    func test_refresh_with_nil_ownerId_is_noop() async {
        let client = StubFindingsSummaryProvider()
        let center = StubBadgeCenter()
        let svc = makeService(client: client, center: center)

        await svc.refresh(ownerId: nil)

        XCTAssertEqual(client.callCount, 0, "network call must not happen without ownerId")
        XCTAssertEqual(center.badgeCountsSet, [], "badge must not be updated without ownerId")
    }

    func test_refresh_with_empty_ownerId_is_noop() async {
        let client = StubFindingsSummaryProvider()
        let center = StubBadgeCenter()
        let svc = makeService(client: client, center: center)

        await svc.refresh(ownerId: "")

        XCTAssertEqual(client.callCount, 0)
        XCTAssertEqual(center.badgeCountsSet, [])
    }

    // MARK: - Accept #3: cancellation on concurrent call

    /// A second `refresh()` that starts before the first one finishes cancels the
    /// first one via cooperative cancellation.
    ///
    /// We simulate an in-flight first call by making the stub sleep briefly, then
    /// fire a second call; after both settle, assert the net effect is correct.
    ///
    /// This test verifies the cancellation *mechanism*, not wall-clock timing:
    /// both calls are awaited; the key invariant is that only one badge update
    /// reaches the center (the second call wins).
    func test_subsequent_call_cancels_inflight_refresh() async {
        // Use a slow stub that takes 100ms so a second call can arrive before it finishes.
        final class SlowStub: FindingsSummaryProvider {
            let delayNs: UInt64
            var callCount = 0
            init(delayNs: UInt64 = 100_000_000) { self.delayNs = delayNs }
            func getFindingsSummary(ownerId: String) async throws -> VSAClient.FindingsSummary {
                callCount += 1
                try await Task.sleep(nanoseconds: delayNs)
                return VSAClient.FindingsSummary(openCount: 1, updatedAt: "2026-09-18T10:00:00Z")
            }
        }

        let slowClient = SlowStub()
        let center = StubBadgeCenter()
        let svc = BadgeService(vsaClient: slowClient, badgeCenter: center)

        // Fire first call (will be running for 100ms)
        // Then immediately fire a second call from the same actor context.
        // Because BadgeService cancels the previous Task before starting the new one,
        // the first Task's `Task.checkCancellation()` after the sleep will throw.
        async let first: Void = svc.refresh(ownerId: "owner-1")
        // Give the first call a tiny head-start before the second call cancels it.
        try? await Task.sleep(nanoseconds: 5_000_000)  // 5ms
        async let second: Void = svc.refresh(ownerId: "owner-1")

        await first
        await second

        // The first task was cancelled (after its slow fetch); the second completes.
        // Net result: the badge was set exactly once (by the second call).
        XCTAssertEqual(center.badgeCountsSet.count, 1,
                       "only the completing (second) refresh should update the badge")
    }

    // MARK: - Accept #4c: errors are logged, not thrown

    /// A network error from `getFindingsSummary` must not propagate to the caller.
    /// The badge center must NOT be called on error.
    func test_network_error_is_swallowed_not_thrown() async {
        let client = StubFindingsSummaryProvider()
        client.errorToThrow = URLError(.notConnectedToInternet)
        let center = StubBadgeCenter()
        let svc = makeService(client: client, center: center)

        // Must not throw or crash
        await svc.refresh(ownerId: "owner-abc")

        XCTAssertEqual(center.badgeCountsSet, [],
                       "badge must not be set when the network call fails")
    }

    /// A `setBadgeCount` error from the system center must also not propagate.
    func test_setBadgeCount_error_is_swallowed_not_thrown() async {
        let client = StubFindingsSummaryProvider()
        client.summaryToReturn = VSAClient.FindingsSummary(openCount: 5, updatedAt: "2026-09-18T10:00:00Z")
        let center = StubBadgeCenter()
        center.errorToThrow = NSError(domain: "UNErrorDomain", code: 0,
                                      userInfo: [NSLocalizedDescriptionKey: "badge permission denied"])
        let svc = makeService(client: client, center: center)

        // Must not throw or crash
        await svc.refresh(ownerId: "owner-abc")
        // Test passes if we reach here without a crash or assertion failure.
    }

    // MARK: - Mutation-verification annotations
    //
    // Per ~/.kiro/steering/testing.md "Mutation testing at the green boundary":
    //
    // Mutation M1 (badge value flows): changed `summary.openCount` to a hardcoded `42`
    //   in BadgeService.refresh(). Result: test_badge_value_flows_from_mocked_summary_response
    //   FAILED (expected [7], got [42]). Restored: PASS.
    //   Recorded in decisions.md § "Task 3.6 mutation evidence".
    //
    // Mutation M2 (timeboxing / background-execution budget): inserted
    //   `try? await Task.sleep(nanoseconds: 60_000_000_000)` (60s) inside refresh().
    //   Simctl silent-push re-run: see decisions.md § "Task 3.6 mutation evidence —
    //   timeboxing mutation outcome".
}
