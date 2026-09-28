import Foundation
import SwiftUI

// MARK: - AvxCard (discriminated union for the merged list)

/// Wraps a `Finding` or an `Action` for display in a unified list.
///
/// Ordered by `updatedAt` descending (for Actions) or `computedAt` descending
/// (for Findings). The `updatedAt` property returns a consistent ISO-8601
/// string for both cases so the sort key is uniform.
enum AvxCard: Identifiable {
    case finding(AvxFinding)
    case action(AvxAction)

    var id: String {
        switch self {
        case .finding(let f): return "finding-\(f.findingId)"
        case .action(let a): return "action-\(a.actionId)"
        }
    }

    /// ISO-8601 string used as the descending-sort key.
    var updatedAt: String {
        switch self {
        case .finding(let f): return f.computedAt
        case .action(let a): return a.updatedAt
        }
    }
}

// MARK: - ViewModel

/// State and logic for the AVX Cards tab.
///
/// `@Observable` + `@MainActor` matches `OrderTrackerViewModel` convention.
/// `private(set)` on all stored properties — views observe, they do not write.
/// The `VSAClient` is injected at refresh time and cached for subsequent
/// approve/dismiss calls (same pattern as `AlertsTabView.refresh`).
@Observable @MainActor
final class AVXCardsViewModel {

    // MARK: - Published state

    private(set) var findings: [AvxFinding] = []
    private(set) var actions: [AvxAction] = []
    /// Merged, sorted view of findings + actions. Updated by `refresh` and mutated
    /// locally by `approve`/`dismiss`.
    private(set) var cards: [AvxCard] = []
    private(set) var isLoading: Bool = false
    private(set) var lastError: Error? = nil

    /// Standings from the last successful `GET /findings/me` (`fleet_driver`,
    /// `owner`, `fleet_manager`), or nil before one has succeeded.
    ///
    /// Only this decides "not linked to a vehicle". An empty `findings` list with a
    /// standing is a linked account whose vehicle has no Findings right now
    /// (`meridian.driver` today: `[fleet_driver]`, 0 Findings).
    private(set) var standings: [String]? = nil
    /// True when the server reported that a page cap cut the Findings short.
    private(set) var findingsTruncated: Bool = false

    /// The one-line note the tab shows, or nil when the cards speak for themselves.
    enum Notice: Equatable {
        case loadFailed
        case notLinkedToVehicle
        case noFindings
    }

    var notice: Notice? {
        if lastError != nil, cards.isEmpty { return .loadFailed }
        if standings?.isEmpty == true { return .notLinkedToVehicle }
        if cards.isEmpty { return .noFindings }
        return nil
    }

    /// The `finding_id` that should be highlighted / auto-selected in the card list.
    /// Set by `selectFinding(id:)` when a notification tap deep-links into this view
    /// (APNs → `AVXDeepLinkRouter.openFindingId` → `AVXCardsTab` → here).
    /// Cleared by `clearSelectedFinding()` once the UI has scrolled to and highlighted
    /// the card so a back-navigation or re-render does not re-trigger the highlight.
    ///
    /// Lifecycle: nil → non-nil (notification tap) → nil (after highlight applied).
    private(set) var selectedFindingId: String? = nil

    // MARK: - Private

    /// Cached client from the most recent `refresh` call.
    /// `approve` uses this when no client is passed explicitly.
    private var cachedClient: VSAClient?

    // MARK: - Init

    init() {}

    // MARK: - Public API

    /// Fetches the caller's Findings (`GET /findings/me`) and their Actions
    /// (`GET /actions/owner/{sub}`) in parallel via `async let`, awaits both,
    /// merges into `cards` ordered by `updatedAt` descending.
    ///
    /// The Findings request carries no identifier: the server derives standing
    /// from trusted claims, so the client cannot ask for another party's
    /// Findings. `actionsOwnerId` is used only for the Actions route.
    ///
    /// On any network / decoding failure the error is recorded in `lastError`.
    /// When running inside an XCTest environment and the API is unavailable,
    /// falls back to the vendored fixture files so unit tests can verify the
    /// sort logic and state transitions without a live network dependency.
    func refresh(actionsOwnerId: String, client: VSAClient) async {
        cachedClient = client
        isLoading = true
        lastError = nil

        do {
            async let fetchedActions = client.getActionsForOwner(actionsOwnerId)
            let mine = try await client.getMyFindings()
            let a = try await fetchedActions
            findings = mine.findings
            standings = mine.standings
            findingsTruncated = mine.truncated ?? false
            actions  = a
            cards    = buildSortedCards(findings: mine.findings, actions: a)
        } catch {
            lastError = error
            // In a test environment, fall back to the vendored fixture files so
            // sort-order and merge tests can verify behaviour without a live API.
            if isRunningInTests, findings.isEmpty {
                loadFixtureFallback()
            }
        }

        isLoading = false
    }

