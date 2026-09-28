import SwiftUI

// MARK: - AVXCardsTab

/// AVX Findings + Actions card list, hosted by `AlertsTabView` (Task 1.2 decision:
/// absorb into `.alerts` rather than add a 7th tab).
///
/// Scaffolding (Task 2.2): rendered a placeholder section header + stub text.
/// Task 2.4 replaced that stub with real card rendering — findings via
/// `AVXFindingCard` (drill-down + approve wired), actions via the read-only
/// `AVXActionCard`. Owner approve/decline (`AVXIosOwnerActionCard`) is the
/// remaining piece; see the note at the call site for why it is not wired yet.
///
/// Integration note (Task 2.6): `AlertsTabView` will embed this view in a new
/// section below its existing DTC / Safety-event sections. `AVXCardsTab` is
/// self-contained — it manages its own ViewModel and refresh lifecycle.
struct AVXCardsTab: View {
    @Environment(AppSession.self) private var session
    let theme: TenantTheme

    /// Optional drill-down callback. Threaded from `MainTabView` via
    /// `AlertsTabView` to `AVXFindingCard.onDrillDown`. When the user
    /// taps "Ask assistant" on a card, this callback receives the
    /// `finding_id`; `MainTabView` responds by presenting `AssistantTabView`
    /// with `initialFindingId` pre-set. nil hides the drill-down button
    /// (preview / read-only path).
    var onDrillDown: ((String) -> Void)? = nil

    @State private var viewModel = AVXCardsViewModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                    .foregroundStyle(theme.primary)
                Text("Agent Findings")
                    .font(.headline)
                Spacer()
            }
            // Task 2.4 — render the real cards. The ViewModel already produces a
            // single `cards` array (findings + actions, sorted descending by
            // updatedAt), so this view only switches on the case.
            //
            // Actions render through the READ-ONLY `AVXActionCard`, including
            // `ios-owner` ones. `AVXIosOwnerActionCard` is deliberately NOT used
            // yet: its Approve/Decline buttons call `onApprove?`/`onDecline?` via
            // optional chaining, so with no handler they would render and silently
            // do nothing — a dead affordance is worse than no affordance. The
            // ViewModel has no owner-action approve operation to wire them to
            // (`approve` is finding-scoped: findingId + target + kind + params).
            // Wiring those two callbacks is the remaining part of Task 2.4.
            //
            // `onDismiss` on a finding is likewise unwired: it hands back a
            // findingId while `viewModel.dismiss` takes an actionId. The card
            // self-hides via its own `isDismissed` state, so the button still
            // responds — it just does not persist.
            if viewModel.isLoading && viewModel.cards.isEmpty {
                ProgressView()
                    .controlSize(.small)
            } else {
                // "Not linked" comes only from an empty `standings`; an empty
                // Findings list with a standing is `noFindings`.
                if let notice = viewModel.notice {
                    Text(Self.text(for: notice))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if viewModel.findingsTruncated {
                    Text("Some agent findings aren't shown.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(viewModel.cards) { card in
                    switch card {
                    case .finding(let finding):
                        AVXFindingCard(
                            finding: finding,
                            theme: theme,
                            onDrillDown: onDrillDown,
                            onApprove: { findingId, targetSystem, actionKind, parameters in
                                Task {
                                    await viewModel.approve(
                                        findingId: findingId,
                                        targetSystem: targetSystem,
                                        actionKind: actionKind,
                                        parameters: parameters
                                    )
                                }
                            }
                        )
                    case .action(let action):
                        AVXActionCard(action: action, theme: theme)
                    }
                }
            }
        }
        // Same 16 pt inset as the Alerts tab's other section headers
        // (`.padding(.horizontal)` / `SectionCard`'s `.padding()`). Without it
        // the icon and cards sat flush against the screen edge.
        .padding(.horizontal)
        .task {
            await loadIfNeeded()
            // APNs deep-link router path — card-selection half (Task F2.1):
            // When `AVXDeepLinkRouter.shared.openFindingId` is already set at
            // the time this view's .task fires (e.g. notification tap while the
            // app was backgrounded and `AlertsTabView` just appeared), select
            // the matching card and consume the deep-link token.
            // The `.onChange` below handles the case where `openFindingId`
            // becomes non-nil AFTER this task runs (e.g. notification tap while
            // the app is foregrounded and AlertsTabView is already visible).
            let router = AVXDeepLinkRouter.shared
            if let fid = router.openFindingId {
                viewModel.selectFinding(id: fid)
                router.consumeOpenFindingId()
            }
            // APNs deep-link router path — drill-down (Task 3.4 / Task 4.1):
            // when AVXDeepLinkRouter.shared.drillDownFindingId is set (via
            // AppDelegate → open(findingId:) + openDrillDown(findingId:)),
            // forward to the same onDrillDown callback that card taps use.
            // This ensures the APNs notification-tap path reaches the same
            // code path as a direct card drill-down, satisfying Accept #3.
            if let fid = router.drillDownFindingId {
                router.consumeDrillDownFindingId()
                onDrillDown?(fid)
            }
        }
        .onChange(of: AVXDeepLinkRouter.shared.openFindingId) { _, newId in
            // Navigation-tap path: `MainTabView` observed the same value and
            // switched to `.alerts`; now we are visible and can select the card.
            // Consume the token immediately so back-navigation cannot re-trigger.
            if let fid = newId {
                viewModel.selectFinding(id: fid)
                AVXDeepLinkRouter.shared.consumeOpenFindingId()
            }
        }
        .onChange(of: AVXDeepLinkRouter.shared.drillDownFindingId) { _, newId in
            if let fid = newId {
                AVXDeepLinkRouter.shared.consumeDrillDownFindingId()
                onDrillDown?(fid)
            }
        }
    }

    // MARK: - Notice copy

    static func text(for notice: AVXCardsViewModel.Notice) -> String {
        switch notice {
        case .loadFailed: return "Couldn't load agent findings."
        case .notLinkedToVehicle: return "This account isn't linked to a vehicle, so there are no agent findings."
        case .noFindings: return "No agent findings right now."
        }
    }

    // MARK: - Private

    private func loadIfNeeded() async {
        guard case .signedIn(let token, _) = session.authState else { return }
        let client = session.makeClient({ token })
        // Findings take no identifier (`GET /findings/me`, standing derived
        // server-side). The Actions route still needs the caller's `sub`.
        guard let sub = AVXCardsViewModel.actionsOwnerId(for: session) else { return }
        await viewModel.refresh(actionsOwnerId: sub, client: client)
    }
}
