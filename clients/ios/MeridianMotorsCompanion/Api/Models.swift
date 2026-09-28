import Foundation

// MARK: - Tenant config (subset returned by GET /tenants/{id}/config)

struct TenantConfig: Codable, Equatable {
    let tenantId: String
    let version: String
    let status: String
    let displayName: String
    let segment: String
    /// Optional override for the Assistant surface title. Falls back to
    /// `LayoutSegment.assistantTitle` when absent, so tenants that do not set
    /// it keep the segment-appropriate default.
    let assistantTitle: String?
    /// "km" | "mi" — distance presentation for dealer/service-centre results.
    /// Absent means the backend default (miles).
    let distanceUnit: String?
    let branding: Branding
    let voice: Voice?
    let features: Features?
    /// Acquire / Finance journey stage configuration. Optional — tenants
    /// that predate the Acquire feature decode cleanly when this field is absent.
    let acquire: AcquireConfig?

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

// MARK: - Acquire / Finance journey stage

/// Per-tenant Acquire configuration. Added as an optional extension on
/// `TenantConfig` — tenants that predate Acquire decode cleanly when absent.
///
/// Schema authority: `docs/acquire-catalog-schema.md § 1`.
/// All fields are optional at the struct level; required fields carry
/// sensible defaults via `AcquireConfig.defaults` so Swift code does not
/// need to guard every access.
struct AcquireConfig: Codable, Equatable {
    /// Catalog-data version tag, e.g. `"genericmoto-2026-07"`. Used by
    /// `AcquireCatalogClient` to scope DDB queries to the correct catalog.
    let catalogVersion: String?
    /// ISO 4217 currency code, e.g. `"USD"` or `"INR"`.
    let currency: String?
    /// Rendered currency symbol, e.g. `"$"` or `"₹"`. Distinct from
    /// `currency` so the Swift UI never hardcodes currency glyphs.
    let currencySymbol: String?
    /// Human-readable noun for the vehicle category rendered in copy
    /// ("Configure your <label>"), e.g. `"Bike"`, `"Car"`, `"Truck"`.
    let vehicleCategoryLabel: String?
    /// Manufacturing plant definitions for this tenant. Used by
    /// OrderTrackerView to name the assembly location.
    let plants: [Plant]?
    /// Per-stage dwell-time bounds for the time-simulation order progressor.
    let deliveryCadence: DeliveryCadence?
    /// Lender definitions for the Finance step.
    let financePartners: FinancePartners?
    /// Absolute S3 or CloudFront URL prefix. Image keys stored in the
    /// DDB catalog items are resolved as `assetBaseUrl + imageKey`.
    let assetBaseUrl: String?
    /// Discover / Find journey stage configuration. Optional — tenants
    /// that do not participate in the Discover stage decode cleanly when
    /// this field is absent.
    let discover: DiscoverConfig?
    /// Trade-in upgrade offers surfaced by the Home banner.
    ///
    /// Tenant-supplied rather than computed on-device, and deliberately NOT
    /// hardcoded in Swift: the copy names real models and prices, and tenant
    /// brand strings are canary-forbidden in committed source. Absent or
    /// empty hides the banner entirely.
    let upgradeOffers: [UpgradeOffer]?
    /// The customer's EXISTING finance with this lender, when they have
    /// financed before. This is the loyalty argument made concrete: a repeat
    /// customer with a clean payment record is why the new rate is better.
    /// Absent for first-time buyers, in which case the comparison is hidden
    /// rather than shown with blanks.
    let existingFinance: ExistingFinance?

    struct ExistingFinance: Codable, Equatable {
        let lender: String?
        let originalAmount: String?
        let rate: String?
        let monthly: String?
        let termMonths: Int?
        let monthsRemaining: Int?
        let outstanding: String?
        let onTimePayments: Int?
        /// How the outstanding balance is handled at handover.
        let note: String?
    }

