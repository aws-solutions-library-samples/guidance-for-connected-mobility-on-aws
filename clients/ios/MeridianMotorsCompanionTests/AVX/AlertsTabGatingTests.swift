import SwiftUI
import UIKit
import XCTest
@testable import MeridianMotorsCompanion

/// The Alerts tab shows Agent Findings whatever the vehicle loads do, stops
/// loading once those loads have finished (success, failure, or no vehicle), and
/// never asks about the demo vehicle for a signed-in user.
///
/// CMS issue `2026-09-27-ios-alerts-tab-stuck-without-vehicle-context` (CVX spec
/// `2026-09-25-avx-own-vehicle-findings`, Fix Group 6, found by T3.2 check 2): a
/// `vehicle-owner` user with no CMS driver row got `currentVehicle == nil`,
/// `effectiveVehicleId` fell back to `VEH-0025`, whose context read returned 404;
/// `vehicleContextLoadedAt` was set only on success, and the whole tab, Agent
/// Findings included, rendered only when it was set. So: a permanent skeleton.
///
/// Two layers: `AppSession` tests for the state rules, and hosted tests that put
/// the real `AlertsTabView` in a window and watch which requests it makes. The
/// hosted tests are the only ones that see where `AVXCardsTab` sits relative to
/// the loading gate: its `.task` runs, and so requests `/findings/me`, only when
/// the view is on screen.
///
/// Mutations run against this file are listed in the CVX spec's `decisions.md`
/// (Fix Group 6 entry).
@MainActor
final class AlertsTabGatingTests: XCTestCase {

    private let sub = "5d0c2a51-7f3e-4b8e-9a61-2c7a0e1f4b10"
    private let email = "t32.pub002@example.com"
    private let vehicleId = "VEH-T31-0001"

    private var window: UIWindow?

    override func setUp() {
        super.setUp()
        AlertsTabStub.reset()
    }

    override func tearDown() {
        // Removing the host cancels the tab's polling `.task`.
        window?.rootViewController = nil
        window?.isHidden = true
        window = nil
        AlertsTabStub.reset()
        super.tearDown()
    }

    // MARK: - AppSession: no vehicle

