import XCTest
@testable import MeridianMotorsCompanion

/// Unit tests for `OrderTrackerViewModel`.
///
/// Covers all four required scenarios per spec Group 2 task 2.3:
/// 1. Stage-status determination (completed / current / upcoming) given a `currentStage`.
/// 2. Polling failure with `AcquireError.endpointUnavailable` swallows silently (no `lastError`).
/// 3. Polling failure with non-endpoint-unavailable errors surfaces `lastError`.
/// 4. Successful order fetch updates `currentStage` and `stageHistory`.
///
/// `OrderTrackerViewModel` was extracted in task 2.3 per spec instruction:
/// "Extract the OrderTrackerView state logic into a testable `OrderTrackerViewModel`
/// in the same task if it isn't already extractable."
@MainActor
final class OrderTrackerViewModelTests: XCTestCase {

    // MARK: - stageStatus(for:)

    /// Stages before `currentStage` in `allCases` order are `.completed`.
    func testStagesBeforeCurrentAreCompleted() throws {
        let vm = OrderTrackerViewModel(initialStage: .paint)
        // paint is index 4; orderPlaced (0), paymentReceived (1), ... subAssembly (3) are before it
        XCTAssertEqual(vm.stageStatus(for: .orderPlaced), .completed)
        XCTAssertEqual(vm.stageStatus(for: .paymentReceived), .completed)
        XCTAssertEqual(vm.stageStatus(for: .subAssembly), .completed)
    }

    /// The stage that matches `currentStage` exactly is `.current`.
    func testCurrentStageIsCurrent() {
        let vm = OrderTrackerViewModel(initialStage: .paint)
        XCTAssertEqual(vm.stageStatus(for: .paint), .current)
    }

    /// Stages after `currentStage` are `.upcoming`.
    func testStagesAfterCurrentAreUpcoming() {
        let vm = OrderTrackerViewModel(initialStage: .paint)
        // finalAssembly (5), qualityControl (6), ... readyForDelivery (9) are after paint (4)
        XCTAssertEqual(vm.stageStatus(for: .finalAssembly), .upcoming)
        XCTAssertEqual(vm.stageStatus(for: .readyForDelivery), .upcoming)
    }

    /// When `currentStage` is the very first stage, all others are upcoming.
    func testAllStagesUpcomingWhenAtOrderPlaced() {
        let vm = OrderTrackerViewModel(initialStage: .orderPlaced)
        XCTAssertEqual(vm.stageStatus(for: .orderPlaced), .current)
        XCTAssertEqual(vm.stageStatus(for: .paymentReceived), .upcoming)
        XCTAssertEqual(vm.stageStatus(for: .readyForDelivery), .upcoming)
    }

    /// When at the final stage, all preceding stages are completed.
    func testAllPreviousStagesCompletedWhenAtReadyForDelivery() {
        let vm = OrderTrackerViewModel(initialStage: .readyForDelivery)
        for stage in PipelineStage.allCases.dropLast() {
            XCTAssertEqual(vm.stageStatus(for: stage), .completed,
                           "\(stage.rawValue) should be completed when current is readyForDelivery")
        }
        XCTAssertEqual(vm.stageStatus(for: .readyForDelivery), .current)
    }

    // MARK: - apply(error:) — endpointUnavailable swallowed silently

    /// `endpointUnavailable` must NOT update `lastError` (spec: "swallows silently").
    func testEndpointUnavailableDoesNotSurfaceLastError() {
        let vm = OrderTrackerViewModel()
        let err = AcquireError.endpointUnavailable("/acquire/orders/test-order")
        vm.apply(error: err)
        XCTAssertNil(vm.lastError,
                     "endpointUnavailable must not set lastError — expected silent swallow")
    }

    /// A subsequent endpointUnavailable after a real error should NOT clear the prior real error.
    /// (The poll loop may recover from a transient error then hit an unavailable endpoint.)
    func testEndpointUnavailableDoesNotClearPriorRealError() {
        let vm = OrderTrackerViewModel()
        // First: a real error
        vm.apply(error: AcquireError.unauthenticated)
        XCTAssertNotNil(vm.lastError)
        // Then: endpoint unavailable — must not wipe the real error
        vm.apply(error: AcquireError.endpointUnavailable("/acquire/orders/x"))
        XCTAssertNotNil(vm.lastError, "endpointUnavailable should not clear a prior real error")
    }

