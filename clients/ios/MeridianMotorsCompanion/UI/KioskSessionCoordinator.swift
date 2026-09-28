import Foundation
import SwiftUI
import UIKit

// MARK: - KioskSession

/// All visitor-derived state captured during one kiosk visit.
///
/// ## Privacy contract
/// Every field is cleared by `KioskSessionCoordinator.reset()`. The scrub test
/// (`KioskSessionCoordinatorTests.testScrubEqualsDefault`) enumerates **every**
/// stored property — adding a field here without also clearing it in `reset()`
/// will fail that test until the oversight is corrected.
///
/// ## Why a separate struct rather than AppSession fields
/// Isolating visitor-derived state means `reset()` has a closed set: it cannot
/// accidentally leave an AppSession field (e.g. an auth token) that was never
/// supposed to be scoped to one visitor.
struct KioskSession: Equatable {
    // MARK: - Per-visitor identity (F7.1)
    /// A per-visitor UUID minted when this session is created.
    ///
    /// `reset()` produces a fresh `KioskSession()`, which mints a new UUID.
    /// The journey container must carry `.id(coordinator.session.visitorId)` via
    /// `.kioskJourneyIdentity(coordinator:)` — when this id changes, SwiftUI
    /// destroys and recreates the entire subtree, taking every `@State` with it.
    /// This guarantees state isolation regardless of which views are present or
    /// how they are wired: the structural teardown is the mechanism, not cooperative
    /// publishing by each view.
    let visitorId: UUID

    // MARK: - Beat 0 fields
    /// First name entered by the visitor. First name only; never a surname.
    /// Trimmed before storage. Nil when the visitor skipped name entry.
    var visitorFirstName: String? = nil

    // MARK: - Upgrade offer fields
    /// The upgrade offer accepted by the visitor (Beat 2).
    var acceptedOffer: AcceptedOfferSummary? = nil

    // MARK: - Trade-in fields
    /// Trade-in valuation shown to the visitor (Beat 3).
    var tradeInFigure: TradeInFigureSummary? = nil

    // MARK: - Financing fields
    /// Monthly finance figure shown to the visitor (Beat 4).
    var financingFigure: FinancingFigureSummary? = nil

    // MARK: - Configuration fields
    /// Colour selection from light configuration (Beat 5).
    var selectedColorId: String? = nil
    /// Interior style selection from light configuration (Beat 5).
    var selectedInteriorStyleId: String? = nil

    // MARK: - Order fields
    /// Order id from the confirmed reservation (Beat 6).
    var orderId: String? = nil

    // MARK: - Init
    /// Creates a new session with a freshly-minted `visitorId`.
    init() {
        self.visitorId = UUID()
    }
}

// MARK: - Supporting value types (small summaries, not the full API models)

struct AcceptedOfferSummary: Equatable {
    let offerId: String
    let modelName: String?
    let edition: String?
}

struct TradeInFigureSummary: Equatable {
    let displayValue: String
    let currencyCode: String
}

struct FinancingFigureSummary: Equatable {
    let monthlyDisplay: String
    let termMonths: Int
}

// MARK: - KioskSessionCoordinator