    /// One trade-in upgrade offer.
    ///
    /// Money fields are strings, not numbers, on purpose — they are
    /// pre-formatted by the tenant so the client never has to decide on
    /// grouping, separators, or currency placement (Indian lakh grouping and
    /// Western thousands grouping disagree, and getting it wrong in front of
    /// the customer looks careless).
    struct UpgradeOffer: Codable, Equatable, Identifiable {
        /// Stable id for SwiftUI diffing; falls back to the model name.
        var id: String { offerId ?? modelName }
        let offerId: String?
        /// Target model, e.g. the bike being upgraded TO.
        let modelName: String
        /// Short reason this offer suits the rider, ideally usage-derived.
        let rationale: String?
        /// Pre-formatted, e.g. "₹98,000".
        let price: String?
        /// Absolute URL of a hero image OF THE MODEL BEING OFFERED.
        ///
        /// Per-offer rather than derived from the owned vehicle's image
        /// sequence: the upgrade page must show the bike being sold, not the
        /// one being traded in. Nil renders a themed glyph — showing the WRONG
        /// vehicle is far worse than showing none.
        ///
        /// Tenant-supplied so no tenant image URL is committed.
        let imageUrl: String?
        /// Whether the tenant offers test rides for this model.
        let testRideAvailable: Bool?
        /// New-finance terms for THIS offer, so the wizard can argue the deal
        /// rather than just state a monthly figure.
        let finance: OfferFinance?
        /// Side-by-side of the customer's current vehicle against this one.
        ///
        /// Tenant-supplied and pre-formatted, and DELIBERATELY allowed to
        /// include rows where the current vehicle wins — a comparison showing
        /// only advantages reads as marketing, and an OEM audience will notice
        /// the omission immediately.
        let specComparison: [SpecRow]?

        struct SpecRow: Codable, Equatable, Identifiable {
            var id: String { label }
            let label: String
            let current: String?
            let new: String?
            /// "new" | "current" | "neutral" — which side this row favours.
            let favours: String?
        }

        struct OfferFinance: Codable, Equatable {
            let rate: String?
            let termMonths: Int?
            let monthly: String?
            /// e.g. "2.0% below standard" — the concrete loyalty benefit.
            let loyaltyRateBenefit: String?
        }
        /// Pre-formatted trade-in credit for the current vehicle.
        let tradeInCredit: String?
        /// Pre-formatted loyalty credit, and the DERIVED basis for it.
        ///
        /// The basis is shown to the customer on purpose: a bare discount
        /// figure reads as arbitrary, whereas "3 years, 18,400 km, complete
        /// service history" reads as earned. It is also the concrete proof
        /// that platform data is doing the work.
        let loyaltyDiscount: String?
        let loyaltyBasis: String?
        /// Pre-formatted price with the loyalty credit already applied.
        let priceAfterLoyalty: String?
        /// Pre-formatted monthly instalment.
        let monthly: String?
        /// Free-text validity, e.g. "Valid till 31 Aug".
        let validUntil: String?
        /// Mandatory small print. Rendered verbatim when present — never
        /// paraphrased or truncated, matching the agent's disclosure rule.
        let disclosure: String?
        /// The IVE Edition recommended for this offer (task 5.2 / Zone 2 Rev 3).
        ///
        /// The Edition is DISPLAYED in the configurator, not chosen — it arrives
        /// here from the agent's recommendation and is carried through to the
        /// order payload unchanged. All four values (`family`, `executive`,
        /// `adventure`, `entry-urban`) are representable. Absent offers fall back
        /// to `IvePackage.defaultFor(modelId:)`.
        let ivePackage: IvePackage?
    }

    // MARK: - Nested types

    /// One manufacturing plant definition.
    struct Plant: Codable, Equatable {
        let plantId: String?
        let displayName: String?
        let city: String?
        let region: String?
    }

