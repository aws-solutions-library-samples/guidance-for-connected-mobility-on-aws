import XCTest
import UserNotifications
@testable import MeridianMotorsCompanion

// MARK: - Mock notification center

/// Mock for UNUserNotificationCenter — required because UNUserNotificationCenter is
/// a class with no protocol. Injected via NotificationConsentService's initializer.
/// Source: clients/ios/docs/tech.md § "UNUserNotificationCenter mockability for tests"
final class MockNotificationCenter: NotificationCenterProtocol {
    // Track calls for assertion
    var requestAuthorizationCallCount = 0
    var lastOptions: UNAuthorizationOptions?
    var registeredCategoryIdentifiers: [String] = []

    // Configurable stubs
    var shouldGrantAuthorization = true
    var authorizationError: Error? = nil

    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool {
        requestAuthorizationCallCount += 1
        lastOptions = options
        if let error = authorizationError {
            throw error
        }
        return shouldGrantAuthorization
    }

    func setNotificationCategories(_ categories: Set<UNNotificationCategory>) {
        registeredCategoryIdentifiers = categories.map(\.identifier).sorted()
    }
}

// MARK: - NotificationConsentServiceTests

final class NotificationConsentServiceTests: XCTestCase {

    // Use an isolated UserDefaults suite so tests don't pollute the real store
    // and can run concurrently without shared-state problems.
    private var testDefaults: UserDefaults!
    private let suiteName = "com.test.NotificationConsentServiceTests"

    override func setUp() {
        super.setUp()
        testDefaults = UserDefaults(suiteName: suiteName)!
        // Ensure a clean slate for each test
        testDefaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        testDefaults.removePersistentDomain(forName: suiteName)
        testDefaults = nil
        super.tearDown()
    }

    // MARK: - Helpers

    private func makeService(center: MockNotificationCenter = MockNotificationCenter()) -> NotificationConsentService {
        NotificationConsentService(center: center, defaults: testDefaults)
    }

    // MARK: - Persistence tests

    /// Toggle safetyEnabled off, create a fresh service with the same defaults,
    /// assert the preference was persisted.
    func test_safetyEnabled_persists_across_reinitialization() {
        let svc = makeService()
        XCTAssertTrue(svc.safetyEnabled, "Default should be true")
        svc.safetyEnabled = false
        // Re-create with the same UserDefaults instance
        let svc2 = makeService()
        XCTAssertFalse(svc2.safetyEnabled, "Persisted value should survive re-init")
    }

    func test_coverageEnabled_persists_across_reinitialization() {
        let svc = makeService()
        XCTAssertTrue(svc.coverageEnabled)
        svc.coverageEnabled = false
        let svc2 = makeService()
        XCTAssertFalse(svc2.coverageEnabled)
    }

    func test_serviceEnabled_persists_across_reinitialization() {
        let svc = makeService()
        XCTAssertTrue(svc.serviceEnabled)
        svc.serviceEnabled = false
        let svc2 = makeService()
        XCTAssertFalse(svc2.serviceEnabled)
    }

    /// First install: keys are absent in UserDefaults → defaults to `true`.
    func test_default_enabled_state_is_true_on_first_install() {
        // Verify keys really are absent before constructing the service
        XCTAssertNil(testDefaults.object(forKey: "avx.notifications.safetyEnabled"))
        XCTAssertNil(testDefaults.object(forKey: "avx.notifications.coverageEnabled"))
        XCTAssertNil(testDefaults.object(forKey: "avx.notifications.serviceEnabled"))

        let svc = makeService()
        XCTAssertTrue(svc.safetyEnabled)
        XCTAssertTrue(svc.coverageEnabled)
        XCTAssertTrue(svc.serviceEnabled)
    }

    // MARK: - Authorization tests

    func test_requestAuthorizationIfNeeded_calls_center_with_alert_sound_badge() async {
        let center = MockNotificationCenter()
        let svc = makeService(center: center)

        await MainActor.run {
            Task { await svc.requestAuthorizationIfNeeded() }
        }
        // Give the async task a moment
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(center.requestAuthorizationCallCount, 1)
        XCTAssertNotNil(center.lastOptions)
        let opts = center.lastOptions!
        XCTAssertTrue(opts.contains(.alert))
        XCTAssertTrue(opts.contains(.sound))
        XCTAssertTrue(opts.contains(.badge))
    }

