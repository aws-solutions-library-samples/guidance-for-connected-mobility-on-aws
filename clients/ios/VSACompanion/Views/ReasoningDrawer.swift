import SwiftUI

struct ReasoningDrawer: View {
    let response: TriageResponse?
    let theme: TenantTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Reasoning").font(.headline)
                Spacer()
                if let r = response {
                    Text("\(r.latencyMs) ms").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let r = response {
                HStack(spacing: 12) {
                    levelBadge(r.classification)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Session \(r.sessionId)").font(.caption2).foregroundStyle(.secondary)
                        Text("Decided at \(r.decidedAt)").font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                Text("Deterministic classifier output. The LLM narrates this, never changes it.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                Text("Trigger a scenario from the presenter panel to see the classifier output.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding()
        .background(theme.primary.opacity(0.04))
    }

    private func levelBadge(_ level: String) -> some View {
        let color: Color = {
            switch level {
            case "P0": return .red
            case "P1": return .orange
            case "P2": return .yellow
            case "P3": return .green
            default:   return .gray
            }
        }()
        return Text(level)
            .font(.title).bold()
            .foregroundStyle(.white)
            .padding(.horizontal, 16).padding(.vertical, 8)
            .background(color)
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
