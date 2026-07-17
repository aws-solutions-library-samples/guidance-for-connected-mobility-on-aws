import Foundation

// MARK: - Tenant config (subset returned by GET /tenants/{id}/config)

struct TenantConfig: Codable, Equatable {
    let tenantId: String
    let version: String
    let status: String
    let displayName: String
    let segment: String
    let branding: Branding
    let voice: Voice?
    let features: Features?

    struct Branding: Codable, Equatable {
        let logoUrl: String?
        let primaryColor: String
        let secondaryColor: String
        let fontFamily: String?
        let mapStyle: String?
        let greeting: Greeting
    }
    struct Greeting: Codable, Equatable {
        let voice: String
        let chat: String
        let app: String
    }
    struct Voice: Codable, Equatable {
        let engine: String
        let voiceId: String
        let locale: String
    }
    struct Features: Codable, Equatable {
        let imageInput: Bool?
        let agentAssistCoPilot: Bool?
        let calendarSlotProposal: Bool?
        let dmsBooking: Bool?
    }
}

// MARK: - Triage

struct TriageRequest: Codable {
    let vin: String
    let sessionId: String?
    let tenantId: String?
    let extra: [String: String]?
}

struct TriageResponse: Codable, Equatable {
    let statusCode: Int
    let classification: String   // "P0" | "P1" | "P2" | "P3"
    let sessionId: String
    let decidedAt: String
    let latencyMs: Int
}

// MARK: - Service history

/// One row from `cms-prod-storage-service-history`. Fields mirror the CMS
/// schema 1:1 (pass-through). We decode only what iOS actually uses —
/// unknown fields are silently ignored.
struct ServiceRecord: Codable, Equatable, Identifiable {
    /// Composite primary key, surfaced as Identifiable so ForEach can key on it.
    var id: String { "\(vehicleId)#\(serviceDate)" }

    let vehicleId: String
    let serviceDate: String           // ISO-8601 with micros
    let status: String                // "scheduled" (ours) | "COMPLETED" (seeded history)
    let category: String?             // "SCHEDULED" | "REPAIR" | "UNSCHEDULED"
    let serviceType: String?          // e.g. "OIL_CHANGE", "VSA_VOICE_TRIAGE"
    let description: String?
    let notes: String?
    let make: String?
    let model: String?
    let mileageAtService: Int?
    /// Total cost for the service. The CMS schema evolved from a flat
    /// numeric to a structured object in mid-2026, so the wire shape
    /// can be either:
    ///
    ///   "cost": 979.38
    ///   "cost": { "totalCost": 979.38, "laborCost": 548.76,
    ///             "partsCost": 355.98, "taxCost": 74.64,
    ///             "currency": "USD" }
    ///
    /// `ServiceCost` decodes both transparently. iOS uses the `total`
    /// computed property for display; the breakdown is available when
    /// callers want to show line items on a detail screen.
    let cost: ServiceCost?
    let provider: String?
    let providerType: String?

    /// Fields only present on VSA-originated (scheduled) rows.
    let requestNumber: String?        // VSA-YYYY-MM-DD-XXXX
    let triagePriority: String?       // P0 | P1 | P2 | P3
    let reportedSymptom: String?
    let scheduledFor: String?
    let source: String?               // "voice-assistant"
    let createdVia: String?
    let driverId: String?

    /// True when this row was created by the VSA voice-booking path.
    var isVsaOriginated: Bool {
        source == "voice-assistant" || serviceType == "VSA_VOICE_TRIAGE"
    }
}

/// Polymorphic cost field for `ServiceRecord`. The CMS service-history
/// table uses a structured object today (`{totalCost, laborCost,
/// partsCost, taxCost, currency}`), but older rows seeded as flat
/// numerics still exist and any future write path that emits a bare
/// number should also decode cleanly. We try the structured shape
/// first because that's what live data looks like; if that fails we
/// fall through to the flat-number shape.
///
/// The previous iOS model (`cost: Double?`) crashed JSONDecoder with
/// `typeMismatch: expected Double but found a dictionary` against the
/// structured shape, taking the entire Service tab + Home dashboard
/// down — `serviceHistoryLoadedAt` stayed nil, so
/// `hasLoadedInitialDashboard` never flipped to true and Home spun
/// on the skeleton view forever. Fixed 2026-05-19.
struct ServiceCost: Codable, Equatable {
    let total: Double?
    let labor: Double?
    let parts: Double?
    let tax: Double?
    let currency: String?