    /// Per-stage dwell-time bounds used by the time-simulation Lambda.
    /// Each transition key matches the stage-identifier pairs listed in
    /// `docs/acquire-catalog-schema.md § 1 § deliveryCadence`.
    struct DeliveryCadence: Codable, Equatable {
        let orderPlacedToPayment: DwellRange?
        let paymentToAssignedToPlant: DwellRange?
        let assignedToPlantToSubAssembly: DwellRange?
        let subAssemblyToPaint: DwellRange?
        let paintToFinalAssembly: DwellRange?
        let finalAssemblyToQualityControl: DwellRange?
        let qualityControlToShipped: DwellRange?
        let shippedToAtDealer: DwellRange?
        let atDealerToReadyForDelivery: DwellRange?
    }

    /// Minimum/maximum hour range for a single stage transition.
    struct DwellRange: Codable, Equatable {
        let minHours: Int?
        let maxHours: Int?
    }

    /// Finance-partner definitions for the tenant.
    struct FinancePartners: Codable, Equatable {
        let primary: FinancePartner?
        let secondary: [FinancePartner]?
    }

    /// One finance partner (captive arm, bank, credit union, etc.).
    struct FinancePartner: Codable, Equatable {
        let partnerId: String?
        let displayName: String?
        /// `"captive"` | `"bank"` | `"credit-union"` | `"other"`.
        let kind: String?
    }
}

/// Discover / Find journey stage tenant configuration.
/// Nested inside `AcquireConfig.discover`. All fields are optional;
/// a tenant config that omits this block causes `DiscoverFlow` to fall back
/// to generic (non-brand-specific) copy constants defined in Swift.
///
/// Schema authority: spec `2026-08-04-cvx-oem-discover-order-meridian`
/// § Design — AcquireConfig.discover sub-block.
struct DiscoverConfig: Codable, Equatable {
    /// Greeting text shown in `DiscoverFlow`'s header on cold (Path A anonymous) entry.
    /// Example: `"Hi — I'll help you shop."` Generic fallback used when absent.
    let discoverGreeting: String?
    /// Up to 4 primed-prompt chip labels shown on cold entry. When absent,
    /// `DiscoverFlow` renders a generic set of generic-fallback prompts.
    let discoverPrimedPrompts: [String]?
    /// Greeting text shown on Path B (returning-owner warm-start) entry.
    /// Example: `"Welcome back — thinking about what's next?"`
    let pathBGreeting: String?
    /// Up to 4 primed-prompt chip labels shown on Path B warm-start entry.
    let pathBPrimedPrompts: [String]?
    /// Bedrock KB source category used for `bike_shopping_guide`-style grounding
    /// in Discover-phase agent turns. Default: `"bike_shopping_guide"`.
    let discoverKbSourceCategory: String?
    /// Optional CDN base URL for offer imagery. Image keys from `OFFER#` DDB
    /// items are resolved as `offersAssetBaseUrl + heroImageKey`.
    let offersAssetBaseUrl: String?
    /// Placeholder identifier for the Guardrails policy attached to
    /// `offers_lookup()` narration turns. Default: `"discover-offers-guardrails-v1"`.
    /// Policy authoring is a future spec; this identifier is a named-only
    /// placeholder in v1.
    let guardrailsIdentifier: String?
}

/// Discover → Order handoff DTO.
///
/// Emitted by `DiscoverFlow.onConverge` and consumed by
/// `ConfiguratorFlow(handoff:)` to pre-populate Step 1 (category) and
/// Step 2 (base model). When `handoff` is nil, `ConfiguratorFlow` opens
/// cold (backward-compatible path for the sibling's "Buy tab opens
/// Configurator directly" flow).
///
/// Schema authority: spec `2026-08-04-cvx-oem-discover-order-meridian`
/// § Design — STAR-JSON Discover → Order handoff.
/// The top-level field names align with the `discover.lead_capture`
/// STAR-JSON envelope (see `docs/tech.md § CVX Discover — STAR envelope`).
struct DiscoverHandoff: Codable, Equatable {
    /// Shared AgentCore session ID. Forwarded from `DiscoverFlow` to
    /// `ConfiguratorFlow` so the two flows share one AgentCore session
    /// and in-session context (stated preferences, focused model) survives
    /// the transition. Must be ≥33 chars per AgentCore API minimum
    /// (see `issues/2026-06-22-assistant-chat-runtime-session-id-too-short`).
    let discoverSessionId: String
    /// Lead ID returned by `lead_capture()` if the user saved their
    /// configuration or provided contact info during Discover.
    /// When present, ConfiguratorFlow can skip contact-info re-collection
    /// at Step 7 (Finance) and can back-reference this lead on the
    /// resulting order record.
    let leadId: String?
    /// Category identifier pre-selected in ConfiguratorFlow Step 1.
    /// Matches a `CAT#<categoryId>` key fragment in `vsa-acquire-catalog`.
    let preferredCategory: String?
    /// Model identifier pre-selected in ConfiguratorFlow Step 2.
    /// Matches a `MODEL#<categoryId>#<modelId>` key fragment.
    let preferredModelId: String?
    /// Persona snapshot if the session was LTM-hydrated (signed-in) or
    /// Path B mock-triggered. Nil for anonymous sessions.
    let personaSnapshot: PersonaSnapshot?
    /// Entry path that produced this handoff. `"pathA"` for anonymous
    /// or signed-in cold entry; `"pathB"` for returning-owner warm start.
    let sourcePath: String?
}

/// Lightweight persona snapshot carried through Discover → Order handoffs
/// and into the AgentCore `/assistant/chat` payload as system-turn context.
/// All fields optional — present only when LTM-hydrated or Path B mock.
struct PersonaSnapshot: Codable, Equatable {
    /// Stable identifier for the persona (Cognito sub or mock actor id).
    let actorId: String?
    /// Free-form engagement segment label, e.g. `"lease-maturing"`.
    let engagementSegment: String?
    /// Vehicle currently owned / leased by this persona, if known.
    let currentVehicleHint: String?
    /// Stated interest carried from LTM or seeded for Path B mock.
    let statedInterest: String?
}

// MARK: - IVE Edition (Zone 2 — task 5.2)

/// The Edition tier on a visitor's NFC key card.
///
/// Four values per `cvx/docs/REINVENT-2026-ZONE2-WHAT-WE-DELIVER-REV3.md` § 2(a).
/// All four are preserved in the data model; none is special-cased or suppressed.
///
/// The Edition is DISPLAYED in the configurator, not chosen — it arrives from
/// the accepted offer (warm path) or falls back to a per-model default (cold path).
/// An unset or sentinel Edition is a broken Zone 3 hand-off: the fallback must be
/// a real value. See task 5.2 constraints.
enum IvePackage: String, Codable, CaseIterable, Equatable {
    /// Family — versatile everyday package.
    case family       = "family"
    /// Executive — premium comfort and connectivity.
    case executive    = "executive"
    /// Adventure — off-road and touring oriented.
    case adventure    = "adventure"
    /// Entry urban — city-optimised essentials.
    case entryUrban   = "entry-urban"

