import Foundation
import Combine

/// Real-time vehicle telemetry via CMS WebSocket.
///
/// Connects to the CMS WebSocket endpoint on sign-in, subscribes to the
/// driver's fleet, and pushes live state updates to the app via Combine.
/// Falls back to HTTP polling if the WebSocket disconnects.
///
/// Message format from ws-fanout (JSON):
/// ```json
/// {"vehicleId":"VEH-0025","timestamp":1715..., "signals":{"speed":45.2,...}}
/// ```
final class VehicleWebSocketClient: ObservableObject {
    
    // MARK: - Published state
    @Published var isConnected = false
    @Published var lastMessage: VehicleTelemetryMessage?
    @Published var alerts: [VehicleAlert] = []
    @Published var connectionError: String?
    
    // MARK: - Config
    private let wsEndpoint: URL
    private let vehicleId: String
    private let fleetId: String
    private let token: String
    
    // MARK: - Internal
    private var webSocket: URLSessionWebSocketTask?
    private var session: URLSession?
    private var reconnectTask: Task<Void, Never>?
    private var pingTask: Task<Void, Never>?
    private var maxReconnectAttempts = 10
    private var reconnectAttempts = 0
    private var reconnectDelay: TimeInterval = 1.0
    
    init(wsEndpoint: URL, vehicleId: String, fleetId: String, token: String) {
        self.wsEndpoint = wsEndpoint
        self.vehicleId = vehicleId
        self.fleetId = fleetId
        self.token = token
    }
    
    // MARK: - Public API
    
    func connect() {
        guard webSocket == nil else { return }
        
        // Build URL with query params: ?fleetId=X&token=JWT
        var components = URLComponents(url: wsEndpoint, resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "fleetId", value: fleetId),
            URLQueryItem(name: "token", value: token),
        ]
        guard let url = components.url else {
            connectionError = "Invalid WebSocket URL"
            return
        }
        
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        session = URLSession(configuration: config)
        
        webSocket = session?.webSocketTask(with: url)
        webSocket?.resume()
        
        isConnected = true
        connectionError = nil
        reconnectAttempts = 0
        
        startReceiving()
        startPing()
    }
    
    func disconnect() {
        pingTask?.cancel()
        reconnectTask?.cancel()
        webSocket?.cancel(with: .normalClosure, reason: nil)
        webSocket = nil
        session = nil
        isConnected = false
    }
    
    // MARK: - Receive loop
    
    private func startReceiving() {
        webSocket?.receive { [weak self] result in
            guard let self else { return }
            
            switch result {
            case .success(let message):
                Task { @MainActor in
                    self.handleMessage(message)
                    self.startReceiving() // continue listening
                }
            case .failure(let error):
                Task { @MainActor in
                    self.handleDisconnect(error: error)
                }
            }
        }
    }
    
    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        let data: Data
        switch message {
        case .string(let text):
            guard let d = text.data(using: .utf8) else { return }
            data = d
        case .data(let d):
            data = d
        @unknown default:
            return
        }
        
        // Peek at the "type" field to route the message
        guard let envelope = try? JSONDecoder().decode(WSEnvelope.self, from: data) else { return }
        guard envelope.vehicleId == vehicleId else { return }
        
        switch envelope.type {
        case "telemetry", nil:
            if let msg = try? JSONDecoder().decode(VehicleTelemetryMessage.self, from: data) {
                lastMessage = msg
            }
        case "alert":
            if let alert = try? JSONDecoder().decode(VehicleAlertMessage.self, from: data) {
                let newAlert = VehicleAlert(
                    id: alert.event.id,
                    vehicleId: alert.vehicleId,
                    eventType: alert.event.eventType,
                    severity: alert.event.severity,
                    title: alert.event.title,
                    description: alert.event.description,
                    dtcCode: alert.event.dtcCode,
                    timestamp: Date(timeIntervalSince1970: alert.event.timestamp),
                    isRead: false
                )
                alerts.insert(newAlert, at: 0)
                // Keep last 50 alerts
                if alerts.count > 50 { alerts.removeLast() }
            }
        case "trip_completed":
            // Future: notify trip list to refresh
            break
        case "service_reminder":
            if let alert = try? JSONDecoder().decode(VehicleAlertMessage.self, from: data) {
                let reminder = VehicleAlert(
                    id: "svc-\(Date().timeIntervalSince1970)",
                    vehicleId: alert.vehicleId,
                    eventType: "service_reminder",
                    severity: 3,
                    title: alert.event.title,
                    description: alert.event.description,
                    dtcCode: nil,
                    timestamp: Date(),
                    isRead: false
                )
                alerts.insert(reminder, at: 0)
            }
        default:
            break
        }
    }
    
    // MARK: - Ping keep-alive
    
    private func startPing() {
        pingTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000) // 30s
                webSocket?.sendPing { [weak self] error in
                    if let error {
                        Task { @MainActor in
                            self?.handleDisconnect(error: error)
                        }
                    }
                }
            }
        }
    }
    
    // MARK: - Reconnect
    
    private func handleDisconnect(error: Error) {
        isConnected = false
        webSocket = nil
        connectionError = error.localizedDescription
        
        guard reconnectAttempts < maxReconnectAttempts else {
            connectionError = "Max reconnect attempts reached. Pull to refresh."
            return
        }
        
        reconnectAttempts += 1
        let delay = reconnectDelay * pow(2.0, Double(reconnectAttempts - 1))
        
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.connect()
        }
    }
}

