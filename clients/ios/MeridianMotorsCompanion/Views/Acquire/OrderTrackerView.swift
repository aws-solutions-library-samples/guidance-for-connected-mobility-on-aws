import SwiftUI
import MapKit

// MARK: - Pipeline stage model

/// The 10 fixed pipeline stages for a vehicle order.
///
/// Stage identifiers are stable Swift enum cases; display names and
/// narration templates come from `AcquireConfig` (tenant-configurable)
/// with hard-coded fallbacks so the view renders without config.
///
/// Per spec: "the stage-name strings displayed to the user come from
/// `AcquireConfig`; the Swift enum uses stable identifiers."
enum PipelineStage: String, CaseIterable, Identifiable {
    case orderPlaced
    case paymentReceived
    case assignedToPlant
    case subAssembly
    case paint
    case finalAssembly
    case qualityControl
    case shipped
    case atDealer
    case readyForDelivery

    var id: String { rawValue }

    /// Default display name used when the tenant config does not override it.
    var defaultDisplayName: String {
        switch self {
        case .orderPlaced:        return "Order Placed"
        case .paymentReceived:    return "Payment Received"
        case .assignedToPlant:    return "Assigned to Plant"
        case .subAssembly:        return "Sub-Assembly"
        case .paint:              return "Paint"
        case .finalAssembly:      return "Final Assembly"
        case .qualityControl:     return "Quality Control"
        case .shipped:            return "Shipped"
        case .atDealer:           return "At Dealer"
        case .readyForDelivery:   return "Ready for Delivery"
        }
    }

    /// Default one-liner narration shown beneath the stage name.
    var defaultNarration: String {
        switch self {
        case .orderPlaced:
            return "We got your order. You'll hear from your dealer within 24 hours."
        case .paymentReceived:
            return "Deposit confirmed. Refundable until manufacturing stage begins."
        case .assignedToPlant:
            return "Your vehicle is queued at the assembly plant."
        case .subAssembly:
            return "Engine and frame are being built. Est. 3–5 days."
        case .paint:
            return "Your chosen livery is being applied."
        case .finalAssembly:
            return "Final systems check and road-worthy validation."
        case .qualityControl:
            return "Passed factory inspection."
        case .shipped:
            return "In transit to your dealer."
        case .atDealer:
            return "Your vehicle is at the dealer, undergoing pre-delivery inspection."
        case .readyForDelivery:
            return "Ready for pickup. Tap below to schedule your handover."
        }
    }

    /// SF Symbol associated with this stage.
    var symbolName: String {
        switch self {
        case .orderPlaced:        return "checkmark.circle"
        case .paymentReceived:    return "creditcard"
        case .assignedToPlant:    return "building.2"
        case .subAssembly:        return "hammer"
        case .paint:              return "paintbrush"
        case .finalAssembly:      return "gearshape.2"
        case .qualityControl:     return "checkmark.seal"
        case .shipped:            return "shippingbox"
        case .atDealer:           return "location.fill"
        case .readyForDelivery:   return "flag.checkered"
        }
    }

    /// Parses a stage ID string (from `AcquireOrder.currentStage`) to an enum case.
    /// Falls back to `.orderPlaced` for unknown values so the UI always renders.
    static func from(_ raw: String) -> PipelineStage {
        PipelineStage(rawValue: raw) ?? .orderPlaced
    }
}

// MARK: - OrderTrackerView

/// Post-order tracking screen.
///
/// Shows the 10 pipeline stages as a vertical stepper. The current stage has
/// a pulsing indicator matching the `AssistantFAB` active-state animation.
///
/// ## Polling
/// Polls `GET /acquire/orders/{orderId}` every 30 s while the view is in the
/// foreground. Because the endpoint is not yet provisioned in this window
/// (decisions.md 2026-07-28 "Demo scope"), polling failures are silently
/// swallowed and the view renders from `initialStage` / `handoff` state
/// instead of blocking.
///
/// ## Assistant priming
/// The floating mic FAB (owned by `MainTabView`) is preserved. The order
/// tracker does not add another FAB — it relies on the existing one. When a
/// user taps the FAB while viewing this screen, the assistant opens in the
/// normal full-screen cover flow.
struct OrderTrackerView: View {
    @Environment(AppSession.self) private var session
    /// Injected at the app root by `.detectAdaptiveLayout()`.
    /// Used to adapt layout for compact-width contexts (iPhone portrait/landscape).
    @Environment(\.adaptiveLayoutContext) private var layoutContext

    let orderId: String
    let theme: TenantTheme
    /// Pre-known stage from the reservation handoff. Used as initial state
    /// before the first poll completes (or if the endpoint is absent).
    let initialStage: PipelineStage
    /// Optional callback invoked when the user taps the assistant prompt chip
    /// below the current pipeline stage. The caller (MainTabView) wires this
    /// to the standard `assistantInitialMessage` + `isAssistantPresented` state
    /// so the assistant opens with a primed `manufacturing_explain` context.
    /// When nil the chip is hidden — backward-compatible with existing call sites.
    var onAskAssistant: ((String) -> Void)? = nil

