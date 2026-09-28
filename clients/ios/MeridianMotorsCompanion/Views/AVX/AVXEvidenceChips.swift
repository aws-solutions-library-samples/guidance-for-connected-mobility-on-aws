import SwiftUI

// MARK: - AVXEvidenceChips

/// Horizontally-scrollable chip row rendering `evidence[]` entries.
///
/// Provenance (`live | mocked | absent`) is visually differentiated with an
/// icon and accessible label per parent PRD § M14. Scaffolding (Task 2.2):
/// chip rendering is a stub. Full rendering is Task 2.4.
struct AVXEvidenceChips: View {
    let evidence: [AvxEvidence]
    let theme: TenantTheme

    /// One chip to draw. `id` is the entry's position: `source_ref` names the
    /// run that produced the evidence, so several entries from one run share it,
    /// and keying on it made SwiftUI draw the first chip once per entry
    /// (three identical `adp.brakes.outcome` chips, 2026-09-25).
    struct ChipItem: Identifiable, Equatable {
        let id: Int
        let label: String
        let provenance: AvxProvenance
    }

    static func items(for evidence: [AvxEvidence]) -> [ChipItem] {
        evidence.enumerated().map { index, entry in
            ChipItem(id: index, label: label(for: entry), provenance: entry.provenance)
        }
    }

    /// "adp.brakes.service_date" + "2018-07-09" → "Brakes · service date: 2018-07-09".
    /// The internal field path is never shown as-is. The value is appended only
    /// when it is short and scalar; an empty list reads "none".
    static func label(for entry: AvxEvidence) -> String {
        let parts = entry.kind.split(separator: ".").map(String.init)
        let fieldWords = humanize(parts.last ?? entry.kind)
        var label = fieldWords
        if parts.count >= 2 {
            let group = humanize(parts[parts.count - 2])
            label = group.prefix(1).uppercased() + group.dropFirst() + " · " + fieldWords
        } else {
            label = fieldWords.prefix(1).uppercased() + fieldWords.dropFirst()
        }
        if let value = shortValue(entry.value) { label += ": " + value }
        return label
    }

    private static let acronyms: [String: String] = [
        "dtc": "DTC", "dtcs": "DTCs", "vin": "VIN", "soc": "SoC", "soh": "SoH",
        "psi": "PSI", "ev": "EV", "ota": "OTA", "rpm": "RPM",
    ]

    private static func humanize(_ token: String) -> String {
        token.split(separator: "_")
            .map { acronyms[$0.lowercased()] ?? $0.lowercased() }
            .joined(separator: " ")
    }

    private static func shortValue(_ value: AvxJSONValue) -> String? {
        let text: String
        switch value {
        case .string(let s):
            let t = s.trimmingCharacters(in: .whitespaces)
            text = (t.isEmpty || t == "[]") ? "none" : t
        case .integer(let i): text = String(i)
        case .number(let d): text = d == d.rounded() ? String(Int(d)) : String(format: "%.1f", d)
        case .bool(let b): text = b ? "yes" : "no"
        case .array(let a) where a.isEmpty: text = "none"
        default: return nil
        }
        return text.count <= 24 ? text : nil
    }

    var body: some View {
        // Provenance values: live / mocked / absent must render distinctly —
        // never collapse mocked or absent to live.
        if evidence.isEmpty {
            EmptyView()
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(Self.items(for: evidence)) { item in
                        AVXEvidenceChip(item: item, theme: theme)
                    }
                }
            }
        }
    }
}

// MARK: - AVXEvidenceChip

/// Single evidence chip: readable label + provenance icon.
private struct AVXEvidenceChip: View {
    let item: AVXEvidenceChips.ChipItem
    let theme: TenantTheme

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: provenanceIcon(item.provenance))
                .font(.caption2)
                .foregroundStyle(provenanceColor(item.provenance))
                .accessibilityLabel(provenanceLabel(item.provenance))
            Text(item.label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(
            Capsule().fill(Color(.systemGray5))
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.label), \(provenanceLabel(item.provenance))")
    }

    private func provenanceIcon(_ provenance: AvxProvenance) -> String {
        switch provenance {
        case .live:    return "checkmark.circle.fill"
        case .mocked:  return "wrench.and.screwdriver"
        case .absent:  return "questionmark.circle"
        }
    }

    private func provenanceColor(_ provenance: AvxProvenance) -> Color {
        switch provenance {
        case .live:   return .green
        case .mocked: return .orange
        case .absent: return .gray
        }
    }

    private func provenanceLabel(_ provenance: AvxProvenance) -> String {
        switch provenance {
        case .live:   return "live data"
        case .mocked: return "simulated data"
        case .absent: return "data unavailable"
        }
    }
}
