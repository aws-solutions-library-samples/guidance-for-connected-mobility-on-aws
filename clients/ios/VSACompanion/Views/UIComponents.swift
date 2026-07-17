import SwiftUI

/// Small reusable UI pieces used across tabs. Centralizes the visual language so
/// reskinning to another tenant only touches theme colors, not layout.
enum AlertLevel: String {
    case p0, p1, p2, p3, none

    init(from classification: String?) {
        switch classification {
        case "P0": self = .p0
        case "P1": self = .p1
        case "P2": self = .p2
        case "P3": self = .p3
        default:   self = .none
        }
    }

    var label: String {
        switch self {
        case .p0: return "P0"
        case .p1: return "P1"
        case .p2: return "P2"
        case .p3: return "P3"
        case .none: return "—"
        }
    }

    var color: Color {
        switch self {
        case .p0: return .red
        case .p1: return .orange
        case .p2: return .yellow
        case .p3: return .green
        case .none: return .gray
        }
    }

    var title: String {
        switch self {
        case .p0: return "Stop driving"
        case .p1: return "Service now"
        case .p2: return "Service soon"
        case .p3: return "Monitor"
        case .none: return "Nominal"
        }
    }

    /// 0–100 health score. Higher is better.
    var healthScore: Int {
        switch self {
        case .p0: return 18
        case .p1: return 45
        case .p2: return 72
        case .p3: return 92
        case .none: return 100
        }
    }
}

struct StatusBadge: View {
    let level: AlertLevel
    var body: some View {
        Text(level.label)
            .font(.caption).bold()
            .foregroundStyle(.white)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(level.color)
            .clipShape(Capsule())
    }
}

struct SectionCard<Content: View>: View {
    let theme: TenantTheme
    let title: String?
    @ViewBuilder let content: () -> Content

    init(_ title: String? = nil, theme: TenantTheme, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.theme = theme
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title {
                Text(title).font(.headline).foregroundStyle(.primary)
            }
            content()
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            // Two-layer fill: standard iOS elevation tier on the bottom
            // so cards visually lift off the systemGroupedBackground
            // canvas (pure black in dark mode → #1C1C1E cards), and a
            // very faint brand tint on top so the surface still reads
            // as the tenant's app rather than generic system chrome.
            //
            // The previous design used `theme.primary.opacity(0.05)`
            // alone — on a black canvas that's nearly invisible, which
            // made the whole UI look like one undifferentiated dark
            // mass. Mixing elevation + tint keeps the brand presence
            // while restoring depth.
            RoundedRectangle(cornerRadius: 14)
                .fill(Color(.secondarySystemGroupedBackground))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .fill(theme.primary.opacity(0.04))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(theme.primary.opacity(0.18), lineWidth: 1)
                )
        )
        // Soft, mostly-imperceptible shadow. In light mode it adds the
        // expected paper-on-table lift; in dark mode it's barely
        // visible but the small offset still contributes to the sense
        // of elevation.
        .shadow(color: Color.black.opacity(0.18), radius: 6, x: 0, y: 2)
    }
}

struct EmptyStateCard: View {
    let theme: TenantTheme
    let systemImage: String
    let title: String
    let subtitle: String
    var body: some View {
        SectionCard(theme: theme) {
            HStack(spacing: 14) {
                Image(systemName: systemImage)
                    .font(.title2)
                    .foregroundStyle(theme.primary.opacity(0.7))
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline).bold()
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
    }
}

struct SkeletonBar: View {
    let width: CGFloat?
    let height: CGFloat
    @State private var shimmer = false
    var body: some View {
        RoundedRectangle(cornerRadius: 4)
            .fill(Color.gray.opacity(0.15))
            .frame(width: width, height: height)
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .fill(LinearGradient(colors: [.clear, .white.opacity(0.4), .clear],
                                         startPoint: .leading, endPoint: .trailing))
                    .offset(x: shimmer ? 200 : -200)
            )
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .onAppear {
                withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) {
                    shimmer = true
                }
            }
    }
}

/// Formats a relative timestamp string like "2m ago" given an ISO 8601 string.
func relativeTimeString(from iso: String) -> String {
    let iso8601 = ISO8601DateFormatter()
    iso8601.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let date = iso8601.date(from: iso) ?? ISO8601DateFormatter().date(from: iso)
    guard let date else { return iso }
    let fmt = RelativeDateTimeFormatter()
    fmt.unitsStyle = .abbreviated
    return fmt.localizedString(for: date, relativeTo: Date())
}

/// Formats a value with unit, with "—" fallback.
func formatValue(_ v: Double?, unit: String, digits: Int = 1) -> String {
    guard let v else { return "—" }
    return String(format: "%.\(digits)f %@", v, unit)
}
