import Foundation

/// Thin wrapper over URLSession for the two REST endpoints we need.
/// Everything else (retries, pagination, offline) is out of scope for v1.
actor VSAClient {
    let session: URLSession
    private let idTokenProvider: () -> String?

    init(idTokenProvider: @escaping () -> String?) {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.waitsForConnectivity = false
        self.session = URLSession(configuration: config)
        self.idTokenProvider = idTokenProvider
    }

    /// Test-seam initialiser. Accepts a pre-configured URLSession so unit tests
    /// can inject a `StubURLProtocol`-backed session without network access.
    /// Not used in production code — the parameter-less `init(idTokenProvider:)`
    /// is the production path.
    init(idTokenProvider: @escaping () -> String?, session: URLSession) {
        self.session = session
        self.idTokenProvider = idTokenProvider
    }

    func getTenantConfig(_ tenantId: String) async throws -> TenantConfig {
        try await get(
            path: "/tenants/\(tenantId)/config",
            as: TenantConfig.self
        )
    }

    func postTriage(_ body: TriageRequest) async throws -> TriageResponse {
        try await post(
            path: "/triage",
            body: body,
            as: TriageResponse.self
        )
    }

    /// GET /vehicles/{vehicleId}/service-history
    /// Returns both scheduled (upcoming voice-booked) and completed (seeded historical)
    /// service records in a single call. The backend reads CMS directly; no
    /// intermediate store.
    func getServiceHistory(vehicleId: String) async throws -> ServiceHistoryResponse {
        try await get(
            path: "/vehicles/\(vehicleId)/service-history",
            as: ServiceHistoryResponse.self
        )
    }

    /// DELETE /vehicles/{vehicleId}/service-history
    /// Deletes all service-history rows tagged source="voice-assistant"
    /// for this vehicle. Backend filters by source so seeded historical
    /// rows (oil changes, recalls, etc.) survive — only demo bookings
    /// created via Nova are removed. Returns deleted count for logging.
    /// Used by the Reset Demo button on the Account tab so each demo
    /// run starts with a clean Service tab.
    @discardableResult
    func deleteVsaServiceRecords(vehicleId: String) async throws -> VsaServiceCleanupResponse {
        guard var comps = URLComponents(url: VSAConfig.restApiUrl, resolvingAgainstBaseURL: false) else {
            throw APIError.http(status: -1, body: "bad base URL")
        }
        comps.path = (comps.path.hasSuffix("/") ? comps.path : comps.path + "/") + "vehicles/\(vehicleId)/service-history"
        guard let url = comps.url, let token = idTokenProvider() else {
            throw APIError.unauthenticated
        }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await perform(req)
    }

    /// GET /vehicles/{vehicleId}/context
    /// Returns the vehicle record and (if present) the primary assigned driver.
    /// Called at Assistant-tab open to render the nameplate and is also used
    /// (server-side) by the supervisor runtime to enrich the voice prompt.
    func getVehicleContext(vehicleId: String) async throws -> VehicleContextResponse {
        try await get(
            path: "/vehicles/\(vehicleId)/context",
            as: VehicleContextResponse.self
        )
    }

    /// GET /vehicles/{vehicleId}/live-state
    /// Realtime connection state + fresh telemetry, read from the same Redis
    /// hash the CMS UI uses. Short cache TTL expected (30s-ish) — the backend
    /// already applies CMS's 5-minute live-connection heuristic.
    func getLiveState(vehicleId: String) async throws -> VehicleLiveState {
        try await get(
            path: "/vehicles/\(vehicleId)/live-state",
            as: VehicleLiveState.self
        )
    }

    /// GET /drivers/me
    /// Resolves the signed-in Cognito user to their CMS driver + assigned
    /// vehicle. Call this once after sign-in to populate AppSession's
    /// currentDriver / currentVehicle state. Returns 200 with null driver
    /// when the user has no CMS driver row (iOS then has no vehicle id; see
    /// `AppSession.vehicleResolution`).
    func getCurrentDriver() async throws -> CurrentDriverResponse {
        try await get(path: "/drivers/me", as: CurrentDriverResponse.self)
    }

    /// GET /vehicles/{vehicleId}/trips?limit=N
    /// Returns recent trips (newest first), route array stripped server-side.
    func getTrips(vehicleId: String, limit: Int = 10) async throws -> TripsResponse {
        // Use URLComponents so the ?limit= query string survives the
        // appendingPathComponent path-escaping that the `get` helper does.
        guard var comps = URLComponents(url: VSAConfig.restApiUrl, resolvingAgainstBaseURL: false) else {
            throw APIError.http(status: -1, body: "bad base URL")
        }
        comps.path = (comps.path.hasSuffix("/") ? comps.path : comps.path + "/") + "vehicles/\(vehicleId)/trips"
        comps.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        guard let url = comps.url, let token = idTokenProvider() else {
            throw APIError.unauthenticated
        }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await perform(req)
    }

    /// GET /vehicles/{vehicleId}/safety-events?days=7&limit=50
    /// Returns recent safety events (newest first) for the Alerts tab.
    /// Same query-string pattern as `getTrips` — go through URLComponents so
    /// the query params don't get mangled by the path-escaping `get` helper.
    func getSafetyEvents(vehicleId: String, days: Int = 7, limit: Int = 50) async throws -> SafetyEventsResponse {
        guard var comps = URLComponents(url: VSAConfig.restApiUrl, resolvingAgainstBaseURL: false) else {
            throw APIError.http(status: -1, body: "bad base URL")
        }
        comps.path = (comps.path.hasSuffix("/") ? comps.path : comps.path + "/") + "vehicles/\(vehicleId)/safety-events"
        comps.queryItems = [
            URLQueryItem(name: "days", value: String(days)),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
        guard let url = comps.url, let token = idTokenProvider() else {
            throw APIError.unauthenticated
        }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await perform(req)
    }

    // MARK: - Booking flow (POST /find-service-center, POST /book)
    //
    // These two methods back the native booking flow on the iOS Service
    // / Dealer tab. They mirror what the voice agent does internally
    // when Nova handles a booking — same DDB tables, same row shape —
    // so a booking made through either channel ends up in the same
    // Upcoming Service list. See lambdas/api-find-service-center and
    // lambdas/api-book on the backend.

    /// POST /find-service-center
    /// Returns nearby service centers ranked by haversine distance,
    /// filtered by capability + persona segment + (optional) make.
    /// segment ("fleet" | "oem" | "rental") is what produces the
    /// dealer-only / chains-first behavior.
    func findServiceCenter(_ body: FindServiceCenterRequest) async throws -> FindServiceCenterResponse {
        try await post(
            path: "/find-service-center",
            body: body,
            as: FindServiceCenterResponse.self
        )
    }

    /// POST /book
    /// Persists a "scheduled" row into CMS service-history. The
    /// returned requestNumber is the audit handle the iOS UI shows on
    /// the confirmation card; the row appears in the Upcoming Service
    /// section the next time the Service tab refreshes.
    func book(_ body: BookRequest) async throws -> BookResponse {
        try await post(
            path: "/book",
            body: body,
            as: BookResponse.self
        )
    }

    // MARK: - CMS UI API (driver self-vehicle-claim)
    //
    // These call the CMS main API (VSAConfig.cmsRestApiUrl), NOT the VSA API.
    // The CMS Cognito authorizer trusts the VSA pool (CMS_EXTRA_USER_POOL_IDS)
    // and main_api constrains driver tokens to a self-service allowlist
    // (GET /api/v1/vehicles, PUT /api/v1/drivers/{self}).
    //
    // Auth header difference: the CMS authorizer expects the RAW id-token in
    // `Authorization` (no "Bearer " prefix — matches the CMS web UI's fetch
    // interceptor). The VSA-API methods above keep their "Bearer" prefix.

    /// GET /api/v1/vehicles — fleet-scoped vehicle list for the claim picker.
    func getClaimableVehicles() async throws -> ClaimableVehiclesResponse {
        let req = try cmsRequest(method: "GET", path: "/api/v1/vehicles")
        return try await perform(req)
    }

    /// PUT /api/v1/drivers/{driverId} { assignedVehicleId } — driver self-assigns
    /// a vehicle to their own record. driverId MUST be the caller's own id (the
    /// backend guard enforces self-scope).
    @discardableResult
    func claimVehicle(driverId: String, vehicleId: String) async throws -> ClaimVehicleResponse {
        var req = try cmsRequest(method: "PUT", path: "/api/v1/drivers/\(driverId)")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["assignedVehicleId": vehicleId])
        return try await perform(req)
    }

    /// Build a CMS-API request with the raw id-token. Throws if the CMS base URL
    /// isn't configured (e.g. prod until wired) or the user isn't authenticated.
    private func cmsRequest(method: String, path: String) throws -> URLRequest {
        guard let base = VSAConfig.cmsRestApiUrl else {
            throw APIError.http(status: -1, body: "CMS API not configured")
        }
        guard let token = idTokenProvider() else { throw APIError.unauthenticated }
        let url = base.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "Authorization")  // raw token, no Bearer
        return req
    }

    // MARK: - Remote commands (cms-${stage}-commands-api)
    //
    // A THIRD base URL, distinct from both the VSA API and the CMS UI API. Its
    // Cognito authorizer trusts the same pool the app signs into, so the id-token
    // already in hand is accepted without extra federation — which is why this
    // reuses the raw-token header form below rather than "Bearer".

    /// `GET /api/commands/catalog` — every actuator the platform advertises.
    ///
    /// Note this returns far more than the app should offer; gate results through
    /// `RemoteCommandAllowlist` before rendering a control. Takes no vehicle id.
    func commandCatalog() async throws -> CommandCatalogResponse {
        let req = try commandsRequest(method: "GET", path: "/api/commands/catalog")
        return try await perform(req)
    }

    /// `POST /api/commands/{vehicleId}` — publish a command.
    ///
    /// A 200 means **published to MQTT**, not actuated: the response `status` is
    /// `SENT`. Callers must present the result as in-flight and confirm separately
    /// via `commandHistory` (status `SUCCEEDED`) or, for stateful commands, a live
    /// state read. Treating 200 as done is how a dead actuator looks healthy — the
    /// exact failure mode that hid a broken FWE command path for six days.
    @discardableResult
    func sendCommand(
        vehicleId: String,
        commandName: String,
        value: String,
        label: String? = nil,
        category: String? = nil
    ) async throws -> SendCommandResponse {
        // Refuse to send anything not verified as actuatable. A client-side guard
        // rather than a UI-only one: the sheet already filters, and this ensures a
        // future caller cannot bypass that by constructing a request directly.
        guard RemoteCommandAllowlist.isAllowed(commandName) else {
            throw APIError.http(
                status: -1,
                body: "\(commandName) is not a verified-actuatable command; see RemoteCommandAllowlist"
            )
        }
        var req = try commandsRequest(method: "POST", path: "/api/commands/\(vehicleId)")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(
            SendCommandRequest(commandName: commandName, value: value,
                               label: label, category: category)
        )
        return try await perform(req)
    }

    /// `GET /api/commands/{vehicleId}` — recent command history, newest first.
    /// This is the confirmation channel for a previously sent command.
    func commandHistory(vehicleId: String) async throws -> CommandHistoryResponse {
        let req = try commandsRequest(method: "GET", path: "/api/commands/\(vehicleId)")
        return try await perform(req)
    }

    /// Build a commands-API request with the raw id-token.
    ///
    /// Raw, not "Bearer" — mirrors `cmsRequest`. Both gateways sit in front of the
    /// same Cognito pool, so the header form that works for one works for the other.
    private func commandsRequest(method: String, path: String) throws -> URLRequest {
        guard let base = VSAConfig.commandsApiUrl else {
            throw APIError.http(status: -1, body: "Commands API not configured")
        }
        guard let token = idTokenProvider() else { throw APIError.unauthenticated }
        let url = base.appendingPathComponent(
            path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "Authorization")  // raw token, no Bearer
        return req
    }

    // MARK: - Internals

    private func get<T: Decodable>(path: String, as: T.Type) async throws -> T {
        guard let token = idTokenProvider() else { throw APIError.unauthenticated }
        let url = VSAConfig.restApiUrl.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await perform(req)
    }

    private func post<Body: Encodable, T: Decodable>(path: String, body: Body, as: T.Type) async throws -> T {
        guard let token = idTokenProvider() else { throw APIError.unauthenticated }
        let url = VSAConfig.restApiUrl.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        return try await perform(req)
    }

    private func perform<T: Decodable>(_ req: URLRequest) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.network(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.http(status: -1, body: "not HTTP")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.http(status: http.statusCode, body: body)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }
}

