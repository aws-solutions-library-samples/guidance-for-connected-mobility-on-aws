import SwiftUI

/// Persona-driven layout selector. Mirrors the backend's tenant-segment
/// concept (`fleet | oem | rental`) on the iOS side so the app can vary
/// tab labels and card visibility without duplicating that switch in
/// every view.
///
/// Resolved from `AppSession.tenantConfig?.segment` after sign-in.
/// Defaults to `.fleet` whenever the segment is missing or unrecognized
/// — that's the broadest layout (most tabs, most cards) so a tenant
/// with a partially-populated config still gets a working app.
///
/// Why this is a Swift enum and not an inline String compare in each
/// view: typo-safety. The string "oem" appears in DDB, the prompt, the
/// agent runtime, and now the iOS app; centralizing the parse here
/// means a typo gets caught at compile time instead of silently
/// rendering the wrong layout.
enum LayoutSegment: String {
    case fleet
    case oem
    case rental

    /// Parse from the raw tenant-config string. Anything we don't
    /// recognize falls back to `.fleet`. Keeps signed-in sessions
    /// usable while a config rollout adds a new segment value.
    static func from(_ raw: String?) -> LayoutSegment {
        switch (raw ?? "").lowercased() {
        case "oem":    return .oem
        case "rental": return .rental
        default:       return .fleet
        }
    }

    // MARK: - Tab bar

    /// Service tab label. OEM-branded apps say "Dealer" instead of
    /// "Service" because OEM customers schedule at their authorized
    /// dealership, not at a generic service center.
    var serviceTabLabel: String {
        switch self {
        case .oem: return "Dealer"
        default:   return "Service"
        }
    }

    /// SF Symbol for the Service/Dealer tab. OEM gets a building icon
    /// that reads as "dealership" rather than the wrench-and-screwdriver
    /// used by the broader fleet/rental flow.
    var serviceTabSymbol: String {
        switch self {
        case .oem: return "building.2.fill"
        default:   return "wrench.and.screwdriver.fill"
        }
    }

    // MARK: - Home tab visibility

    /// Whether to show the driver safety-score card. Fleet operators
    /// care about driver scoring (insurance, performance reviews);
    /// OEM owners and rental drivers don't.
    var showsSafetyScoreCard: Bool { self == .fleet }

    /// Whether to show the recent-trips activity card. Fleet/OEM
    /// owners want to see their trip history; rental drivers won't —
    /// they're mid-trip and the history is mostly someone else's.
    var showsRecentActivityCard: Bool { self != .rental }

    /// Whether to show the recall banner. Recalls are an OEM/fleet
    /// concern; rentals get them resolved by the rental company at
    /// turn-in time, so showing them to a renter mid-trip just adds
    /// noise.
    var showsRecallBanner: Bool { self != .rental }

    /// Whether to show the upcoming-service card. Visible for fleet
    /// (fleet manager-driven service) and OEM (owner-scheduled). Not
    /// shown for rental — service is the rental company's problem.
    var showsUpcomingServiceCard: Bool { self != .rental }

    /// Whether to show the next-service countdown card on Home.
    /// Same rationale as upcomingServiceCard — rentals don't need it.
    var showsNextServiceCountdown: Bool { self != .rental }

    /// Whether to show the last-trip summary card. Rental drivers
    /// don't want to see the previous renter's trip, and even their
    /// own current-trip summary lives more naturally on the
    /// Trip-Time-Remaining card we add below.
    var showsLastTripCard: Bool { self != .rental }

    /// Whether to show the rental-specific cards: trip time remaining
    /// and return-to location. Only shown for rental drivers.
    var showsRentalTripCards: Bool { self == .rental }

    /// Whether to show the latest-alert (triage history) card.
    /// Reasonable for fleet/OEM; for rental we keep Home minimal.
    var showsLatestAlertCard: Bool { self != .rental }

    // MARK: - Alerts tab filtering

    /// Severity filter applied to the Alerts tab. Rental drivers only
    /// see CRITICAL and HIGH faults — anything they can't safely act
    /// on themselves (low tire pressure, sensor calibration drift) is
    /// the rental company's problem, not theirs. Fleet/OEM see all
    /// severities.
    var minAlertSeverity: AlertSeverity {
        switch self {
        case .rental: return .high
        default:      return .low
        }
    }
}

/// Ranked severity for alert filtering. Lower rawValue = more severe,
/// matching the backend's severityRank ordering.
enum AlertSeverity: Int, Comparable {
    case critical = 0
    case high     = 1
    case medium   = 2
    case low      = 3

    static func < (a: AlertSeverity, b: AlertSeverity) -> Bool { a.rawValue < b.rawValue }

    /// Parse from the backend's severity strings ("CRITICAL", "HIGH",
    /// etc.). Unknowns map to `.low` so they show by default rather
    /// than silently disappearing.
    static func from(_ raw: String?) -> AlertSeverity {
        switch (raw ?? "").uppercased() {
        case "CRITICAL": return .critical
        case "HIGH":     return .high
        case "MEDIUM":   return .medium
        case "LOW":      return .low
        default:         return .low
        }
    }
}
