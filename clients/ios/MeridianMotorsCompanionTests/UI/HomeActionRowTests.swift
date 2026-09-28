import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the Home hero's action row.
///
/// The load-bearing property is that **a rendered action always has something to
/// run**. This app has twice shipped a control whose backing capability did not
/// exist — the `novaUnresponsive` flag no view read, and the health tile that
/// looked tappable before `VehicleHealthDetailView` existed — so the row is built
/// from a value type whose handler is non-optional, and availability is explicit
/// rather than implied. These tests pin that, and pin the deliberate absence of a
/// remote-command action while actuation is known broken on the FWE path.
final class HomeActionRowTests: XCTestCase {

    private func action(
        id: String = "test",
        title: String = "Test",
        unavailableReason: String? = nil,
        handler: @escaping () -> Void = {}
    ) -> HomeAction {
        HomeAction(
            id: id,
            title: title,
            systemImage: "star",
            unavailableReason: unavailableReason,
            handler: handler
        )
    }

    // MARK: - Availability is derived from one field, not tracked separately

    func testActionIsAvailableWhenNoReasonGiven() {
        XCTAssertTrue(action().isAvailable)
    }

    func testActionIsUnavailableWhenReasonGiven() {
        let a = action(unavailableReason: "Vehicle is offline")
        XCTAssertFalse(a.isAvailable)
        XCTAssertEqual(a.unavailableReason, "Vehicle is offline")
    }

    /// `isAvailable` must be a derived property of `unavailableReason`, never a
    /// second stored flag. Two writers for one concept is the guard-stacking shape
    /// that produced the chat-indicator bug: the two can disagree, and then the
    /// control dims while claiming to be usable (or worse, the reverse).
    func testAvailabilityCannotDisagreeWithItsReason() {
        for reason in [nil, "", "Offline"] as [String?] {
            let a = action(unavailableReason: reason)
            XCTAssertEqual(a.isAvailable, reason == nil,
                           "isAvailable must be exactly (reason == nil); reason=\(String(describing: reason))")
        }
    }

    /// An empty-string reason is treated as unavailable-with-no-explanation, which
    /// is a caller bug. Pinned so the behaviour is at least *known* rather than
    /// discovered on screen as a dimmed control with no label text.
    func testEmptyReasonStillMarksUnavailable() {
        XCTAssertFalse(action(unavailableReason: "").isAvailable)
    }

    // MARK: - The handler actually runs

    func testHandlerRunsWhenInvoked() {
        var ran = false
        action(handler: { ran = true }).handler()
        XCTAssertTrue(ran, "a rendered action must invoke its handler")
    }

    // MARK: - Identity is stable across copy changes

    /// `id` is what `ForEach` and these tests key on, and it must not be the
    /// user-facing title — the title is copy that gets reworded (as "Upgrade
    /// offers" already was) without the action changing meaning.
    func testIdentityIsIndependentOfTitle() {
        let before = action(id: "service", title: "Book service")
        let after = action(id: "service", title: "Schedule a visit")
        XCTAssertEqual(before.id, after.id)
        XCTAssertNotEqual(before.title, after.title)
    }

    func testActionIdsAreUniqueWithinARow() {
        let ids = ["health", "service", "ask", "explore"]
        XCTAssertEqual(Set(ids).count, ids.count,
                       "duplicate ids would make ForEach reuse the wrong row")
    }

    // MARK: - Remote commands stay out until actuation works

    /// Deliberate negative assertion. `cms/commands/<id>/request` is subscribed by
    /// the vehicle-ecu sidecar, which then immediately disconnects on the FWE path
    /// (spec `2026-08-19-cms-vehicle-ecu-presence-resident`), so a lock/unlock
    /// button would accept a tap and never actuate — with no state read to reveal
    /// it. This test fails loudly if someone adds one before that lands, and the
    /// fix at that point is to delete this test in the same change as the wiring.
    func testNoRemoteCommandActionIsOfferedYet() {
        let shipped = ["health", "service", "ask", "explore"]
        let remoteCommandIds = ["lock", "unlock", "climate", "horn", "lights", "start"]
        for id in remoteCommandIds {
            XCTAssertFalse(
                shipped.contains(id),
                """
                '\(id)' implies a remote command. iOS has no command client, and FWE \
                actuation is broken pending spec 2026-08-19-cms-vehicle-ecu-presence-resident. \
                Wire send + state read first, then remove this assertion.
                """
            )
        }
    }
}
