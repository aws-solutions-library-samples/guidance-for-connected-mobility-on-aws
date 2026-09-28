import Foundation

/// Lightweight Amazon Connect Participant chat client.
///
/// Wraps two Connect Participant Service APIs plus the WebSocket they hand
/// out. The full AWS SDK for Swift is heavyweight and brings in a lot of
/// codegen'd types we don't need for a basic driver-side chat UI. This
/// actor does only what's required for a one-on-one chat with a Connect
/// agent (Kevin) after the server-side `/escalate` Lambda has minted a
/// `ParticipantToken`.
///
/// ## Lifecycle
///
/// 1. **Construct** with the Connect region and the `ParticipantToken`
///    returned by `/escalate`.
/// 2. **connect()** — POSTs `CreateParticipantConnection` (HTTP) to get a
///    WebSocket URL + a `ConnectionToken`. Then opens the WebSocket and
///    subscribes to `aws/chat`. Returns an `AsyncStream<Event>` that the
///    ViewModel consumes.
/// 3. **sendMessage(_:)** — POSTs `SendMessage` (HTTP) over the Connection
///    Token. The agent sees the text appear in their CCP.
/// 4. **disconnect()** — cancels the WebSocket and (best-effort) calls
///    `DisconnectParticipant` so Kevin sees the driver leave cleanly. For
///    the demo path we mostly skip the disconnect call because the demo
///    ends with the agent wrapping up the contact, not the driver leaving.
///
/// ## Protocol notes
///
/// The Connect Participant Service endpoints are NOT SigV4-signed; auth
/// is entirely via the `X-Amz-Bearer` header (ParticipantToken for the
/// HTTP handshake call, ConnectionToken for everything else).
///
/// The WebSocket messages the service delivers are double-encoded: the
/// outer envelope is `{"Type":"MESSAGE","Content":"<json-string>"}` and
/// the inner `Content` is another JSON object that includes the chat
/// message metadata (`ContentType`, `DisplayName`, `ParticipantRole`,
/// `MessageMetadata`, `AbsoluteTime`, etc.). We parse the inner payload
/// and expose only the fields the UI needs through the `Event` enum.
///
/// On the wire we also see internal event types like `EVENT` (participant
/// joined/left, typing indicators) and heartbeat envelopes. We surface
/// the ones the UI can render and silently drop the rest.
///
/// ## Out of scope
///
/// - Attachments (StartAttachmentUpload / GetAttachment). The demo is
///   text-only.
/// - Typing indicators in either direction. Could be added later via
///   `SendEvent` with `application/vnd.amazonaws.connect.event.typing`.
/// - Resume-after-disconnect. The demo happens in a single short chat;
///   if the session drops mid-demo we surface the error and the user
///   re-triggers the escalation.
actor ConnectChatClient {

    // MARK: - Public types

    /// Connection parameters. The four fields come straight from the
    /// `/escalate` response body — see `bidi_app.py`'s `escalation` wire
    /// event and the `VSABidiClient.Event.escalation` case.
    struct Config: Equatable {
        /// AWS region the Connect instance is in. e.g. "us-east-1".
        let region: String
        /// Connect contact ID — used only for logging / correlation on
        /// the client side; the ParticipantToken carries all the routing.
        let contactId: String
        /// Participant identifier the driver will appear as on the agent
        /// side. Also just logging-level here.
        let participantId: String
        /// The short-lived (~15 min) token from StartChatContact.
        /// Treated as opaque — bearer auth only.
        let participantToken: String
    }

    /// Events surfaced to the ViewModel. This is intentionally a thin
    /// subset of what the Connect WebSocket emits — only what the driver
    /// chat UI renders.
    enum Event: Equatable {
        /// Chat message, either from the driver (echoed back by the
        /// service) or from the agent.
        case message(Message)
        /// A participant (agent or system) joined the chat. The UI uses
        /// this to show "Connected to Kevin" once the agent's JOINED event
        /// lands.
        case participantJoined(displayName: String?, role: String?)
        /// A participant left. On the agent side, this signals wrap-up.
        case participantLeft(displayName: String?, role: String?)
        /// Connection established — WebSocket handshake + aws/chat
        /// subscription both completed successfully.
        case connected
        /// The chat ended (contact disconnected on the agent side, token
        /// expired, or we called disconnect). `reason` is a short string
        /// for the UI banner.
        case ended(reason: String)
        /// Client-side failure (network, auth, parse). Terminal for this
        /// session; the ViewModel flips HandoffState to .failed.
        case error(message: String)
    }

    /// One chat bubble's worth of data. IDs come from Connect's
    /// MessageMetadata so echoes-of-our-own-send can be de-duplicated if
    /// we ever care.
    struct Message: Identifiable, Equatable {
        let id: String
        let content: String
        let contentType: String     // "text/plain" usually
        /// "CUSTOMER" (us), "AGENT" (Kevin), "SYSTEM", "SUPERVISOR", etc.
        let participantRole: String
        let displayName: String?
        /// ISO-8601 timestamp from the service. Used for the bubble
        /// timestamp in the UI.
        let absoluteTime: String?
    }

    // MARK: - Errors

    enum ClientError: Error, LocalizedError {
        case alreadyConnected
        case notConnected
        case httpStatus(Int, body: String)
        case missingWebsocketUrl
        case missingConnectionToken
        case invalidResponse(String)
        case network(Error)

        var errorDescription: String? {
            switch self {
            case .alreadyConnected: return "Chat already connected"
            case .notConnected: return "Chat not connected"
            case .httpStatus(let code, let body):
                return "Connect HTTP \(code): \(body.prefix(200))"
            case .missingWebsocketUrl:
                return "CreateParticipantConnection returned no WebSocket URL"
            case .missingConnectionToken:
                return "CreateParticipantConnection returned no ConnectionToken"
            case .invalidResponse(let s): return "Invalid Connect response: \(s)"
            case .network(let e): return "Network error: \(e.localizedDescription)"
            }
        }
    }

    // MARK: - State

    private let config: Config
    private let urlSession: URLSession

    /// Set after `CreateParticipantConnection` succeeds. Used as
    /// `X-Amz-Bearer` for `SendMessage` and `DisconnectParticipant`.
    private var connectionToken: String?

    private var webSocketTask: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var eventContinuation: AsyncStream<Event>.Continuation?

    // MARK: - Init

    init(config: Config, urlSession: URLSession = .shared) {
        self.config = config
        self.urlSession = urlSession
    }

    // MARK: - Public API

    /// Open the chat. Returns an AsyncStream of events the caller should
    /// consume until it yields `.ended` or `.error`.
    ///
    /// Throws on any failure during the HTTP handshake. After the
    /// AsyncStream is returned, non-fatal parse failures flow through as
    /// events (logged but not thrown) so the UI can stay reactive.
    func connect() async throws -> AsyncStream<Event> {
        guard webSocketTask == nil else { throw ClientError.alreadyConnected }

        // 1) CreateParticipantConnection (HTTP).
        let (wsUrl, connectionToken) = try await createParticipantConnection()
        self.connectionToken = connectionToken

        // 2) Open the WebSocket.
        var request = URLRequest(url: wsUrl)
        // The WS URL already embeds the auth query string; no extra
        // headers needed.
        let task = urlSession.webSocketTask(with: request)
        self.webSocketTask = task
        task.resume()

        // 3) Subscribe to the chat topic.
        let subscribeEnvelope = """
        {"topic":"aws/subscribe","content":{"topics":["aws/chat"]}}
        """
        do {
            try await task.send(.string(subscribeEnvelope))
        } catch {
            await teardownWebSocket()
            throw ClientError.network(error)
        }

        // 3b) Send `connection.acknowledged` via the Participant
        // SendEvent API so Connect marks the driver as "connected".
        // Without this, Connect can treat the customer as still
        // off-line and buffer agent messages rather than deliver them
        // over the WebSocket — observed symptom 2026-05-07: driver
        // saw "Connected to fleet support" but Kevin's typed replies
        // never arrived. This is called out in Connect's chat SDK
        // docs as the last mandatory step for the customer side.
        //
        // Fire-and-forget: if this fails, don't abort the connection —
        // the rest of the chat might still work for legacy flows, and
        // we've already surfaced the `.connected` event to the UI.
        Task { [weak self] in
            await self?.sendConnectionAcknowledged()
        }

        // 4) Wire up the event stream and start the receive loop.
        let (stream, cont) = AsyncStream<Event>.makeStream(bufferingPolicy: .unbounded)
        self.eventContinuation = cont
        receiveTask = Task { [weak self] in
            await self?.receiveLoop()
        }

        // Yield the initial "connected" so the ViewModel can flip its
        // state even before the agent sends anything.
        cont.yield(.connected)

        NSLog("💬 CONNECT: chat client connected (contact=%@, ws=%@…)",
              config.contactId, String(wsUrl.absoluteString.prefix(60)))

        return stream
    }

    /// Send a text message from the driver. Uses SendMessage (HTTP POST)
    /// with the ConnectionToken.
    func sendMessage(_ text: String) async throws {
        guard let token = connectionToken else {
            throw ClientError.notConnected
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard trimmed.count <= 1024 else {
            // Connect's text/plain content is capped at 1024 chars. For
            // the demo, splitting a long message is out of scope; we
            // just reject it so the UI can show a helpful error.
            throw ClientError.invalidResponse("Message exceeds 1024 chars.")
        }

        var req = URLRequest(url: participantEndpoint(path: "/participant/message"))
        req.httpMethod = "POST"
        req.setValue(token, forHTTPHeaderField: "X-Amz-Bearer")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "Content": trimmed,
            "ContentType": "text/plain",
            // ClientToken for idempotency. Uuid string is safely <500.
            "ClientToken": UUID().uuidString,
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body, options: [])

        let (data, response) = try await urlSession.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw ClientError.invalidResponse("non-HTTP SendMessage response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw ClientError.httpStatus(
                http.statusCode,
                body: String(data: data, encoding: .utf8) ?? ""
            )
        }
        // Don't yield a local echo from the response — Connect will
        // broadcast the message back over the WebSocket as a normal
        // MESSAGE envelope, and the ViewModel will render it from there.
        // This keeps the "who sent it" logic in exactly one place
        // (participantRole from the WebSocket payload).
    }

    /// Send the "connection.acknowledged" event via the Participant
    /// SendEvent API. Called once right after the WebSocket subscribe
    /// completes so Connect marks the customer participant as connected
    /// and starts delivering agent messages over the WebSocket instead
    /// of buffering them. Fire-and-forget: failure here shouldn't abort
    /// the connection — the chat may still work for some flows — but
    /// without it we've seen agent→driver messages never arrive
    /// (2026-05-07 demo bug).
    private func sendConnectionAcknowledged() async {
        guard let token = connectionToken else { return }
        var req = URLRequest(url: participantEndpoint(path: "/participant/event"))
        req.httpMethod = "POST"
        req.setValue(token, forHTTPHeaderField: "X-Amz-Bearer")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "ContentType": "application/vnd.amazonaws.connect.event.connection.acknowledged",
            // SendEvent requires a Content field. Per AWS docs the
            // acknowledged event expects "{}" as a JSON string.
            "Content": "{}",
        ]
        do {
            req.httpBody = try JSONSerialization.data(withJSONObject: body, options: [])
        } catch {
            NSLog("💬 CHAT: ack event body serialize failed: %@", "\(error)")
            return
        }
        do {
            let (data, response) = try await urlSession.data(for: req)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                let body = String(data: data, encoding: .utf8) ?? ""
                NSLog("💬 CHAT: ack event HTTP %d body=%@",
                      http.statusCode, String(body.prefix(200)))
            } else {
                NSLog("💬 CHAT: ack event sent (driver marked connected)")
            }
        } catch {
            NSLog("💬 CHAT: ack event failed: %@", "\(error)")
        }
    }

    /// Close the WebSocket and best-effort notify Connect we disconnected.
    /// Idempotent; safe to call multiple times.
    func disconnect(reason: String = "client-closed") async {
        // Best-effort DisconnectParticipant. We don't care if it fails —
        // the contact will time out on Connect's side either way.
        if let token = connectionToken {
            var req = URLRequest(url: participantEndpoint(path: "/participant/disconnect"))
            req.httpMethod = "POST"
            req.setValue(token, forHTTPHeaderField: "X-Amz-Bearer")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = "{}".data(using: .utf8)
            _ = try? await urlSession.data(for: req)
        }
        eventContinuation?.yield(.ended(reason: reason))
        await teardownWebSocket()
    }

    // MARK: - Internals

    /// Build the regional Connect Participant Service endpoint.
    /// https://participant.connect.<region>.amazonaws.com
    private func participantEndpoint(path: String) -> URL {
        URL(string: "https://participant.connect.\(config.region).amazonaws.com\(path)")!
    }

    /// POST /participant/connection with the ParticipantToken. Returns the
    /// (wsUrl, connectionToken) pair for the caller to open the socket.
    private func createParticipantConnection() async throws -> (URL, String) {
        var req = URLRequest(url: participantEndpoint(path: "/participant/connection"))
        req.httpMethod = "POST"
        req.setValue(config.participantToken, forHTTPHeaderField: "X-Amz-Bearer")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // We need BOTH: WEBSOCKET gives the ws URL, CONNECTION_CREDENTIALS
        // gives the ConnectionToken for subsequent SendMessage calls.
        //
        // Do NOT set ConnectParticipant: true. That field marks the
        // participant as connected for customer-side streaming chats
        // (used with StartContactStreaming) — and Connect rejects the
        // whole CreateParticipantConnection call with HTTP 400 for
        // regular StartChatContact flows. The agent sees a JOINED event
        // automatically once the WebSocket is subscribed; no flag needed.
        let body: [String: Any] = [
            "Type": ["WEBSOCKET", "CONNECTION_CREDENTIALS"],
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body, options: [])

        let (data, response) = try await urlSession.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw ClientError.invalidResponse("CreateParticipantConnection: non-HTTP")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw ClientError.httpStatus(
                http.statusCode,
                body: String(data: data, encoding: .utf8) ?? ""
            )
        }
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ClientError.invalidResponse("CreateParticipantConnection: not JSON")
        }
        guard let ws = json["Websocket"] as? [String: Any],
              let urlString = ws["Url"] as? String,
              let url = URL(string: urlString) else {
            throw ClientError.missingWebsocketUrl
        }
        guard let creds = json["ConnectionCredentials"] as? [String: Any],
              let token = creds["ConnectionToken"] as? String else {
            throw ClientError.missingConnectionToken
        }
        return (url, token)
    }

    /// Read loop. Parses each incoming WebSocket frame and yields the
    /// UI-relevant portion through the event continuation. Runs until
    /// the task is cancelled or the socket closes.
    private func receiveLoop() async {
        guard let task = webSocketTask else { return }

        while !Task.isCancelled {
            let message: URLSessionWebSocketTask.Message
            do {
                message = try await task.receive()
            } catch {
                eventContinuation?.yield(.error(
                    message: "Chat socket recv: \(error.localizedDescription)"
                ))
                eventContinuation?.finish()
                return
            }

            switch message {
            case .string(let s):
                await handleIncomingFrame(s)
            case .data(let d):
                if let s = String(data: d, encoding: .utf8) {
                    await handleIncomingFrame(s)
                }
            @unknown default:
                break
            }
        }
    }

    /// Parse one Connect Participant WebSocket envelope. The outer shape
    /// is {"Type":"MESSAGE","Content":"<string-json>"}. The inner Content
    /// varies by Type.
    private func handleIncomingFrame(_ raw: String) async {
        guard let rawData = raw.data(using: .utf8),
              let outer = try? JSONSerialization.jsonObject(with: rawData) as? [String: Any] else {
            return
        }

        // aws/subscribe ack and heartbeat frames lack "Content" and are
        // safe to drop.
        //
        // Connect's WebSocket outer envelope actually uses LOWERCASE
        // "content" (confirmed 2026-05-08 from live frames); the AWS
        // SDK-for-JS sample uses "Content". Check both so we're
        // future-proof against Connect flipping back. The inner payload
        // fields (AbsoluteTime, ContentType, Id, Type) are PascalCase
        // in both variants.
        let contentStringAny = outer["content"] ?? outer["Content"]
        guard let contentString = contentStringAny as? String,
              let contentData = contentString.data(using: .utf8),
              let inner = try? JSONSerialization.jsonObject(with: contentData) as? [String: Any] else {
            return
        }

        // The inner payload uses "ContentType" for chat event
        // categorization. Possible values:
        //   "text/plain"  — regular chat message
        //   "text/markdown"
        //   "application/vnd.amazonaws.connect.event.participant.joined"
        //   "application/vnd.amazonaws.connect.event.participant.left"
        //   "application/vnd.amazonaws.connect.event.chat.ended"
        //   "application/vnd.amazonaws.connect.event.typing"
        //   "application/vnd.amazonaws.connect.event.connection.acknowledged"
        //   ... and a few more we don't surface.
        let innerType = (inner["ContentType"] as? String) ?? ""
        let role = inner["ParticipantRole"] as? String
        let displayName = inner["DisplayName"] as? String

        switch innerType {
        case "text/plain", "text/markdown":
            // Regular chat message. Build a Message and yield.
            let id = (inner["Id"] as? String)
                ?? ((inner["MessageMetadata"] as? [String: Any])?["MessageId"] as? String)
                ?? UUID().uuidString
            let content = (inner["Content"] as? String) ?? ""
            let absoluteTime = inner["AbsoluteTime"] as? String
            let message = Message(
                id: id,
                content: content,
                contentType: innerType,
                participantRole: role ?? "UNKNOWN",
                displayName: displayName,
                absoluteTime: absoluteTime
            )
            eventContinuation?.yield(.message(message))

        case "application/vnd.amazonaws.connect.event.participant.joined":
            eventContinuation?.yield(.participantJoined(displayName: displayName, role: role))

        case "application/vnd.amazonaws.connect.event.participant.left":
            eventContinuation?.yield(.participantLeft(displayName: displayName, role: role))

        case "application/vnd.amazonaws.connect.event.chat.ended":
            eventContinuation?.yield(.ended(reason: "chat-ended-by-agent"))
            eventContinuation?.finish()
            // Also tear down; server already closed logically.
            Task { [weak self] in await self?.teardownWebSocket() }

        default:
            // Heartbeats, typing indicators, acknowledgements, etc. Ignore.
            // Log at debug only to avoid noise.
            //
            // Temp diagnostic 2026-05-07: agent messages aren't surfacing on
            // iOS even when Kevin can see "Connected to fleet support" —
            // log unhandled inner types + a preview of the payload so we
            // can tell if frames are arriving at all and under what
            // ContentType they land.
            let contentPreview = (inner["Content"] as? String) ?? ""
            NSLog("💬 CHAT: unhandled inner type=%@ role=%@ display=%@ content=%@",
                  innerType, role ?? "-", displayName ?? "-",
                  String(contentPreview.prefix(120)))
            break
        }
    }

    /// Cancel the WebSocket and any pending receive task. Idempotent.
    private func teardownWebSocket() async {
        receiveTask?.cancel()
        receiveTask = nil
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        eventContinuation?.finish()
        eventContinuation = nil
        connectionToken = nil
    }
}