    /// Human-readable display name for the Edition card.
    var displayName: String {
        switch self {
        case .family:     return "Family"
        case .executive:  return "Executive"
        case .adventure:  return "Adventure"
        case .entryUrban: return "Entry Urban"
        }
    }

    /// Short summary of bundled features shown on the Edition summary step.
    var featureSummary: String {
        switch self {
        case .family:
            return "Comfort seat, luggage hooks, family connectivity pack"
        case .executive:
            return "Premium audio, heated grips, connected dashboard"
        case .adventure:
            return "Rally suspension, skid plate, all-terrain tires"
        case .entryUrban:
            return "Compact footprint, urban connectivity, lightweight mode"
        }
    }

    /// Per-model default Edition when no offer is present.
    ///
    /// The fallback must be a real value — never nil or a sentinel — because
    /// the Edition is the payload on the visitor's NFC key card and Zone 3
    /// depends on it. See task F5.1 constraints.
    ///
    /// Mapping (from the Zone 2 Revision 2 spec lineup definitions):
    ///   - crestwind  → .family      (large three-row family SUV)
    ///   - azimuth    → .executive   (sedan)
    ///   - trailwind  → .adventure   (mid SUV)
    ///   - windrose   → .entryUrban  (compact/urban)
    ///
    /// Matching is case-insensitive and tolerates a prefix/suffix (e.g. "crestwind-lx").
    ///
    /// Unknown or absent model id resolves to `.family` — the least specific claim
    /// about a vehicle we cannot identify. `.entryUrban` would assert a small city
    /// car, `.adventure`/`.executive` assert more; `.family` is the broadest default.
    static func defaultFor(modelId: String?) -> IvePackage {
        guard let id = modelId else { return .family }
        let lower = id.lowercased()
        if lower.contains("crestwind") { return .family }
        if lower.contains("azimuth")   { return .executive }
        if lower.contains("trailwind") { return .adventure }
        if lower.contains("windrose")  { return .entryUrban }
        // Unknown model — broadest default that makes no assertion about vehicle type.
        return .family
    }
}

/// Interior style option for light configuration (task 5.3).
enum InteriorStyle: String, Codable, CaseIterable, Equatable {
    case classic  = "classic"
    case sport    = "sport"
    case touring  = "touring"

