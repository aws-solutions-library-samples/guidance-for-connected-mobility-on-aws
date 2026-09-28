import Foundation

/// Presenter-control scenario enum for the Discover / Find journey stage.
///
/// Lives in the developer-only Presenter layer (same module as
/// `PresenterControls.swift` and `TelemetryScenario`). Entirely additive —
/// the existing telemetry `TelemetryScenario` enum and its behavior are
/// unchanged.
///
/// Usage:
///   `PresenterControls` gains a second "Acquire scenarios" section that
///   allows the demo presenter to toggle between:
///
///   - `.pathAAnonymousCold` (default): standard anonymous Discover entry,
///     no persona, cold greeting, standard primed prompts.
///
///   - `.pathBReturningOwnerWarm`: seeds an in-memory returning-owner
///     persona snapshot, activates the Buy tab, and opens DiscoverFlow
///     with the warm-start greeting + primed prompts. A persistent
///     "Path B (warm)" toast appears on `MainTabView` while the flag is
///     active.
///
/// Path B is in-memory only — no LTM write, no real push infrastructure.
///
/// Spec authority: spec `2026-07-28-cvx-discover-ios`
/// § Design — Path B mock trigger — presenter control.
enum AcquireScenario: String, CaseIterable, Identifiable {
    /// Default anonymous cold-entry path. Clears any active Path B state.
    case pathAAnonymousCold = "pathAAnonymousCold"
    /// Mock warm-start: seeds a returning-owner persona and activates the
    /// Buy tab. In-memory only.
    case pathBReturningOwnerWarm = "pathBReturningOwnerWarm"

    var id: String { rawValue }

    /// Human-readable label shown in PresenterControls.
    var displayName: String {
        switch self {
        case .pathAAnonymousCold:     return "Path A — Anonymous cold entry"
        case .pathBReturningOwnerWarm: return "Path B — Returning owner (warm)"
        }
    }

    /// Optional sub-caption shown below the display name in the picker row.
    var caption: String? {
        switch self {
        case .pathAAnonymousCold:
            return "Standard anonymous Discover entry"
        case .pathBReturningOwnerWarm:
            return "Seeds in-memory persona, activates Buy tab, shows warm toast"
        }
    }

    /// The seeded in-memory persona injected when Path B is activated.
    /// This snapshot mirrors the shape `DiscoverFlow` reads from
    /// `session._personaHint`. All values are mock — no real CRM or LTM write.
    ///
    /// The fictional engagement segment "lease-maturing" is the canonical
    /// IG Stage 1 Path B trigger condition (owner approaching lease end who
    /// re-engages via an OEM proactive push).
    static var pathBMockPersona: PersonaSnapshot {
        PersonaSnapshot(
            actorId: "mock-actor-returning-owner",
            engagementSegment: "lease-maturing",
            currentVehicleHint: "commuter",
            statedInterest: "Looking for an upgrade with better range"
        )
    }
}