// MARK: - AVX (Findings + Actions)
//
// Four methods backing `AVXCardsViewModel`.  All four forward the Cognito JWT
// via the existing `idTokenProvider` closure using the same "Bearer" header
// form as every other VSA-API method in this file.

extension VSAClient {

    // MARK: Request / response types

    /// Body sent to `POST /findings/{finding_id}/approve`.
    ///
    /// Per the AVX contract, `target_system` must be one of the five
    /// owner-selectable values (dms / vehicle-command / retail / none / chat).
    /// `ios-owner` is the inbound direction and is NOT accepted on this route.
    struct ApproveFindingRequest: Encodable {
        let targetSystem: AvxTargetSystem
        let actionKind: AvxActionKind
        let parameters: AvxJSONValue

        enum CodingKeys: String, CodingKey {
            case targetSystem = "target_system"
            case actionKind   = "action_kind"
            case parameters
        }

        /// Custom encode: `ios-owner` must never be serialised on this route —
        /// fail loudly rather than silently constructing an invalid request.
        func encode(to encoder: Encoder) throws {
            precondition(
                targetSystem != .iosOwner,
                "ios-owner is the inbound direction and is not valid on POST /findings/{id}/approve"
            )
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(targetSystem, forKey: .targetSystem)
            try c.encode(actionKind,   forKey: .actionKind)
            try c.encode(parameters,   forKey: .parameters)
        }
    }