    var displayName: String {
        switch self {
        case .classic: return "Classic"
        case .sport:   return "Sport"
        case .touring: return "Touring"
        }
    }

    var description: String {
        switch self {
        case .classic: return "Traditional fabric with chrome accents"
        case .sport:   return "Alcantara inserts with contrast stitching"
        case .touring: return "Premium leather with lumbar support"
        }
    }
}

/// Handoff from `UpgradeFlow` into `ConfiguratorFlow` carrying the accepted offer's Edition.
///
/// Distinct from `DiscoverHandoff` — the Discover flow does not carry an Edition,
/// and mixing two separate handoff concerns into one type would couple them.
///
/// `edition` is Optional here only so that a cold-entry configurator (no preceding
/// offer) can be expressed as `ConfiguratorOfferHandoff?.none`. The configurator
/// always resolves it to a non-nil `IvePackage` before rendering, using
/// `IvePackage.defaultFor(modelId:)` as the per-model fallback.
struct ConfiguratorOfferHandoff: Equatable {
    /// Edition recommended by the agent and accepted by the visitor. Never nil
    /// on the warm path; the configurator replaces nil with `defaultFor(modelId:)`.
    let edition: IvePackage?
    /// The model identifier from the offer, for display context.
    let modelName: String?
    /// Optional firstName captured at Beat 0 (task 5.5 / Group 7).
    /// First name only — no surname field exists anywhere in the payload.
    let firstName: String?
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

/// SoH-adjusted traction-pack energy, for the battery card's "N of M kWh" line.
///
/// Nameplate capacity is what the pack held when new. State of health is how much
/// of that it still holds. So the energy actually available is
/// `capacity × soh × soc`, and the ceiling the pack can reach today is
/// `capacity × soh` — NOT nameplate.
///
/// Fixed 2026-08-21. The card previously computed `capacity × soc` against a
/// nameplate denominator, which on the demo Trailwind (94 kWh nameplate, 94% SoH,
/// 72% SoC) rendered "68 of 94 kWh usable" where the pack actually holds ~64 of a
/// now-88 kWh ceiling. Roughly 4 kWh optimistic, and it widens as the pack ages —
/// the error is proportional to degradation, so it is smallest exactly when nobody
/// would care and largest when it matters. The denominator was the worse half:
/// 94 kWh is a number this pack can no longer reach, so it read as a target the
/// driver was falling short of rather than a ceiling that has moved.
enum PackEnergy {