// MARK: - Message models

/// Envelope for routing — just peeks at type + vehicleId
private struct WSEnvelope: Codable {
    let type: String?
    let vehicleId: String
}

/// Alert event pushed from Flink safety/maintenance processors
private struct VehicleAlertMessage: Codable {
    let type: String
    let vehicleId: String
    let event: AlertEvent
    
    struct AlertEvent: Codable {
        let id: String
        let eventType: String
        let severity: Int
        let title: String
        let description: String
        let dtcCode: String?
        let timestamp: Double
    }
}

/// Real-time alert displayed in the iOS app
struct VehicleAlert: Identifiable, Equatable {
    let id: String
    let vehicleId: String
    let eventType: String
    let severity: Int  // 0=critical, 1=high, 2=medium, 3=low
    let title: String
    let description: String
    let dtcCode: String?
    let timestamp: Date
    var isRead: Bool
    
    var severityLabel: String {
        switch severity {
        case 0: return "Critical"
        case 1: return "High"
        case 2: return "Medium"
        default: return "Low"
        }
    }
    
    var severityColor: String {
        switch severity {
        case 0: return "red"
        case 1: return "orange"
        case 2: return "yellow"
        default: return "blue"
        }
    }
}

struct VehicleTelemetryMessage: Codable, Identifiable {
    var id: String { "\(vehicleId)-\(timestamp)" }
    
    let vehicleId: String
    let timestamp: Double
    let type: String?
    let signals: TelemetrySignals?
    
    struct TelemetrySignals: Codable {
        let speed: Double?
        let engineRpm: Double?
        let fuelLevel: Double?
        let engineTemp: Double?
        let batteryVoltage: Double?
        let latitude: Double?
        let longitude: Double?
        let odometer: Double?
        let tirePressureFl: Double?
        let tirePressureFr: Double?
        let tirePressureRl: Double?
        let tirePressureRr: Double?
        
        enum CodingKeys: String, CodingKey {
            case speed = "VehicleSpeed"
            case engineRpm = "EngineRPM"
            case fuelLevel = "FuelLevel"
            case engineTemp = "EngineCoolantTemp"
            case batteryVoltage = "BatteryVoltage"
            case latitude = "Latitude"
            case longitude = "Longitude"
            case odometer = "Odometer"
            case tirePressureFl = "TirePressureFL"
            case tirePressureFr = "TirePressureFR"
            case tirePressureRl = "TirePressureRL"
            case tirePressureRr = "TirePressureRR"
        }
    }
}
