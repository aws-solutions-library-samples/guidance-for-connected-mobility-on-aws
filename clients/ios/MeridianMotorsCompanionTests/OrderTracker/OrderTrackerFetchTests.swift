import XCTest
@testable import MeridianMotorsCompanion

// MARK: - Mock order fetcher

/// Hermetic stand-in for `AcquireCatalogClient` used only in tests.
///
/// Configured at construction with a `Result` to return (or throw) on the
/// first call to `fetchOrder(orderId:)`.  Carries no state, makes no network
/// calls.
private struct MockOrderFetcher: OrderFetching {
    private let result: Result<AcquireOrder, Error>

    init(returning order: AcquireOrder) {
        self.result = .success(order)
    }

    init(throwing error: Error) {
        self.result = .failure(error)
    }

    func fetchOrder(orderId: String) async throws -> AcquireOrder {
        switch result {
        case .success(let order): return order
        case .failure(let error): throw error
        }
    }
}

// MARK: - Tests

/// Tests for `OrderTrackerViewModel.fetchOrder(orderId:session:)`.
///
/// Each test exercises the composed integration path (auth-guard → client →
/// `apply(order:)` / `apply(error:)`) via a hermetic `MockOrderFetcher`.
/// No network calls are made.
///
/// Cases:
/// 1. Success → `currentStage` reflects the fetched order; `isLoading` false; `lastError` nil.
/// 2. `AcquireError.http` → `lastError` set; `isLoading` false.
/// 3. Retention-then-clear: failing fetch sets `lastError`; succeeding fetch clears it to nil.
/// 4. `isEndpointUnavailable` → `isLoading` false AND `lastError` nil (deliberate silence).
/// 5. Non-`AcquireError` thrown error → wrapped as `.network(_)` in `lastError`.
/// 6. Auth-guard early return: not `.signedIn` → `isLoading` never true after return.
@MainActor
final class OrderTrackerFetchTests: XCTestCase {

    // MARK: - Private helpers (all @MainActor because the class is @MainActor)

    private func makeSignedInSession(token: String = "test-token") -> AppSession {
        let session = AppSession()
        session.authState = .signedIn(idToken: token, email: "test@example.com")
        return session
    }

    private func makeSignedOutSession() -> AppSession {
        let session = AppSession()
        // authState defaults to .signedOut; explicit assignment for clarity
        session.authState = .signedOut
        return session
    }

    private func makeOrder(
        orderId: String = "ord-001",
        currentStage: String = "paint"
    ) -> AcquireOrder {
        AcquireOrder(
            orderId: orderId,
            tenantId: "test-tenant",
            currentStage: currentStage,
            stages: [],
            modelId: nil, variantId: nil, colorId: nil,
            selectedAccessoryIds: nil, reservationDepositRef: nil,
            dealerRef: nil, vinAssigned: nil,
            createdAt: nil, updatedAt: nil
        )
    }

    private func makeViewModel(fetcher: MockOrderFetcher) -> OrderTrackerViewModel {
        OrderTrackerViewModel(
            initialStage: .orderPlaced,
            orderFetcher: { _ in fetcher }
        )
    }

    // MARK: - Case 1: success path

    /// A successful fetch updates `currentStage`, leaves `isLoading` false, leaves `lastError` nil.
    func testSuccessfulFetchUpdatesStageAndClearsLoadingAndError() async {
        let order = makeOrder(currentStage: "paint")
        let vm = makeViewModel(fetcher: MockOrderFetcher(returning: order))
        let session = makeSignedInSession()

        XCTAssertEqual(vm.currentStage, .orderPlaced, "precondition: starts at orderPlaced")
        XCTAssertFalse(vm.isLoading)
        XCTAssertNil(vm.lastError)

        await vm.fetchOrder(orderId: "ord-001", session: session)

        XCTAssertEqual(vm.currentStage, .paint, "currentStage must reflect the fetched order")
        XCTAssertFalse(vm.isLoading, "isLoading must be false after fetch completes")
        XCTAssertNil(vm.lastError, "lastError must be nil on success")
    }

    // MARK: - Case 2: AcquireError.http → lastError set

    /// An `AcquireError.http` from the client is stored in `lastError`; `isLoading` ends false.
    func testHttpErrorSetsLastErrorAndClearsLoading() async {
        let err = AcquireError.http(status: 403, body: "Forbidden")
        let vm = makeViewModel(fetcher: MockOrderFetcher(throwing: err))
        let session = makeSignedInSession()

        await vm.fetchOrder(orderId: "ord-002", session: session)

        XCTAssertFalse(vm.isLoading, "isLoading must be false after error")
        XCTAssertNotNil(vm.lastError, "lastError must be set on HTTP error")
        if case .http(let status, _) = vm.lastError {
            XCTAssertEqual(status, 403)
        } else {
            XCTFail("lastError should be .http(403, ...) but was \(String(describing: vm.lastError))")
        }
    }