    // MARK: - apply(error:) — other errors surface lastError

    /// A real AcquireError (not endpoint-unavailable) must be stored in `lastError`.
    func testNonEndpointUnavailableAcquireErrorSurfacesLastError() {
        let vm = OrderTrackerViewModel()
        vm.apply(error: AcquireError.http(status: 403, body: "Forbidden"))
        XCTAssertNotNil(vm.lastError, "HTTP 403 error must surface in lastError")
    }

    /// A non-AcquireError (e.g. URLError) is wrapped as `.network(_:)` and surfaced.
    func testNonAcquireErrorIsWrappedAndSurfaced() {
        let vm = OrderTrackerViewModel()
        vm.apply(error: URLError(.timedOut))
        guard case .network = vm.lastError else {
            XCTFail("URLError should be wrapped as AcquireError.network in lastError")
            return
        }
    }

    // MARK: - apply(order:)

    /// Successful order fetch updates `currentStage` to match `order.currentStage`.
    func testSuccessfulFetchUpdatesCurrentStage() {
        let vm = OrderTrackerViewModel(initialStage: .orderPlaced)
        let order = AcquireOrder(
            orderId: "test-001",
            tenantId: "test-tenant",
            currentStage: "paint",
            stages: [],
            modelId: nil, variantId: nil, colorId: nil,
            selectedAccessoryIds: nil, reservationDepositRef: nil,
            dealerRef: nil, vinAssigned: nil,
            createdAt: nil, updatedAt: nil
        )
        vm.apply(order: order)
        XCTAssertEqual(vm.currentStage, .paint,
                       "currentStage must update to .paint from order.currentStage == 'paint'")
    }

    /// Successful order fetch populates `stageHistory` from stage records with `enteredAt`.
    func testSuccessfulFetchPopulatesStageHistory() {
        let vm = OrderTrackerViewModel()
        let date = Date(timeIntervalSinceReferenceDate: 1_000_000)
        let order = AcquireOrder(
            orderId: "test-002",
            tenantId: "test-tenant",
            currentStage: "paymentReceived",
            stages: [
                OrderStageRecord(stageId: "orderPlaced", enteredAt: date, narration: nil, metadata: nil),
                OrderStageRecord(stageId: "paymentReceived", enteredAt: date, narration: nil, metadata: nil)
            ],
            modelId: nil, variantId: nil, colorId: nil,
            selectedAccessoryIds: nil, reservationDepositRef: nil,
            dealerRef: nil, vinAssigned: nil,
            createdAt: nil, updatedAt: nil
        )
        vm.apply(order: order)
        XCTAssertEqual(vm.stageHistory["orderPlaced"], date)
        XCTAssertEqual(vm.stageHistory["paymentReceived"], date)
    }

    /// Successful order fetch clears any prior `lastError`.
    func testSuccessfulFetchClearsLastError() {
        let vm = OrderTrackerViewModel()
        vm.apply(error: AcquireError.http(status: 500, body: "Server Error"))
        XCTAssertNotNil(vm.lastError)
        let order = AcquireOrder(
            orderId: "test-003",
            tenantId: "test-tenant",
            currentStage: "orderPlaced",
            stages: [],
            modelId: nil, variantId: nil, colorId: nil,
            selectedAccessoryIds: nil, reservationDepositRef: nil,
            dealerRef: nil, vinAssigned: nil,
            createdAt: nil, updatedAt: nil
        )
        vm.apply(order: order)
        XCTAssertNil(vm.lastError,
                     "A successful fetch must clear lastError (so the error footer disappears)")
    }

    /// Stage records without `enteredAt` are not stored in stageHistory.
    func testStageRecordsWithoutEnteredAtAreSkipped() {
        let vm = OrderTrackerViewModel()
        let order = AcquireOrder(
            orderId: "test-004",
            tenantId: "test-tenant",
            currentStage: "orderPlaced",
            stages: [
                OrderStageRecord(stageId: "orderPlaced", enteredAt: nil, narration: nil, metadata: nil)
            ],
            modelId: nil, variantId: nil, colorId: nil,
            selectedAccessoryIds: nil, reservationDepositRef: nil,
            dealerRef: nil, vinAssigned: nil,
            createdAt: nil, updatedAt: nil
        )
        vm.apply(order: order)
        XCTAssertTrue(vm.stageHistory.isEmpty,
                      "Stages without enteredAt must not appear in stageHistory")
    }
}
