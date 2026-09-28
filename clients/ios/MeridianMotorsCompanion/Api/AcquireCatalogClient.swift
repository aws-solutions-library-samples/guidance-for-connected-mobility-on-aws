import Foundation

// MARK: - Acquire error type

/// Typed errors surfaced by `AcquireCatalogClient`. Every networking,
/// decoding, or auth failure maps to a specific case so callers can
/// display appropriate recovery UI without stringing-matching error messages.
///
/// The `/acquire/*` REST routes are not provisioned server-side in this
/// window (demo-scope constraint per decisions.md 2026-07-28 "Demo scope").
/// Methods in `AcquireCatalogClient` catch `APIError` and rethrow as
/// `AcquireError.endpointUnavailable` when a 404/5xx is returned, so the
/// UI never crashes or hangs waiting for a response that will never arrive.
enum AcquireError: LocalizedError {
    /// The `/acquire/*` endpoint is not yet provisioned server-side.
    /// Expected in the current demo window — UIs should surface a graceful
    /// fallback rather than blocking the user.
    case endpointUnavailable(String)
    /// The tenant has no `acquire` config block; flow cannot proceed.
    case configMissing
    /// Network connectivity problem (no response at all).
    case network(Error)
    /// HTTP response received but status is outside 2xx.
    case http(status: Int, body: String)
    /// Response body could not be decoded into the expected DTO.
    case decoding(Error)
    /// User is not authenticated.
    case unauthenticated
    /// Lead submission failed server-side validation (e.g. invalid email
    /// format, phone format). `reason` carries the server-supplied message.
    case leadValidationFailed(reason: String)
    /// The requested offer does not exist or is no longer active.
    case offerNotFound

    var errorDescription: String? {
        switch self {
        case .endpointUnavailable(let path):
            return "Catalog endpoint not yet available (\(path)). Using in-session data."
        case .configMissing:
            return "Acquire configuration is missing for this tenant."
        case .network(let err):
            return "Network error: \(err.localizedDescription)"
        case .http(let status, let body):
            return "Server error \(status): \(body.prefix(120))"
        case .decoding(let err):
            return "Response format error: \(err.localizedDescription)"
        case .unauthenticated:
            return "Not signed in."
        case .leadValidationFailed(let reason):
            return "Lead validation failed: \(reason)"
        case .offerNotFound:
            return "The requested offer was not found or is no longer active."
        }
    }

    /// Replace email- and phone-shaped substrings with fixed placeholders.
    ///
    /// Applied to server-supplied validation messages before they reach
    /// `AcquireError.leadValidationFailed`, so a message that quotes the
    /// submitted contact value back cannot carry it into an error string that a
    /// caller might log or display. The redaction is deliberately coarse — it
    /// over-matches rather than under-matches, because the cost of mangling a
    /// diagnostic string is far lower than the cost of emitting a customer's
    /// email address.
    static func redactContactPII(_ text: String) -> String {
        var out = text
        // Emails: local@domain.tld
        out = out.replacingOccurrences(
            of: "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}",
            with: "<email-redacted>",
            options: .regularExpression
        )
        // Phone numbers: optional +, then 7+ digits with common separators.
        // Runs after the email pass so an email's digits are already gone.
        out = out.replacingOccurrences(
            of: "\\+?[0-9][0-9()\\-.\\s]{6,}[0-9]",
            with: "<phone-redacted>",
            options: .regularExpression
        )
        return out
    }

    /// True when the error is the expected "endpoint not provisioned yet"
    /// state — callers can use this to suppress error banners and fall back
    /// silently to in-session state.
    var isEndpointUnavailable: Bool {
        if case .endpointUnavailable = self { return true }
        return false
    }
}

// MARK: - Catalog DTOs

/// One vehicle category from the catalog, e.g. "Sport", "Commuter".
/// Backed by `CAT#<categoryId>` DDB items under `vsa-acquire-catalog`.
struct CatalogCategory: Codable, Identifiable, Equatable {
    let categoryId: String
    let displayName: String
    let symbolName: String?         // SF Symbol or asset key for the card icon
    let sortOrder: Int?

    var id: String { categoryId }
}

/// One base model within a category, e.g. "Meridian 300", "Solstice-e".
struct CatalogModel: Codable, Identifiable, Equatable {
    let modelId: String
    let categoryId: String
    let displayName: String
    let basePrice: Double?
    let imageKey: String?           // resolved against AcquireConfig.assetBaseUrl
    /// Top-line spec badges rendered on the model card.
    let specBadges: [SpecBadge]?
    let sortOrder: Int?

