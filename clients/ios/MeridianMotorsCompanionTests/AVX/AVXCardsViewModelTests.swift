import XCTest
@testable import MeridianMotorsCompanion

// MARK: - Fixture helpers

private var samplesDirectory: URL {
    URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()          // AVX/
        .deletingLastPathComponent()          // MeridianMotorsCompanionTests/
        .appendingPathComponent("Fixtures/avx/samples")
}

private func loadFixture(_ name: String) throws -> Data {
    try Data(contentsOf: samplesDirectory.appendingPathComponent(name))
}

private func decodeFixtureFinding(_ name: String) throws -> AvxFinding {
    try JSONDecoder().decode(AvxFinding.self, from: loadFixture(name))
}

private func decodeFixtureAction(_ name: String) throws -> AvxAction {
    try JSONDecoder().decode(AvxAction.self, from: loadFixture(name))
}

// MARK: - Tests

/// Red-phase skeleton for `AVXCardsViewModel` (Task 2.1).
///
/// Wave B (CONTRACT-COUPLED): tests are LIVE — no XCTSkipIf predicate.
/// They will FAIL until Task 2.3 implements the ViewModel's network bodies.
///
/// These tests assert against the real `AVXCardsViewModel` signatures scaffolded
/// in Task 2.2. All four test methods are EXACT per tasks.md Task 2.1 Accept #1.
///
/// ## Why these tests are "red-phase" and what makes them fail
///
/// Task 2.2 scaffolded empty method bodies (// Task 2.3 body). So:
/// - `refresh` never populates `findings`, `actions`, or `cards`
/// - `approve` never inserts an action into `actions`
/// - `dismiss` never transitions an action to `.dismissed`
///
/// The tests below assert the INTENDED post-call state. They fail because the
/// bodies are empty. Task 2.3 makes them green.
///
/// ## VSAClient injection
///
/// `refresh` and `dismiss` take `client: VSAClient` as a parameter (the scaffolded
/// API from Task 2.2). A real VSAClient is constructed with a no-op token provider
/// so the test compiles against the real type; the actual network calls fail (no
/// server) or are never made (empty body). The tests assert the ViewModel's state
/// transitions, not the network traffic — network-layer assertions belong to
/// `VSAClientTests` (Task 2.3+).
@MainActor
final class AVXCardsViewModelTests: XCTestCase {

    // MARK: - Helpers

    /// A VSAClient that will always return `unauthenticated` (nil token).
    /// Sufficient for the test skeleton: once Task 2.3 adds real method bodies,
    /// the VSAClient actor will attempt the call; because the token is nil it will
    /// throw `.unauthenticated`. The test assertions are on ViewModel state AFTER
    /// the call completes, so a throw in the client body is part of the expected
    /// failure mode in the red phase.
    private func makeClient() -> VSAClient {
        VSAClient(idTokenProvider: { nil })
    }

    // MARK: - Test (a): refresh calls both endpoints