    /// What `cost` used to be on iOS before the schema evolved. Cards
    /// that previously read `record.cost` should read `cost?.total`
    /// now (or the convenience `record.totalCost` below).
    init(total: Double?, labor: Double? = nil, parts: Double? = nil,
         tax: Double? = nil, currency: String? = nil) {
        self.total = total
        self.labor = labor
        self.parts = parts
        self.tax = tax
        self.currency = currency
    }

    init(from decoder: Decoder) throws {
        // Try the structured object first.
        if let container = try? decoder.container(keyedBy: ObjectKey.self) {
            self.total    = try container.decodeIfPresent(Double.self, forKey: .totalCost)
            self.labor    = try container.decodeIfPresent(Double.self, forKey: .laborCost)
            self.parts    = try container.decodeIfPresent(Double.self, forKey: .partsCost)
            self.tax      = try container.decodeIfPresent(Double.self, forKey: .taxCost)
            self.currency = try container.decodeIfPresent(String.self, forKey: .currency)
            return
        }
        // Fall back to a flat numeric.
        let single = try decoder.singleValueContainer()
        if let n = try? single.decode(Double.self) {
            self.total = n
        } else {
            self.total = nil
        }
        self.labor = nil
        self.parts = nil
        self.tax = nil
        self.currency = nil
    }

    func encode(to encoder: Encoder) throws {
        // Round-trip as the structured shape — matches what the server
        // emits today. iOS doesn't actually post service rows back to
        // CMS, but Codable conformance requires this.
        var container = encoder.container(keyedBy: ObjectKey.self)
        try container.encodeIfPresent(total, forKey: .totalCost)
        try container.encodeIfPresent(labor, forKey: .laborCost)
        try container.encodeIfPresent(parts, forKey: .partsCost)
        try container.encodeIfPresent(tax, forKey: .taxCost)
        try container.encodeIfPresent(currency, forKey: .currency)
    }

    private enum ObjectKey: String, CodingKey {
        case totalCost, laborCost, partsCost, taxCost, currency
    }
}

struct ServiceHistoryResponse: Codable, Equatable {
    let vehicleId: String
    let scheduled: [ServiceRecord]
    let completed: [ServiceRecord]
    let generatedAt: String
}

/// Response from DELETE /vehicles/{vehicleId}/service-history.
/// Backend deletes every row where source = "voice-assistant" and
/// returns the count so the iOS Reset Demo flow can show a confirming
/// toast ("Cleared 4 demo bookings"). Failed reflects per-row deletes
/// that didn't succeed; expected to be 0 in practice.
struct VsaServiceCleanupResponse: Codable, Equatable {
    let vehicleId: String
    let deleted: Int
    let failed: Int
}

// MARK: - Vehicle context

/// Returned by GET /vehicles/{vehicleId}/context. Used at Assistant-tab
/// open to render the nameplate and to prime the voice prompt (backend
/// does the same lookup independently for the supervisor runtime).
struct VehicleContextResponse: Codable, Equatable {
    let vehicleId: String
    let vehicle: VehicleInfo
    let driver: DriverInfo?
    /// Active Diagnostic Trouble Codes currently open on the vehicle,
    /// newest first. Populated by the API Lambda from
    /// cms-prod-storage-dtc-history (status=ACTIVE). Empty array when
    /// the vehicle has no open faults. The same data is the input to
    /// the classifier, so iOS and the classifier show a consistent
    /// view.
    let activeDtcs: [ActiveDtc]?
    /// Server-computed vehicle health score (0..100). Single source of
    /// truth — both iOS Home tab and the CMS UI Vehicle Detail page
    /// render this value verbatim. Optional so older Lambda deploys
    /// (pre-2026-05-19) that don't emit it still decode cleanly; iOS
    /// renders 100 in the unlikely null case (matches the empty-state
    /// behaviour of the previous client-side computeHealthScore()).
    let healthScore: Int?
    /// Per-deduction breakdown that explains the score. Used today by
    /// the CMS UI for an expandable tooltip; iOS keeps the field on
    /// the model in case the Home tab grows a "why?" affordance later.
    let healthScoreBreakdown: HealthScoreBreakdown?
    let generatedAt: String
}