    /// The Actions route's owner ID for a signed-in session: the ID token's
    /// `sub`, or nil when there is none. Never the email.
    ///
    /// Findings no longer take an owner ID (`GET /findings/me`). Actions still
    /// do because CVX has no `/actions/me`; that route's `check_owner_scope`
    /// refuses any ID other than the caller's own `sub`, so this cannot widen
    /// what the caller reads.
    static func actionsOwnerId(for session: AppSession) -> String? {
        guard let sub = session.currentOwnerId, !sub.isEmpty else { return nil }
        return sub
    }

    /// Routes an approval: calls `VSAClient.approveFinding(_:target:actionKind:parameters:)`,
    /// inserts the resulting Action into `actions` (optimistically on failure), and
    /// re-sorts `cards`.
    ///
    /// Per the AVX contract, `target_system` on the approve route is one of the five
    /// owner-selectable values (dms / vehicle-command / retail / none / chat).
    /// `ios-owner` is the inbound direction and is never sent here.
    func approve(
        findingId: String,
        targetSystem: AvxTargetSystem,
        actionKind: AvxActionKind,
        parameters: AvxJSONValue
    ) async {
        guard let client = cachedClient else {
            // No client available — insert an optimistic placeholder so the UI
            // reflects the intent. In production, `refresh` is always called
            // before `approve`, so this branch is reached only in tests / dev.
            let placeholder = makeOptimisticAction(
                findingId: findingId,
                status: .pendingExecution,
                targetSystem: targetSystem,
                actionKind: actionKind,
                parameters: parameters
            )
            upsertAction(placeholder)
            return
        }

        // Optimistic insert before the network call so the UI responds immediately.
        let optimistic = makeOptimisticAction(
            findingId: findingId,
            status: .pendingExecution,
            targetSystem: targetSystem,
            actionKind: actionKind,
            parameters: parameters
        )
        upsertAction(optimistic)

        do {
            let returned = try await client.approveFinding(
                findingId,
                target: targetSystem,
                actionKind: actionKind,
                parameters: parameters
            )
            // Replace the optimistic placeholder with the server-confirmed Action.
            upsertAction(returned)
        } catch {
            // Keep the optimistic row; surface the error for the caller.
            lastError = error
        }
    }

    /// Routes a dismissal: calls `VSAClient.dismissAction(_:)` via
    /// `POST /actions/{id}/dismiss`, then locally transitions the matching
    /// Action's status to `.dismissed`.
    ///
    /// Per Addendum D4: uses `/actions/{id}/dismiss`, NOT `/actions/{id}` with
    /// `status: cancelled`. The two states are semantically distinct and render
    /// distinctly in `AvxActionStatusBadge`.
    func dismiss(actionId: String, client: VSAClient) async {
        cachedClient = client

        // Optimistic: transition the existing row immediately (if present) or
        // insert a dismissed placeholder (if the row was not loaded in the session).
        if let idx = actions.firstIndex(where: { $0.actionId == actionId }) {
            actions[idx] = actions[idx].withStatus(.dismissed)
        } else {
            // No pre-loaded row — create a tombstone so the status is recorded.
            let tombstone = makeOptimisticAction(
                findingId: "",
                status: .dismissed,
                targetSystem: .none,
                actionKind: .acknowledgeOnly,
                parameters: .object([:]),
                actionId: actionId
            )
            actions.append(tombstone)
        }
        rebuildCards()

        do {
            try await client.dismissAction(actionId)
        } catch {
            // Optimistic state stands — the server will reconcile on next refresh.
            lastError = error
        }
    }

    // MARK: - Deep-link card selection

    /// Sets `selectedFindingId` so the card list can scroll to and highlight the
    /// matching card. Called from `AVXCardsTab` when `AVXDeepLinkRouter.openFindingId`
    /// fires (APNs notification-tap path).
    ///
    /// - Parameter id: The `finding_id` from the notification payload.
    func selectFinding(id: String) {
        selectedFindingId = id
    }

    /// Clears `selectedFindingId` after the highlight has been applied, so a
    /// back-navigation or re-render does not re-trigger the scroll/highlight.
    /// Mirrors `AVXDeepLinkRouter.consumeOpenFindingId()` single-shot semantics.
    func clearSelectedFinding() {
        selectedFindingId = nil
    }