    /// Asserts that after `refresh(ownerId:client:)`, the ViewModel's `findings`
    /// and `cards` are populated (non-empty when the server returns data).
    ///
    /// Red-phase expectation: `findings` is `[]` and `cards` is `[]` because the
    /// method body is `// Task 2.3 body`. The XCTAssertFalse on `findings.isEmpty`
    /// FAILS, confirming we're in the red phase.
    ///
    /// What makes it GREEN (Task 2.3): the method body calls
    /// `VSAClient.getMyFindings()` and `VSAClient.getActionsForOwner(_:)`,
    /// populates `findings` + `actions`, and merges into `cards`.
    func test_refresh_calls_findings_and_actions_endpoints() async throws {
        // Arrange: a vanilla ViewModel and a client
        let vm = AVXCardsViewModel()
        let client = makeClient()

        // Precondition: both collections empty before any refresh
        XCTAssertTrue(vm.findings.isEmpty, "precondition: findings must be empty before refresh")
        XCTAssertTrue(vm.actions.isEmpty, "precondition: actions must be empty before refresh")
        XCTAssertTrue(vm.cards.isEmpty, "precondition: cards must be empty before refresh")

        // Act: call refresh — Task 2.3 body will fire both endpoint calls
        await vm.refresh(actionsOwnerId: "owner-usr-4a5b6c7d", client: client)

        // Assert: at minimum, refresh must have attempted to load (isLoading was
        // set and cleared, or the API was hit). In a network-connected environment
        // with the staging API, findings and cards would be populated.
        //
        // For the red-phase this assertion fails because `findings` stays [].
        // For the green-phase, Task 2.3 populates `findings` from the staging API.
        //
        // We assert `isLoading` is false after awaiting (it must be settled regardless
        // of success/failure — a stuck isLoading=true after await is a bug in itself).
        XCTAssertFalse(
            vm.isLoading,
            "isLoading must be false after refresh completes (regardless of success/failure)"
        )

        // The load was attempted: either findings populated (network success)
        // or lastError is set (network failure / unauthenticated). In neither case
        // should both remain in their initial state.
        //
        // RED phase: both stay nil/empty because the body is a stub. This assertion fails.
        let loadAttempted = !vm.findings.isEmpty || vm.lastError != nil
        XCTAssertTrue(
            loadAttempted,
            "refresh must either populate findings OR record a lastError — "
            + "a no-op body (Task 2.2 stub) satisfies neither. "
            + "findings.count=\(vm.findings.count), lastError=\(String(describing: vm.lastError))"
        )
    }

    // MARK: - Test (b): merged list ordering

    /// Asserts that `cards` is sorted by `updatedAt` (ISO-8601) descending after
    /// `refresh` populates both findings and actions.
    ///
    /// Uses the three vendored D4/D5 fixture actions plus the tires finding.
    /// All carry real `computed_at` / `updated_at` timestamps with distinct ordering.
    ///
    /// RED phase: `cards` stays [] (empty body). The assertion `cards.count >= 2`
    /// FAILS, confirming red.
    ///
    /// GREEN phase (Task 2.3): merge returns a populated, sorted list.
    func test_merged_list_orders_by_updated_at_desc() async throws {
        // Load fixtures to confirm the test data is valid (fails fast on fixture drift)
        let finding  = try decodeFixtureFinding("finding_health_tires_valid.json")
        let actionA  = try decodeFixtureAction("action_dms_book_visit_valid.json")
        let actionD4 = try decodeFixtureAction("action_dismissed_addendum_d4_valid.json")

        // Confirm timestamps are distinct (fixture integrity check)
        XCTAssertNotEqual(finding.computedAt, actionA.updatedAt)
        XCTAssertNotEqual(actionA.updatedAt, actionD4.updatedAt)

        // Expected descending order by ISO-8601 timestamp:
        // 1. actionD4  2026-09-18T13:00:00Z  ← latest
        // 2. actionA   2026-09-18T10:15:00Z
        // 3. finding   2026-09-18T02:00:00Z  ← earliest
        XCTAssertGreaterThan(actionD4.updatedAt, actionA.updatedAt, "fixture timestamp ordering")
        XCTAssertGreaterThan(actionA.updatedAt, finding.computedAt, "fixture timestamp ordering")

        let vm = AVXCardsViewModel()
        let client = makeClient()

        // Act: call refresh (Task 2.3 body will populate from API)
        await vm.refresh(actionsOwnerId: "owner-usr-4a5b6c7d", client: client)

        // Assert: cards must be populated and sorted
        // RED phase: cards.count == 0, this assertion FAILS
        XCTAssertGreaterThanOrEqual(
            vm.cards.count, 1,
            "After refresh, cards must be non-empty. "
            + "Task 2.2 stub returns empty — this is the expected red-phase failure."
        )

        // Sort contract: each card's updatedAt must be ≥ the next
        for i in 0..<max(0, vm.cards.count - 1) {
            let current = vm.cards[i].updatedAt
            let next    = vm.cards[i + 1].updatedAt
            XCTAssertGreaterThanOrEqual(
                current, next,
                "cards[\(i)].updatedAt ('\(current)') must be ≥ cards[\(i+1)].updatedAt ('\(next)') "
                + "— list must be ordered by updatedAt descending"
            )
        }
    }