    var id: String { modelId }

    struct SpecBadge: Codable, Equatable {
        let label: String           // e.g. "34 hp", "150 km range"
        let symbolName: String?     // optional SF Symbol
    }
}

/// One variant / trim of a base model, e.g. "Standard", "Racing".
struct CatalogVariant: Codable, Identifiable, Equatable {
    let variantId: String
    let modelId: String
    let displayName: String
    let priceAdder: Double?         // added to base model price; may be 0 or negative
    let specOverrides: [String: String]?
    let sortOrder: Int?

    var id: String { variantId }
}

/// One color/livery option for a variant.
struct CatalogColor: Codable, Identifiable, Equatable {
    let colorId: String
    let variantId: String
    let displayName: String
    /// Hex string, e.g. `"#C0392B"`. Used to render the swatch circle when
    /// no imageKey is present.
    let hexColor: String?
    let imageKey: String?
    let priceAdder: Double?

    var id: String { colorId }
}

/// One optional accessory that can be added to an order.
struct CatalogAccessory: Codable, Identifiable, Equatable {
    let accessoryId: String
    let displayName: String
    let description: String?
    let price: Double?
    let categoryId: String?         // category-scoped or nil for universal
    let imageKey: String?
    let sortOrder: Int?
    /// Additional manufacturing lead days added to the customer-facing delivery
    /// estimate when this accessory is selected. Optional — accessories that
    /// install at the dealer (rather than on the assembly line) omit this or
    /// set 0; line-side accessories carry a positive value (e.g. 7 or 14 days).
    let leadDays: Int?

    var id: String { accessoryId }
}

/// The full catalog payload returned by `GET /acquire/catalog/{tenant}`.
struct CatalogResponse: Codable {
    let tenantId: String
    let catalogVersion: String?
    let categories: [CatalogCategory]
    let models: [CatalogModel]
    let variants: [CatalogVariant]
    let colors: [CatalogColor]
    let accessories: [CatalogAccessory]
}

// MARK: - Order DTOs

/// One pipeline-stage progress record inside an order.
struct OrderStageRecord: Codable, Equatable {
    let stageId: String             // matches `PipelineStage.id`
    let enteredAt: Date?
    let narration: String?
    let metadata: [String: String]?
}

/// Full order record returned by `GET /acquire/orders/{id}`.
/// This is the same shape the backend writes via `reservation_handoff`.
struct AcquireOrder: Codable, Identifiable, Equatable {
    let orderId: String
    let tenantId: String
    let currentStage: String        // matches a `PipelineStage.id` raw value
    let stages: [OrderStageRecord]
    let modelId: String?
    let variantId: String?
    let colorId: String?
    let selectedAccessoryIds: [String]?
    let reservationDepositRef: String?
    let dealerRef: String?
    let vinAssigned: String?
    let createdAt: Date?
    let updatedAt: Date?

    var id: String { orderId }
}

/// Thin request body for `POST /acquire/reservations`.
///
/// Extended for Zone 2 (tasks 5.2, 5.3, 5.5):
/// - `edition`: IVE Edition arriving from the accepted offer; never nil in the payload.
/// - `interiorStyle`: interior style selected during light configuration.
/// - `firstName`: first name captured at Beat 0. First name only — no surname field.
///   Omitted cleanly (key absent) when not captured. Never logged.
///
/// Cross-repo dependency (task 5.5): adding `firstName` to the `order.placed` event
/// that Zone 3 subscribes to is a CVX-side change. This file ensures the client sends
/// it; see task 8.4 for the counterpart filing.
struct ReservationRequest: Codable {
    let tenantId: String
    let modelId: String
    let variantId: String
    let colorId: String
    let selectedAccessoryIds: [String]
    let depositRef: String?
    let qualificationTier: String?
    let discoverSessionId: String?  // forwarded from DiscoverHandoff when present
    let leadId: String?
    /// IVE Edition from the accepted offer. Never nil in a well-formed payload.
    let edition: String?
    /// Interior style selected during light configuration (task 5.3).
    let interiorStyle: String?
    /// First name captured at Beat 0 (task 5.5 / Group 7).
    /// First name only — no surname field exists anywhere in the payload. Never logged.
    let firstName: String?
    /// Handover method: "pickup" or "delivery". Optional for wire back-compat.
    let handoverMethod: String?
    /// Center id for dealer pickup. Nil for home delivery. Optional for wire back-compat.
    let handoverCenterId: String?