/// Server-emitted breakdown of the vehicle health score. Mirrors the
/// shape returned by the api-vehicle-context Lambda's
/// `_compute_health_score`. Each deduction's `reason` is a stable
/// string ("DTC P0299 HIGH", "Scheduled service overdue", "Vehicle
/// disconnected") that the CMS UI renders verbatim — keeping the
/// model `Decodable` rather than just a JSON dict means we get type
/// safety and can wire a `ForEach` directly off `deductions`.
struct HealthScoreBreakdown: Codable, Equatable {
    let score: Int
    let deductions: [Deduction]
    let computedAt: String

    struct Deduction: Codable, Equatable, Identifiable {
        /// Reason is unique within a breakdown (DTC codes are deduped
        /// upstream and the two non-DTC reasons appear at most once
        /// each), so it doubles as the SwiftUI ForEach key.
        var id: String { reason }
        let reason: String
        let amount: Int
    }
}

/// One open DTC surfaced to iOS. Mirrors the fields the /context Lambda's
/// `_load_active_dtcs` helper returns. All fields optional except `code`
/// and `status` because the upstream DynamoDB rows are inconsistent
/// (rows written by different source paths omit different fields).
struct ActiveDtc: Codable, Equatable, Identifiable {
    /// Provider-assigned id. Missing on some older rows, in which case
    /// `id` falls back to `code-timestamp` so SwiftUI has a stable key.
    let dtcId: String?
    /// SAE/OBD-II code like "P0217" or "C1234". Required.
    let code: String
    /// "ACTIVE" | "CLEARED" | "PENDING" — this endpoint returns only
    /// ACTIVE rows, so in practice you'll always see "ACTIVE".
    let status: String
    /// "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | or occasionally the
    /// lowercase variant. UI code should compare case-insensitively.
    let severity: String?
    /// System affected, derived from the code prefix by the producer:
    /// POWERTRAIN / CHASSIS / BODY / COMMUNICATION / UNKNOWN.
    let system: String?
    /// One-line human-readable description of the fault.
    let description: String?
    /// Millis since epoch for when the row was written. Some producers
    /// also write firstSeenAt as a separate field; iOS prefers
    /// `firstSeenAt` when present for display.
    let timestamp: Int64?
    let firstSeenAt: Int64?
    /// Which pipeline emitted this DTC. Examples:
    ///   flink-maintenance-processor   — Flink rule produced it from telemetry
    ///   fwe-uds-dtc                   — FleetWise UDS DTC extractor
    ///   force_event.py                — manual seed/injection
    let source: String?
    /// Whether this DTC should flag a service visit. True for serious
    /// faults; false for informational signals.
    let serviceRequired: Bool?

    var id: String {
        if let d = dtcId, !d.isEmpty { return d }
        return "\(code)-\(timestamp ?? firstSeenAt ?? 0)"
    }
}

struct VehicleInfo: Codable, Equatable {
    let vehicleId: String
    let vin: String?
    let make: String?
    let model: String?
    let year: Int?
    let color: String?
    let licensePlate: String?
    let vehicleType: String?
    let fuelType: String?
    let odometer: Int?
    let mileage: Int?
    let fuelLevel: Double?
    let engineTemp: Double?
    let batteryVoltage: Double?
    let lastSpeed: Double?
    let lastLatitude: Double?
    let lastLongitude: Double?
    let lastSeenAt: String?
    let fleetId: String?
    let status: String?
    let connectionStatus: String?
    let name: String?
    // Fields added 2026-05-05 to match the CMS Vehicle Detail page.
    // All optional so older Lambda versions (which don't emit these)
    // still decode cleanly. `fleetName` is denormalised server-side
    // from cms-prod-storage-fleets so the UI doesn't need to do its
    // own lookup. The rest are flat passthroughs from the vehicles
    // table.
    let fleetName: String?
    let enrollmentStatus: String?
    let purchaseDate: String?
    let purchasePrice: Double?
    let totalTrips: Int?

    /// Human-friendly title: "2022 Chevrolet Equinox" or closest subset.
    var displayTitle: String {
        var parts: [String] = []
        if let year { parts.append(String(year)) }
        if let make { parts.append(make) }
        if let model { parts.append(model) }
        return parts.isEmpty ? (name ?? vehicleId) : parts.joined(separator: " ")
    }
}

// MARK: - CMS driver self-vehicle-claim

/// Response from CMS `GET /api/v1/vehicles` — the fleet inventory the driver
/// picks from when claiming a vehicle. Items reuse `VehicleInfo`; extra envelope
/// fields (total/page/…) are ignored by the decoder.
struct ClaimableVehiclesResponse: Codable, Equatable {
    let vehicles: [VehicleInfo]
}