/// Coordinates the inter-visitor lifecycle for the kiosk: idle timeout, operator
/// gesture, and inter-visitor state scrub.
///
/// ## Idle timeout (Beat 7 path A)
/// 60 seconds of no input from anywhere in the journey triggers `reset()`.
/// Tests inject a `Clock` to advance time without real waiting.
///
/// ## Operator gesture (Beat 7 path B)
/// A two-second long-press in a fixed screen corner. Invisible hit target —
/// visitors won't find it, but staff know where to look.
///
/// Why NOT a double-tap: visitors double-tap constantly (images they expect to
/// zoom, buttons that feel slow). A mid-journey accidental reset at 60–80
/// visitors/hour is a visible failure in front of an audience.
/// See `decisions.md` 2026-08-11 for the rationale.
///
/// ## State scrub (task 7.4)
/// `reset()` clears every field on `KioskSession` and returns the coordinator to
/// `.chooser`. The equality assertion in tests uses `KioskSession == KioskSession()`
/// so a newly-added field fails the test until it is explicitly cleared.
///
/// ## Wiring contract for privacy (F7.1)
/// The identity modifier `.kioskJourneyIdentity(coordinator:)` MUST be applied at the
/// **kiosk root** — the outermost view of everything a visitor can reach — and NOT
/// merely at the guided journey's container. This applies
/// `.id(coordinator.session.visitorId)`, which causes SwiftUI to destroy and recreate
/// the entire subtree whenever `reset()` is called, because `reset()` replaces
/// `session` with a fresh `KioskSession()` carrying a new `visitorId`.
///
/// **Why the kiosk root and not the journey container.** The guided journey is
/// UpgradeFlow -> ConfiguratorFlow -> OrderTrackerView, but the station deliberately
/// runs the *whole* companion app: a visitor at a quiet station is expected to browse
/// freely — Home, Vehicle, Service, Alerts, Assistant, Discover — which is the point
/// of the demo. `LeadCaptureModal`, reachable from `DiscoverFlow`, holds **email and
/// phone** in its own `@State` (lines 39-40). Scoping the identity to the journey
/// container would leave that PII alive across a reset, so one visitor's email could
/// be on screen under the next visitor's name. Security review cycle 8 named this the
/// sharpest adjacent leak surface; widening the application point is what closes it.
///
/// Gating those surfaces instead would also work, and is rejected: it contradicts the
/// product intent that the station is a real, fully navigable app rather than a
/// scripted demo.
///
/// This structural teardown is the privacy mechanism. It guarantees that every
/// `@State` property inside any reachable view — including state added in the future
/// by someone who has never read this docstring — is destroyed on reset. The scrub
/// does not need to know about those fields, and views do not need to cooperate by
/// publishing their state into `KioskSession`. That cooperative design is what review
/// cycle 9 and security cycle 8 both flagged, and it is deliberately not what this
/// relies on.
///
/// Wiring authors MUST observe these invariants:
///   - Apply `.kioskJourneyIdentity(coordinator:)` on the outermost view that
///     encloses the full journey (all three surfaces: UpgradeFlow, ConfiguratorFlow,
///     OrderTrackerView, and any steps or modals presented from them).
///   - Swap journey views structurally on `currentScreen` transitions via a
///     `switch coordinator.currentScreen` in the parent body — NOT via
///     `.opacity(0)`, `.hidden()`, `.disabled(true)`, or a NavigationStack that
///     retains views across the chooser/journey boundary.
///   - Gate access to non-kiosk-journey surfaces (Discover / lead capture) when in
///     kiosk mode. Those surfaces collect PII in their own `@State` that this
///     coordinator does not see, and are not covered by the identity modifier.
///   - Apply `.kioskTouchActivity(coordinator:)` on the journey root so that
///     arbitrary touch interaction (scrolling, browsing, non-committing taps)
///     keeps the idle timer alive — not only setter calls.
@MainActor
@Observable
final class KioskSessionCoordinator {

    // MARK: - Injected clock

    /// Abstraction used by idle-timeout tests to inject a controlled clock.
    /// Production uses `{ Date() }`.
    var clock: () -> Date

    // MARK: - Screen state

    enum Screen: Equatable {
        case chooser
        case nameEntry
        case journey   // any of the three journey surfaces (UpgradeFlow, ConfiguratorFlow, OrderTrackerView)
    }

    var currentScreen: Screen = .chooser

    // MARK: - Session

    private(set) var session: KioskSession = KioskSession()

    // MARK: - Idle timeout

    /// Duration after which inactivity resets the kiosk.
    let idleTimeoutSeconds: TimeInterval

    /// Time of the last user interaction, per the injected clock.
    private(set) var lastActivityAt: Date

    // MARK: - Test seam

    /// Sets `lastActivityAt` to `date`. Available to `@testable import` tests
    /// so they can position the idle clock without real waiting.
    /// Not part of the public API — internal only.
    func _setLastActivityAt(_ date: Date) {
        lastActivityAt = date
    }

    /// Whether the coordinator has been started (i.e. `start()` has been called
    /// and idle-timeout polling is in flight). Prevents double-start.
    private(set) var isRunning: Bool = false

    // MARK: - Init

    /// - Parameters:
    ///   - idleTimeoutSeconds: Inactivity window before auto-reset. Defaults to 60.
    ///   - clock: Injected clock. Defaults to `{ Date() }`.
    init(idleTimeoutSeconds: TimeInterval = 60, clock: @escaping () -> Date = { Date() }) {
        self.idleTimeoutSeconds = idleTimeoutSeconds
        self.clock = clock
        self.lastActivityAt = clock()
    }