    /// Encodes only present optional fields; absent keys are omitted entirely
    /// (not encoded as `null`) to keep the wire payload minimal.
    /// `firstName` is trimmed before encoding and omitted if empty.
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(tenantId, forKey: .tenantId)
        try c.encode(modelId, forKey: .modelId)
        try c.encode(variantId, forKey: .variantId)
        try c.encode(colorId, forKey: .colorId)
        try c.encode(selectedAccessoryIds, forKey: .selectedAccessoryIds)
        try c.encodeIfPresent(depositRef, forKey: .depositRef)
        try c.encodeIfPresent(qualificationTier, forKey: .qualificationTier)
        try c.encodeIfPresent(discoverSessionId, forKey: .discoverSessionId)
        try c.encodeIfPresent(leadId, forKey: .leadId)
        try c.encodeIfPresent(edition, forKey: .edition)
        try c.encodeIfPresent(interiorStyle, forKey: .interiorStyle)
        // firstName: omit key entirely when nil or whitespace-only.
        // Never logged — it is the one piece of visitor-supplied data in the journey.
        if let firstName = firstName {
            let trimmed = firstName.trimmingCharacters(in: .whitespaces)
            if !trimmed.isEmpty {
                try c.encode(trimmed, forKey: .firstName)
            }
        }
        try c.encodeIfPresent(handoverMethod, forKey: .handoverMethod)
        try c.encodeIfPresent(handoverCenterId, forKey: .handoverCenterId)
    }

    private enum CodingKeys: String, CodingKey {
        case tenantId, modelId, variantId, colorId, selectedAccessoryIds,
             depositRef, qualificationTier, discoverSessionId, leadId,
             edition, interiorStyle, firstName, handoverMethod, handoverCenterId
    }
}

/// Response from `POST /acquire/reservations`.
struct ReservationResponse: Codable {
    let orderId: String
    let orderNumber: String         // human-readable, e.g. "ORD-2026-07-0042"
    let depositRef: String?
}

// MARK: - Lead + Offer DTOs

/// Contact information carried in a lead submission.
/// All PII is supplied by the user explicitly through the consent modal.
struct LeadContactInfo: Codable, Equatable {
    let email: String
    /// Optional. E.164 format preferred; empty string when omitted.
    let phone: String?
    let firstName: String?
    let lastName: String?
}

/// Saved configuration preferences forwarded from DiscoverFlow.
/// Both fields optional — the lead is valid without any pre-selection.
struct LeadSavedConfig: Codable, Equatable {
    let preferredCategory: String?
    let preferredModelId: String?
}

/// Consent flags required by the `lead_capture` deterministic seam.
///
/// **`contactConsent` MUST be `true` before this type is used in a POST.**
/// `AcquireCatalogClient.postLead` enforces this client-side as a
/// deterministic seam (spec § Tier classification — consent gate on
/// `lead_capture`) mirroring the server-side `lead_capture` tool rule.
struct LeadConsentFlags: Codable, Equatable {
    /// Required. Must be `true`. Indicates the user has consented to being
    /// contacted by the dealership / OEM. POST is refused when `false`.
    let contactConsent: Bool
    /// Optional marketing communications opt-in. Defaults `false`.
    let marketingConsent: Bool
}

/// Response from `POST /acquire/leads`.
struct LeadResponse: Codable, Equatable {
    let leadId: String
    let tenantId: String
    let createdAt: String?
}

/// One promotional offer returned by `GET /acquire/offers/{modelId}`.
/// `disclosureTxt` is required and must be rendered verbatim per spec
/// § deterministic seams.
struct Offer: Codable, Identifiable, Equatable {
    let offerId: String
    let modelId: String
    let displayName: String
    let body: String?
    let depositAmount: Double?
    let validFrom: String?
    let validUntil: String?
    let regions: [String]?
    let heroImageKey: String?
    /// Verbatim disclosure text. Must be presented unmodified to the user.
    let disclosureTxt: String

    var id: String { offerId }
}

/// Response envelope from `GET /acquire/offers/{modelId}`.
private struct OffersResponse: Codable {
    let offers: [Offer]
}