/// Response from CMS `PUT /api/v1/drivers/{id}` on a successful claim. We only
/// surface the optional mirror note; `driver`/`displacedDrivers` are ignored.
struct ClaimVehicleResponse: Codable, Equatable {
    let cognitoMirrorNote: String?
}

struct DriverInfo: Codable, Equatable {
    let driverId: String
    let firstName: String?
    let lastName: String?
    let email: String?
    let phone: String?
    let homeBase: String?
    let safetyScore: Double?
    let licenseClass: String?
    let licenseState: String?

    var fullName: String {
        [firstName, lastName].compactMap { $0 }.joined(separator: " ")
    }
}

// MARK: - /drivers/me — current driver resolved from Cognito JWT

/// Returned by GET /drivers/me. Used once at sign-in to resolve the signed-in
/// user to their CMS driver record and assigned vehicle. Both fields are
/// nullable: when the Cognito user has no matching CMS driver row (e.g. the
/// legacy demo@fleet.example user before auth-to-driver binding), iOS
/// falls back to VSAConfig defaults.
struct CurrentDriverResponse: Codable, Equatable {
    let driver: CurrentDriver?
    let vehicle: VehicleInfo?
    let email: String?
    let generatedAt: String
}

/// Richer driver shape than the nested DriverInfo inside VehicleContextResponse
/// because /drivers/me is the authoritative driver source and iOS needs
/// everything for the Home tab (license, experience, totals).
struct CurrentDriver: Codable, Equatable, Identifiable {
    var id: String { driverId }

    let driverId: String
    let firstName: String?
    let lastName: String?
    let email: String?
    let cognitoEmail: String?
    let phone: String?
    let homeBase: String?
    let safetyScore: Double?
    let licenseClass: String?
    let licenseState: String?
    let licenseNumber: String?
    let licenseExpiry: String?
    let hireDate: String?
    let yearsExperience: Int?
    let totalMiles: Int?
    let totalTrips: Int?
    let incidentCount: Int?
    let lastTripDate: String?
    let certifications: [String]?
    let status: String?
    let assignedVehicleId: String?

    var fullName: String {
        [firstName, lastName].compactMap { $0 }.joined(separator: " ")
    }

    /// Short initials for avatar placeholder. "SJ" for Stephanie Johnson.
    var initials: String {
        let f = (firstName?.first).map { String($0) } ?? ""
        let l = (lastName?.first).map { String($0) } ?? ""
        let combined = f + l
        return combined.isEmpty ? "?" : combined.uppercased()
    }
}

// MARK: - Trips

/// Returned by GET /vehicles/{id}/trips. Route array is stripped server-side.
struct TripsResponse: Codable, Equatable {
    let vehicleId: String
    let trips: [TripSummary]
    let generatedAt: String
}

/// One recent trip. Fields are pass-through from cms-prod-storage-trips
/// minus the route list. Optional-heavy because the schema evolved over
/// time; not every seeded row has every field.
struct TripSummary: Codable, Equatable, Identifiable {
    var id: String { tripId }

    let tripId: String
    let vehicleId: String?
    let vin: String?
    let fleetId: String?

    /// CMS stores driverName but the value is actually a driverId (DRV-NNNN).
    /// Keep the raw field; UI can look up the real name if needed.
    let driverName: String?

    let startTime: Int64?
    let endTime: Int64?
    let startTimeISO: String?
    let endTimeISO: String?
    let duration: Double?        // minutes
    let durationMs: Int64?
    let distance: Double?        // miles
    let totalDistance: Double?
    let averageSpeed: Double?
    let maxSpeed: Double?
    let driverScore: Int?
    let safetyEventsCount: Int?
    let tripType: String?
    let status: String?

    let startLocation: TripLocation?
    let endLocation: TripLocation?

    /// Best-available ISO8601 timestamp for the start of the trip.
    ///
    /// Trips produced by the Flink TripProcessor (2026-05+) expose
    /// `startTime` as epoch millis and omit `startTimeISO`. Older seeded
    /// trips carry `startTimeISO` directly. This accessor unifies both
    /// so Home-tab timestamps populate correctly regardless of which
    /// producer wrote the row. Returns "" when neither field is present,
    /// which matches the pre-2026-05-04 behavior of `startTimeISO ?? ""`.
    var effectiveStartTimeISO: String {
        if let iso = startTimeISO, !iso.isEmpty {
            return iso
        }
        if let ms = startTime {
            let dt = Date(timeIntervalSince1970: TimeInterval(ms) / 1000.0)
            let fmt = ISO8601DateFormatter()
            fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return fmt.string(from: dt)
        }
        return ""
    }