    /// Body returned by `GET /findings/me` (CVX `api-avx-findings` `_findings_me`).
    ///
    /// The server derives the caller's standing from trusted ID-token claims; the
    /// request carries no identifier and the route ignores query parameters.
    /// Findings are contract-shaped (projected scope attributes stripped), so the
    /// standing that admitted each one is reported beside them in `admittedBy`.
    struct MyFindingsResponse: Decodable {
        let findings: [AvxFinding]
        /// Standings the caller holds (`fleet_driver`, `owner`, `fleet_manager`).
        /// Required: an answer without it is a decode failure, never "no standing".
        /// Empty means the account is linked to no vehicle, customer or fleet.
        let standings: [String]
        /// `finding_id` → the standings that admitted it.
        let admittedBy: [String: [String]]?
        let computedAt: String?
        /// True when a per-standing page cap cut the result short.
        let truncated: Bool?

        enum CodingKeys: String, CodingKey {
            case findings
            case standings
            case admittedBy = "admitted_by"
            case computedAt = "computed_at"
            case truncated
        }
    }

    /// Wrapper returned by `GET /actions/owner/{owner_id}`.
    struct ActionsOwnerResponse: Decodable {
        let actions: [AvxAction]
    }

    // MARK: Methods

    /// `GET /findings/me` — the Findings the signed-in caller has standing over.
    ///
    /// Takes no identifier on purpose. Until 2026-09-26 the app requested
    /// `/findings/owner/{id}` with `custom:customerId` and `sub`, which gave a
    /// driver owner standing over every vehicle under their customer
    /// (`issues/2026-09-25-ios-findings-owner-id-is-email/`, the Critical).
    func getMyFindings() async throws -> MyFindingsResponse {
        try await get(path: "/findings/me", as: MyFindingsResponse.self)
    }

