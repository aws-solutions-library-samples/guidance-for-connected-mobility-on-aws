import SwiftUI

/// Service tab — upcoming (voice-booked + DTC-approved) service appointments
/// and historical service rows for the signed-in driver's vehicle.
///
/// Extracted from AlertsTabView on 2026-05-06 when the Alerts tab was
/// narrowed to driver-relevant signals only (critical DTCs + safety events
/// + triage history). Sections + rows were lifted verbatim to preserve the
/// exact look and behaviour drivers are used to.
///
/// Data source: `session.scheduledService` + `session.completedService`,
/// both populated by `AppSession.loadServiceHistory` (same loader the
/// Alerts tab used before). A tab-open `.task` kicks a fresh fetch with
/// the 15-second debounce already baked into that loader.
struct ServiceTabView: View {
    @Environment(AppSession.self) private var session
    let theme: TenantTheme

    /// Optional callback invoked when the driver taps "Book by voice".
    /// Receives a primed prompt that the receiver (typically MainTabView)
    /// hands to the assistant cover so Nova starts the conversation
    /// already on-topic. nil keeps the CTA hidden — useful for
    /// previews or layouts where booking-via-voice doesn't apply.
    var onBookService: ((String) -> Void)? = nil

    /// Whether the native booking flow sheet is currently presented.
    /// The flow is owned by this tab (rather than MainTabView) because
    /// it's structurally tab-local — opening it from Service makes
    /// sense; opening it from Home or Vehicle would mean ferrying it
    /// up to the parent. Sheet presentation keeps tab navigation alive
    /// behind the sheet, unlike fullScreenCover.
    @State private var isBookingFlowPresented: Bool = false

    var body: some View {
        NavigationStack {
            ScrollView {
                if session.hasLoadedInitialService {
                    VStack(spacing: 16) {
                        if onBookService != nil {
                            bookingCTAs
                        }
                        UpcomingServiceSection(
                            records: session.scheduledService,
                            isLoading: session.serviceHistoryLoading,
                            error: session.serviceHistoryError,
                            theme: theme
                        )
                        ServiceHistorySection(
                            records: session.completedService,
                            isLoading: session.serviceHistoryLoading,
                            theme: theme
                        )
                    }
                    .padding(.bottom, 24)
                } else {
                    // Two sections: upcoming + history. Two card
                    // placeholders is a close visual stand-in.
                    TabLoadingSkeleton(cardCount: 2)
                }
            }
            .refreshable { await refresh(force: true) }
            .task { await refresh(force: false) }
            .navigationTitle("Service")
            .navigationBarTitleDisplayMode(.inline)
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
            .sheet(isPresented: $isBookingFlowPresented) {
                BookingFlow(theme: theme)
                    .environment(session)
            }
        }
    }

    /// Two booking CTAs side by side: a primary native flow and a
    /// secondary voice option. Both produce the same downstream
    /// "scheduled" service-history row, and the same persona-aware
    /// filtering logic — they just differ in input modality.
    @ViewBuilder
    private var bookingCTAs: some View {
        VStack(spacing: 10) {
            // Primary: native flow. Filled, theme-tinted, prominent.
            Button {
                isBookingFlowPresented = true
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "calendar.badge.plus")
                        .font(.title2)
                        .foregroundStyle(.white)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(nativeBookingLabel).font(.headline).foregroundStyle(.white)
                        Text("Pick a service, location, and time.")
                            .font(.caption).foregroundStyle(.white.opacity(0.85))
                            .lineLimit(1)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .foregroundStyle(.white.opacity(0.7))
                }
                .padding(14)
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(theme.primary)
                        .shadow(color: theme.primary.opacity(0.25), radius: 8, x: 0, y: 3)
                )
                .padding(.horizontal)
            }
            .buttonStyle(.plain)

            // Secondary: voice flow. Outline-styled in the same brand
            // color so it reads as the alternate option, not the primary.
            Button {
                onBookService?(primedVoicePrompt)
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "mic.circle.fill")
                        .font(.title3)
                        .foregroundStyle(theme.primary)
                    Text(voiceBookingLabel)
                        .font(.subheadline.bold())
                        .foregroundStyle(theme.primary)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .foregroundStyle(theme.primary.opacity(0.6))
                }
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(theme.primary.opacity(0.08))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(theme.primary.opacity(0.3), lineWidth: 1)
                        )
                )
                .padding(.horizontal)
            }
            .buttonStyle(.plain)
        }
    }

    /// Persona-adapted label for the primary native CTA.
    private var nativeBookingLabel: String {
        switch session.layoutSegment {
        case .oem:    return "Book at Your Dealer"
        case .rental: return "Book a Quick Service"
        default:      return "Book Service"
        }
    }

    /// Persona-adapted label for the secondary voice CTA.
    private var voiceBookingLabel: String {
        switch session.layoutSegment {
        case .oem:    return "Or book by voice"
        case .rental: return "Or talk to assistant"
        default:      return "Or book by voice"
        }
    }

    /// Persona-adapted primed prompt sent to Nova when the driver
    /// taps the voice CTA. Mirrors what we used in the original
    /// (Option 1) implementation.
    private var primedVoicePrompt: String {
        switch session.layoutSegment {
        case .oem:
            return "I'd like to book a service appointment at my dealer."
        case .rental:
            return "I'd like to book a quick service for this rental."
        default:
            return "I'd like to book a service appointment."
        }
    }

    private func refresh(force: Bool) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        let client = VSAClient(idTokenProvider: { token })
        await session.loadServiceHistory(client: client, force: force)
    }
}