    /// Human-friendly summary used on the Home tab.
    var displayDate: String {
        let iso = effectiveStartTimeISO
        if !iso.isEmpty {
            return _formatIsoDate(iso)
        }
        return "—"
    }

    /// One-line summary: "9.5 mi · 21 min · score 96"
    var displaySummary: String {
        var parts: [String] = []
        if let d = distance ?? totalDistance {
            parts.append(String(format: "%.1f mi", d))
        }
        if let m = duration {
            parts.append(String(format: "%.0f min", m))
        }
        if let s = driverScore {
            parts.append("score \(s)")
        }
        return parts.joined(separator: " · ")
    }
}

struct TripLocation: Codable, Equatable {
    let latitude: Double?
    let longitude: Double?
    let address: String?
}

/// ISO-8601 date formatter lives at file scope so TripSummary can reuse it
/// without recreating it on every row.
private let _isoFormatter: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()
private let _mediumDateFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateStyle = .medium
    return f
}()
private func _formatIsoDate(_ iso: String) -> String {
    let date = _isoFormatter.date(from: iso) ?? ISO8601DateFormatter().date(from: iso)
    guard let date else { return String(iso.prefix(10)) }
    return _mediumDateFormatter.string(from: date)
}

// MARK: - Vehicle live state (GET /vehicles/{id}/live-state)

/// Realtime connection + telemetry state backed by the same Redis hash the
/// CMS UI reads. Refreshed separately from `session.currentVehicle` because
/// this data has a ~30s half-life; the vehicle record is stable.
struct VehicleLiveState: Codable, Equatable {
    let vehicleId: String
    /// "connected" or "disconnected". The server already applies CMS's
    /// 5-minute freshness window before returning, so iOS can trust this.
    let connectionStatus: String
    /// Origin of the connectionStatus decision, for debugging. Values:
    /// "redis-live-window", "redis", "ddb".
    let connectionStatusSource: String?
    /// ISO-8601 timestamp of the freshest last-connected signal.
    let lastConnectedAt: String?
    /// Convenience: seconds-since-last-connection so the client doesn't
    /// need to reparse the ISO string.
    let lastSeenAgoSeconds: Int?
    let fuelLevel: Double?
    let batteryLevel: Double?
    let speed: Double?
    let engineTemp: Double?
    // Added 2026-05-05 so iOS can read the same live Redis signal
    // values CMS shows. Both optional to stay backward-compatible
    // with older Lambda versions that didn't emit them.
    let odometer: Double?
    let batteryVoltage: Double?
    // Added 2026-05-06 for map parity with CMS UI's "Vehicle Location"
    // widget. DDB's lastLatitude/lastLongitude can be days stale (VEH-0047
    // was showing Phoenix from Apr 24 while telemetry had moved to Seattle);
    // the Lambda now reads sig_by_name["lat"]/["lng"] from the Redis
    // signals hash — the same source CMS reads — with DDB fallback.
    let latitude: Double?
    let longitude: Double?
    let heading: Double?

    var isConnected: Bool { connectionStatus.lowercased() == "connected" }
}

// MARK: - Safety events (GET /vehicles/{id}/safety-events)

/// Server response wrapper. `windowDays` echoes back the effective query
/// window (default 7) so the UI can render "Last 7 days" without guessing.
struct SafetyEventsResponse: Codable, Equatable {
    let vehicleId: String
    let windowDays: Int
    let events: [SafetyEvent]
    let generatedAt: String?
}

/// A single driver-facing safety event (harsh braking, rapid acceleration,
/// phone usage, speeding, crash, etc.) over the lookback window.
///
/// Severity is always a canonical string (CRITICAL/HIGH/MEDIUM/LOW) because
/// the backend normalises away the numeric-string variants (4=CRITICAL,
/// 1=LOW) that some simulator-seeded rows carry. See the Lambda handler for
/// the mapping rules.
///
/// `Identifiable` conformance uses `eventId` (DDB partition key, guaranteed
/// unique) so SwiftUI ForEach stays stable across refreshes.
struct SafetyEvent: Codable, Equatable, Identifiable {
    let eventId: String
    /// Lowercase snake_case — e.g. "harsh_acceleration", "phone_usage".
    /// The "safety." prefix from the event catalog has been stripped.
    let eventType: String
    /// Canonical string: CRITICAL / HIGH / MEDIUM / LOW.
    let severity: String
    let description: String?
    /// Epoch milliseconds. Present on every row (it's the GSI range key),
    /// optional here only because we want decoding to be tolerant.
    let timestamp: Int?
    /// ISO-8601. Synthesised from `timestamp` if the row had no
    /// explicit `createdAt`.
    let occurredAt: String?
    let resolved: Bool?
    let tripId: String?
    let location: SafetyEventLocation?

