import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `KioskGuidedAccessMonitor` — Guided Access state observation.
///
/// Tests inject a separate `NotificationCenter` and a controllable checker
/// closure so no system state is touched.
///
/// Per task 7.5 constraints:
/// - The journey must NOT be blocked when Guided Access is off.
/// - Engagement is an operator action — never enabled programmatically here.
@MainActor
final class KioskGuidedAccessMonitorTests: XCTestCase {

    // MARK: - Helpers

    private func makeMonitor(
        gaEnabled: Bool,
        notificationCenter: NotificationCenter = NotificationCenter()
    ) -> KioskGuidedAccessMonitor {
        KioskGuidedAccessMonitor(
            notificationCenter: notificationCenter,
            guidedAccessChecker: { gaEnabled }
        )
    }

    // MARK: - 7.5: Enabled → no hint shown

    func testEnabledShowsNoHint() {
        let monitor = makeMonitor(gaEnabled: true)
        monitor.isKioskModeActive = true
        XCTAssertFalse(monitor.showsUnlockedHint,
                       "When Guided Access is ON, no hint must be shown")
    }

    // MARK: - 7.5: Disabled while in kiosk mode → hint shown

    func testDisabledInKioskModeShowsHint() {
        let monitor = makeMonitor(gaEnabled: false)
        monitor.isKioskModeActive = true
        XCTAssertTrue(monitor.showsUnlockedHint,
                      "When GA is OFF and kiosk is active, hint must be shown")
    }

    // MARK: - 7.5: Disabled but kiosk mode is NOT active → no hint

    func testDisabledOutsideKioskModeNoHint() {
        let monitor = makeMonitor(gaEnabled: false)
        monitor.isKioskModeActive = false
        XCTAssertFalse(monitor.showsUnlockedHint,
                       "Hint must not appear when kiosk mode is not active")
    }

    // MARK: - 7.5: Toggling updates live (via injected NotificationCenter)

    func testTogglingUpdatesLive() {
        // Start with GA enabled
        let nc = NotificationCenter()
        var currentGA = true

        let monitor = KioskGuidedAccessMonitor(
            notificationCenter: nc,
            guidedAccessChecker: { currentGA }
        )
        monitor.isKioskModeActive = true

        // GA on → no hint
        XCTAssertFalse(monitor.showsUnlockedHint)

        // Flip the checker to simulate GA turning off, then post the notification
        currentGA = false
        nc.post(name: UIAccessibility.guidedAccessStatusDidChangeNotification, object: nil)

        // Give the MainActor Task in the observer a chance to run
        let expectation = XCTestExpectation(description: "hint updates after notification")
        Task { @MainActor in
            // The observer schedules a Task { @MainActor in ... }; we need one more
            // run-loop tick to let it execute.
            try? await Task.sleep(nanoseconds: 10_000_000)  // 10ms
            XCTAssertTrue(monitor.showsUnlockedHint,
                          "After GA disabled notification, hint must be shown")
            expectation.fulfill()
        }
        wait(for: [expectation], timeout: 1.0)
    }
}
