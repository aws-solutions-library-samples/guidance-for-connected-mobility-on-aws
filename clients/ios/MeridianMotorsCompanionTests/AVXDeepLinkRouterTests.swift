import XCTest
@testable import MeridianMotorsCompanion

// MARK: - AVXDeepLinkRouterTests

final class AVXDeepLinkRouterTests: XCTestCase {

    // MARK: - open(findingId:)

    @MainActor
    func test_open_sets_openFindingId() {
        let router = AVXDeepLinkRouter()
        XCTAssertNil(router.openFindingId,
            "openFindingId must be nil before any open call")

        router.open(findingId: "FIND-abc-0001")

        XCTAssertEqual(router.openFindingId, "FIND-abc-0001",
            "openFindingId must be set to the passed findingId")
    }

    @MainActor
    func test_open_overwrites_previous_openFindingId() {
        let router = AVXDeepLinkRouter()
        router.open(findingId: "FIND-first")
        router.open(findingId: "FIND-second")

        XCTAssertEqual(router.openFindingId, "FIND-second",
            "A second open call must overwrite the previous findingId")
    }

    // MARK: - consumeOpenFindingId()

    @MainActor
    func test_consumeOpenFindingId_clears_openFindingId() {
        let router = AVXDeepLinkRouter()
        router.open(findingId: "FIND-test-0001")
        XCTAssertNotNil(router.openFindingId)

        router.consumeOpenFindingId()

        XCTAssertNil(router.openFindingId,
            "consumeOpenFindingId must clear openFindingId to nil (prevents back-navigation re-trigger)")
    }

    @MainActor
    func test_consumeOpenFindingId_is_safe_when_already_nil() {
        let router = AVXDeepLinkRouter()
        XCTAssertNil(router.openFindingId)

        // Must not crash on a nil → nil transition.
        router.consumeOpenFindingId()
        XCTAssertNil(router.openFindingId)
    }

    // MARK: - Deep-link open-consume lifecycle

    @MainActor
    func test_open_then_consume_is_idempotent_for_multiple_navigations() {
        let router = AVXDeepLinkRouter()

        // First navigation.
        router.open(findingId: "FIND-001")
        XCTAssertEqual(router.openFindingId, "FIND-001")
        router.consumeOpenFindingId()
        XCTAssertNil(router.openFindingId)

        // Second navigation after the first was consumed.
        router.open(findingId: "FIND-002")
        XCTAssertEqual(router.openFindingId, "FIND-002")
        router.consumeOpenFindingId()
        XCTAssertNil(router.openFindingId)
    }

    // MARK: - drillDownFindingId (Group 4 hook — declared, not wired in Task 3.4)

    @MainActor
    func test_openDrillDown_sets_drillDownFindingId() {
        let router = AVXDeepLinkRouter()
        XCTAssertNil(router.drillDownFindingId)

        router.openDrillDown(findingId: "FIND-drill-001")

        XCTAssertEqual(router.drillDownFindingId, "FIND-drill-001",
            "drillDownFindingId MUST be set (interface exposed for Task 4.1, not wired yet)")
    }

    @MainActor
    func test_consumeDrillDownFindingId_clears_drillDownFindingId() {
        let router = AVXDeepLinkRouter()
        router.openDrillDown(findingId: "FIND-drill-002")
        XCTAssertNotNil(router.drillDownFindingId)

        router.consumeDrillDownFindingId()

        XCTAssertNil(router.drillDownFindingId,
            "consumeDrillDownFindingId MUST clear the field (single-shot pre-seeding)")
    }

    // MARK: - Independence: openFindingId and drillDownFindingId do not interfere

    @MainActor
    func test_open_does_not_affect_drillDownFindingId() {
        let router = AVXDeepLinkRouter()
        router.openDrillDown(findingId: "FIND-drill-003")

        // Opening a deep-link must not clear drillDownFindingId.
        router.open(findingId: "FIND-tap-001")

        XCTAssertEqual(router.openFindingId, "FIND-tap-001",
            "openFindingId must be set")
        XCTAssertEqual(router.drillDownFindingId, "FIND-drill-003",
            "drillDownFindingId must NOT be affected by open(findingId:)")
    }

    @MainActor
    func test_openDrillDown_does_not_affect_openFindingId() {
        let router = AVXDeepLinkRouter()
        router.open(findingId: "FIND-tap-002")

        router.openDrillDown(findingId: "FIND-drill-004")

        XCTAssertEqual(router.openFindingId, "FIND-tap-002",
            "openFindingId must NOT be affected by openDrillDown(findingId:)")
        XCTAssertEqual(router.drillDownFindingId, "FIND-drill-004",
            "drillDownFindingId must be set")
    }

    // MARK: - Shared instance is distinct from test instances

    @MainActor
    func test_shared_instance_is_singleton() {
        let a = AVXDeepLinkRouter.shared
        let b = AVXDeepLinkRouter.shared
        XCTAssertIdentical(a, b, "AVXDeepLinkRouter.shared must always return the same instance")
    }

    @MainActor
    func test_test_instance_is_independent_from_shared() {
        let shared = AVXDeepLinkRouter.shared
        let testInstance = AVXDeepLinkRouter()

        testInstance.open(findingId: "FIND-test-only")

        XCTAssertNotEqual(shared.openFindingId, testInstance.openFindingId,
            "Test-created instances must not pollute the shared singleton's state")

        // Cleanup: consume so shared state isn't accidentally set.
        testInstance.consumeOpenFindingId()
    }
}