    /// `GET /actions/owner/{owner_id}` — returns all Actions for the given owner.
    func getActionsForOwner(_ ownerId: String) async throws -> [AvxAction] {
        let response = try await get(
            path: "/actions/owner/\(ownerId)",
            as: ActionsOwnerResponse.self
        )
        return response.actions
    }

    /// `POST /findings/{finding_id}/approve` — approves a finding with the
    /// specified target system and action kind, returning the created Action.
    ///
    /// Sends `ApproveFindingRequest` with exactly three fields:
    /// `target_system`, `action_kind`, `parameters` — as required by the contract.
    @discardableResult
    func approveFinding(
        _ findingId: String,
        target: AvxTargetSystem,
        actionKind: AvxActionKind,
        parameters: AvxJSONValue
    ) async throws -> AvxAction {
        let body = ApproveFindingRequest(
            targetSystem: target,
            actionKind: actionKind,
            parameters: parameters
        )
        return try await post(
            path: "/findings/\(findingId)/approve",
            body: body,
            as: AvxAction.self
        )
    }

    /// `POST /actions/{action_id}/dismiss` — dismisses an action.
    ///
    /// Per Addendum D4: this is a **dedicated sub-resource** (`/dismiss`), not a
    /// status update to `cancelled`. The two states are semantically distinct:
    /// `dismissed` = owner chose not to act; `cancelled` = owner cancels an
    /// already-approved action.
    ///
    /// An empty body is sent; the server requires no additional parameters.
    func dismissAction(_ actionId: String) async throws {
        struct EmptyBody: Encodable {}
        _ = try await post(
            path: "/actions/\(actionId)/dismiss",
            body: EmptyBody(),
            as: EmptyResponse.self
        )
    }
}