/// Full lead record from `GET /acquire/leads/{leadId}`.
struct Lead: Codable, Equatable {
    let leadId: String
    let tenantId: String
    let contactInfo: LeadContactInfo?
    let savedConfig: LeadSavedConfig?
    let contactConsent: Bool
    let marketingConsent: Bool
    let createdAt: String?
    let sourcePath: String?
}

// MARK: - OrderFetching protocol

/// Dependency-injection seam for `OrderTrackerViewModel.fetchOrder`.
///
/// `AcquireCatalogClient` conforms to this protocol below.  Tests inject a
/// lightweight `MockOrderFetcher` that throws or returns canned data without
/// making any network call.  Production code never supplies a value — the
/// default behaviour builds the real client.
protocol OrderFetching: Sendable {
    func fetchOrder(orderId: String) async throws -> AcquireOrder
}

extension AcquireCatalogClient: OrderFetching {}

// MARK: - AcquireCatalogClient

/// REST client for the `/acquire/*` routes.
///
/// **Demo-scope constraint (decisions.md 2026-07-28 "Demo scope"):**
/// The six `/acquire/*` routes are not provisioned server-side in this
/// window. All methods catch HTTP 404 / 5xx and rethrow as
/// `AcquireError.endpointUnavailable` so callers can fall back gracefully
/// to in-session or handoff-supplied state without crashing or hanging.
actor AcquireCatalogClient {
    private let session: URLSession
    private let idTokenProvider: () -> String?
    private let baseURL: URL

    init(idTokenProvider: @escaping () -> String?, baseURL: URL = VSAConfig.restApiUrl) {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 12
        config.waitsForConnectivity = false
        self.session = URLSession(configuration: config)
        self.idTokenProvider = idTokenProvider
        self.baseURL = baseURL
    }

    // MARK: - Public API

    /// `GET /acquire/catalog/{tenantId}`
    ///
    /// Returns the full catalog (categories, models, variants, colors, accessories)
    /// for the tenant. On 404 or any server error, returns
    /// `AcquireError.endpointUnavailable` — the UI should fall back to the empty
    /// state and allow voice-assistant-driven configuration.
    func fetchCatalog(tenantId: String) async throws -> CatalogResponse {
        try await get(
            path: "acquire/catalog/\(tenantId)",
            as: CatalogResponse.self
        )
    }

    /// `GET /acquire/orders/{orderId}`
    ///
    /// Returns the current order record including pipeline-stage progress.
    /// On 404 (order not yet replicated to REST surface) returns
    /// `AcquireError.endpointUnavailable`.
    func fetchOrder(orderId: String) async throws -> AcquireOrder {
        try await get(
            path: "acquire/orders/\(orderId)",
            as: AcquireOrder.self
        )
    }

    /// `POST /acquire/reservations`
    ///
    /// Submits the configured order as a reservation. The mock backend returns a
    /// fake order id and order number immediately; the real backend would call
    /// `reservation_handoff()` and write to `vsa-acquire-orders`.
    func createReservation(_ body: ReservationRequest) async throws -> ReservationResponse {
        try await post(
            path: "acquire/reservations",
            body: body,
            as: ReservationResponse.self
        )
    }

    /// `POST /acquire/leads`
    ///
    /// Captures a sales lead from the Discover flow.
    ///
    /// **Consent gate (spec § Tier classification, deterministic seam):**
    /// Throws `AcquireError.leadValidationFailed(reason:)` immediately — without
    /// making a network call — when `consentFlags.contactConsent` is `false`.
    /// This mirrors the server-side `lead_capture` tool rule client-side so the
    /// constraint is enforced at both boundaries, not just one.
    ///
    /// On HTTP 422 the server returned a validation error (e.g. invalid email
    /// format); this surfaces as `AcquireError.leadValidationFailed(reason:)`.
    /// On 404 / 5xx returns `AcquireError.endpointUnavailable` per the demo-scope
    /// graceful-degradation contract.
    func postLead(
        contactInfo: LeadContactInfo,
        savedConfig: LeadSavedConfig? = nil,
        consentFlags: LeadConsentFlags
    ) async throws -> LeadResponse {
        // Deterministic consent gate — never send when consent is absent.
        // This is intentional client-side enforcement of a spec seam, not
        // defensive programming: the server enforces the same rule, but
        // "rely only on the server" is explicitly rejected by spec §
        // "IMPORTANT: postLead must refuse to send when contact consent is false".
        guard consentFlags.contactConsent else {
            throw AcquireError.leadValidationFailed(
                reason: "Contact consent is required before a lead can be submitted."
            )
        }

        // Build the request body as a simple encodable struct.
        struct LeadRequest: Encodable {
            let contactInfo: LeadContactInfo
            let savedConfig: LeadSavedConfig?
            let consentFlags: LeadConsentFlags
        }
        let body = LeadRequest(
            contactInfo: contactInfo,
            savedConfig: savedConfig,
            consentFlags: consentFlags
        )
        return try await post(
            path: "acquire/leads",
            body: body,
            as: LeadResponse.self,
            handle422AsValidationError: true
        )
    }

    /// `GET /acquire/offers/{modelId}`
    ///
    /// Returns active promotional offers for a specific catalog model. `region`
    /// is an optional filter; when `nil` all regions are returned.
    ///
    /// On 404 returns `AcquireError.offerNotFound`. On 5xx returns
    /// `AcquireError.endpointUnavailable` per the demo-scope contract.
    func fetchOffers(modelId: String, region: String? = nil) async throws -> [Offer] {
        var path = "acquire/offers/\(modelId)"
        if let r = region {
            path += "?region=\(r)"
        }
        do {
            let envelope = try await get(path: path, as: OffersResponse.self)
            return envelope.offers
        } catch AcquireError.http(let status, _) where status == 404 {
            throw AcquireError.offerNotFound
        }
    }

    /// `GET /acquire/leads/{leadId}`
    ///
    /// Fetches a previously-captured lead record. Used by the Path A → Path A
    /// resumption flow so a returning visitor can pick up where they left off.
    ///
    /// On 404 returns `AcquireError.endpointUnavailable` (the lead may not yet
    /// have replicated to the REST surface) per the demo-scope contract.
    func fetchLeadById(leadId: String) async throws -> Lead {
        try await get(path: "acquire/leads/\(leadId)", as: Lead.self)
    }

    // MARK: - Internals

    private func get<T: Decodable>(path: String, as _: T.Type) async throws -> T {
        guard let token = idTokenProvider() else { throw AcquireError.unauthenticated }
        let url = baseURL.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await perform(req, path: path)
    }

    private func post<Body: Encodable, T: Decodable>(
        path: String,
        body: Body,
        as _: T.Type,
        handle422AsValidationError: Bool = false
    ) async throws -> T {
        guard let token = idTokenProvider() else { throw AcquireError.unauthenticated }
        let url = baseURL.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        return try await perform(req, path: path, handle422AsValidationError: handle422AsValidationError)
    }

    private func perform<T: Decodable>(
        _ req: URLRequest,
        path: String,
        handle422AsValidationError: Bool = false
    ) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw AcquireError.network(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw AcquireError.endpointUnavailable(path)
        }
        // Treat 404 and any 5xx as "endpoint not yet provisioned" per
        // the demo-scope constraint — don't crash or block the UI.
        if http.statusCode == 404 || http.statusCode >= 500 {
            throw AcquireError.endpointUnavailable(path)
        }
        // 422 Unprocessable Entity = server-side validation failure.
        // Only rethrow as leadValidationFailed when the caller explicitly
        // requests it (postLead); all other callers get the generic http error.
        if http.statusCode == 422 && handle422AsValidationError {
            let bodyText = String(data: data, encoding: .utf8) ?? "Validation error"
            let rawReason: String
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String ?? json["message"] as? String {
                rawReason = detail
            } else {
                rawReason = String(bodyText.prefix(200)).trimmingCharacters(in: .whitespacesAndNewlines)
            }
            // Redact contact PII before it enters an error string. The only
            // caller is postLead, whose request body carries an email and phone,
            // and a validation message commonly quotes the offending value back
            // ("email 'jane@example.com' is invalid"). `errorDescription`
            // interpolates this reason, so any caller that surfaces or logs the
            // error would emit the address verbatim. Redacting at the boundary
            // is cheaper than auditing every present and future call site.
            // Hardening Suggestion from security-review cycle 3 of spec
            // 2026-08-04-cvx-oem-discover-order-meridian.
            throw AcquireError.leadValidationFailed(reason: AcquireError.redactContactPII(rawReason))
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw AcquireError.http(status: http.statusCode, body: body)
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw AcquireError.decoding(error)
        }
    }
}