    // MARK: - Test (c): approve routes to findings approve with target

    /// Asserts that `approve(findingId:targetSystem:actionKind:parameters:)` results
    /// in the ViewModel's `actions` containing a new Action derived from the
    /// approval (i.e. the server response was inserted).
    ///
    /// RED phase: `actions` stays [] after `approve` (empty body). The assertion
    /// on `actions.count` FAILS.
    ///
    /// GREEN phase (Task 2.3): `approve` calls `VSAClient.approveFinding`, inserts
    /// the resulting Action, and re-sorts `cards`.
    func test_approve_routes_to_findings_approve_with_target() async throws {
        let vm = AVXCardsViewModel()
        let client = makeClient()

        let findingId = "FIND-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        let targetSystem = AvxTargetSystem.dms
        let actionKind = AvxActionKind.bookCombinedVisit
        let parameters = AvxJSONValue.object([
            "preferred_date": .string("2026-10-01"),
            "dealership_id": .string("dlr-west-coast-001"),
        ])

        // Act
        await vm.approve(
            findingId: findingId,
            targetSystem: targetSystem,
            actionKind: actionKind,
            parameters: parameters
        )

        // Assert: after approve, actions must contain an entry
        // (either the echoed-back action from the server, or an optimistic local insert)
        // RED phase: actions stays [] → this assertion FAILS
        XCTAssertFalse(
            vm.actions.isEmpty,
            "After approve, ViewModel.actions must contain the resulting Action. "
            + "Task 2.2 stub returns nothing — this is the expected red-phase failure. "
            + "Task 2.3 wires VSAClient.approveFinding and inserts the response Action."
        )

        // If an action was inserted, verify it references the finding we approved
        if let insertedAction = vm.actions.first(where: { $0.findingId == findingId }) {
            XCTAssertEqual(
                insertedAction.targetSystem, targetSystem,
                "Inserted action must have the target_system that was passed to approve"
            )
        }

        // cards must also be updated (re-sorted merge includes the new action)
        XCTAssertFalse(
            vm.cards.isEmpty,
            "After approve, cards must be updated to include the new action. "
            + "Task 2.2 stub leaves cards empty — expected red-phase failure."
        )
    }

    // MARK: - Test (d): dismiss routes to /actions/{id}/dismiss (Addendum D4)

    /// Asserts that `dismiss(actionId:client:)` transitions the matching action's
    /// local status to `.dismissed` — NOT `.cancelled`.
    ///
    /// This is the Addendum D4 correctness test. The dismiss endpoint is
    /// `/actions/{id}/dismiss` (a dedicated sub-resource per Addendum D4),
    /// semantically distinct from cancelling an action.
    ///
    /// RED phase: `dismiss` body is empty. No status transition occurs.
    /// The assertion checking status == `.dismissed` FAILS.
    ///
    /// GREEN phase (Task 2.3): `dismiss` calls `VSAClient.dismissAction`,
    /// which POSTs to `/actions/{id}/dismiss`, and locally transitions
    /// the action's status to `.dismissed`.
    func test_dismiss_routes_to_actions_dismiss_and_transitions_to_dismissed_terminal() async throws {
        // Load the D4 fixture — it has status "dismissed" and represents the
        // server's response to a POST /actions/{id}/dismiss call.
        let dismissedAction = try decodeFixtureAction("action_dismissed_addendum_d4_valid.json")
        let actionId = dismissedAction.actionId

        // Verify fixture integrity: the D4 fixture must decode as .dismissed
        XCTAssertEqual(
            dismissedAction.status, .dismissed,
            "Fixture integrity: action_dismissed_addendum_d4_valid.json must have status=dismissed"
        )
        // And .dismissed must NOT equal .cancelled (Addendum D4 model-layer invariant)
        XCTAssertNotEqual(AvxActionStatus.dismissed, AvxActionStatus.cancelled)

        let vm = AVXCardsViewModel()
        let client = makeClient()

        // Act: call dismiss
        await vm.dismiss(actionId: actionId, client: client)

        // Assert 1: the ViewModel must have recorded the dismissal.
        // After dismiss, either:
        //   (a) the action appears in vm.actions with status .dismissed (optimistic update), OR
        //   (b) vm.cards contains an action card with status .dismissed
        //
        // RED phase: dismiss body is empty. Neither (a) nor (b) is satisfied.
        // This XCTFail fires, confirming red phase.

        // Check for the action in the actions array
        let matchingAction = vm.actions.first(where: { $0.actionId == actionId })

        if let action = matchingAction {
            // If the action is present, it MUST have status .dismissed (not .cancelled)
            XCTAssertEqual(
                action.status, .dismissed,
                "After dismiss, the action's local status MUST be .dismissed (Addendum D4). "
                + "A status of .\(action.status.rawValue) is incorrect. "
                + "dismissed ≠ cancelled: /actions/{id}/dismiss is a dedicated endpoint, "
                + "NOT a status update to 'cancelled'."
            )
            XCTAssertNotEqual(
                action.status, .cancelled,
                "After dismiss, status must NOT be .cancelled. "
                + "Addendum D4: dismissed and cancelled are semantically distinct terminal states."
            )
        } else {
            // The action was not inserted — this is the red-phase failure path
            // (empty body means nothing was inserted)
            XCTFail(
                "After dismiss(actionId:\(actionId)), the ViewModel must record the action "
                + "with status=.dismissed in vm.actions or vm.cards. "
                + "Task 2.2 stub leaves both empty — this is the expected red-phase failure. "
                + "Task 2.3 implements the dismiss call and local status transition."
            )
        }
    }
}


