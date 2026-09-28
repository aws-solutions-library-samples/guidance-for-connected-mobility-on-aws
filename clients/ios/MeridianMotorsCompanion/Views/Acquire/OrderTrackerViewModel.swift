import Foundation
import SwiftUI

// MARK: - Stage status

/// Status of a single pipeline stage relative to the order's current stage.
///
/// Extracted from `OrderTrackerView` to be independently testable.
/// `OrderTrackerView` imports this type via `@testable import MeridianMotorsCompanion`
/// and uses `OrderTrackerViewModel.stageStatus(for:currentStage:)` to drive
/// its icon rendering.
enum OrderStageStatus: Equatable {
    case completed
    case current
    case upcoming
}

// MARK: - ViewModel

/// Testable state and logic extracted from `OrderTrackerView`.
///
/// Owns:
/// - Stage-status determination (`stageStatus(for:currentStage:)`).
/// - Poll-result application (`apply(order:)`).
/// - Error classification (endpoint-unavailable is silently swallowed).
/// - Order fetching (`fetchOrder(orderId:session:)`) — moved here in Group 3
///   (task 3.3) so the view only drives `startPolling` / `stopPolling`.
///
/// `private(set)` on all stored properties preserves the view's original write
/// boundary at module level (security review cycle 3, S1).
@Observable @MainActor
final class OrderTrackerViewModel {
    private(set) var currentStage: PipelineStage
    private(set) var stageHistory: [String: Date] = [:]
    private(set) var lastError: AcquireError? = nil
    private(set) var isLoading: Bool = false

    /// Dependency-injection seam for `fetchOrder`.
    ///
    /// In production this is `nil`, which causes `fetchOrder` to build the real
    /// `AcquireCatalogClient` from the authenticated token — identical behaviour
    /// to the inline code it replaced.  Tests supply a `(String) -> any OrderFetching`
    /// closure that returns a `MockOrderFetcher` without touching the network.
    ///
    /// The view never reads or writes this property; the seam is invisible to all
    /// production call sites.
    var orderFetcher: ((String) -> any OrderFetching)?

    init(
        initialStage: PipelineStage = .orderPlaced,
        orderFetcher: ((String) -> any OrderFetching)? = nil
    ) {
        self.currentStage = initialStage
        self.orderFetcher = orderFetcher
    }

    // MARK: - Stage status

    /// Returns the display status of `stage` relative to `currentStage`.
    func stageStatus(for stage: PipelineStage) -> OrderStageStatus {
        let ordered = PipelineStage.allCases
        guard
            let currentIdx = ordered.firstIndex(of: currentStage),
            let stageIdx   = ordered.firstIndex(of: stage)
        else { return .upcoming }

        if stageIdx < currentIdx  { return .completed }
        if stageIdx == currentIdx { return .current }
        return .upcoming
    }

    // MARK: - Poll-result application

    /// Applies a successful fetch result to the view-model state.
    func apply(order: AcquireOrder) {
        currentStage = PipelineStage.from(order.currentStage)
        var history: [String: Date] = [:]
        for record in order.stages {
            if let date = record.enteredAt {
                history[record.stageId] = date
            }
        }
        stageHistory = history
        lastError = nil
    }

    /// Applies a fetch failure, classifying it correctly:
    /// - `endpointUnavailable` → silently swallowed (no `lastError` update).
    /// - all other `AcquireError` cases → stored in `lastError`.
    /// - non-`AcquireError` errors → wrapped as `.network(_:)` in `lastError`.
    func apply(error: Error) {
        if let acquireErr = error as? AcquireError {
            if acquireErr.isEndpointUnavailable {
                // Expected in demo window — do not surface to the user.
                return
            }
            lastError = acquireErr
        } else {
            lastError = .network(error)
        }
    }

    // MARK: - Order fetch

    /// Fetches the order from the REST surface and applies the result.
    ///
    /// Called by the view's polling loop; delegated here so all state mutations
    /// stay within the view model and `private(set)` is satisfied.
    ///
    /// In production `orderFetcher` is `nil`, so a fresh `AcquireCatalogClient`
    /// is built from the authenticated token — identical to the original inline
    /// behaviour.  Tests supply an `orderFetcher` closure to avoid network I/O.
    func fetchOrder(orderId: String, session: AppSession) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        isLoading = true
        defer { isLoading = false }
        let client: any OrderFetching = orderFetcher?(token)
            ?? AcquireCatalogClient(idTokenProvider: { token })
        do {
            let order = try await client.fetchOrder(orderId: orderId)
            apply(order: order)
        } catch {
            apply(error: error)
        }
    }
}