/// Thin decodable for endpoints that return `{}` or `{ "status": "ok" }`.
private struct EmptyResponse: Decodable {}

// MARK: - AVX Notifications + Summary
//
// Two methods backing the APNs registration path and the silent-push badge
// refresh.  Both follow the exact same VSA-API idiom as the four AVX
// methods above: `Bearer` auth header, VSAConfig.restApiUrl base.

extension VSAClient {

    // MARK: Request / response types

    /// Per-category push-consent booleans sent in the register-device body.
    ///
    /// Matches AVX core's `POST /notifications/register-device` OpenAPI:
    ///   `per_category_consent: { safety: bool, coverage: bool, service: bool }`
    struct RegisterDeviceCategoryConsent: Encodable {
        let safety: Bool
        let coverage: Bool
        let service: Bool
    }

    /// Body sent to `POST /notifications/register-device`.
    ///
    /// Wire shape (byte-identical to AVX core's OpenAPI contract):
    /// ```json
    /// {
    ///   "device_token":         "<hex string>",
    ///   "per_category_consent": { "safety": true, "coverage": true, "service": true },
    ///   "platform":             "apns-staging"
    /// }
    /// ```
    /// `platform` MUST be `"apns-{stage}"` (e.g. `"apns-staging"`).
    /// Passing a bare stage string (e.g. `"staging"`) is a contract violation —
    /// the mutation test in `VSAClientTests` enforces this shape.
    struct RegisterDeviceRequest: Encodable {
        let deviceToken: String
        let perCategoryConsent: RegisterDeviceCategoryConsent
        let platform: String

        enum CodingKeys: String, CodingKey {
            case deviceToken         = "device_token"
            case perCategoryConsent  = "per_category_consent"
            case platform
        }
    }

    /// Response from `POST /notifications/register-device`.
    ///
    /// AVX core returns only a boolean success indicator (no endpoint ARN
    /// in the response — device tokens are secrets and are never echoed).
    struct RegisterDeviceResponse: Decodable {
        /// `true` when the device was registered or updated successfully.
        let success: Bool
    }

    /// Small summary struct returned by `GET /findings/owner/{id}/summary`.
    ///
    /// Used by the silent-push handler to update the app-icon badge count
    /// without loading the full Finding list.
    struct FindingsSummary: Decodable {
        /// Count of open (non-dismissed, non-terminal) Findings for this owner.
        let openCount: Int
        /// ISO-8601 timestamp of the most recently updated Finding.
        let updatedAt: String

        enum CodingKeys: String, CodingKey {
            case openCount  = "open_count"
            case updatedAt  = "updated_at"
        }
    }

    // MARK: Methods

    /// `POST /notifications/register-device` — registers (or refreshes) the
    /// device's APNs token with AVX core's notification substrate.
    ///
    /// - Parameters:
    ///   - token: Raw device-token `Data` from
    ///     `application(_:didRegisterForRemoteNotificationsWithDeviceToken:)`,
    ///     converted to a lowercase hex string before sending.
    ///   - perCategoryConsent: Per-category push consent state from
    ///     `NotificationConsentService`.
    ///   - platform: `"apns-{stage}"` (e.g. `"apns-staging"`, `"apns-prod"`).
    ///     The prefix `"apns-"` is REQUIRED by the contract — do not pass a
    ///     bare stage name.
    @discardableResult
    func registerDevice(
        token: Data,
        perCategoryConsent: RegisterDeviceCategoryConsent,
        platform: String
    ) async throws -> RegisterDeviceResponse {
        let hexToken = token.map { String(format: "%02x", $0) }.joined()
        let body = RegisterDeviceRequest(
            deviceToken: hexToken,
            perCategoryConsent: perCategoryConsent,
            platform: platform
        )
        return try await post(
            path: "/notifications/register-device",
            body: body,
            as: RegisterDeviceResponse.self
        )
    }

    /// `GET /findings/owner/{owner_id}/summary` — returns a lightweight badge
    /// count so the silent-push handler can update the app icon without loading
    /// the full Finding list.
    ///
    /// - Parameter ownerId: The Cognito `sub` claim for the signed-in user
    ///   (`AppSession.currentOwnerId`).
    func getFindingsSummary(ownerId: String) async throws -> FindingsSummary {
        try await get(
            path: "/findings/owner/\(ownerId)/summary",
            as: FindingsSummary.self
        )
    }
}