    var id: String { eventId }
}

struct SafetyEventLocation: Codable, Equatable {
    let latitude: Double
    let longitude: Double
}

// MARK: - API errors

enum APIError: Error, LocalizedError {
    case network(Error)
    case http(status: Int, body: String)
    case decoding(Error)
    case unauthenticated

    var errorDescription: String? {
        switch self {
        case .network(let e): return "Network: \(e.localizedDescription)"
        case .http(let s, let b): return "HTTP \(s): \(b)"
        case .decoding(let e): return "Decode: \(e.localizedDescription)"
        case .unauthenticated: return "Not signed in"
        }
    }
}


// MARK: - Booking flow (find-service-center + book)

/// POST /find-service-center request body. The Lambda accepts these
/// fields verbatim; field names match the backend handler's reads.
struct FindServiceCenterRequest: Encodable {
    /// Service capability the driver wants (e.g. "tire-inflation",
    /// "brakes", "diagnostics"). The backend's capability_map
    /// fuzzy-matches this against the centers' supported capabilities,
    /// so the iOS picker can use friendly labels here.
    let capability: String
    /// Driver's current location for distance sorting. iOS reads from
    /// session.liveState (Redis-backed) so the result agrees with the
    /// Vehicle tab map.
    let latitude: Double
    let longitude: Double
    /// Tenant persona that drives the result mix. Pulled from
    /// session.layoutSegment.rawValue. "oem" returns brand-only
    /// dealers; "rental" prefers chains; "fleet" returns the broad
    /// mix.
    let segment: String
    /// Optional brand filter. For OEM the backend additionally
    /// requires this when present (only that brand's dealers).
    let vehicleMake: String?
    /// Cap on returned centers. The backend clamps to 1-5.
    let maxResults: Int
}

/// Response from POST /find-service-center.
struct FindServiceCenterResponse: Decodable, Equatable {
    let found: Int
    let centers: [ServiceCenter]
}

/// One service center entry as returned by the backend. Field order
/// mirrors lambdas/api-find-service-center/handler.py:_shape_center
/// so any new field added there shows up on iOS without a redeploy.
struct ServiceCenter: Decodable, Equatable, Identifiable {
    var id: String { centerId }
    let centerId: String
    let name: String
    /// One of "dealer" | "independent" | "fleet-service" |
    /// "quick-service" | "body-shop" | "tire-specialist". UI uses
    /// this to colour and badge the card.
    let type: String
    /// Pre-formatted "{street}, {city}, {state} {zip}".
    let address: String
    let phone: String?
    /// Approx miles from the driver. nil only when the request didn't
    /// provide coords (we always do, so this should be populated).
    let distanceMiles: Double?
    let rating: Double?
    let averageWaitDays: Int?
    let fleetDiscount: Bool
    let brandsServiced: [String]
    /// Free-form slot strings ("Tuesday May 19 at 10:00 AM"). The book
    /// Lambda parses these back to UTC datetimes; iOS just renders.
    let nextAvailableSlots: [String]
    let hours: [String: String]?
}

/// POST /book request body. Field names match the backend handler's
/// required-keys list.
struct BookRequest: Encodable {
    let vehicleId: String
    let vin: String
    let driverId: String
    let tenantId: String
    let centerId: String
    let centerName: String
    let centerAddress: String
    let slot: String
    let capability: String
    let reportedSymptom: String
    let narrative: String
}

/// Response from POST /book. requestNumber is the audit handle shown
/// on the confirmation card.
struct BookResponse: Decodable, Equatable {
    let requestNumber: String
    let status: String
    /// ISO 8601 datetime, UTC, parsed from the slot string. Used by
    /// iOS to render "Tuesday at 10:00 AM" via DateFormatter rather
    /// than echoing the raw slot text.
    let scheduledFor: String
    let serviceDate: String
    let centerName: String
    let slot: String
}
