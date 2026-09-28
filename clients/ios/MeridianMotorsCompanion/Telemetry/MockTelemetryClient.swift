import Foundation

/// Emits a telemetry frame every 2s based on the active scenario. The real CMS
/// client will conform to the same `TelemetryClient` protocol and swap in via
/// dependency injection once the WebSocket producer is live.
final class MockTelemetryClient: TelemetryClient {
    private(set) var currentScenario: TelemetryScenario = .baseline
    private var continuation: AsyncStream<TelemetryFrame>.Continuation?
    let frames: AsyncStream<TelemetryFrame>

    /// Simulated GPS drift: start at Dallas and creep east toward Memphis.
    private var lat: Double = 32.7767
    private var lng: Double = -96.7970

    init() {
        var cont: AsyncStream<TelemetryFrame>.Continuation!
        self.frames = AsyncStream { cont = $0 }
        self.continuation = cont
        Task { await self.loop() }
    }

    func setScenario(_ scenario: TelemetryScenario) {
        currentScenario = scenario
        continuation?.yield(frame(for: scenario))
    }

    private func loop() async {
        while true {
            continuation?.yield(frame(for: currentScenario))
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
    }

    private func frame(for scenario: TelemetryScenario) -> TelemetryFrame {
        var f = TelemetryFrame.green

        // small jitter + GPS creep
        f.speedMph = (f.speedMph ?? 58) + Double.random(in: -1.5...1.5)
        f.coolantTempC = (f.coolantTempC ?? 88) + Double.random(in: -0.4...0.4)
        f.batterySocPct = (f.batterySocPct ?? 82) + Double.random(in: -0.1...0.05)
        lng += 0.0003                  // drift ~east
        lat += Double.random(in: -0.00005...0.00005)
        f.lat = lat
        f.lng = lng

        switch scenario {
        case .baseline: break
        case .p3Tire:
            f.tireFlPsi = 32
        case .p1Coolant:
            f.coolantTempC = 118 + Double.random(in: -0.5...0.5)
        case .p0Brake:
            f.absStatusOk = false
        }
        return f
    }
}