    /// Assigned assembly plant, for the map at the top of the tracker.
    ///
    /// Optional because the tracker is also reachable without a plan in hand (a
    /// resumed order, or a caller that has only an orderId). Nil renders no map
    /// rather than an empty frame or a default region — a map centred on nowhere
    /// is worse than no map.
    var facility: SupplyChainPlan.Facility? = nil

    /// Whether to wrap content in a `NavigationStack`.
    ///
    /// False when presented inside a flow that already supplies one. Every existing
    /// caller gets the previous behaviour by default. This is the same trap that
    /// `ConfiguratorFlow.Chrome` exists for, and the reason the dealer picker stopped
    /// being a sheet: nesting presentation and navigation containers in this flow has
    /// already broken it once.
    var showsOwnNavigationStack: Bool = true

    /// View model extracted in Group 2 (task 2.3). Owns stage-status
    /// determination, poll-result application, and error classification.
    /// Adopted here in Group 3 (task 3.3) — the view model previously had
    /// no runtime consumer.
    @State private var viewModel: OrderTrackerViewModel
    @State private var pollTask: Task<Void, Never>? = nil

    // Active-indicator pulse driven by continuous animation
    @State private var pulse: Bool = false

    init(orderId: String,
         theme: TenantTheme,
         initialStage: PipelineStage = .orderPlaced,
         onAskAssistant: ((String) -> Void)? = nil,
         facility: SupplyChainPlan.Facility? = nil,
         showsOwnNavigationStack: Bool = true) {
        self.orderId = orderId
        self.theme = theme
        self.initialStage = initialStage
        self.onAskAssistant = onAskAssistant
        self.facility = facility
        self.showsOwnNavigationStack = showsOwnNavigationStack
        self._viewModel = State(initialValue: OrderTrackerViewModel(initialStage: initialStage))
    }
    // MARK: - Body

    var body: some View {
        if showsOwnNavigationStack {
            NavigationStack { trackerContent }
        } else {
            trackerContent
        }
    }

    /// Map of the assigned plant, zoomed in on it.
    ///
    /// **Not live vehicle tracking, and must not read as it.** The pin is the plant the
    /// build was assigned to by `SupplyChainPlan`, which is curated demo data — the
    /// caption says so, for the same reason `SupplyChainPlan.attributionNote` exists.
    /// Showing a moving vehicle here would assert telemetry this system does not have.
    @ViewBuilder
    private var factoryMap: some View {
        if let facility {
            let coord = CLLocationCoordinate2D(latitude: facility.latitude,
                                               longitude: facility.longitude)
            VStack(alignment: .leading, spacing: 6) {
                Map(initialPosition: .region(
                    MKCoordinateRegion(center: coord,
                                       // ~25 km across: close enough to read as "this
                                       // plant", wide enough to show its setting.
                                       latitudinalMeters: 25_000,
                                       longitudinalMeters: 25_000)
                )) {
                    Marker(facility.displayName, systemImage: "building.2.fill", coordinate: coord)
                        .tint(theme.primary)
                }
                .frame(height: layoutContext.isCompact ? 180 : 260)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .allowsHitTesting(false)   // decorative; the flow owns the gestures

                Text("Assembly assigned to \(facility.displayName) — \(facility.regionLabel). "
                     + "Plant location, not live vehicle position.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.bottom, 10)
        }
    }

    @ViewBuilder
    private var trackerContent: some View {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    factoryMap
                    orderHeaderRow
                    Divider().padding(.bottom, 4)
                    stageList
                    if let onAsk = onAskAssistant {
                        assistantPrimedPromptChip(onAsk: onAsk)
                            .padding(.top, 8)
                    }
                    if viewModel.currentStage == .readyForDelivery {
                        scheduleHandoverButton
                    }
                    if let err = viewModel.lastError, !err.isEndpointUnavailable {
                        errorFooter(err)
                    }
                }
                .padding(layoutContext.isCompact ? 12 : 16)
            }
            .navigationTitle("Order Tracker")
            .navigationBarTitleDisplayMode(.inline)
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
            .onAppear { startPolling() }
            .onDisappear { stopPolling() }
    }

    // MARK: - Header

    @ViewBuilder
    private var orderHeaderRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Order ID")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(orderId)
                .font(.subheadline.monospaced())
            if viewModel.isLoading {
                ProgressView()
                    .scaleEffect(0.7)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .padding(.bottom, 12)
    }

    // MARK: - Stage list