/// The Alerts tab reads Agent Findings from `GET /findings/me`, naming no owner.
///
/// History (`issues/2026-09-25-ios-findings-owner-id-is-email/`): until
/// 2026-09-25 the tab sent the sign-in email as the owner ID (403 on every call);
/// `46a1828a` then sent `custom:customerId` and `sub` to `/findings/owner/{id}`,
/// which gave a driver owner standing over every vehicle under their customer
/// (security Cycle 1 Critical). The server now derives standing from trusted
/// claims (CVX spec `2026-09-25-avx-own-vehicle-findings`, T2.1), so the client
/// chooses no identifier for Findings. Actions still go to
/// `/actions/owner/{sub}`, whose scope check refuses any ID but the caller's own.
///
/// "Not linked to a vehicle" is keyed on `standings` alone. An empty `findings`
/// list with a standing is a linked account with nothing to show
/// (`meridian.driver` on 2026-09-26: `[fleet_driver]`, 0 Findings).
///
/// `samples_local/findings_me_response_VEH-DEMO-PUB-002.json` is CVX
/// `_findings_me` run on the two live `VEH-DEMO-PUB-002` rows of
/// `vsa-staging-avx-findings` (2026-09-26), so the decode is pinned to the
/// deployed handler's shape rather than a hand-written one.
///
/// Mutations verified (each fails at least one test here; CVX spec
/// `decisions.md`, T3.1 entry):
///   1. `refresh` also requests `/findings/owner/{sub}` and merges it.
///   2. `actionsOwnerId(for:)` prefers `custom:customerId` over `sub`.
///   3. `notice` says "not linked" when `findings` is empty.
///   4. `standings` optional, a missing field read as `[]`.
///   5. `notice` says "not linked" before any answer (`standings == nil`).
///   6. `truncated` ignored.
@MainActor
final class AVXCardsOwnerIdTests: XCTestCase {

    private let sub = "086183b0-1081-4c2a-9e1f-0123456789ab"
    private let customerId = "CUST-004000DD"
    private let vehicleId = "VEH-MRDN-0015"
    private let email = "meridian.driver@example.com"

    override func setUp() {
        super.setUp()
        OwnerPathStub.responses = [:]
        OwnerPathStub.requestedURLs = []
    }

    // MARK: - Request shape