    // MARK: - Case 3: retention-then-clear (security review cycle 4, S4-1)

    /// A failing fetch sets `lastError`.  A subsequent succeeding fetch clears it to nil.
    /// This is the error-retention invariant: success clears what a prior failure set.
    func testSucceedingFetchAfterFailingFetchClearsLastError() async {
        let session = makeSignedInSession()

        // First call: error
        let vm = makeViewModel(fetcher: MockOrderFetcher(throwing: AcquireError.http(status: 500, body: "Server Error")))
        await vm.fetchOrder(orderId: "ord-003", session: session)
        XCTAssertNotNil(vm.lastError, "precondition: lastError set after first failing fetch")

        // Second call: success — replace the fetcher
        let order = makeOrder(currentStage: "qualityControl")
        vm.orderFetcher = { _ in MockOrderFetcher(returning: order) }
        await vm.fetchOrder(orderId: "ord-003", session: session)

        XCTAssertNil(vm.lastError,
                     "A succeeding fetch must clear lastError set by a prior failing fetch")
        XCTAssertEqual(vm.currentStage, .qualityControl)
    }

    // MARK: - Case 4: isEndpointUnavailable → lastError nil (deliberate silence)

    /// `endpointUnavailable` must NOT set `lastError`.
    ///
    /// The original `OrderTrackerView.fetchOrder()` explicitly swallowed this error to
    /// avoid showing an error banner to visitors during the demo window (the `/acquire`
    /// endpoints are not provisioned server-side).  Preserving this behaviour is a
    /// hard constraint.  A test that asserts an error *is* set would enshrine the
    /// opposite of the intended design.
    func testEndpointUnavailableDoesNotSetLastError() async {
        let err = AcquireError.endpointUnavailable("/acquire/orders/ord-004")
        let vm = makeViewModel(fetcher: MockOrderFetcher(throwing: err))
        let session = makeSignedInSession()

        await vm.fetchOrder(orderId: "ord-004", session: session)

        XCTAssertNil(vm.lastError,
                     "endpointUnavailable must not set lastError — deliberate silent swallow per original design")
        XCTAssertFalse(vm.isLoading, "isLoading must be false after endpointUnavailable")
    }

    // MARK: - Case 5: non-AcquireError wrapped as .network(_)

    /// A `URLError` (or any non-`AcquireError`) thrown by the client is wrapped as
    /// `AcquireError.network(_:)` and stored in `lastError`.
    func testNonAcquireErrorIsWrappedAsNetwork() async {
        let urlErr = URLError(.timedOut)
        let vm = makeViewModel(fetcher: MockOrderFetcher(throwing: urlErr))
        let session = makeSignedInSession()

        await vm.fetchOrder(orderId: "ord-005", session: session)

        guard case .network = vm.lastError else {
            XCTFail("URLError should be wrapped as .network(_) in lastError, got \(String(describing: vm.lastError))")
            return
        }
        XCTAssertFalse(vm.isLoading)
    }

    // MARK: - Case 6: auth-guard early return

    /// When `authState` is not `.signedIn`, `fetchOrder` returns immediately without
    /// setting `isLoading` to true (the guard fires before `isLoading = true`).
    /// This matches the original view's behaviour: unauthenticated calls are silently ignored.
    func testAuthGuardEarlyReturnDoesNotLeaveIsLoadingTrue() async {
        // The mock fetcher would succeed, but the auth guard must prevent it from
        // being reached.  We use a signedOut session.
        let order = makeOrder(currentStage: "shipped")
        let vm = makeViewModel(fetcher: MockOrderFetcher(returning: order))
        let session = makeSignedOutSession()

        // Precondition: isLoading starts false
        XCTAssertFalse(vm.isLoading)

        await vm.fetchOrder(orderId: "ord-006", session: session)

        // The guard returns before `isLoading = true` — so it must still be false.
        XCTAssertFalse(vm.isLoading, "isLoading must be false after auth-guard early return")
        // currentStage must be unchanged — the fetch was never made
        XCTAssertEqual(vm.currentStage, .orderPlaced, "currentStage must be unchanged after auth-guard early return")
        // lastError must remain nil — the guard returns silently
        XCTAssertNil(vm.lastError, "lastError must be nil after auth-guard early return")
    }
}