// MARK: - Upcoming service section

/// Visual layout lifted verbatim from the pre-2026-05-06 AlertsTabView.
/// Kept `fileprivate` because it's the only consumer; if ServiceTabView
/// ever wants to share sections with another tab we can promote it.
fileprivate struct UpcomingServiceSection: View {
    let records: [ServiceRecord]
    let isLoading: Bool
    let error: String?
    let theme: TenantTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionHeader("Upcoming Service", systemImage: "calendar.badge.clock")

            if let error {
                SectionCard(theme: theme) {
                    HStack(spacing: 10) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Couldn't load upcoming service").font(.subheadline).bold()
                            Text(error).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                }
                .padding(.horizontal)
            } else if records.isEmpty {
                SectionCard(theme: theme) {
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark.seal.fill")
                            .foregroundStyle(theme.primary.opacity(0.7))
                        VStack(alignment: .leading, spacing: 2) {
                            Text("No upcoming service").font(.subheadline).bold()
                            Text(isLoading
                                 ? "Refreshing…"
                                 : "Voice-booked appointments appear here after the assistant creates them.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                }
                .padding(.horizontal)
            } else {
                VStack(spacing: 10) {
                    ForEach(records) { r in
                        UpcomingServiceRow(record: r, theme: theme)
                    }
                }
                .padding(.horizontal)
            }
        }
    }

    private func sectionHeader(_ title: String, systemImage: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage).foregroundStyle(theme.primary)
            Text(title).font(.headline)
            Spacer()
        }
        .padding(.horizontal)
    }
}

fileprivate struct UpcomingServiceRow: View {
    let record: ServiceRecord
    let theme: TenantTheme

    var body: some View {
        let level = AlertLevel(from: record.triagePriority)
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                StatusBadge(level: level)
                Text(headlineText).font(.subheadline).bold()
                Spacer()
                if let when = record.scheduledFor {
                    Text(relativeTimeString(from: when))
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            if let symptom = record.reportedSymptom, !symptom.isEmpty {
                Text(symptom)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            HStack(spacing: 8) {
                if let req = record.requestNumber {
                    Label(req, systemImage: "number")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                if let make = record.make, let model = record.model {
                    Text("· \(make) \(model)")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(level.color.opacity(0.25), lineWidth: 1)
                )
        )
    }

    /// Prefer the description field (from book()'s narrative/reportedSymptom),
    /// fall back to the serviceType enum with a light bit of prettification.
    private var headlineText: String {
        if let desc = record.description, !desc.isEmpty {
            return desc
        }
        if let t = record.serviceType {
            return t.replacingOccurrences(of: "_", with: " ").capitalized
        }
        return "Scheduled service"
    }
}

// MARK: - Service history section

fileprivate struct ServiceHistorySection: View {
    let records: [ServiceRecord]
    let isLoading: Bool
    let theme: TenantTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "wrench.and.screwdriver.fill").foregroundStyle(theme.primary)
                Text("Service History").font(.headline)
                Spacer()
                if !records.isEmpty {
                    Text("\(records.count)")
                        .font(.caption).foregroundStyle(.secondary)
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .background(Capsule().fill(Color(.tertiarySystemFill)))
                }
            }
            .padding(.horizontal)

            if records.isEmpty {
                SectionCard(theme: theme) {
                    HStack(spacing: 10) {
                        Image(systemName: "tray")
                            .foregroundStyle(theme.primary.opacity(0.5))
                        Text(isLoading ? "Loading history…" : "No service history on file.")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                    }
                }
                .padding(.horizontal)
            } else {
                VStack(spacing: 8) {
                    ForEach(records) { r in
                        ServiceHistoryRow(record: r)
                    }
                }
                .padding(.horizontal)
            }
        }
    }
}

fileprivate struct ServiceHistoryRow: View {
    let record: ServiceRecord

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(record.description ?? (record.serviceType ?? "Service")).font(.subheadline)
                HStack(spacing: 6) {
                    if let provider = record.provider { Text(provider).font(.caption2).foregroundStyle(.secondary) }
                    if let m = record.mileageAtService {
                        Text("· \(m) mi").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(formatDate(record.serviceDate)).font(.caption).foregroundStyle(.secondary)
                if let c = record.cost?.total {
                    Text("$\(Int(c))").font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.vertical, 10).padding(.horizontal, 12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    private func formatDate(_ iso: String) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = f.date(from: iso) ?? ISO8601DateFormatter().date(from: iso)
        guard let date else { return String(iso.prefix(10)) }
        let out = DateFormatter()
        out.dateStyle = .medium
        return out.string(from: date)
    }
}