    @ViewBuilder
    private var stageList: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(PipelineStage.allCases.enumerated()), id: \.element.id) { index, stage in
                stageRow(stage: stage, isLast: index == PipelineStage.allCases.count - 1)
            }
        }
    }

    @ViewBuilder
    private func stageRow(stage: PipelineStage, isLast: Bool) -> some View {
        let status = viewModel.stageStatus(for: stage)
        HStack(alignment: .top, spacing: 12) {
            // Left column: icon + connector line
            VStack(spacing: 0) {
                stageIcon(stage: stage, status: status)
                if !isLast {
                    Rectangle()
                        .fill(status == .completed ? theme.primary : Color(.separator))
                        .frame(width: 2)
                        .frame(minHeight: 36)
                }
            }
            // Right column: name + narration + timestamp
            VStack(alignment: .leading, spacing: 4) {
                Text(stage.defaultDisplayName)
                    .font(status == .current ? .subheadline.bold() : .subheadline)
                    .foregroundStyle(status == .upcoming ? .tertiary : .primary)
                Text(narration(for: stage))
                    .font(.caption)
                    .foregroundStyle(status == .upcoming ? .quaternary : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let ts = viewModel.stageHistory[stage.rawValue] {
                    Text(ts, style: .date)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(.bottom, 16)
        }
    }

    @ViewBuilder
    private func stageIcon(stage: PipelineStage, status: OrderStageStatus) -> some View {
        ZStack {
            Circle()
                .fill(iconBackground(status))
                .frame(width: 32, height: 32)
            if status == .current {
                // Pulsing indicator — mirrors AssistantFAB active-state animation
                Circle()
                    .fill(theme.primary.opacity(0.3))
                    .frame(width: 40, height: 40)
                    .scaleEffect(pulse ? 1.15 : 0.9)
                    .opacity(pulse ? 0.7 : 1.0)
                    .onAppear {
                        withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                            pulse.toggle()
                        }
                    }
            }
            Image(systemName: iconSymbol(stage, status: status))
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(iconForeground(status))
        }
        .frame(width: 32, height: 32)
    }

    // MARK: - Stage status helpers

    private func iconBackground(_ status: OrderStageStatus) -> Color {
        switch status {
        case .completed: return theme.primary
        case .current:   return theme.primary
        case .upcoming:  return Color(.systemGray5)
        }
    }

    private func iconForeground(_ status: OrderStageStatus) -> Color {
        switch status {
        case .completed, .current: return .white
        case .upcoming:            return Color(.systemGray3)
        }
    }

    private func iconSymbol(_ stage: PipelineStage, status: OrderStageStatus) -> String {
        status == .completed ? "checkmark" : stage.symbolName
    }

    private func narration(for stage: PipelineStage) -> String {
        // Prefer any server-supplied narration from stageHistory; fall back to default.
        stage.defaultNarration
    }

    // MARK: - Assistant primed-prompt chip

    /// Tappable chip below the tracker stages that primes the assistant with
    /// a `manufacturing_explain` question for the current stage + order.
    /// Fires `onAskAssistant` which is wired to MainTabView's standard
    /// `assistantInitialMessage` + `assistantPresentationToken` + `isAssistantPresented`
    /// state — no assistant plumbing is duplicated here.
    @ViewBuilder
    private func assistantPrimedPromptChip(onAsk: @escaping (String) -> Void) -> some View {
        Button {
            let stageName = viewModel.currentStage.defaultDisplayName
            // Include orderId context so the agent can ground its response
            // against the specific order (spec: "FAB tap must pass stage + order_id context").
            let message = "what does \(stageName) mean? (order \(orderId))"
            onAsk(message)
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                    .font(.caption.bold())
                    .foregroundStyle(theme.primary)
                Text("Ask about \"\(viewModel.currentStage.defaultDisplayName)\"")
                    .font(.caption.bold())
                    .foregroundStyle(theme.primary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(theme.primary.opacity(0.5))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(theme.primary.opacity(0.08))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .strokeBorder(theme.primary.opacity(0.25), lineWidth: 1)
                    )
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Ask assistant what \(viewModel.currentStage.defaultDisplayName) means")
    }

    // MARK: - Footer items

    @ViewBuilder
    private var scheduleHandoverButton: some View {
        Button {
            // Reuses the existing Service stage book() pattern.
            // Actual handover scheduling is a Group 4 concern; this
            // is the CTA shell.
        } label: {
            Label("Schedule handover", systemImage: "calendar.badge.plus")
                .bold()
                .frame(maxWidth: .infinity, minHeight: 32)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(theme.primary)
        .padding(.top, 16)
    }

    @ViewBuilder
    private func errorFooter(_ error: AcquireError) -> some View {
        Text("Tracker update failed: \(error.localizedDescription)")
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.top, 12)
    }

    // MARK: - Polling

    private func startPolling() {
        pollTask = Task { @MainActor in
            // Initial fetch immediately
            await viewModel.fetchOrder(orderId: orderId, session: session)
            // Then repeat every 30 s while not cancelled
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                guard !Task.isCancelled else { break }
                await viewModel.fetchOrder(orderId: orderId, session: session)
            }
        }
    }

    private func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }
}