    /// The tab's real inputs: a session whose token carries `sub`, a customer id,
    /// a vehicle id and the email. Only `sub` may reach a request, and only the
    /// Actions one.
    func test_refresh_requestsFindingsMeOnceWithNoIdentifier() async throws {
        let session = AppSession()
        session.authState = .signedIn(
            idToken: Self.jwt(["sub": sub, "custom:customerId": customerId,
                               "custom:vehicleId": vehicleId, "email": email]),
            email: email)
        let actionsId = try XCTUnwrap(AVXCardsViewModel.actionsOwnerId(for: session))
        OwnerPathStub.responses = [
            "/findings/me": try Self.liveDriverBody(),
            "/actions/owner/\(sub)": Self.emptyActions,
        ]

        let vm = AVXCardsViewModel()
        await vm.refresh(actionsOwnerId: actionsId, client: Self.stubClient())

        XCTAssertNil(vm.lastError, "unexpected error: \(String(describing: vm.lastError))")
        let urls = OwnerPathStub.requestedURLs
        let findingsURLs = urls.filter { $0.path.contains("/findings") }
        XCTAssertEqual(findingsURLs.count, 1, "one Findings request; got \(findingsURLs.map(\.path))")
        let findingsURL = try XCTUnwrap(findingsURLs.first)
        XCTAssertTrue(findingsURL.path.hasSuffix("/findings/me"), "got \(findingsURL.path)")
        XCTAssertNil(findingsURL.query, "the route takes no identifier")
        XCTAssertFalse(findingsURL.absoluteString.contains(sub), "sub must not reach the Findings request")
        for url in urls {
            for claim in [customerId, vehicleId, email] {
                XCTAssertFalse(url.absoluteString.contains(claim), "\(claim) reached \(url.absoluteString)")
            }
        }
        XCTAssertEqual(Set(urls.map { Self.routeSuffix($0.path) }), ["/findings/me", "/actions/owner/\(sub)"])
    }

    func test_actionsOwnerId_isSubNeverCustomerIdOrEmail() {
        let session = AppSession()
        XCTAssertNil(AVXCardsViewModel.actionsOwnerId(for: session), "signed out")
        session.authState = .signedIn(
            idToken: Self.jwt(["sub": sub, "custom:customerId": customerId, "email": email]),
            email: email)
        XCTAssertEqual(AVXCardsViewModel.actionsOwnerId(for: session), sub)
        session.authState = .signedIn(
            idToken: Self.jwt(["custom:customerId": customerId, "email": email]), email: email)
        XCTAssertNil(AVXCardsViewModel.actionsOwnerId(for: session), "no sub: no request, no fallback")
        session.authState = .signedIn(idToken: Self.jwt(["sub": ""]), email: email)
        XCTAssertNil(AVXCardsViewModel.actionsOwnerId(for: session))
    }

    // MARK: - Live response shape

    func test_liveDriverResponse_decodesFindingsAndStandings() async throws {
        let body = try Self.liveDriverBody()
        let decoded = try JSONDecoder().decode(VSAClient.MyFindingsResponse.self, from: body)
        XCTAssertEqual(decoded.standings, ["fleet_driver"])
        XCTAssertEqual(decoded.findings.count, 2)
        XCTAssertEqual(Set((decoded.admittedBy ?? [:]).keys), Set(decoded.findings.map(\.findingId)))
        XCTAssertEqual(decoded.truncated, false)
        XCTAssertFalse(decoded.computedAt?.isEmpty ?? true)

        OwnerPathStub.responses = ["/findings/me": body, "/actions/owner/\(sub)": Self.emptyActions]
        let vm = AVXCardsViewModel()
        await vm.refresh(actionsOwnerId: sub, client: Self.stubClient())
        XCTAssertNil(vm.lastError)
        XCTAssertEqual(vm.standings, ["fleet_driver"])
        XCTAssertEqual(vm.cards.count, 2)
        XCTAssertNil(vm.notice, "two cards and a standing: no note")
        XCTAssertFalse(vm.findingsTruncated)
    }

    // MARK: - "Not linked" is `standings` empty, and nothing else

    /// `meridian.driver`'s live answer on 2026-09-26.
    func test_standingWithNoFindings_isNoFindings_notNotLinked() async throws {
        let vm = try await Self.refreshed(with: Self.body(standings: ["fleet_driver"]), sub: sub)
        XCTAssertNil(vm.lastError)
        XCTAssertEqual(vm.standings, ["fleet_driver"])
        XCTAssertTrue(vm.findings.isEmpty)
        XCTAssertEqual(vm.notice, .noFindings)
    }