    // MARK: - Lifecycle

    /// Starts the idle-timeout polling loop. Call once when kiosk mode activates.
    /// Idempotent — calling twice has no effect.
    func start() {
        guard !isRunning else { return }
        isRunning = true
        scheduleIdleCheck()
    }

    // MARK: - Activity tracking

    /// Record that the visitor has interacted. Resets the idle timer.
    func recordActivity() {
        lastActivityAt = clock()
    }

    // MARK: - Reset

    /// Performs the inter-visitor state scrub and returns to Beat 0.
    ///
    /// Replaces `session` with a fresh `KioskSession()` — this mints a new
    /// `visitorId`, which causes any journey container carrying
    /// `.kioskJourneyIdentity(coordinator:)` to be destroyed and recreated by
    /// SwiftUI. Every `@State` inside that subtree is destroyed with it,
    /// regardless of what state each view holds or whether it was ever published
    /// into `KioskSession` via a setter.
    ///
    /// Idempotent — calling from `.chooser` is a no-op on the screen state
    /// but still scrubs the session (defensive; the session should already be
    /// pristine at that point).
    ///
    /// Called by:
    ///   - Idle timeout firing.
    ///   - Operator long-press gesture.
    func reset() {
        session = KioskSession()    // scrub every stored property; mints new visitorId
        currentScreen = .chooser
        lastActivityAt = clock()    // restart idle clock from now
    }

    // MARK: - Navigation helpers

    func navigateTo(_ screen: Screen) {
        currentScreen = screen
        recordActivity()
    }

    /// Write the visitor's first name into the session. Trims whitespace.
    /// Nil / whitespace-only input is stored as nil.
    func setVisitorFirstName(_ name: String?) {
        let trimmed = name?.trimmingCharacters(in: .whitespaces)
        session.visitorFirstName = (trimmed?.isEmpty == false) ? trimmed : nil
        recordActivity()
    }

    /// Apply an accepted offer to the session.
    func setAcceptedOffer(_ offer: AcceptedOfferSummary?) {
        session.acceptedOffer = offer
        recordActivity()
    }

    /// Apply a trade-in figure to the session.
    func setTradeInFigure(_ figure: TradeInFigureSummary?) {
        session.tradeInFigure = figure
        recordActivity()
    }

    /// Apply a financing figure to the session.
    func setFinancingFigure(_ figure: FinancingFigureSummary?) {
        session.financingFigure = figure
        recordActivity()
    }

    /// Apply a colour selection.
    func setSelectedColor(id: String?) {
        session.selectedColorId = id
        recordActivity()
    }

    /// Apply an interior style selection.
    func setSelectedInteriorStyle(id: String?) {
        session.selectedInteriorStyleId = id
        recordActivity()
    }

    /// Apply the order id from the confirmed reservation.
    func setOrderId(_ orderId: String?) {
        session.orderId = orderId
        recordActivity()
    }

    // MARK: - Idle-check loop (real clock path)

    private func scheduleIdleCheck() {
        // Poll every second for simulator precision; production can afford a coarser
        // interval, but 1s makes the 60-second threshold well-defined.
        Task { [weak self] in
            while let self = self, self.isRunning {
                try? await Task.sleep(nanoseconds: 1_000_000_000)  // 1s
                await self.checkIdle()
            }
        }
    }

    private func checkIdle() {
        let elapsed = clock().timeIntervalSince(lastActivityAt)
        if elapsed >= idleTimeoutSeconds {
            reset()
        }
    }
}

// MARK: - KioskJourneyIdentityModifier (F7.1)

/// Applies `.id(coordinator.session.visitorId)` to the modified view.
///
/// When `KioskSessionCoordinator.reset()` is called, `session` is replaced with a
/// fresh `KioskSession()` carrying a new `visitorId`. SwiftUI sees the id change and
/// destroys the entire subtree — including every `@State` held by any descendant
/// view, regardless of whether that state was ever published into `KioskSession`.
///
/// This is the structural guarantee that no visitor state can survive a reset.
/// It does not require every view to cooperate; future views with new `@State`
/// fields are protected automatically.
///
/// Apply once on the outermost view enclosing all three kiosk journey surfaces.
/// Do NOT apply inside a navigation stack or sheet that is retained across the
/// chooser/journey boundary — the container being keyed must be the container
/// that is conditionally inserted when `coordinator.currentScreen == .journey`.
///
/// Usage:
/// ```swift
/// journeyRootView
///     .kioskJourneyIdentity(coordinator: coordinator)
/// ```
struct KioskJourneyIdentityModifier: ViewModifier {
    let coordinator: KioskSessionCoordinator

