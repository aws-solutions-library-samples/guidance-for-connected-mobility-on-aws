import Foundation

/// Thin wrapper over URLSession for the two REST endpoints we need.
/// Everything else (retries, pagination, offline) is out of scope for v1.
actor VSAClient {
    private let session: URLSession
    private let idTokenProvider: () -> String?

    init(idTokenProvider: @escaping () -> String?) {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.waitsForConnectivity = false
        self.session = URLSession(configuration: config)
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
    /// when the user has no CMS driver row (iOS falls back to
    /// VSAConfig.demoDriverId / demoVehicleId in that case).
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
