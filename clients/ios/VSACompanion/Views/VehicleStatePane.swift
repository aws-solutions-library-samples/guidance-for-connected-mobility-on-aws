import SwiftUI

struct VehicleStatePane: View {
    let frame: TelemetryFrame
    let theme: TenantTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Vehicle State").font(.headline)
            row("Speed", value: format(frame.speedMph, "mph"))
            row("Coolant", value: format(frame.coolantTempC, "°C"), warn: (frame.coolantTempC ?? 0) >= 115)
            row("Tire FL", value: format(frame.tireFlPsi, "PSI"), warn: (frame.tireFlPsi ?? 34) < 30)
            row("Tire FR", value: format(frame.tireFrPsi, "PSI"))
            row("Tire RL", value: format(frame.tireRlPsi, "PSI"))
            row("Tire RR", value: format(frame.tireRrPsi, "PSI"))
            row("Brake pad", value: format(frame.brakePadWearMm, "mm"))
            row("Battery", value: format(frame.batterySocPct, "%"))
            row("ABS", value: frame.absStatusOk.map { $0 ? "OK" : "FAULT" } ?? "—",
                warn: frame.absStatusOk == false)
            if frame.stale {
                Text("STALE").font(.caption).foregroundStyle(.orange)
            }
            Spacer()
        }
        .padding()
    }

    private func row(_ label: String, value: String, warn: Bool = false) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .foregroundStyle(warn ? .red : .primary)
                .monospacedDigit()
        }
        .font(.subheadline)
    }

    private func format(_ v: Double?, _ unit: String) -> String {
        guard let v else { return "—" }
        return String(format: "%.1f %@", v, unit)
    }
}