    func body(content: Content) -> some View {
        content.id(coordinator.session.visitorId)
    }
}

extension View {
    /// Keys this view on the coordinator's per-visitor identity.
    ///
    /// Apply on the journey container so SwiftUI destroys and recreates the
    /// entire subtree — and every `@State` it holds — whenever
    /// `KioskSessionCoordinator.reset()` mints a new `visitorId`.
    func kioskJourneyIdentity(coordinator: KioskSessionCoordinator) -> some View {
        self.modifier(KioskJourneyIdentityModifier(coordinator: coordinator))
    }
}

// MARK: - TouchActivityModifier (F7.2)

/// Records any touch on the modified view as activity, keeping the idle timer alive.
///
/// Uses a `simultaneousGesture` with a zero-minimum-distance `DragGesture` so that
/// the recogniser fires on any touch — including taps, scrolls, and swipes — without
/// consuming or preventing delivery of those gestures to the view's own recognisers.
/// `simultaneousGesture` is the correct mechanism: it participates in gesture
/// arbitration cooperatively rather than winning exclusively.
///
/// The idle timer fires only when **no input** is received for `idleTimeoutSeconds`.
/// A visitor browsing colour swatches or scrolling without committing a selection
/// is still actively engaged; this modifier makes that engagement visible to the
/// idle clock.
///
/// Existing `recordActivity()` calls from the coordinator's setters remain. This
/// modifier is additive — it covers the gap between data commits.
///
/// Usage:
/// ```swift
/// journeyRootView
///     .kioskTouchActivity(coordinator: coordinator)
/// ```
struct TouchActivityModifier: ViewModifier {
    let coordinator: KioskSessionCoordinator

    func body(content: Content) -> some View {
        content.simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    coordinator.recordActivity()
                }
        )
    }
}

extension View {
    /// Records any touch interaction on this view as activity, deferring the idle timer.
    ///
    /// The underlying `DragGesture(minimumDistance: 0)` fires on the first touch
    /// move (including zero-move taps) and reports via `.onChanged`. It does NOT
    /// consume the gesture — normal tap and control actions continue to fire.
    func kioskTouchActivity(coordinator: KioskSessionCoordinator) -> some View {
        self.modifier(TouchActivityModifier(coordinator: coordinator))
    }
}

// MARK: - LongPressResetModifier

/// Attaches a two-second long-press gesture to a fixed corner of the screen.
///
/// The hit target is intentionally invisible — visitors won't find it by
/// accident, but operators know where to look after being briefed. A double-tap
/// is explicitly NOT used: see `KioskSessionCoordinator` documentation.
///
/// Usage:
/// ```swift
/// someView
///     .kioskResetGesture(coordinator: coordinator)
/// ```
struct LongPressResetModifier: ViewModifier {
    let coordinator: KioskSessionCoordinator
    /// Minimum press duration before the reset fires. Two seconds is long enough
    /// to be deliberate, short enough not to feel broken to a staff member
    /// who knows the gesture.
    static let pressDuration: TimeInterval = 2.0

    @State private var isPressingCorner: Bool = false

    func body(content: Content) -> some View {
        content.overlay(
            // Invisible 44×44 pt hit target in the bottom-trailing corner.
            // 44 pt is Apple's recommended minimum touch target.
            Color.clear
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
                .simultaneousGesture(
                    LongPressGesture(minimumDuration: Self.pressDuration)
                        .onEnded { _ in
                            Task { @MainActor in
                                coordinator.reset()
                            }
                        }
                )
                .accessibilityHidden(true),   // invisible to VoiceOver / Guided Access
            alignment: .bottomTrailing
        )
    }
}

extension View {
    /// Attaches the operator long-press reset gesture to this view.
    ///
    /// Apply once on the root journey container, not on individual steps.
    func kioskResetGesture(coordinator: KioskSessionCoordinator) -> some View {
        self.modifier(LongPressResetModifier(coordinator: coordinator))
    }
}