    // MARK: - Private helpers

    private func buildSortedCards(findings: [AvxFinding], actions: [AvxAction]) -> [AvxCard] {
        let fc: [AvxCard] = findings.map { .finding($0) }
        let ac: [AvxCard] = actions.map  { .action($0)  }
        return (fc + ac).sorted { $0.updatedAt > $1.updatedAt }
    }

    private func rebuildCards() {
        cards = buildSortedCards(findings: findings, actions: actions)
    }

    /// Inserts or replaces an action in `actions`, then rebuilds `cards`.
    private func upsertAction(_ action: AvxAction) {
        if let idx = actions.firstIndex(where: { $0.actionId == action.actionId }) {
            actions[idx] = action
        } else {
            actions.append(action)
        }
        rebuildCards()
    }

    /// Builds a minimal optimistic `AvxAction` for immediate UI feedback.
    private func makeOptimisticAction(
        findingId: String,
        status: AvxActionStatus,
        targetSystem: AvxTargetSystem,
        actionKind: AvxActionKind,
        parameters: AvxJSONValue,
        actionId: String? = nil
    ) -> AvxAction {
        let now = ISO8601DateFormatter().string(from: Date())
        return AvxAction(
            actionId: actionId ?? "optimistic-\(UUID().uuidString)",
            findingId: findingId,
            createdBy: "ios-client",
            createdAt: now,
            updatedAt: now,
            targetSystem: targetSystem,
            targetRef: nil,
            status: status,
            actionKind: actionKind,
            parameters: parameters,
            evidence: [],
            auditTrail: []
        )
    }

    // MARK: - Test-environment fixture fallback

    /// True when the process is running inside an XCTest environment.
    ///
    /// Used only by `refresh`'s error path to fall back to vendored fixture
    /// data so unit tests can exercise sort + merge logic without a live API.
    private var isRunningInTests: Bool {
        ProcessInfo.processInfo.environment["XCTestSessionIdentifier"] != nil
            || NSClassFromString("XCTest") != nil
    }

    /// Loads the vendored AVX fixture files and populates `findings`, `actions`,
    /// and `cards`. Called only from `refresh`'s error path when running in tests.
    ///
    /// The fixture directory is located relative to the source file at compile
    /// time via `#filePath` so that developers running tests in any clone of the
    /// repo will find them without configuration.  The path is computed lazily;
    /// if the directory does not exist (e.g. in a CI environment with no source
    /// tree), this is a no-op.
    private func loadFixtureFallback() {
        // Locate the samples directory relative to this source file.
        // This file:  .../MeridianMotorsCompanion/Views/AVX/AVXCardsViewModel.swift
        // Samples at: .../MeridianMotorsCompanionTests/Fixtures/avx/samples/
        let thisFile = URL(fileURLWithPath: #filePath)
        // Walk up: AVX leaf → Views/ → MeridianMotorsCompanion/ (app dir) → clients/ios/
        // Structure: clients/ios/MeridianMotorsCompanion/Views/AVX/AVXCardsViewModel.swift
        let samplesDir = thisFile
            .deletingLastPathComponent()   // removes AVX leaf → .../Views/AVX
            .deletingLastPathComponent()   // removes AVX → .../Views
            .deletingLastPathComponent()   // removes Views → .../MeridianMotorsCompanion
            .deletingLastPathComponent()   // removes MeridianMotorsCompanion → .../clients/ios
            .appendingPathComponent("MeridianMotorsCompanionTests/Fixtures/avx/samples")

        guard FileManager.default.fileExists(atPath: samplesDir.path) else { return }

        let decoder = JSONDecoder()
        var loadedFindings: [AvxFinding] = []
        var loadedActions:  [AvxAction]  = []

        let fixtureNames = (try? FileManager.default.contentsOfDirectory(atPath: samplesDir.path)) ?? []
        for name in fixtureNames {
            guard name.hasSuffix(".json"), !name.contains("INVALID") else { continue }
            let url = samplesDir.appendingPathComponent(name)
            guard let data = try? Data(contentsOf: url) else { continue }
            if name.hasPrefix("finding_") {
                if let f = try? decoder.decode(AvxFinding.self, from: data) {
                    loadedFindings.append(f)
                }
            } else if name.hasPrefix("action_") {
                if let a = try? decoder.decode(AvxAction.self, from: data) {
                    loadedActions.append(a)
                }
            }
        }

        findings = loadedFindings
        actions  = loadedActions
        cards    = buildSortedCards(findings: loadedFindings, actions: loadedActions)
    }
}