    /// The T3.2 check 2 user: `/drivers/me` answers with no driver and no vehicle.
    func test_noVehicle_skipsEveryVehicleRead_endsLoading_andNeverAsksTheDemoVehicle() async {
        let session = signedInSession()
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: nil))]
        let client = Self.stubClient()

        await session.loadCurrentDriver(client: client)
        XCTAssertEqual(session.vehicleResolution, .noVehicle)
        XCTAssertNil(session.effectiveVehicleId, "no demo fallback for a signed-in user")

        await session.loadVehicleContext(client: client, force: true)
        await session.loadSafetyEvents(client: client, force: true)
        await session.loadLiveState(client: client, force: true)
        await session.loadRecentTrips(client: client, force: true)
        await session.loadServiceHistory(client: client, force: true)

        XCTAssertTrue(session.hasLoadedInitialAlerts, "no vehicle is a finished attempt, not a spinner")
        XCTAssertEqual(session.faultsSectionState, .noVehicle)
        XCTAssertEqual(session.safetySectionState, .noVehicle)
        XCTAssertTrue(session.activeDtcs.isEmpty)
        let paths = AlertsTabStub.requestedURLs.map(\.path)
        XCTAssertEqual(paths.filter { $0.contains("/vehicles/") }, [], "no vehicle, no vehicle request")
        for url in AlertsTabStub.requestedURLs {
            XCTAssertFalse(url.absoluteString.contains(VSAConfig.demoVehicleId),
                           "the demo vehicle reached \(url.absoluteString)")
        }
    }

    /// Context from an earlier vehicle must not survive into a no-vehicle state
    /// (it would badge and list that vehicle's faults).
    func test_noVehicle_dropsContextFromAnEarlierVehicle() async throws {
        let session = signedInSession()
        let client = Self.stubClient()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/context": .json(200, Self.context(vehicleId: vehicleId, dtcCodes: ["P0217"])),
        ]
        await session.loadCurrentDriver(client: client)
        await session.loadVehicleContext(client: client, force: true)
        XCTAssertEqual(session.activeDtcs.map(\.code), ["P0217"])

        AlertsTabStub.routes["/drivers/me"] = .json(200, Self.driversMe(vehicleId: nil))
        await session.loadCurrentDriver(client: client, force: true)
        await session.loadVehicleContext(client: client, force: true)
        XCTAssertTrue(session.activeDtcs.isEmpty)
        XCTAssertNil(session.vehicleContext)
        XCTAssertEqual(session.faultsSectionState, .noVehicle)
    }

    // MARK: - AppSession: the context read fails

    func test_contextReadFails_endsLoading_andIsNeverAllClear() async {
        let session = signedInSession()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/context": .json(500, Data("{}".utf8)),
            "/vehicles/\(vehicleId)/safety-events": .json(200, Self.safetyEvents(vehicleId: vehicleId)),
        ]
        let client = Self.stubClient()
        await session.loadCurrentDriver(client: client)
        await session.loadVehicleContext(client: client, force: true)
        await session.loadSafetyEvents(client: client, force: true)

        XCTAssertTrue(session.hasLoadedInitialAlerts, "a failed context read ends the loading state")
        XCTAssertNil(session.vehicleContextLoadedAt, "the success stamp stays success-only")
        guard case .failed = session.faultsSectionState else {
            return XCTFail("a failed read must not render as all clear; got \(session.faultsSectionState)")
        }
        XCTAssertEqual(session.safetySectionState, .loaded, "safety events did load")
    }

    /// Control: the same vehicle with a successful read is `.loaded`, so the
    /// failure test above is not passing because nothing is ever `.loaded`.
    func test_contextReadSucceeds_isLoaded() async {
        let session = signedInSession()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/context": .json(200, Self.context(vehicleId: vehicleId, dtcCodes: [])),
            "/vehicles/\(vehicleId)/safety-events": .json(200, Self.safetyEvents(vehicleId: vehicleId)),
        ]
        let client = Self.stubClient()
        await session.loadCurrentDriver(client: client)
        await session.loadVehicleContext(client: client, force: true)
        await session.loadSafetyEvents(client: client, force: true)
        XCTAssertTrue(session.hasLoadedInitialAlerts)
        XCTAssertEqual(session.faultsSectionState, .loaded)
        XCTAssertEqual(session.safetySectionState, .loaded)
        XCTAssertTrue(AlertsTabStub.requestedURLs.contains { $0.path.hasSuffix("/vehicles/\(vehicleId)/context") })
    }

    // MARK: - AppSession: the driver lookup fails or hasn't answered

    func test_driverLookupFails_endsLoading_asFailed() async {
        let session = signedInSession()
        AlertsTabStub.routes = ["/drivers/me": .json(500, Data("{}".utf8))]
        let client = Self.stubClient()
        await session.loadCurrentDriver(client: client)
        XCTAssertEqual(session.vehicleResolution, .failed)
        await session.loadVehicleContext(client: client, force: true)
        await session.loadSafetyEvents(client: client, force: true)

        XCTAssertTrue(session.hasLoadedInitialAlerts)
        guard case .failed = session.faultsSectionState else {
            return XCTFail("got \(session.faultsSectionState)")
        }
        guard case .failed = session.safetySectionState else {
            return XCTFail("got \(session.safetySectionState)")
        }
        XCTAssertFalse(AlertsTabStub.requestedURLs.contains { $0.path.contains("/vehicles/") })
    }

    /// Before `/drivers/me` answers there is nothing to attempt yet: the loaders
    /// neither request anything nor end the loading state.
    func test_driverLookupPending_isNotAnAttempt() async {
        let session = signedInSession()
        let client = Self.stubClient()
        XCTAssertEqual(session.vehicleResolution, .pending)
        await session.loadVehicleContext(client: client, force: true)
        await session.loadSafetyEvents(client: client, force: true)
        XCTAssertNil(session.vehicleContextAttemptedAt)
        XCTAssertNil(session.safetyEventsLoadedAt)
        XCTAssertFalse(session.hasLoadedInitialAlerts)
        XCTAssertEqual(AlertsTabStub.requestedURLs, [])
    }

    func test_signOut_clearsTheAttemptStamp() async {
        let session = signedInSession()
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: nil))]
        let client = Self.stubClient()
        await session.loadCurrentDriver(client: client)
        await session.loadVehicleContext(client: client, force: true)
        XCTAssertNotNil(session.vehicleContextAttemptedAt)
        await session.signOut()
        XCTAssertNil(session.vehicleContextAttemptedAt, "the next user must not inherit a finished load")
    }

    func test_noVehicleCopy_doesNotClaimAllClear() {
        let faults = AlertsTabView.noVehicleText(subject: "vehicle faults")
        let safety = AlertsTabView.noVehicleText(subject: "safety events")
        for text in [faults, safety] {
            XCTAssertTrue(text.contains("couldn't find a vehicle"), text)
            for claim in ["all clear", "No active", "No events"] {
                XCTAssertFalse(text.localizedCaseInsensitiveContains(claim), "\(claim) in \(text)")
            }
        }
        XCTAssertTrue(faults.hasPrefix("Vehicle faults"), faults)
    }

    // MARK: - Review cycle 1 (2026-09-27): no claim without a read for this vehicle

    /// C1: a driver refresh must not replace a real context with the warm stub, so a
    /// vehicle with a fault never reads as all clear.
    func test_driverRefresh_keepsTheRealContext_andAFailedReadNeverBecomesAllClear() async {
        let session = signedInSession()
        let client = Self.stubClient()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/context": .json(200, Self.context(vehicleId: vehicleId, dtcCodes: ["P0217"])),
        ]
        await session.loadCurrentDriver(client: client)
        await session.loadVehicleContext(client: client, force: true)
        XCTAssertEqual(session.activeDtcs.map(\.code), ["P0217"])

        await session.loadCurrentDriver(client: client, force: true)
        XCTAssertEqual(session.activeDtcs.map(\.code), ["P0217"], "the warm stub replaced a real read")

        AlertsTabStub.routes["/vehicles/\(vehicleId)/context"] = .json(500, Data("{}".utf8))
        await session.loadVehicleContext(client: client, force: true)
        let body = AlertsTabView.faultsBody(state: session.faultsSectionState,
                                            all: session.activeDtcs, filtered: session.activeDtcs)
        XCTAssertNotEqual(body, .allClear, "a vehicle with P0217 read as all clear")
        XCTAssertEqual(session.activeDtcs.map(\.code), ["P0217"])
    }

    /// C1: vehicle A's successful read says nothing about vehicle B.
    /// Also pins `vehicleContextLoadedFor` on its own: however `currentVehicle` comes
    /// to change, A's success stamp must not make B "loaded".
    func test_anotherVehiclesRead_isNeverThisVehiclesAllClear() async throws {
        let session = signedInSession()
        let client = Self.stubClient()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/context": .json(200, Self.context(vehicleId: vehicleId, dtcCodes: [])),
        ]
        await session.loadCurrentDriver(client: client)
        await session.loadVehicleContext(client: client, force: true)
        XCTAssertNotNil(session.vehicleContextLoadedAt)
        session.currentVehicle = try JSONDecoder().decode(
            VehicleInfo.self, from: Data(#"{"vehicleId":"VEH-T31-0003"}"#.utf8))
        XCTAssertEqual(session.faultsSectionState, .loading)
    }

    /// C1: vehicle A's successful read says nothing about vehicle B.
    func test_reassignedVehicle_isNotAllClearOnTheEarlierVehiclesRead() async {
        let session = signedInSession()
        let client = Self.stubClient()
        let other = "VEH-T31-0002"
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/context": .json(200, Self.context(vehicleId: vehicleId, dtcCodes: [])),
            "/vehicles/\(other)/context": .json(500, Data("{}".utf8)),
        ]
        await session.loadCurrentDriver(client: client)
        await session.loadVehicleContext(client: client, force: true)
        XCTAssertEqual(session.faultsSectionState, .loaded)

        AlertsTabStub.routes["/drivers/me"] = .json(200, Self.driversMe(vehicleId: other))
        await session.loadCurrentDriver(client: client, force: true)
        XCTAssertEqual(session.faultsSectionState, .loading, "B has not been read")
        await session.loadVehicleContext(client: client, force: true)
        guard case .failed = session.faultsSectionState else {
            return XCTFail("got \(session.faultsSectionState)")
        }
    }

    /// C2 (1): the no-vehicle attempt stamp is not a read for the vehicle the user
    /// then claims.
    func test_noVehicleThenAVehicle_safetyIsNotLoadedUntilRead() async {
        let session = signedInSession()
        let client = Self.stubClient()
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: nil))]
        await session.loadCurrentDriver(client: client)
        await session.loadSafetyEvents(client: client, force: true)
        XCTAssertNotNil(session.safetyEventsLoadedAt)

        AlertsTabStub.routes["/drivers/me"] = .json(200, Self.driversMe(vehicleId: vehicleId))
        await session.loadCurrentDriver(client: client, force: true)
        XCTAssertEqual(session.safetySectionState, .loading)
        XCTAssertEqual(AlertsTabView.safetyBody(state: session.safetySectionState, events: session.safetyEvents),
                       .notice(.loading))
    }

    /// C2 (2): a failed read for a vehicle is `.failed`, never "No events".
    func test_safetyReadFails_isFailed_notNoEvents() async {
        let session = signedInSession()
        let client = Self.stubClient()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/safety-events": .json(500, Data("{}".utf8)),
        ]
        await session.loadCurrentDriver(client: client)
        await session.loadSafetyEvents(client: client, force: true)
        guard case .failed = session.safetySectionState else {
            return XCTFail("got \(session.safetySectionState)")
        }
        XCTAssertNotEqual(AlertsTabView.safetyBody(state: session.safetySectionState, events: []), .noEvents)
    }

    /// C2 (3): while a retry after a failure is in flight, still not "No events".
    func test_safetyRetryInFlight_afterAFailure_isNotNoEvents() async throws {
        let session = signedInSession()
        let client = Self.stubClient()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/safety-events": .json(500, Data("{}".utf8)),
        ]
        await session.loadCurrentDriver(client: client)
        await session.loadSafetyEvents(client: client, force: true)
        AlertsTabStub.routes["/vehicles/\(vehicleId)/safety-events"] = .hang
        let retry = Task { await session.loadSafetyEvents(client: client, force: true) }
        let inFlight = await waitUntil(timeout: 5) { session.safetyEventsLoading }
        XCTAssertTrue(inFlight)
        XCTAssertNotEqual(session.safetySectionState, .loaded)
        XCTAssertNotEqual(AlertsTabView.safetyBody(state: session.safetySectionState, events: session.safetyEvents),
                          .noEvents)
        retry.cancel()
        _ = await retry.value
    }

    // MARK: - Review cycle 1: the view's body choice (W1)

    func test_faultsBody_noClaimWithoutALoad() {
        let dtc = try! JSONDecoder().decode(ActiveDtc.self, from: Data(#"{"code":"P0217","status":"ACTIVE","severity":"HIGH"}"#.utf8))
        for state: AppSession.VehicleSectionState in [.loading, .noVehicle, .failed("x"), .failed(nil)] {
            XCTAssertEqual(AlertsTabView.faultsBody(state: state, all: [], filtered: []), .notice(state))
            XCTAssertEqual(AlertsTabView.faultsBody(state: state, all: [dtc], filtered: [dtc]), .notice(state))
        }
        XCTAssertEqual(AlertsTabView.faultsBody(state: .loaded, all: [], filtered: []), .allClear)
        XCTAssertEqual(AlertsTabView.faultsBody(state: .loaded, all: [dtc], filtered: []), .filterEmpty(other: 1))
        XCTAssertEqual(AlertsTabView.faultsBody(state: .loaded, all: [dtc], filtered: [dtc]), .rows)
    }

    func test_safetyBody_noClaimWithoutALoad() {
        for state: AppSession.VehicleSectionState in [.loading, .noVehicle, .failed("x"), .failed(nil)] {
            XCTAssertEqual(AlertsTabView.safetyBody(state: state, events: []), .notice(state))
        }
        XCTAssertEqual(AlertsTabView.safetyBody(state: .loaded, events: []), .noEvents)
    }

    // MARK: - Review cycle 1: booking, other tabs, identity, telemetry (W2, W4, S1)

    func test_bookRequest_refusesWithoutTheUsersOwnVehicle() async throws {
        let draft = BookingDraft(capability: .brakes, center: try Self.center(), slot: "Tue 1pm")
        let session = signedInSession()
        let client = Self.stubClient()
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: nil))]
        await session.loadCurrentDriver(client: client)
        XCTAssertNil(BookingFlow.makeBookRequest(session: session, draft: draft), "no vehicle: no booking")

        AlertsTabStub.routes["/drivers/me"] = .json(200, Self.driversMe(vehicleId: vehicleId, vin: "", driverId: "DRV-T31"))
        await session.loadCurrentDriver(client: client, force: true)
        XCTAssertNil(BookingFlow.makeBookRequest(session: session, draft: draft), "no VIN on record: no demo VIN")

        AlertsTabStub.routes["/drivers/me"] = .json(200, Self.driversMe(vehicleId: vehicleId, vin: "VINT31000000000001", driverId: "DRV-T31"))
        await session.loadCurrentDriver(client: client, force: true)
        let req = try XCTUnwrap(BookingFlow.makeBookRequest(session: session, draft: draft))
        XCTAssertEqual(req.vehicleId, vehicleId)
        XCTAssertEqual(req.vin, "VINT31000000000001")
        XCTAssertEqual(req.driverId, "DRV-T31")
        for demo in [VSAConfig.demoVehicleId, VSAConfig.demoVin, VSAConfig.demoDriverId] {
            XCTAssertFalse([req.vehicleId, req.vin, req.driverId].contains(demo))
        }
    }

    func test_vehicleTabs_showANoticeNotASkeleton_withNoVehicle() async {
        let session = signedInSession()
        XCTAssertEqual(session.vehicleTabGate(loaded: false), .loading, "pending")
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: nil))]
        await session.loadCurrentDriver(client: Self.stubClient())
        XCTAssertEqual(session.vehicleTabGate(loaded: false), .noVehicle)
        XCTAssertEqual(session.vehicleTabGate(loaded: session.hasLoadedInitialVehicle), .noVehicle)
        XCTAssertEqual(session.vehicleTabGate(loaded: session.hasLoadedInitialService), .noVehicle)
    }

    /// Review cycle 2 W: the gate must still load and show a user's own vehicle.
    func test_vehicleTabs_loadAndShow_withAVehicle() async {
        let session = signedInSession()
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId))]
        await session.loadCurrentDriver(client: Self.stubClient())
        XCTAssertEqual(session.vehicleTabGate(loaded: false), .loading)
        XCTAssertEqual(session.vehicleTabGate(loaded: true), .ready)
    }

    func test_acquireIdentity_isTheUsersOwnNeverTheDemoPair() async {
        let session = signedInSession()
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: nil))]
        await session.loadCurrentDriver(client: Self.stubClient())
        let identity = ReviewStep.ownIdentity(session: session)
        XCTAssertNil(identity.actorId)
        XCTAssertNil(identity.vehicleHint)

        AlertsTabStub.routes["/drivers/me"] = .json(
            200, Self.driversMe(vehicleId: vehicleId, vin: "VINT31000000000001", driverId: "DRV-T31"))
        await session.loadCurrentDriver(client: Self.stubClient(), force: true)
        let own = ReviewStep.ownIdentity(session: session)
        XCTAssertEqual(own.actorId, "DRV-T31")
        XCTAssertEqual(own.vehicleHint, "VINT31000000000001")

        // A driver record with no vehicle: the driver's own id, and no demo VIN
        // (review cycle 3 S1).
        AlertsTabStub.routes["/drivers/me"] = .json(200, Self.driversMe(vehicleId: nil, driverId: "DRV-T31"))
        await session.loadCurrentDriver(client: Self.stubClient(), force: true)
        let driverOnly = ReviewStep.ownIdentity(session: session)
        XCTAssertEqual(driverOnly.actorId, "DRV-T31")
        XCTAssertNil(driverOnly.vehicleHint)
    }

    func test_telemetrySocket_notOpenedWithoutAVehicle() async {
        let session = signedInSession()
        AlertsTabStub.routes = ["/drivers/me": .json(200, Self.driversMe(vehicleId: nil))]
        await session.loadCurrentDriver(client: Self.stubClient())
        session.connectWebSocket(token: "t")
        XCTAssertNil(session.wsClient)
    }

    // MARK: - Hosted AlertsTabView

    /// `/drivers/me` never answers, so the vehicle loads cannot start and the gate
    /// stays shut. Agent Findings must render anyway.
    func test_hosted_findingsRender_whileTheVehicleLoadsCannotStart() async throws {
        let session = signedInSession()
        AlertsTabStub.routes = [
            "/drivers/me": .hang,
            "/findings/me": .json(200, try Self.findingsMeBody()),
            "/actions/owner/\(sub)": .json(200, Data(#"{"actions":[]}"#.utf8)),
        ]
        try host(session)

        let asked = await waitUntil { AlertsTabStub.requested("/findings/me") }
        XCTAssertTrue(asked, "Agent Findings must render while the vehicle loads are stuck; requests: \(AlertsTabStub.paths)")
        XCTAssertFalse(session.hasLoadedInitialAlerts, "precondition: the gate is still shut")
    }

    /// The T3.2 check 2 user end to end: the tab leaves the skeleton, loads
    /// Findings, and never names the demo vehicle.
    func test_hosted_noVehicle_leavesTheSkeleton_andNeverAsksTheDemoVehicle() async throws {
        let session = signedInSession()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: nil)),
            "/findings/me": .json(200, try Self.findingsMeBody()),
            "/actions/owner/\(sub)": .json(200, Data(#"{"actions":[]}"#.utf8)),
        ]
        try host(session)

        let done = await waitUntil { session.hasLoadedInitialAlerts && AlertsTabStub.requested("/findings/me") }
        XCTAssertTrue(done, "requests: \(AlertsTabStub.paths)")
        XCTAssertEqual(session.faultsSectionState, .noVehicle)
        XCTAssertFalse(AlertsTabStub.requestedURLs.contains { $0.path.contains("/vehicles/") },
                       "requests: \(AlertsTabStub.paths)")
        XCTAssertFalse(AlertsTabStub.requestedURLs.contains { $0.absoluteString.contains(VSAConfig.demoVehicleId) })
    }

    /// The vehicle resolves but its context read fails: the tab still leaves the
    /// skeleton, and the faults section is `.failed`, not "all clear".
    func test_hosted_contextReadFails_leavesTheSkeleton() async throws {
        let session = signedInSession()
        AlertsTabStub.routes = [
            "/drivers/me": .json(200, Self.driversMe(vehicleId: vehicleId)),
            "/vehicles/\(vehicleId)/context": .json(404, Data(#"{"message":"not found"}"#.utf8)),
            "/vehicles/\(vehicleId)/safety-events": .json(200, Self.safetyEvents(vehicleId: vehicleId)),
            "/findings/me": .json(200, try Self.findingsMeBody()),
            "/actions/owner/\(sub)": .json(200, Data(#"{"actions":[]}"#.utf8)),
        ]
        try host(session)

        let done = await waitUntil {
            session.hasLoadedInitialAlerts && AlertsTabStub.requested("/findings/me")
        }
        XCTAssertTrue(done, "requests: \(AlertsTabStub.paths)")
        guard case .failed = session.faultsSectionState else {
            return XCTFail("got \(session.faultsSectionState)")
        }
    }

    // MARK: - Helpers

    private func signedInSession() -> AppSession {
        let session = AppSession()
        session.authState = .signedIn(
            idToken: Self.jwt(["sub": sub, "email": email, "custom:vehicleId": "VEH-DEMO-PUB-002"]),
            email: email)
        session.makeClient = { provider in
            VSAClient(idTokenProvider: provider, session: Self.stubURLSession())
        }
        return session
    }

    private func host(_ session: AppSession) throws {
        let scene = try XCTUnwrap(
            UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first,
            "the test host has no window scene")
        let root = AlertsTabView(theme: .fallback, dtcFilter: .constant(.critical))
            .environment(session)
        let window = UIWindow(windowScene: scene)
        window.frame = CGRect(x: 0, y: 0, width: 402, height: 874)
        window.rootViewController = UIHostingController(rootView: root)
        window.makeKeyAndVisible()
        self.window = window
    }

    private func waitUntil(timeout: TimeInterval = 10, _ condition: () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return true }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return condition()
    }

    private static func stubURLSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [AlertsTabStub.self]
        return URLSession(configuration: config)
    }

    private static func stubClient() -> VSAClient {
        VSAClient(idTokenProvider: { "t" }, session: stubURLSession())
    }

    private static func driversMe(vehicleId: String?, vin: String? = nil, driverId: String? = nil) -> Data {
        var body: [String: Any] = ["email": "t32.pub002@example.com",
                                   "generatedAt": "2026-09-27T12:00:00Z"]
        body["driver"] = driverId.map { ["driverId": $0] as [String: Any] } ?? NSNull()
        body["vehicle"] = vehicleId.map { id -> [String: Any] in
            var v: [String: Any] = ["vehicleId": id]
            if let vin { v["vin"] = vin }
            return v
        } ?? NSNull()
        return try! JSONSerialization.data(withJSONObject: body)
    }

    private static func center() throws -> ServiceCenter {
        try JSONDecoder().decode(ServiceCenter.self, from: Data(#"""
        {"centerId":"SC-1","name":"X Motors","type":"dealer","address":"1 Main St",
         "fleetDiscount":false,"brandsServiced":[],"nextAvailableSlots":["Tue 1pm"]}
        """#.utf8))
    }

    private static func context(vehicleId: String, dtcCodes: [String]) -> Data {
        try! JSONSerialization.data(withJSONObject: [
            "vehicleId": vehicleId,
            "vehicle": ["vehicleId": vehicleId],
            "activeDtcs": dtcCodes.map { ["code": $0, "status": "ACTIVE", "severity": "CRITICAL"] },
            "generatedAt": "2026-09-27T12:00:00Z",
        ] as [String: Any])
    }

    private static func safetyEvents(vehicleId: String) -> Data {
        try! JSONSerialization.data(withJSONObject: [
            "vehicleId": vehicleId, "windowDays": 7, "events": [],
            "generatedAt": "2026-09-27T12:00:00Z",
        ] as [String: Any])
    }

    /// The deployed handler's answer for `VEH-DEMO-PUB-002` (see `AVXCardsOwnerIdTests`).
    private static func findingsMeBody() throws -> Data {
        try Data(contentsOf: URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Fixtures/avx/samples_local/findings_me_response_VEH-DEMO-PUB-002.json"))
    }

    private static func jwt(_ claims: [String: String]) -> String {
        func b64url(_ d: Data) -> String {
            d.base64EncodedString()
                .replacingOccurrences(of: "+", with: "-")
                .replacingOccurrences(of: "/", with: "_")
                .replacingOccurrences(of: "=", with: "")
        }
        let header = b64url(#"{"alg":"none"}"#.data(using: .utf8)!)
        let payload = b64url(try! JSONSerialization.data(withJSONObject: claims))
        return "\(header).\(payload).sig"
    }
}

/// Answers by path suffix, records every URL, and can leave a request hanging.
/// Anything unrouted is a 404.
final class AlertsTabStub: URLProtocol {
    enum Route {
        case json(Int, Data)
        /// Never answers (until the session cancels the task).
        case hang
    }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var _routes: [String: Route] = [:]
    nonisolated(unsafe) private static var _requestedURLs: [URL] = []

    static var routes: [String: Route] {
        get { lock.lock(); defer { lock.unlock() }; return _routes }
        set { lock.lock(); _routes = newValue; lock.unlock() }
    }

    static var requestedURLs: [URL] {
        lock.lock(); defer { lock.unlock() }; return _requestedURLs
    }

    static var paths: [String] { requestedURLs.map(\.path) }

    static func requested(_ suffix: String) -> Bool {
        requestedURLs.contains { $0.path.hasSuffix(suffix) }
    }

    static func reset() {
        lock.lock(); _routes = [:]; _requestedURLs = []; lock.unlock()
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let url = request.url!
        AlertsTabStub.lock.lock()
        AlertsTabStub._requestedURLs.append(url)
        let route = AlertsTabStub._routes.first { url.path.hasSuffix($0.key) }?.value
        AlertsTabStub.lock.unlock()
        switch route {
        case .hang:
            return
        case .json(let status, let body):
            respond(status: status, body: body, url: url)
        case nil:
            respond(status: 404, body: Data(), url: url)
        }
    }

    private func respond(status: Int, body: Data, url: URL) {
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