    func test_requestAuthorizationIfNeeded_registers_categories_when_granted() async {
        let center = MockNotificationCenter()
        center.shouldGrantAuthorization = true
        let svc = makeService(center: center)

        await svc.requestAuthorizationIfNeeded()

        // Verify all three AVX categories were registered
        XCTAssertTrue(center.registeredCategoryIdentifiers.contains("AVX_SAFETY"))
        XCTAssertTrue(center.registeredCategoryIdentifiers.contains("AVX_COVERAGE"))
        XCTAssertTrue(center.registeredCategoryIdentifiers.contains("AVX_SERVICE"))
        XCTAssertEqual(center.registeredCategoryIdentifiers.count, 3)
    }

    func test_requestAuthorizationIfNeeded_does_not_register_categories_when_denied() async {
        let center = MockNotificationCenter()
        center.shouldGrantAuthorization = false
        let svc = makeService(center: center)

        await svc.requestAuthorizationIfNeeded()

        XCTAssertTrue(center.registeredCategoryIdentifiers.isEmpty,
                      "Categories must not be registered when authorization is denied")
    }

    func test_requestAuthorizationIfNeeded_does_not_crash_on_error() async {
        let center = MockNotificationCenter()
        center.authorizationError = NSError(domain: "test", code: -1,
                                            userInfo: [NSLocalizedDescriptionKey: "Simulated error"])
        let svc = makeService(center: center)
        // Must not throw or crash
        await svc.requestAuthorizationIfNeeded()
        XCTAssertFalse(svc.authorizationGranted)
    }

    // MARK: - Category consent gate tests

    func test_isEnabled_returns_safetyEnabled_for_AVX_SAFETY() {
        let svc = makeService()
        svc.safetyEnabled = false
        XCTAssertFalse(svc.isEnabled(categoryIdentifier: "AVX_SAFETY"))
        svc.safetyEnabled = true
        XCTAssertTrue(svc.isEnabled(categoryIdentifier: "AVX_SAFETY"))
    }

    func test_isEnabled_returns_coverageEnabled_for_AVX_COVERAGE() {
        let svc = makeService()
        svc.coverageEnabled = false
        XCTAssertFalse(svc.isEnabled(categoryIdentifier: "AVX_COVERAGE"))
        svc.coverageEnabled = true
        XCTAssertTrue(svc.isEnabled(categoryIdentifier: "AVX_COVERAGE"))
    }

    func test_isEnabled_returns_serviceEnabled_for_AVX_SERVICE() {
        let svc = makeService()
        svc.serviceEnabled = false
        XCTAssertFalse(svc.isEnabled(categoryIdentifier: "AVX_SERVICE"))
        svc.serviceEnabled = true
        XCTAssertTrue(svc.isEnabled(categoryIdentifier: "AVX_SERVICE"))
    }

    /// Unknown category identifiers pass through (default `true`).
    func test_isEnabled_returns_true_for_unknown_category() {
        let svc = makeService()
        XCTAssertTrue(svc.isEnabled(categoryIdentifier: "UNKNOWN_CATEGORY"))
    }

    // MARK: - State transitions (toggle) tests

    func test_toggle_safety_off_then_on() {
        let svc = makeService()
        svc.safetyEnabled = false
        XCTAssertFalse(svc.safetyEnabled)
        svc.safetyEnabled = true
        XCTAssertTrue(svc.safetyEnabled)
    }

    func test_toggle_coverage_off_then_on() {
        let svc = makeService()
        svc.coverageEnabled = false
        XCTAssertFalse(svc.coverageEnabled)
        svc.coverageEnabled = true
        XCTAssertTrue(svc.coverageEnabled)
    }

    func test_toggle_service_off_then_on() {
        let svc = makeService()
        svc.serviceEnabled = false
        XCTAssertFalse(svc.serviceEnabled)
        svc.serviceEnabled = true
        XCTAssertTrue(svc.serviceEnabled)
    }

    // MARK: - Mutation verification annotations
    // Per ~/.kiro/steering/testing.md "Mutation testing at the green boundary":
    // The persistence tests above were mutation-verified:
    // - Mutation: changed `safetyEnabled = false` persisted value read from defaults
    //   to always return `true`. Result: test_safetyEnabled_persists_across_reinitialization FAILED.
    //   Restored: PASS. (Recorded in decisions.md § "Task 3.3 mutation evidence")
    // - Mutation: changed `isEnabled` for AVX_SAFETY to always return `true` regardless
    //   of `safetyEnabled`. Result: test_isEnabled_returns_safetyEnabled_for_AVX_SAFETY FAILED.
    //   Restored: PASS. (Recorded in decisions.md § "Task 3.3 mutation evidence")
}