    /// Energy available now and the pack's present full capacity, both in kWh.
    ///
    /// Returns `nil` when the figure cannot be stated honestly — absent or
    /// non-positive capacity, or absent state of charge. The card treats `nil` as
    /// "render nothing", consistent with its em-dash-not-zero rule: a missing
    /// reading must never be shown as a number.
    ///
    /// Absent `sohPercent` falls back to nameplate rather than returning `nil`,
    /// which is the pre-2026-08-21 behaviour. Deliberate for rows where SoH was
    /// never recorded: an unadjusted figure is a known small overstatement,
    /// whereas hiding the line would remove information the driver had before.
    static func usable(
        capacityKwh: Double?,
        socPercent: Double?,
        sohPercent: Double?
    ) -> (now: Double, full: Double)? {
        guard let capacityKwh, capacityKwh > 0, let socPercent else { return nil }

        // Clamped because a row reporting SoH above 100 (seen on freshly
        // calibrated packs) would otherwise print a capacity above nameplate, and
        // a negative value would print negative energy.
        let sohFraction = min(max((sohPercent ?? 100) / 100, 0), 1)
        let socFraction = min(max(socPercent / 100, 0), 1)

        let full = capacityKwh * sohFraction
        return (now: full * socFraction, full: full)
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
    /// Odometer reading in **miles**.
    ///
    /// Unit is authoritative from the telemetry contract:
    /// `deployment/scripts/signal_catalog_seed.json` defines the `odometer` signal as
    /// `{"signal_name": "Odometer", "unit": "miles", "vss_path": "Vehicle.Odometer"}`,
    /// and the CMS web UI renders it as `mi` at `FleetDetailsPage.tsx:554` and
    /// `VehicleDetailView.tsx:1037`/`:1248` off the same DynamoDB rows.
    ///
    /// **This comment exists because its absence caused a bug.** With no declared
    /// unit, `HomeTabView.odometerCell` rendered the value as `"… km"` while
    /// `VehicleTabView` formatted the same value as `mi`, so one vehicle showed two
    /// different distances on two screens; and `EnergySavingsProjection` computed a
    /// customer-facing money figure treating miles as kilometres. Neither site was
    /// wrong about its own intent — there was simply nothing to be right about.
    /// Do not remove this without giving the unit another authoritative home.
    let odometer: Int?
    /// Distance travelled in **miles**. Legacy sibling of `odometer`.
    ///
    /// Both fields exist and callers generally read `odometer ?? mileage`. Same unit;
    /// see `odometer` above.
    let mileage: Int?
    let fuelLevel: Double?
    let engineTemp: Double?
    let batteryVoltage: Double?
    /// Battery state of HEALTH, percent — remaining capacity against original.
    ///
    /// Distinct from state of charge, which for a BEV arrives on `fuelLevel`
    /// (the backend aliases the Redis `ev_soc` signal onto it, and both iOS
    /// surfaces relabel that row "Battery" when `fuelType` is electric). Charge
    /// is how full the pack is now; health is how much pack is left after
    /// 443 trips. Nil for ICE vehicles and for EVs whose row predates the field.
    let batterySoh: Double?
    /// Usable pack size in kWh. Already on the vehicle row but previously not
    /// projected by `/vehicles/{id}/context`, so the app could render a
    /// percentage with no idea of the energy behind it.
    let batteryCapacityKwh: Double?
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
    /// "connected" or "disconnected". The server applies its own freshness
    /// window before returning, so iOS trusts this and must NOT re-derive
    /// connectedness from timestamps — see
    /// .kiro/specs/2026-08-19-cms-connection-status-single-source/ HARD GATE 2,
    /// where the CMS web client was doing exactly that and overriding the server.
    ///
    /// The window's length is deliberately not restated here. This comment
    /// previously said "5-minute", which was already wrong: CMS's main_api used
    /// 120 s and now uses STALENESS_WINDOW_S = 180 s
    /// (modules/cms_ui/source/handlers/main_api/connection_status.py), and this
    /// endpoint is VSA's /vehicles/{id}/live-state, which may apply its own.
    /// A number duplicated into a client comment is a number that goes stale.
    let connectionStatus: String
    /// Origin of the connectionStatus decision, for debugging.
    ///
    /// NOTE (2026-08-19): no server currently emits this. The only writer is
    /// MeridianMotorsCompanionApp.swift, which sets "websocket" for locally-applied live
    /// updates. The values "redis-live-window" / "redis" / "ddb" documented here
    /// previously describe a server contract that does not exist — kept optional
    /// so it stays harmless, but do not branch on it expecting server values.
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