    func test_emptyStandings_isNotLinked() async throws {
        let vm = try await Self.refreshed(with: Self.body(standings: []), sub: sub)
        XCTAssertNil(vm.lastError)
        XCTAssertEqual(vm.standings, [])
        XCTAssertEqual(vm.notice, .notLinkedToVehicle)
    }

    /// An answer without `standings` is a failure, never proof of no standing.
    func test_responseWithoutStandings_isALoadFailure_neverNotLinked() async throws {
        let vm = try await Self.refreshed(with: #"{"findings":[]}"#.data(using: .utf8)!, sub: sub)
        XCTAssertNotNil(vm.lastError)
        XCTAssertNil(vm.standings)
        XCTAssertNotEqual(vm.notice, .notLinkedToVehicle)
    }

    func test_beforeAnyAnswer_isNotNotLinked() {
        XCTAssertNotEqual(AVXCardsViewModel().notice, .notLinkedToVehicle)
    }

    func test_truncatedIsSurfaced() async throws {
        let vm = try await Self.refreshed(with: Self.body(standings: ["fleet_driver"], truncated: true), sub: sub)
        XCTAssertTrue(vm.findingsTruncated)
    }

    func test_noticeCopy_onlyNotLinkedMentionsLinking() {
        XCTAssertTrue(AVXCardsTab.text(for: .notLinkedToVehicle).contains("linked to a vehicle"))
        XCTAssertFalse(AVXCardsTab.text(for: .noFindings).contains("linked"))
        XCTAssertFalse(AVXCardsTab.text(for: .loadFailed).contains("linked"))
    }

    // MARK: - Claims

    func test_currentOwnerId_readsSub() {
        let session = AppSession()
        XCTAssertNil(session.currentOwnerId)
        session.authState = .signedIn(idToken: Self.jwt(["sub": sub, "email": email]), email: email)
        XCTAssertEqual(session.currentOwnerId, sub)
    }

    // MARK: - Helpers

    private static let emptyActions = #"{"actions":[]}"#.data(using: .utf8)!

    private static func stubClient() -> VSAClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [OwnerPathStub.self]
        return VSAClient(idTokenProvider: { "t" }, session: URLSession(configuration: config))
    }

    private static func refreshed(with findingsMe: Data, sub: String) async throws -> AVXCardsViewModel {
        OwnerPathStub.responses = ["/findings/me": findingsMe, "/actions/owner/\(sub)": emptyActions]
        let vm = AVXCardsViewModel()
        await vm.refresh(actionsOwnerId: sub, client: stubClient())
        return vm
    }

    private static func body(standings: [String], truncated: Bool = false) -> Data {
        try! JSONSerialization.data(withJSONObject: [
            "findings": [], "admitted_by": [:], "standings": standings,
            "computed_at": "2026-09-26T20:00:00Z", "truncated": truncated,
        ] as [String: Any])
    }

    private static func liveDriverBody() throws -> Data {
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

    private static func routeSuffix(_ path: String) -> String {
        for prefix in ["/findings/", "/actions/"] {
            if let r = path.range(of: prefix) { return String(path[r.lowerBound...]) }
        }
        return path
    }
}

/// Answers by path suffix and records every URL requested.
final class OwnerPathStub: URLProtocol {
    nonisolated(unsafe) static var responses: [String: Data] = [:]
    nonisolated(unsafe) private static var _requestedURLs: [URL] = []
    private static let lock = NSLock()
    static var requestedURLs: [URL] {
        get { lock.lock(); defer { lock.unlock() }; return _requestedURLs }
        set { lock.lock(); _requestedURLs = newValue; lock.unlock() }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let url = request.url!
        OwnerPathStub.lock.lock()
        OwnerPathStub._requestedURLs.append(url)
        OwnerPathStub.lock.unlock()
        let match = OwnerPathStub.responses.first { url.path.hasSuffix($0.key) }
        let status = match == nil ? 404 : 200
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: match?.value ?? Data())
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
