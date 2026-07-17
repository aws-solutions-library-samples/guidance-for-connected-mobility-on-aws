import SwiftUI

/// Pull-up tray content showing the live reasoning behind a voice session:
/// a prominent P0/P1/P2/P3 classification badge at the top, and a
/// chronological log of tool calls joined with their results.
///
/// Rendered inside a `.sheet` with `.presentationDetents` in the parent view.
/// Reads a snapshot of the view model's `toolInteractions` and
/// `latestClassification` — no direct observation here, so the sheet can
/// stay a plain `View` and the parent's @Observable binding drives updates.
///
/// ## Per-tool summary rendering
///
/// Each tool has a small renderer that extracts one line of signal from the
/// input/output. Fallback for unknown tools shows the tool name + truncated
/// JSON so new tools are still legible without code changes.
struct VoiceReasoningDrawer: View {
    let interactions: [VoiceSessionViewModel.ToolInteraction]
    let classification: String?
    /// Source tag attached to the classification by the server. When
    /// non-nil, rendered as a small badge under the tier title so the
    /// viewer can tell sensor-backed triage apart from driver-confirmed
    /// and driver-unclear-default escalations. See VoiceSessionViewModel
    /// .latestClassificationSource for the value set.
    let classificationSource: String?
    /// Emergency category (e.g. "brake_failure") that triggered a
    /// driver-confirmed escalation. Rendered alongside the source badge.
    let classificationCategory: String?
    let theme: TenantTheme

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    classificationHeader
                    if interactions.isEmpty {
                        emptyState
                    } else {
                        interactionLog
                    }
                    footnote
                }
                .padding()
            }
            .navigationTitle("Reasoning")
            .navigationBarTitleDisplayMode(.inline)
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
        }
    }

    // MARK: - Sections

    @ViewBuilder
    private var classificationHeader: some View {
        HStack(spacing: 12) {
            if let level = classification {
                classificationBadge(level)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title(for: level))
                        .font(.title3).bold()
                    Text(sourceSubtitle)
                        .font(.caption).foregroundStyle(.secondary)
                    if let pillText = driverConfirmedPillText {
                        Text(pillText)
                            .font(.caption2).bold()
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(
                                Capsule().fill(Color.orange.opacity(0.15))
                            )
                            .foregroundStyle(Color.orange)
                    }
                }
            } else {
                ZStack {
                    Circle().fill(Color.gray.opacity(0.12)).frame(width: 56, height: 56)
                    Image(systemName: "waveform.badge.magnifyingglass")
                        .foregroundStyle(.secondary).font(.title2)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("Awaiting classifier")
                        .font(.subheadline).bold()
                    Text("Start speaking to trigger triage.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 14)
                .fill(theme.primary.opacity(0.05))
        )
    }

    /// Subtitle text under the tier title, chosen to make the origin of
    /// the decision unambiguous to anyone reviewing a recording or live
    /// screen. Keeps the same single-line shape regardless of source.
    private var sourceSubtitle: String {
        switch classificationSource {
        case "driver-confirmed":
            return "Driver-confirmed escalation"
        case "driver-unclear-default":
            return "Driver response unclear — defensive escalation"
        case "classifier", nil:
            return "Deterministic classifier output"
        default:
            return "Classification source: \(classificationSource ?? "")"
        }
    }

    /// Small colored pill rendered below the subtitle for the two
    /// driver-involved paths. Returns nil for sensor-backed classifier
    /// results, which render without a pill.
    private var driverConfirmedPillText: String? {
        guard let source = classificationSource else { return nil }
        let prettyCategory = classificationCategory?
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
        switch source {
        case "driver-confirmed":
            if let cat = prettyCategory {
                return "Driver confirmed · \(cat)"
            }
            return "Driver confirmed"
        case "driver-unclear-default":
            if let cat = prettyCategory {
                return "Driver unclear · \(cat)"
            }
            return "Driver unclear"
        default:
            return nil
        }
    }

    private var interactionLog: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Tool calls").font(.subheadline).bold()
            ForEach(interactions) { interaction in
                interactionRow(interaction)
            }
        }
    }

    private func interactionRow(_ i: VoiceSessionViewModel.ToolInteraction) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: icon(for: i.name))
                    .foregroundStyle(theme.primary)
                    .font(.subheadline)
                Text(summary(for: i))
                    .font(.callout).monospaced()
                    .lineLimit(2)
                Spacer(minLength: 4)
                statusChip(for: i)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    private var emptyState: some View {
        HStack(spacing: 12) {
            Image(systemName: "hourglass")
                .foregroundStyle(.secondary)
            Text("No tool calls yet. They appear here as the agent acts.")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    private var footnote: some View {
        Text("The assistant narrates these decisions but cannot override them.")
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .padding(.top, 4)
    }

    // MARK: - Per-tool rendering

    private func icon(for toolName: String) -> String {
        switch toolName {
        case "triage": return "stethoscope"
        case "book": return "ticket"
        case "retrieve": return "magnifyingglass"
        default: return "wrench.and.screwdriver"
        }
    }

    /// One-line human-readable summary of a tool call + result. Each tool
    /// pulls the specific fields we care about; unknown tools fall back to
    /// a compact key=value join so the drawer still shows something sensible
    /// if the backend adds a new tool before the iOS client updates.
    private func summary(for i: VoiceSessionViewModel.ToolInteraction) -> String {
        switch i.name {
        case "triage":
            let vin = stringField(i.input, "vin") ?? "?"
            if let out = i.output {
                let cls = stringField(out, "classification") ?? "?"
                if let ms = intField(out, "latencyMs") {
                    return "triage(\(vin)) → \(cls) in \(ms)ms"
                }
                return "triage(\(vin)) → \(cls)"
            }
            return "triage(\(vin)) …"

        case "book":
            let vin = stringField(i.input, "vin") ?? "?"
            if let out = i.output {
                let ticket = stringField(out, "requestNumber") ?? "?"
                let status = stringField(out, "status") ?? "?"
                return "book(\(vin)) → \(ticket) (\(status))"
            }
            return "book(\(vin)) …"

        case "retrieve":
            let q = stringField(i.input, "query") ?? "?"
            let count = (i.output.flatMap { $0["results"] }).map { _ in "…" } ?? "…"
            return "retrieve(\(truncate(q, to: 40))) → \(count)"

        default:
            return "\(i.name)(\(compactFields(i.input)))"
        }
    }

    // MARK: - Visual helpers

    private func classificationBadge(_ level: String) -> some View {
        Text(level)
            .font(.system(size: 30, weight: .bold))
            .foregroundStyle(.white)
            .frame(width: 56, height: 56)
            .background(classificationColor(level))
            .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func classificationColor(_ level: String) -> Color {
        switch level {
        case "P0": return .red
        case "P1": return .orange
        case "P2": return .yellow
        case "P3": return .green
        default:   return .gray
        }
    }

    private func title(for level: String) -> String {
        switch level {
        case "P0": return "Stop driving"
        case "P1": return "Service now"
        case "P2": return "Service soon"
        case "P3": return "Monitor"
        default:   return level
        }
    }

    @ViewBuilder
    private func statusChip(for i: VoiceSessionViewModel.ToolInteraction) -> some View {
        if i.output == nil {
            Text("in flight")
                .font(.caption2).bold()
                .foregroundStyle(.white)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Capsule().fill(Color.orange))
        } else {
            Text("done")
                .font(.caption2).bold()
                .foregroundStyle(.white)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Capsule().fill(Color.green))
        }
    }

    // MARK: - Field extraction helpers

    private func stringField(_ dict: [String: AnyCodable], _ key: String) -> String? {
        guard let v = dict[key]?.value else { return nil }
        if let s = v as? String { return s }
        return String(describing: v)
    }

    private func intField(_ dict: [String: AnyCodable], _ key: String) -> Int? {
        guard let v = dict[key]?.value else { return nil }
        if let i = v as? Int { return i }
        if let d = v as? Double { return Int(d) }
        if let s = v as? String, let i = Int(s) { return i }
        return nil
    }

    private func compactFields(_ dict: [String: AnyCodable]) -> String {
        let pairs = dict.sorted { $0.key < $1.key }.prefix(3).map { k, v in
            "\(k): \(truncate(String(describing: v.value), to: 16))"
        }
        return pairs.joined(separator: ", ")
    }

    private func truncate(_ s: String, to n: Int) -> String {
        s.count <= n ? s : String(s.prefix(n - 1)) + "…"
    }
}
