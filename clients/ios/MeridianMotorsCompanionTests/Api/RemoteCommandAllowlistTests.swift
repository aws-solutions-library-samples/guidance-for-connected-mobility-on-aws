import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the remote-command allowlist and status semantics.
///
/// Two properties are load-bearing here, and both exist because of defects this
/// repo has already paid for:
///
/// 1. **No command is offered that cannot actuate.** `GET /api/commands/catalog`
///    advertises 48 actuators; the simulator that must move the signal implements
///    13. A catalog-driven UI would render 37 controls that accept a tap, return
///    HTTP 200, and do nothing.
/// 2. **`SENT` is not success.** The POST responds `SENT` on publish. A dead FWE
///    command path stayed green for six days because publishing was read as an
///    outcome.
final class RemoteCommandAllowlistTests: XCTestCase {

    /// Verified 2026-08-19 against `_ACTUATOR_MAP` + `_TRANSIENT_ACTUATORS` in
    /// `services/simulation/realtime_telemetry_simulator.py`, intersected with the
    /// live catalog response. If the simulator changes, this list — and the
    /// allowlist it guards — must be re-derived rather than assumed.
    private let simulatorImplements: Set<String> = [
        // stateful (_ACTUATOR_MAP)
        "lock_all_doors", "lock_door_frontleft", "lock_door_frontright",
        "lock_door_rearleft", "lock_door_rearright", "trunk_lock",
        "open_charge_door", "remote_start",
        // transient (_TRANSIENT_ACTUATORS)
        "honk_horn", "flash_hazards", "find_my_vehicle", "panic_mode",
        "start_preconditioning"
    ]

    // MARK: - Nothing unactuatable is offered

    func testEveryAllowlistedCommandIsImplementedBySimulator() {
        for name in RemoteCommandAllowlist.all {
            XCTAssertTrue(
                simulatorImplements.contains(name),
                "'\(name)' is allowlisted but has no simulator implementation — it would publish and do nothing"
            )
        }
    }

    func testEveryFeaturedCommandIsAllowlisted() {
        for name in RemoteCommandAllowlist.featured {
            XCTAssertTrue(RemoteCommandAllowlist.isAllowed(name),
                          "featured '\(name)' must be allowlisted")
        }
    }

    /// Commands the catalog advertises but nothing implements must be refused.
    /// Sampled from the 37-command gap.
    func testAdvertisedButUnimplementedCommandsAreRefused() {
        for name in ["set_sunroof", "set_window_frontleft", "set_hvac_mode",
                     "set_charge_limit", "set_valet_mode", "set_geofence",
                     "toggle_interior_lights", "set_immobilizer", "set_speed_limit"] {
            XCTAssertFalse(RemoteCommandAllowlist.isAllowed(name),
                           "'\(name)' is advertised by the catalog but not implemented; offering it ships a dead control")
        }
    }

    /// Trunk remains excluded — but not for the reason first recorded.
    ///
    /// The original note said `lock_trunk` (catalog) and `trunk_lock` (simulator) were the
    /// same intent under two names and neither could be trusted. The simulator now accepts
    /// BOTH (2026-08-21), so that mismatch is closed. What is still missing is a verified
    /// round trip — the bar every other allowlist entry cleared — so trunk stays out until
    /// someone sends it and watches the state change.
    func testTrunkIsExcludedWhileItsNamingDiverges() {
        XCTAssertFalse(RemoteCommandAllowlist.isAllowed("lock_trunk"))
        XCTAssertFalse(RemoteCommandAllowlist.isAllowed("trunk_lock"))
    }

    // MARK: - Transient vs stateful

    func testStatefulAndTransientDoNotOverlap() {
        XCTAssertTrue(
            RemoteCommandAllowlist.stateful
                .intersection(RemoteCommandAllowlist.transient).isEmpty,
            "a command cannot be both stateful and transient — feedback model would be ambiguous"
        )
    }

    func testTransientCommandsAreClassifiedAsSuch() {
        for name in ["find_my_vehicle", "flash_hazards", "panic_mode"] {
            XCTAssertTrue(RemoteCommandAllowlist.isTransient(name))
        }
        XCTAssertFalse(RemoteCommandAllowlist.isTransient("lock_all_doors"))
    }

    // MARK: - SENT is not success

    /// The status returned by a successful POST. It must classify as pending, not
    /// confirmed — this is the assertion that stops "published" reading as "done".
    func testSentStatusIsPendingNotConfirmed() {
        let row = historyEntry(status: "SENT")
        XCTAssertTrue(row.isPending)
        XCTAssertFalse(row.isConfirmed)
        XCTAssertFalse(row.isFailed)
    }

    func testOnlySucceededCountsAsConfirmed() {
        XCTAssertTrue(historyEntry(status: "SUCCEEDED").isConfirmed)
        XCTAssertTrue(historyEntry(status: "succeeded").isConfirmed,
                      "status casing must not change the verdict")
        for other in ["SENT", "PENDING", "IN_PROGRESS", "QUEUED", ""] {
            XCTAssertFalse(historyEntry(status: other).isConfirmed,
                           "'\(other)' must not be reported as confirmed")
        }
    }

    func testFailureStatusesAreRecognised() {
        for s in ["FAILED", "TIMEOUT", "REJECTED"] {
            let row = historyEntry(status: s)
            XCTAssertTrue(row.isFailed, "'\(s)' should be failed")
            XCTAssertFalse(row.isPending)
        }
    }

    /// An unknown status is pending, never confirmed and never failed. If the
    /// backend adds a state, the UI keeps waiting rather than inventing an outcome.
    func testUnknownStatusDegradesToPending() {
        let row = historyEntry(status: "SOME_NEW_BACKEND_STATE")
        XCTAssertTrue(row.isPending)
        XCTAssertFalse(row.isConfirmed)
        XCTAssertFalse(row.isFailed)
    }

    // MARK: - Wire shape

    /// The request value is a String so this client cannot add a third shape to the
    /// `"1"` vs `"1.0"` divergence already tracked in
    /// `issues/2026-08-04-actuator-value-shape-divergence`.
    func testCommandValueIsEncodedAsString() throws {
        let body = SendCommandRequest(commandName: "lock_all_doors", value: "1",
                                      label: "Lock/Unlock All Doors", category: "security")
        let json = try JSONSerialization.jsonObject(
            with: try JSONEncoder().encode(body)) as? [String: Any]
        XCTAssertEqual(json?["value"] as? String, "1")
        XCTAssertNil(json?["value"] as? Bool, "a JSON boolean would be a third value shape")
    }

    private func historyEntry(status: String) -> CommandHistoryEntry {
        CommandHistoryEntry(commandId: "c1", commandName: "lock_all_doors",
                            status: status, issuedAt: nil, value: "1", label: nil)
    }
}
