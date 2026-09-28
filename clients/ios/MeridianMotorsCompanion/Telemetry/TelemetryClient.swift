import Foundation
import CoreLocation

/// Telemetry snapshot rendered by VehicleStatePane. Mirrors the subset we care
/// about from CMS Redis LKS (normalized to Celsius/PSI/mph/%).
struct TelemetryFrame: Equatable {
    var coolantTempC: Double?
    var tireFlPsi: Double?
    var tireFrPsi: Double?
    var tireRlPsi: Double?
    var tireRrPsi: Double?
    var tireSpecPsi: Double = 34
    var brakePadWearMm: Double?
    var batterySocPct: Double?
    var fuelLevelPct: Double?
    var odometerMiles: Double?
    var speedMph: Double?
    var absStatusOk: Bool?
    var lat: Double?
    var lng: Double?
    var heading: Double?
    var stale: Bool = false

    static let green = TelemetryFrame(
        coolantTempC: 88, tireFlPsi: 34, tireFrPsi: 34, tireRlPsi: 34, tireRrPsi: 34,
        brakePadWearMm: 6, batterySocPct: 82, fuelLevelPct: 64, odometerMiles: 187_432,
        speedMph: 58, absStatusOk: true,
        lat: 32.7767, lng: -96.7970, heading: 90  // Dallas
    )

    var coordinate: CLLocationCoordinate2D? {
        guard let lat, let lng else { return nil }
        return CLLocationCoordinate2D(latitude: lat, longitude: lng)
    }
}

enum TelemetryScenario: String, CaseIterable, Identifiable {
    case baseline, p3Tire = "p3-tire", p1Coolant = "p1-coolant", p0Brake = "p0-brake"
    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .baseline: return "Baseline (green)"
        case .p3Tire:   return "P3 — Tire dip (32 PSI)"
        case .p1Coolant: return "P1 — Coolant + DTC + recall"
        case .p0Brake:  return "P0 — Brake fault"
        }
    }
}

protocol TelemetryClient {
    /// Emits a frame every ~2 seconds. Consumers should await the stream on the main actor.
    var frames: AsyncStream<TelemetryFrame> { get }
    var currentScenario: TelemetryScenario { get }
    func setScenario(_ scenario: TelemetryScenario)
}
