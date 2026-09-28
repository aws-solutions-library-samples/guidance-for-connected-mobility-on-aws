import Foundation

/// WebSocket client for the VSA voice runtime.
///
/// Wire protocol is defined in `agents/supervisor/bidi_app.py`:
///
///   Client -> server:
///     {"type":"session.start","tenantId":...,"vin":...}   (fallback; preferred path is query params)
///     {"type":"audio.chunk","data":"<base64 PCM>","sampleRate":16000}
///     {"type":"text.input","text":"..."}
///     {"type":"session.end"}
///
///   Server -> client:
///     {"type":"audio.chunk","data":...,"sampleRate":24000}
///     {"type":"transcript","role":"user|assistant","text":"...","isFinal":bool}
///     {"type":"tool.call","name":...,"input":{...}}
///     {"type":"tool.result","name":...,"output":{...}}
///     {"type":"classification","value":"P0|P1|P2|P3"}
///     {"type":"interruption"}
///     {"type":"escalation","status":"initiated|failed","severity":"P0",
///       "contactId":...,"participantId":...,"participantToken":...,
///       "connectionExpiry":...,"rsaDispatched":bool,"rsaActionId":...}
///     {"type":"session.ended","reason":"..."}
///     {"type":"error","message":"..."}
///     {"type":"debug","event_type":"..."}
///
/// BACKEND-AGNOSTIC NOTE. Right now the `Backend` enum points at the
/// AgentCore WebSocket runtime (`vsa_supervisor_bidi`). That runtime is
/// currently blocked on Nova Sonic emitting output — see PHASE3_STEP3_RESULTS.md.
/// If we pivot to Pattern A (Nova Sonic wrapper Lambda in front of the
/// text runtime), the wire contract above stays the same; only the URL
/// and auth style change. Adding a `.patternA(url: URL)` case to Backend
/// and wiring `connect()` to use URLSessionWebSocketTask against that
/// URL instead of the AgentCore signed URL is all the swap requires.
///
/// For Phase 3 step 4 local testing, the `.local(url:)` case connects
/// to a dev server on the laptop (run `python -m agents.supervisor.bidi_app`
/// after setting the right env vars). That path skips SigV4 signing
/// and custom headers.
///
/// Scaffolding: not yet wired into AssistantTabView. See
/// PHASE3_STEP3_RESULTS.md §3 for backend choice.
actor VSABidiClient {
    /// Wire name for the AVX drill-down anchor custom header — the single
    /// source of truth for this side of the contract.
    ///
    /// `connect()` must reference this rather than inlining the literal, so a
    /// rename is observable to a test. It is `nonisolated` so a test can read it
    /// without `await`ing into the actor.
    ///
    /// The server accepts exactly three lowercase spellings of the delivered
    /// key — `avxfindingid`, `avx-finding-id`, `avx_finding_id` — because
    /// AgentCore's camelCase normalization is not deterministic. Changing this
    /// value without changing `AVX_FINDING_ID_QUERY_PARAMS` in the CVX repo's
    /// `agents/supervisor/bidi_app.py` degrades silently to "no pre-seed":
    /// nothing errors, the assistant simply opens cold again.
    nonisolated static let avxFindingIdHeaderName = "AvxFindingId"

    enum Backend {
        /// Deployed AgentCore WebSocket runtime. Requires SigV4-signed
        /// connection and custom headers. URL comes from
        /// `AgentCoreRuntimeClient.generate_ws_connection()` on the server
        /// side; iOS would need an equivalent SigV4 signer or have the
        /// backend mint a pre-signed URL.
        case agentcoreBidi(agentRuntimeArn: String, region: String)

        /// Local dev server (bidi_app.py run directly, bypasses AgentCore).
        /// No SigV4 needed. Useful for mic-to-backend loop testing.
        case local(url: URL)

        /// Future Pattern A fallback — Nova Sonic wrapper Lambda.
        /// TODO(phase3.pattern-a): implement if voice backend pivots.
        // case patternA(url: URL)
    }

    enum Event: Equatable {
        case audioChunk(base64: String, sampleRate: Int)
        /// `isSynthetic` is true when the backend tagged this transcript
        /// as synthesized server-side (e.g. the auto-book "Done — you're
        /// booked..." confirmation). iOS uses this to remember the text
        /// and suppress Nova's late TTS-streamed duplicate when it
        /// arrives 60-90s later. Default false for normal Nova transcripts.
        case transcript(role: String, text: String, isFinal: Bool, isSynthetic: Bool = false)
        case toolCall(name: String, input: [String: AnyCodable])
        case toolResult(name: String, output: [String: AnyCodable])
        /// Classification is emitted by the server when the triage result
        /// is ready. `source` distinguishes "classifier" (deterministic
        /// sensor-backed triage) from "driver-confirmed" (driver verbally
        /// confirmed an emergency the sensors did not corroborate) and
        /// "driver-unclear-default" (driver response was ambiguous, so
        /// the server escalated defensively to P1). `category` is
        /// populated for driver-confirmed / driver-unclear events and
        /// names the emergency keyword category (e.g. "brake_failure").
        ///
        /// `source` is ALWAYS sent by a current server (CVX Task 1.5a) and
        /// is one of `EscalationRecommendation.knownClassificationSources`.
        /// An absent (`nil`) or unrecognised `source` therefore does NOT mean
        /// "classifier" — it means a server older than that contract, or a
        /// shape this build does not understand, and verdict-dependent UI
        /// must fail closed on it. (This comment previously said the reverse:
        /// that absent meant a legacy classifier event. That was true before
        /// Task 1.5a and is now the inverse of the server contract.)
        /// `category` stays optional and its absence carries no meaning.
        case classification(String, source: String?, category: String?)
        case interruption
        case escalation(Escalation)
        /// Screen-side info card emitted by sub-agent tools (e.g.
        /// the diy_repair_advisor's tire pressure table). Renders
        /// as a markdown-formatted bubble in the transcript so Nova
        /// can stay brief in voice while the driver still sees the
        /// detailed reference data on screen. `source` is the tool
        /// name (e.g. "diy_repair_advisor") for diagnostics.
        case infoMessage(source: String, markdown: String)
        case sessionEnded(reason: String)
        case error(message: String)
        case debug(type: String)
    }

    /// Payload for the `escalation` side-channel event emitted by the
    /// supervisor after a successful (or failed) /escalate call. See
    /// `bidi_app.py` `_auto_triage_and_inject` and
    /// `agents/supervisor/tools/escalate.py`.
    ///
    /// status == "initiated" → contactId / participantId /
    /// participantToken / connectionExpiry are all present and the iOS
    /// client should open a ConnectChatClient with them.
    /// status == "failed"    → only `message` is populated; the UI shows
    /// an error instead of trying to join chat.
    struct Escalation: Equatable {
        let status: String                // "initiated" | "failed"
        let severity: String              // "P0" | "P1" | ...
        let contactId: String?
        let participantId: String?
        let participantToken: String?
        let connectionExpiry: String?
        let rsaDispatched: Bool
        let rsaActionId: String?
        /// Populated on status="failed" with a short human-readable reason.
        let message: String?
        /// Optional plain-text fault diagnosis, supplied by the supervisor
        /// when a catalog-mapped critical DTC is active. iOS renders it
        /// as a one-shot SYSTEM bubble in the Connect chat so the driver
        /// has fault context while waiting for the live agent.
        /// Never sent to Connect — local render only.
        let chatDiagnosis: String?
    }

    enum ClientError: Error, LocalizedError {
        case notConnected
        case alreadyConnected
        case handshakeFailed(Error)
        case invalidMessage(String)
        case sendFailed(Error)

        var errorDescription: String? {
            switch self {
            case .notConnected: return "WebSocket not connected"
            case .alreadyConnected: return "WebSocket already connected"
            case .handshakeFailed(let e): return "Handshake failed: \(e.localizedDescription)"
            case .invalidMessage(let m): return "Invalid message: \(m)"
            case .sendFailed(let e): return "Send failed: \(e.localizedDescription)"
            }
        }
    }

    private let backend: Backend
    private let session: URLSession
    private var task: URLSessionWebSocketTask?
    private var receiverTask: Task<Void, Never>?
    private var eventStream: AsyncStream<Event>?
    private var eventContinuation: AsyncStream<Event>.Continuation?

    init(backend: Backend, session: URLSession = .shared) {
        self.backend = backend
        self.session = session
    }

    /// Connect, send session.start if needed, and return an AsyncStream of events.
    /// For the AgentCore case, tenantId/vin/jwt are folded into the signed URL
    /// as query params (see `AgentCoreSigner`). `credentialProvider` is required
    /// for agentcore; ignored for local.
    ///
    /// `vehicleId` and `driverId` are optional side-channel identifiers for
    /// the CMS row primary keys. When provided they're forwarded as
    /// AgentCore custom headers so the supervisor runtime can populate
    /// SessionContext.vehicle_id / driver_id for book() writes and for
    /// voice prompt enrichment. Without them, book() falls back to
    /// `draft-created` (no CMS write) instead of `sink-accepted`.
    func connect(tenantId: String,
                 vin: String,
                 jwt: String,
                 vehicleId: String? = nil,
                 driverId: String? = nil,
                 latitude: Double? = nil,
                 longitude: Double? = nil,
                 avxFindingId: String? = nil,
                 credentialProvider: AwsCredentialProvider? = nil) async throws -> AsyncStream<Event> {
        // `🎤 BIDI:` is the diagnostic prefix for VSABidiClient. Filter via
        //   xcrun simctl spawn booted log stream --predicate \
        //     'eventMessage CONTAINS "🎤"' --style compact
        // Added 2026-05-27 alongside the broader voice-flow instrumentation
        // (cvx/issues/2026-05-27-ios-bidi-websocket-not-connected).
        NSLog("🎤 BIDI: connect() entry tenantId=%@ vin=%@ jwtLen=%d vehicleId=%@ driverId=%@ avxFindingId=%@",
              tenantId, vin, jwt.count, vehicleId ?? "-", driverId ?? "-", avxFindingId ?? "-")
        if task != nil {
            NSLog("🎤 BIDI: connect() throwing alreadyConnected — task=non-nil")
            throw ClientError.alreadyConnected
        }

        let url: URL
        var request: URLRequest

        switch backend {
        case .agentcoreBidi(let arn, let region):
            NSLog("🎤 BIDI: connect() backend=agentcoreBidi region=%@", region)
            guard let provider = credentialProvider else {
                throw ClientError.handshakeFailed(NSError(
                    domain: "VSABidiClient", code: -1,
                    userInfo: [NSLocalizedDescriptionKey:
                        "agentcoreBidi backend requires a credentialProvider"]
                ))
            }
            let creds: AwsCredentialProvider.Credentials
            do {
                creds = try await provider.credentials()
                NSLog("🎤 BIDI: connect() got temp credentials accessKeyPrefix=%@ exp=%@",
                      String(creds.accessKeyId.prefix(4)),
                      "\(creds.expiration)")
            } catch {
                NSLog("🎤 BIDI: connect() credential fetch threw: %@ (%@)",
                      "\(error.localizedDescription)", String(describing: error))
                throw ClientError.handshakeFailed(error)
            }
            // One session id per connect; reused by the server for the whole
            // bidi session.
            //
            // MUST be >=33 chars. AgentCore binds the
            // X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header to shape
            // `SessionType` = {min: 33, max: 256}; anything shorter is rejected
            // before the WebSocket upgrade completes, and URLSession reports
            // that only as NSURLErrorDomain -1011 "bad response from the
            // server" — the HTTP status never surfaces. This previously used
            // UUID().uuidString.prefix(12) for a 20-char id, so the bidi
            // handshake could never succeed. Do NOT shorten this for log
            // readability. Same constraint as the chat path, see
            // issues/2026-06-22-assistant-chat-runtime-session-id-too-short
            // and issues/2026-09-21-ios-voice-bidi-session-id-below-agentcore-minimum.
            // "vsa-ios-" (8) + UUID (36) = 44 chars.
            let sessionId = "vsa-ios-\(UUID().uuidString)"
            do {
                var headers: [String: String] = [
                    "User-Token": jwt,
                    "Tenant-Id": tenantId,
                    "Vin": vin,
                ]
                if let vehicleId, !vehicleId.isEmpty { headers["VehicleId"] = vehicleId }
                if let driverId, !driverId.isEmpty { headers["DriverId"] = driverId }
                // Fresh telemetry coordinates from session.liveState. The
                // backend uses these to skip its own /vehicles/{id}/live-
                // state HTTP roundtrip during session setup, ensuring
                // find_service_center distance computations agree with
                // the position the driver sees on the iOS Vehicle map.
                // Only sent when we have non-zero values; the backend
                // falls back to its own live-state lookup when absent.
                if let latitude, latitude != 0 {
                    headers["Latitude"] = String(latitude)
                }
                if let longitude, longitude != 0 {
                    headers["Longitude"] = String(longitude)
                }
                // The wire name is a PINNED CONTRACT with the server. It lives in
                // `VSABidiClient.avxFindingIdHeaderName` as the single source of
                // truth so a test can assert the literal itself — an earlier
                // version inlined the string here, and the test (which builds its
                // own dict and calls AgentCoreSigner directly) could not see a
                // rename, so a mutation renaming it passed 4/4. Referencing the
                // constant is what makes the mutation fail.
                //
                // The server accepts three lowercase casings of this name because
                // AgentCore's camelCase normalization is not deterministic; a
                // rename here degrades SILENTLY to "no pre-seed" — nothing errors.
                // Server: `agents/supervisor/avx_preseed.py` + the
                // `AVX_FINDING_ID_QUERY_PARAMS` tuple in `bidi_app.py` (CVX repo,
                // spec 2026-09-18-avx-tier2-ios-cards-and-actions Fix Group 18).
                //
                // NOTE: delivery is additionally gated by `requestHeaderAllowlist`
                // in the server repo's gitignored `.bedrock_agentcore.yaml`.
                // AgentCore drops unlisted headers with no error, so if this
                // appears not to arrive, check that allowlist before suspecting
                // this code.
                if let avxFindingId, !avxFindingId.isEmpty {
                    headers[Self.avxFindingIdHeaderName] = avxFindingId
                }
                url = try AgentCoreSigner.sign(.init(
                    credentials: creds,
                    region: region,
                    runtimeArn: arn,
                    sessionId: sessionId,
                    customHeaders: headers,
                    expiresInSeconds: 300
                ))
                NSLog("🎤 BIDI: connect() signed URL host=%@ path=%@",
                      url.host ?? "-", url.path)
            } catch {
                NSLog("🎤 BIDI: connect() sign threw: %@ (%@)",
                      "\(error.localizedDescription)", String(describing: error))
                throw ClientError.handshakeFailed(error)
            }
            // All auth + identity + session routing is already in the URL
            // as query params; no request headers needed. URLSession's
            // WebSocket upgrade will forward the URL verbatim.
            request = URLRequest(url: url)

        case .local(let u):
            NSLog("🎤 BIDI: connect() backend=local url=%@", u.absoluteString)
            url = u
            request = URLRequest(url: url)
            // local dev: skip custom headers, use session.start fallback below.
        }

        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        NSLog("🎤 BIDI: connect() task.resume() called — handshake in flight")

        // If we hit the local backend, it won't know tenant/vin/jwt yet.
        // Send a session.start fallback immediately. For agentcore, the
        // headers already carry everything, but sending session.start
        // is a no-op fallback that gets ignored because headers resolved first.
        var start: [String: Any] = [
            "type": "session.start",
            "tenantId": tenantId,
            "vin": vin,
            "jwt": jwt,
            "vehicleId": vehicleId ?? "",
            "driverId": driverId ?? "",
        ]
        // Send fresh coords ONLY when we have non-zero values. The
        // backend's session.start JSON path is the same one that
        // populates ctx.client_latitude/_longitude, and a literal 0
        // is treated as a valid coordinate downstream — which sends
        // find_service_center off looking for centers near Null
        // Island (0°,0° in the Atlantic). Original code emitted
        // `latitude ?? 0` unconditionally, which produced the
        // "Tukwila even when in Atlanta" bug observed 2026-05-19.
        // The local-dev backend benefits from these too; absence
        // means "do your own live-state lookup", same semantics as
        // the AgentCore custom-header path.
        if let latitude, latitude != 0 {
            start["latitude"] = latitude
        }
        if let longitude, longitude != 0 {
            start["longitude"] = longitude
        }
        do {
            try await send(json: start)
            NSLog("🎤 BIDI: connect() session.start fallback sent OK")
        } catch {
            NSLog("🎤 BIDI: connect() session.start fallback send threw: %@ (%@)",
                  "\(error.localizedDescription)", String(describing: error))
            throw error
        }

        let (stream, cont) = AsyncStream<Event>.makeStream(bufferingPolicy: .unbounded)
        self.eventStream = stream
        self.eventContinuation = cont

        receiverTask = Task { [weak self] in
            await self?.receiveLoop()
        }

        NSLog("🎤 BIDI: connect() returning eventStream — receiver loop started")
        return stream
    }

    /// Send an audio chunk. base64 must be 16-bit mono PCM at 16kHz
    /// (the default from AudioCapture).
    func sendAudioChunk(_ base64: String, sampleRate: Int = 16_000) async throws {
        try await send(json: [
            "type": "audio.chunk",
            "data": base64,
            "sampleRate": sampleRate,
        ])
    }

    /// Send a typed text message (useful for the text-input fallback).
    func sendText(_ text: String) async throws {
        try await send(json: [
            "type": "text.input",
            "text": text,
        ])
    }

    /// The driver tapped the escalation-recommendation affordance.
    ///
    /// Sends only the intent (`EscalationRecommendation.requestMessage`). The
    /// server replies with the ordinary `escalation` event, which
    /// `VoiceSessionViewModel.handleEscalation` already turns into a Connect
    /// chat — so a tapped handoff and a tenant-configured automatic one join
    /// the chat through the same code.
    func sendEscalationRequest(_ intent: EscalationRecommendation.RequestIntent) async throws {
        try await send(json: EscalationRecommendation.requestMessage(intent: intent))
    }

    /// Close the session cleanly.
    func disconnect() async {
        if let task = task {
            try? await send(json: ["type": "session.end"])
            task.cancel(with: .goingAway, reason: nil)
        }
        receiverTask?.cancel()
        receiverTask = nil
        eventContinuation?.finish()
        eventContinuation = nil
        eventStream = nil
        task = nil
    }

    // MARK: - Internal

    private func send(json: [String: Any]) async throws {
        guard let task = task else {
            // Bug A signal: log who's hitting send with task=nil so the
            // user's log timeline pins the call site (typically sendText
            // or sendAudioChunk from a wedged session).
            NSLog("🎤 BIDI: send() throwing notConnected — task=nil json.type=%@",
                  (json["type"] as? String) ?? "?")
            throw ClientError.notConnected
        }
        let data = try JSONSerialization.data(withJSONObject: json, options: [])
        guard let s = String(data: data, encoding: .utf8) else {
            throw ClientError.invalidMessage("json -> utf8 conversion failed")
        }
        do {
            try await task.send(.string(s))
        } catch {
            throw ClientError.sendFailed(error)
        }
    }

    private func receiveLoop() async {
        guard let task = task else { return }

        while !Task.isCancelled {
            do {
                let message = try await task.receive()
                switch message {
                case .string(let s):
                    if let evt = decode(s) {
                        eventContinuation?.yield(evt)
                        if case .sessionEnded = evt { return }
                    }
                case .data(let d):
                    if let s = String(data: d, encoding: .utf8), let evt = decode(s) {
                        eventContinuation?.yield(evt)
                        if case .sessionEnded = evt { return }
                    }
                @unknown default:
                    break
                }
            } catch {
                eventContinuation?.yield(.error(message: "recv: \(error.localizedDescription)"))
                return
            }
        }
    }

    private func decode(_ json: String) -> Event? {
        guard let data = json.data(using: .utf8),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = dict["type"] as? String else {
            return nil
        }
        switch type {
        case "audio.chunk":
            guard let b64 = dict["data"] as? String else { return nil }
            let sr = (dict["sampleRate"] as? Int) ?? 24_000
            return .audioChunk(base64: b64, sampleRate: sr)
        case "transcript":
            let role = (dict["role"] as? String) ?? "assistant"
            let text = (dict["text"] as? String) ?? ""
            let isFinal = (dict["isFinal"] as? Bool) ?? false
            // Backend tags server-synthesized transcripts (auto-book
            // confirmation, primed-text echo) so iOS can dedup against
            // Nova's late TTS-streamed duplicate. See Event.transcript
            // docstring.
            let isSynthetic = (dict["_synthetic_from_auto_book"] as? Bool) ?? false
            return .transcript(role: role, text: text, isFinal: isFinal, isSynthetic: isSynthetic)
        case "tool.call":
            let name = (dict["name"] as? String) ?? ""
            let input = (dict["input"] as? [String: Any]) ?? [:]
            return .toolCall(name: name, input: input.mapValues(AnyCodable.init))
        case "tool.result":
            let name = (dict["name"] as? String) ?? ""
            // `output` is the documented key, but the backend also emits
            // `result` in some paths (see bidi_app.py tool-injection branch).
            // Accept either so reasoning-drawer display is consistent.
            let raw = dict["output"] ?? dict["result"]

            // Tool results are not all objects. `retrieve` returns a JSON ARRAY of
            // hits (`list[dict]` in `agents/supervisor/tools/retrieve.py`), and so do
            // several sibling tools. The previous `as? [String: Any] ?? [:]` cast
            // failed on those and fell through to an empty dictionary — silently.
            //
            // The cost was a misdiagnosis, not just cosmetics: on 2026-08-19 the log
            // read `tool.result retrieve output=[:]` for a DTC lookup that had in fact
            // SUCCEEDED. The KB returned `dtc-P0420.md` as its top hit and the runtime
            // logged no fallback, but from the client it looked like the knowledge base
            // had nothing — which sent an investigation at the KB, the persona filters
            // and the score threshold before the decode was suspected.
            //
            // An array is wrapped under `items` so downstream consumers keep a
            // dictionary shape, and the count is surfaced so a non-empty result can
            // never again present as empty.
            let output: [String: Any]
            if let obj = raw as? [String: Any] {
                output = obj
            } else if let arr = raw as? [Any] {
                output = ["items": arr, "itemCount": arr.count]
            } else if let scalar = raw {
                // Scalar or string payload — keep it visible rather than dropping it.
                output = ["value": scalar]
            } else {
                output = [:]
            }
            return .toolResult(name: name, output: output.mapValues(AnyCodable.init))
        case "classification":
            guard let v = dict["value"] as? String else { return nil }
            // `source` is always sent by a current server; decoded as optional
            // so an old server's event still parses. nil is NOT "classifier" —
            // it is an unknown shape and verdict-dependent UI fails closed on
            // it (see Event.classification docs). `category` is optional.
            let source = dict["source"] as? String
            let category = dict["category"] as? String
            return .classification(v, source: source, category: category)
        case "interruption":
            return .interruption
        case "info.message":
            let source = (dict["source"] as? String) ?? ""
            let markdown = (dict["markdown"] as? String) ?? ""
            if markdown.isEmpty { return nil }
            return .infoMessage(source: source, markdown: markdown)
        case "escalation":
            let status = (dict["status"] as? String) ?? "initiated"
            let severity = (dict["severity"] as? String) ?? "P0"
            let payload = Escalation(
                status: status,
                severity: severity,
                contactId: dict["contactId"] as? String,
                participantId: dict["participantId"] as? String,
                participantToken: dict["participantToken"] as? String,
                connectionExpiry: dict["connectionExpiry"] as? String,
                rsaDispatched: (dict["rsaDispatched"] as? Bool) ?? false,
                rsaActionId: dict["rsaActionId"] as? String,
                message: dict["message"] as? String,
                chatDiagnosis: (dict["chatDiagnosis"] as? String).flatMap {
                    $0.isEmpty ? nil : $0
                }
            )
            return .escalation(payload)
        case "session.ended":
            return .sessionEnded(reason: (dict["reason"] as? String) ?? "")
        case "error":
            return .error(message: (dict["message"] as? String) ?? "unknown error")
        case "debug":
            return .debug(type: (dict["event_type"] as? String) ?? "unknown")
        default:
            return .debug(type: type)
        }
    }
}

/// Minimal type-erased wrapper so tool-call inputs can ride through the
/// event stream without forcing a concrete schema. A richer version
/// should codable-encode nested dictionaries properly — this is enough
/// for the reasoning-drawer display case.
struct AnyCodable: Equatable {
    let value: Any

    init(_ value: Any) { self.value = value }

    static func == (lhs: AnyCodable, rhs: AnyCodable) -> Bool {
        String(describing: lhs.value) == String(describing: rhs.value)
    }
}
