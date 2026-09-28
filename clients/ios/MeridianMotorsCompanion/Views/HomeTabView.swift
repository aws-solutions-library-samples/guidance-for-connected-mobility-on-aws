import SwiftUI

/// Driver-centric home screen. Post-2026-04-30 rewrite: the landing page
/// is organized around "you are this driver, this is your vehicle, this is
/// what's upcoming, this is how you're doing" — not around the tenant /
/// telemetry-frame abstraction of the earlier prototype.
///
/// Data sources, all CMS-backed:
///   - session.currentDriver  → GET /drivers/me (JWT-resolved)
///   - session.currentVehicle → GET /drivers/me (assigned vehicle)
///   - session.scheduledService → GET /vehicles/{id}/service-history
///   - session.completedService → GET /vehicles/{id}/service-history
///   - session.recentTrips    → GET /vehicles/{id}/trips
///
/// The triage history + mock telemetry frame remain available for the
/// "Latest alert" strip when present, but they're no longer the primary
/// organizing principle.
struct HomeTabView: View {
    let frame: TelemetryFrame
    let theme: TenantTheme
    /// Invoked when the user taps the "critical alerts" banner. The
    /// parent (MainTabView) sets the TabView selection to .alerts so
    /// the driver lands directly on the list. The argument is the
    /// DTC severity filter that should be active when Alerts mounts —
    /// .critical for a critical-banner tap, .highPlus when only
    /// HIGH alerts exist (so the driver actually sees the row the
    /// banner promised; default Critical-only filter would hide it).
    /// Optional for tests / previews that don't need the behavior.
    /// Added 2026-05-04, filter parameter added 2026-05-19.
    var onJumpToAlerts: ((DtcFilter?) -> Void)? = nil
    /// Switches to the Buy tab. Supplied by MainTabView, which owns tab
    /// selection. Optional so previews and any future embedding of this view
    /// still compile without it; the offer CTA is simply inert when nil.
    var onOpenBuyTab: (() -> Void)? = nil

    /// Switches to the Service tab. Injected rather than reached directly so
    /// Home stays free of tab-selection state, matching `onOpenBuyTab`.
    var onOpenService: (() -> Void)? = nil
    /// Opens Account, which is no longer a tab. Presented as a sheet by
    /// `MainTabView`; this view only asks for it.
    var onOpenAccount: (() -> Void)? = nil
    /// Opens the assistant with a primed message. Used by the vehicle-health
    /// breakdown to hand prioritisation to the agent rather than inventing it
    /// client-side. Mirrors `ConfiguratorFlow.onAskAssistant`.
    var onAskAssistant: ((String) -> Void)? = nil
    @Environment(AppSession.self) private var session

    /// Whether the welcome banner is currently rendered. Starts true,
    /// flips to false 2.8s after first appearance — the .task on the
    /// banner view handles the timer. Once dismissed, the
    /// session-scoped `hasShownWelcomeForCurrentSession` flag prevents
    /// it from re-appearing on subsequent Home visits.
    @State private var welcomeBannerVisible: Bool = true
    /// Presents the vehicle-health breakdown. The `healthScoreBreakdown` field has
    /// been on the model unused since 2026-05-19 awaiting exactly this affordance.
    @State private var showHealthDetail = false
    @State private var showControls = false
    /// PROTOTYPE (2026-08-19): presents the upgrade offer as a temporary detented
    /// sheet instead of a permanent card in the scroll flow. Being compared against
    /// the inline treatment — see `offerSheet`.
    @State private var showOfferSheet = false

    /// Set when the user taps "Not interested in upgrading", consumed by the offer
    /// sheet's `onDismiss`. Exists so the dismissal is persisted only AFTER the drawer
    /// has finished animating away.
    ///
    /// Also the flag that separates the two ways the drawer can close: a swipe means
    /// "not now" and must leave this nil, so the offer returns next session. Only an
    /// explicit decline sets it.
    @State private var declinedOffer: AcquireConfig.UpgradeOffer?
    /// Prompt to hand to the assistant once the health sheet has finished
    /// dismissing. See the `onDismiss` handler for why this is staged rather than
    /// fired directly.
    @State private var pendingHealthAssistantPrompt: String?

    /// Drives the vehicle-claim picker sheet shown from the no-vehicle state.
    @State private var showClaimPicker: Bool = false
    /// Offer whose upgrade wizard is open. Non-nil presents `UpgradeFlow`.
    @State private var selectedOffer: AcquireConfig.UpgradeOffer? = nil
    /// Warm configurator entry from an accepted upgrade offer.
    ///
    /// A separate cover cannot be attached to the same view — HomeTabView's body
    /// already owns a `.sheet` and this subtree owns a `.fullScreenCover`, and
    /// SwiftUI honours only one presentation modifier per view. So the upgrade
    /// cover's own content presents this one, which is a supported nesting.
    @State private var configureHandoff: ConfiguratorOfferHandoff? = nil
    var body: some View {
        NavigationStack {
            ScrollView {
                if session.vehicleResolution == .noVehicle {
                    // Driver resolved but has no assigned vehicle. Show a claim
                    // affordance instead of spinning forever on the (impossible)
                    // vehicle-scoped loads. This is the fix for the "Home tab
                    // spins after sign-in" dead-end when a driver is unassigned.
                    noVehicleClaimView
                } else if session.hasLoadedInitialDashboard {
                    dashboardContent
                } else {
                    dashboardSkeleton
                }
            }
            .refreshable { await refreshAll(force: true) }
            .task {
                await refreshAll(force: false)
                await maybeAutoPresentOffer()
            }
            // Re-evaluate when the offers actually arrive.
            //
            // `tenantConfig` is loaded by MainTabView, NOT by Home's `refreshAll`, so the
            // `.task` above frequently ran BEFORE any offer existed — the guard returned
            // early and nothing ever re-checked, so the drawer never appeared. Same race
            // class as the Buy-tile prompt that opened the wrong assistant conversation:
            // a one-shot read of state that another view populates.
            //
            // Safe to fire repeatedly — `hasAutoPresentedOfferSheet` makes presentation
            // once-per-session, so this is idempotent.
            .onChange(of: session.tenantConfig?.acquire?.upgradeOffers?.count) { _, _ in
                Task { await maybeAutoPresentOffer() }
            }
            // And when the dashboard finishes loading, since that is the other half of the
            // gate and can settle after the config does.
            .onChange(of: session.hasLoadedInitialDashboard) { _, loaded in
                guard loaded else { return }
                Task { await maybeAutoPresentOffer() }
            }
            .navigationTitle("Home")
            // Account lives here now rather than in the tab bar — a profile button is
            // where iOS users look for sign-out, and freeing the tab slot let the Buy
            // tab out of the "More" list.
            .toolbar {
                if let onOpenAccount {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button(action: onOpenAccount) {
                            Image(systemName: "person.crop.circle")
                        }
                        .accessibilityLabel("Account and settings")
                    }
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .background(Color(.systemGroupedBackground).ignoresSafeArea())
            .sheet(isPresented: $showClaimPicker) {
                ClaimVehicleSheet(onClaimed: {
                    showClaimPicker = false
                })
            }
        }
    }

    // MARK: - No-vehicle / claim state

    /// Shown when the signed-in driver has no assigned vehicle. Offers a
    /// self-service claim (when the CMS API is configured) instead of an
    /// indefinite loading spinner.
    @ViewBuilder
    private var noVehicleClaimView: some View {
        VStack(spacing: 18) {
            Image(systemName: "car.2")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
                .padding(.top, 48)
            Text("No vehicle assigned")
                .font(.title3).bold()
            Text("You're signed in, but no vehicle is linked to your driver profile yet.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            if VSAConfig.cmsRestApiUrl != nil {
                Button {
                    showClaimPicker = true
                } label: {
                    Label("Claim a vehicle", systemImage: "plus.circle.fill")
                        .font(.headline)
                        .padding(.horizontal, 20).padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
            } else {
                Text("Contact your fleet administrator to get a vehicle assigned.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }
            Spacer(minLength: 40)
        }
        .frame(maxWidth: .infinity)
    }

    /// Real dashboard. Only rendered once every resource that backs it
    /// has loaded at least once (see `AppSession.hasLoadedInitialDashboard`).
    /// This prevents the "100 → 39" health-score flash and similar
    /// stub-then-update jumps across the cards. Subsequent refreshes
    /// (pull-to-refresh) don't return us to the skeleton — the
    /// LoadedAt timestamps stay non-nil — so cards update in place.
    @ViewBuilder
    private var dashboardContent: some View {
        // Per-persona layout selection. Cards are gated on
        // `LayoutSegment` flags so OEM tenants drop fleet-only
        // metrics (driver safety score, recent trips), rental
        // tenants get a stripped Home plus trip-time-remaining /
        // return-to cards, and fleet tenants see everything.
        // Default segment is `.fleet` so an unconfigured tenant
        // gets the broadest layout.
        let segment = session.layoutSegment
        VStack(alignment: .leading, spacing: 16) {
            // Per-persona welcome card — shown once after sign-in,
            // auto-dismisses after ~3s. The card itself manages its
            // own visibility via .task; HomeTabView just plants it
            // here. Skipped after the first appearance via the
            // session-scoped flag.
            if !session.hasShownWelcomeForCurrentSession {
                welcomeBanner
            }
            identityStrip
                // Anchored here because every other candidate already owns a
                // presentation: the body has the claim picker, the health card and
                // the hero each have their own, and SwiftUI honours only one per
                // view — a second silently never presents.
                .sheet(isPresented: $showOfferSheet, onDismiss: {
                    // Fires for every close, so the flag is what distinguishes an
                    // explicit decline from a swipe-down. Persisting unconditionally
                    // here would turn "not now" into "never".
                    if let declined = declinedOffer {
                        UpgradeOfferDismissals.persist(dismissing: declined.id)
                        declinedOffer = nil
                    }
                }) { offerSheet }
            vehicleAlertsBanner
            // Triage sits with the other attention surfaces, not at the bottom of the page.
            // It was slot 9 — below the rental and service cards — even though it carries
            // the agent's own P0/P1 classification, which is the most actionable thing on
            // the screen when it exists. Different SOURCE from the banner above (a
            // TriageResponse, not activeDtcs), so it stays a separate surface rather than
            // being merged.
            if let triage = session.lastTriage {
                latestAlertCard(triage: triage)
            }
            // Hero before health (2026-08-19): the score used to render above the
            // card that says which vehicle it belongs to, so "79" appeared before
            // its subject. Urgency is not lost by demoting it — every DTC that
            // deducts from the score also feeds `vehicleAlertsBanner` above via
            // the same `activeDtcs`, and the two non-DTC deductions are covered
            // independently (disconnection by the hero's connection badge,
            // overdue service by `nextServiceCountdown`).
            vehicleHeroCard
            // PROTOTYPE: `upgradeOfferBanner` is deliberately NOT rendered here.
            // It moved into `offerSheet`, a temporary detented sheet, so the offer
            // occupies no permanent scroll real estate. Restoring the inline
            // treatment is one line — put `upgradeOfferBanner` back on this line and
            // drop the `.sheet` on `identityStrip`.
            vehicleHealthCard
            if segment.showsRentalTripCards {
                tripTimeRemainingCard
                returnToCard
            }
            if segment.showsNextServiceCountdown {
                nextServiceCountdown
            }
            if segment.showsUpcomingServiceCard, !session.scheduledService.isEmpty {
                // Upcoming service intentionally NOT shown here.
                //
                // It moved to the Service tab, which already rendered the same
                // data more completely: it handles the error and empty states and
                // lists ALL records, where this card capped at `.prefix(2)`. So this
                // was a strictly-worse duplicate of a screen one tap away.
                //
                // Home now answers "does anything need me, and what can I do right
                // now" — scheduled appointments are a Service-tab concern.
            }
            if segment.showsSafetyScoreCard {
                }
            if segment.showsRecentActivityCard {
                }
            if segment.showsLatestAlertCard, let triage = session.lastTriage {
            }
        }
        .padding()
    }

    /// Single quiet skeleton shown while the first dashboard load is
    /// in flight. Deliberately bland — no numbers, no labels with
    /// stub values — so the driver doesn't see anything that could
    /// be misread as real data. The ProgressView gives a clear
    /// "still loading" signal while the layout reserves rough space
    /// for the cards that are coming.
    @ViewBuilder
    private var dashboardSkeleton: some View {
        // Identity strip + several card placeholders matches the real
        // Home layout closely enough that the swap-in doesn't jump.
        TabLoadingSkeleton(cardCount: 4, showsIdentityStrip: true)
    }
    
    // MARK: - Welcome banner

    /// Persona-tinted welcome card shown once per signed-in session.
    /// Self-dismisses after ~2.8s via a `.task` timer; flips the
    /// session-scoped flag on dismiss so subsequent Home visits skip
    /// it. The banner uses the tenant's theme primary color (fleet
    /// navy / OEM blue / Enterprise green) so the brand reads
    /// instantly even before the rest of the dashboard finishes
    /// loading.
    @ViewBuilder
    private var welcomeBanner: some View {
        if welcomeBannerVisible {
            let firstName = session.currentDriver?.firstName ?? "Driver"
            let title: String = {
                switch session.layoutSegment {
                case .oem:    return "Welcome back, \(firstName)"
                case .rental: return "Hi \(firstName) — let's get you on the road"
                default:      return "Welcome back, \(firstName)"
                }
            }()
            let subtitle: String = {
                switch session.layoutSegment {
                case .oem:
                    return "Your authorized dealer is one tap away."
                case .rental:
                    return "Quick help while you're on your trip."
                default:
                    return "Live support for your fleet, on demand."
                }
            }()
            HStack(spacing: 14) {
                Image(systemName: welcomeSymbol)
                    .font(.title2)
                    .foregroundStyle(.white)
                    .frame(width: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline.bold()).foregroundStyle(.white)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.85))
                        .lineLimit(2)
                }
                Spacer()
            }
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(theme.primary.gradient)
            )
            .transition(.move(edge: .top).combined(with: .opacity))
            .task {
                // Fire-and-forget timer. SwiftUI cancels this task if
                // the view disappears, which is fine — the banner is
                // already gone visually at that point.
                try? await Task.sleep(nanoseconds: 2_800_000_000)
                withAnimation(.easeInOut(duration: 0.35)) {
                    welcomeBannerVisible = false
                }
                // Persist the "shown for this session" bit so a return
                // visit to Home doesn't re-fire the banner. We set
                // this AFTER the fade so the animation isn't cut short
                // by SwiftUI re-evaluating the parent's `if !flag`
                // gate during the transition.
                session.hasShownWelcomeForCurrentSession = true
            }
        }
    }

    /// Choose a persona-appropriate greeting glyph. Hand-wave for
    /// fleet (familiar driver), key card for rental (renter), car
    /// front for OEM (owner-context).
    private var welcomeSymbol: String {
        switch session.layoutSegment {
        case .oem:    return "car.front.waves.up.fill"
        case .rental: return "key.card.fill"
        default:      return "hand.wave.fill"
        }
    }

    // MARK: - Recall banner
    
    
    // MARK: - Vehicle health score
    
    /// Trade-in upgrade offers, popped down from the tenant config.
    ///
    /// Sits directly under the vehicle still so the trade-in framing is
    /// obvious: this is your bike, here is what replacing it costs. Content is
    /// entirely tenant-supplied (`AcquireConfig.upgradeOffers`) — no offers
    /// means no banner, which is why there is no empty state.
    @ViewBuilder
    /// The offer surface. Parameterised on presentation so the same wiring — the
    /// `onExplore` handler AND the `fullScreenCover` that presents `UpgradeFlow` —
    /// serves both the inline card and the sheet. Duplicating it for the sheet would
    /// have meant two covers to keep in step.
    private func upgradeOfferBannerView(
        presentation: UpgradeOfferBanner.Presentation = .card
    ) -> some View {
        if let offers = session.tenantConfig?.acquire?.upgradeOffers, !offers.isEmpty {
            UpgradeOfferBanner(
                presentation: presentation,
                offers: offers,
                currentVehicleTitle: session.currentVehicle?.displayTitle,
                ownedModelName: session.currentVehicle?.model,
                theme: theme,
                onExplore: { offer in
                    // Explore opens the three-step upgrade wizard rather than
                    // going straight to chat. The wizard front-loads what the
                    // platform already knows — price, loyalty credit,
                    // trade-in, indicative EMI — so the agent is left for
                    // judgement instead of data entry.
                    selectedOffer = offer
                },
                onDecline: { offer in
                    // Close the drawer first; persist once it has gone. Recording the
                    // dismissal here instead would flip the banner's render guard
                    // immediately and blank the sheet's content while the sheet was
                    // still on screen — the reported behaviour, where declining
                    // emptied the drawer rather than closing it.
                    declinedOffer = offer
                    showOfferSheet = false
                }
            )
            // Attached HERE, not on the body: HomeTabView's body already owns
            // a `.sheet` (the claim picker), and SwiftUI honours only one
            // presentation modifier per view — a second one on the same view
            // silently never presents.
            .fullScreenCover(item: $selectedOffer) { offer in
                UpgradeFlow(
                    offer: offer,
                    theme: theme,
                    onTalkToAdvisor: {
                        // UpgradeFlow has already written the summary into
                        // session.pendingDiscoverPrompt; DiscoverFlow consumes
                        // it on appear so the agent continues rather than
                        // restarting.
                        onOpenBuyTab?()
                    },
                    onConfigure: { handoff in
                        // Presented from THIS cover's content rather than from
                        // HomeTabView's body — see `configureHandoff`. UpgradeFlow
                        // has already called dismiss(), so wait for that
                        // transition before presenting or SwiftUI drops the
                        // incoming cover (same race as BuyLandingView).
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
                            configureHandoff = handoff
                        }
                    },
                    // Threaded through to the hosted configurator's success step, whose
                    // "Book a test drive" otherwise had nowhere to go.
                    onAskAssistant: onAskAssistant
                )
                .fullScreenCover(
                    isPresented: Binding(
                        get: { configureHandoff != nil },
                        set: { if !$0 { configureHandoff = nil } }
                    )
                ) {
                    if let handoff = configureHandoff {
                        ConfiguratorFlow(
                            theme: theme,
                            handoff: nil,
                            offerHandoff: handoff,
                            onAskAssistant: onAskAssistant
                        )
                    }
                }
            }
        }
    }

    /// The vehicle image alone — no card chrome, no title, no colour caption.
    ///
    /// Extracted from the former `vehicleStillCard` (2026-08-19) when the image
    /// card and the metadata card merged into `vehicleHeroCard`. The four-branch
    /// precedence below is unchanged and still load-bearing; only the surrounding
    /// `SectionCard`/title/colour rendering moved out, because the hero card now
    /// owns those and rendering them twice was the "two cards, two shadows, same
    /// title" duplication this merge removes.
    ///
    /// Precedence (2026-08-12, revised after UAT):
    ///   1. Bundled interactive sweep for this make/model/year.
    ///   2. Bundled static hero asset.
    ///   3. `VSA_VEHICLE_360_BASE_URL` hero frame.
    ///   4. Nothing (quiet fallback).
    ///
    /// Bundled deliberately beats URL: the URL was a global override from before
    /// per-make demo art existed, and a stale one (e.g. a leftover TVS Radeon
    /// turntable from a prior demo tenant) must not win over correct Meridian art.
    /// If a future spec ships a hosted Meridian turntable, replace the bundled
    /// asset or add a per-make override — do NOT resurrect the global override.
    @ViewBuilder
    private var vehicleVisual: some View {
        if let v = session.currentVehicle,
           let sweep = VehicleSweepView.bundledSweep(make: v.make, model: v.model, year: v.year) {
            VehicleSweepView(
                resourceSubdirectory: sweep.subdirectory,
                frameCount: sweep.frameCount,
                theme: theme,
                rotationMode: sweep.rotationMode,
                height: 190
            )
            .accessibilityLabel(v.displayTitle)
        } else if let v = session.currentVehicle,
                  let bundled = bundledHeroImage(for: v) {
            bundled
                .resizable()
                .scaledToFit()
                .frame(maxWidth: .infinity)
                .frame(height: 150)
                .accessibilityLabel(v.displayTitle)
        } else if let base = VSAConfig.vehicle360BaseUrl,
                  VSAConfig.vehicle360FrameCount >= Self.heroFrameIndex,
                  let url = URL(string: "\(base)/\(Self.heroFrameIndex).png"),
                  session.currentVehicle != nil {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().scaledToFit()
                case .failure:
                    // Quiet failure — a missing hero shot must not look like a
                    // malfunction on the landing screen.
                    EmptyView()
                default:
                    ProgressView().tint(theme.primary)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 150)
        }
    }

    /// Bundled hero image for a specific vehicle, or `nil` when no asset is
    /// bundled for it.
    ///
    /// Delegates to `VehicleSweepView.bundledStaticImageName(make:model:)` so the
    /// make/model → asset mapping lives in exactly one place. This helper and its
    /// twin in `VehicleTabView` were previously duplicated verbatim and keyed on
    /// make alone, which meant all four Meridian models rendered one body style.
    private func bundledHeroImage(for v: VehicleInfo) -> Image? {
        guard let name = VehicleSweepView.bundledStaticImageName(
            make: v.make, model: v.model, year: v.year) else { return nil }
        return Image(name)
    }

    /// Which frame of the 360 sequence reads best as a static hero.
    /// Frame 2 is a three-quarter/right-side view in the sequences we use;
    /// frame 1 is usually dead-on front, which looks flat as a still.
    private static let heroFrameIndex = 2

    @ViewBuilder
    private var vehicleHealthCard: some View {
        // Only show once vehicle context has loaded (prevents 100→0 flash)
        if session.vehicleContext != nil {
            // Source of truth is the server: GET /vehicles/{id}/context
            // returns `healthScore` (0..100) computed by the
            // api-vehicle-context Lambda. iOS no longer recomputes the
            // score — keeping a single formula on the backend means
            // the Home tab and the CMS UI Vehicle Detail page can
            // never disagree on the number. Default to 100 only as a
            // graceful fallback for older Lambda deploys that don't
            // emit the field; the `vehicleContext != nil` guard above
            // already prevents the pre-load flash.
            let score = session.vehicleContext?.healthScore ?? 100
            SectionCard("Vehicle Health", theme: theme) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("\(score)")
                            // Display face, not `.system`. This is the largest type in the
                            // app and the clearest place a brand voice reads — a light
                            // geometric numeral matching the Meridian wordmark rather than
                            // a bold rounded one.
                            //
                            // Applied HERE first and deliberately nowhere else: the display
                            // face is `AvenirNext-UltraLight`, and light geometric faces
                            // lose legibility fast at UI sizes. Body copy and labels stay
                            // San Francisco, which is designed for exactly that job.
                            // `displayFont` falls back to `.system` for any tenant without
                            // a display face, so non-Meridian tenants are unaffected.
                            .font(theme.displayFont(size: 46))
                            .foregroundStyle(healthColor(score))
                        Text(healthLabel(score))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    ZStack {
                        Circle()
                            .stroke(Color(.systemGray5), lineWidth: 8)
                            .frame(width: 70, height: 70)
                        Circle()
                            .trim(from: 0, to: Double(score) / 100.0)
                            .stroke(healthColor(score), style: StrokeStyle(lineWidth: 8, lineCap: .round))
                            .frame(width: 70, height: 70)
                            .rotationEffect(.degrees(-90))
                        Image(systemName: score >= 80 ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .foregroundStyle(healthColor(score))
                    }
                }
                // Affordance for the breakdown. Without a visible cue the card reads
                // as a static readout and nobody discovers the explanation behind it.
                HStack(spacing: 4) {
                    Text("Why this score?")
                        .font(.caption.weight(.semibold))
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .bold))
                }
                .foregroundStyle(theme.primary)
                .padding(.top, 2)
            }
            // Whole card is the tap target, not just the chevron row.
            .contentShape(Rectangle())
            .onTapGesture { showHealthDetail = true }
            .accessibilityAddTraits(.isButton)
            .accessibilityHint("Shows why your health score is \(score) and how to raise it")
            // `onDismiss` sequences the hand-off instead of a timer.
            //
            // The first version dismissed the sheet and then presented the assistant
            // after a fixed 0.35s, which is a race dressed as a delay: it guesses at
            // UIKit's dismissal duration and presents a fullScreenCover that may still
            // be layered under a dismissing sheet. UIKit logged "Unbalanced calls to
            // begin/end appearance transitions" around these interactions, and the
            // assistant's own start-up activates an AVAudioSession on the main thread
            // (the log carries Hang Risk diagnostics for exactly that call) — so
            // colliding the two is how this path stops responding.
            //
            // `onDismiss` fires when dismissal has actually completed, so the cover is
            // presented against a settled hierarchy with no timing assumption.
            // `BuyLandingView` documents a 0.45s wait for the same class of problem;
            // that predates this and should move to the same approach.
            .sheet(isPresented: $showHealthDetail, onDismiss: {
                guard let prompt = pendingHealthAssistantPrompt else { return }
                pendingHealthAssistantPrompt = nil
                onAskAssistant?(prompt)
            }) {
                VehicleHealthDetailView(
                    score: score,
                    breakdown: session.vehicleContext?.healthScoreBreakdown,
                    theme: theme,
                    onAskAssistant: onAskAssistant.map { _ in
                        { message in
                            // Stage the prompt and close; `onDismiss` delivers it.
                            pendingHealthAssistantPrompt = message
                            showHealthDetail = false
                        }
                    },
                    onDone: { showHealthDetail = false }
                )
            }
        }
    }
    
    private func healthColor(_ score: Int) -> Color {
        if score >= 80 { return .green }
        if score >= 60 { return .orange }
        return .red
    }
    
    private func healthLabel(_ score: Int) -> String {
        if score >= 90 { return "Excellent" }
        if score >= 80 { return "Good" }
        if score >= 60 { return "Needs Attention" }
        return "Service Required"
    }
    
    // MARK: - Next service countdown
    
    @ViewBuilder
    private var nextServiceCountdown: some View {
        if let nextService = session.scheduledService.first {
            SectionCard("Next Service", theme: theme) {
                HStack(spacing: 12) {
                    Image(systemName: "wrench.and.screwdriver.fill")
                        .font(.title2)
                        .foregroundStyle(theme.primary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(nextService.serviceType ?? "Scheduled Service")
                            .font(.subheadline.bold())
                        let date = ISO8601DateFormatter().date(from: nextService.serviceDate)
                        if let date {
                            let days = Calendar.current.dateComponents([.day], from: Date(), to: date).day ?? 0
                            if days > 0 {
                                Text("In \(days) day\(days == 1 ? "" : "s")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            } else if days == 0 {
                                Text("Today")
                                    .font(.caption.bold())
                                    .foregroundStyle(.orange)
                            } else {
                                Text("Overdue by \(abs(days)) day\(abs(days) == 1 ? "" : "s")")
                                    .font(.caption.bold())
                                    .foregroundStyle(.red)
                            }
                        }
                        if let provider = nextService.provider {
                            Text(provider)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                }
            }
        }
    }
    
    // MARK: - Last trip card
    
    


    @ViewBuilder
    private func alertsBannerRow(icon: String, tint: Color, title: String, subtitle: String, jumpFilter: DtcFilter) -> some View {
        Button(action: { onJumpToAlerts?(jumpFilter) }) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.title3)
                    .foregroundStyle(tint)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline).bold().foregroundStyle(.primary)
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.footnote).foregroundStyle(.tertiary)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(tint.opacity(0.12))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(tint.opacity(0.4), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Refresh

    private func refreshAll(force: Bool) async {
        guard case .signedIn(let token, _) = session.authState else { return }
        let client = VSAClient(idTokenProvider: { token })
        // Load driver first so effectiveVehicleId resolves correctly for
        // the subsequent calls. loadCurrentDriver eager-warms vehicleContext
        // internally, so we don't need to call that separately.
        await session.loadCurrentDriver(client: client, force: force)
        // These run in parallel — they all key off effectiveVehicleId.
        async let svc: Void = session.loadServiceHistory(client: client, force: force)
        async let trips: Void = session.loadRecentTrips(client: client, force: force)
        async let live: Void = session.loadLiveState(client: client, force: force)
        async let ctx: Void = session.loadVehicleContext(client: client, force: force)
        _ = await (svc, trips, live, ctx)
    }

    // MARK: - Identity strip

    @ViewBuilder
    private var identityStrip: some View {
        HStack(spacing: 12) {
            avatarCircle
            VStack(alignment: .leading, spacing: 2) {
                Text(greetingText)
                    .font(.title2).bold()
                Text(identitySubtext)
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1)
                // Secondary stats line (2026-05-05): total trips /
                // miles / last trip date. Mirrors what the CMS driver
                // detail card shows under "Driver Statistics" so the
                // two surfaces agree on lifetime metrics. Only renders
                // when we have the data to avoid a spurious blank line.
                // Lifetime driver stats (total trips / miles / last trip date) removed
                // from Home 2026-08-21. Home answers "does anything need me, and what can I
                // do now"; a lifetime total answers neither, and it made the greeting a
                // three-line block sitting above the alert banner. `driverStatsLine` is
                // retained and unused here — it is the natural content for the Account
                // sheet, which is where a driver would look for their own history.
            }
            Spacer()
        }
    }

    /// Second-line driver stats: "4,096 trips · 360K mi · last Apr 20".
    /// Returns nil when there's nothing meaningful to show so the
    /// Text() above is dropped entirely (keeps the identity strip
    /// compact for new drivers with no data yet).
    private var driverStatsLine: String? {
        guard let d = session.currentDriver else { return nil }
        var parts: [String] = []
        if let t = d.totalTrips, t > 0 {
            parts.append(Self.formatCount(t) + " trips")
        }
        if let m = d.totalMiles, m > 0 {
            parts.append(Self.abbreviateCount(m) + " mi")
        }
        if let last = d.lastTripDate, !last.isEmpty {
            parts.append("last \(Self.shortDate(last))")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// "4096" → "4.1K"; "360180" → "360K". Match the CMS compact style.
    private static func formatCount(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 10_000    { return "\(n / 1000)K" }
        if n >= 1_000     { return String(format: "%.1fK", Double(n) / 1000) }
        return "\(n)"
    }

    /// Abbreviates a large number ("39840" -> "39K"). Appends **no** unit.
    ///
    /// Renamed from `formatMiles` — that name was wrong twice over. It adds no unit,
    /// and `VehicleTabView` has a *different* function also called `formatMiles`
    /// which does append `" mi"`. Two same-named helpers with different contracts in
    /// two files is how a caller ends up double-labelling or not labelling at all.
    /// The one call site supplies " mi" itself.
    private static func abbreviateCount(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000     { return "\(n / 1000)K" }
        return "\(n)"
    }

    /// "2026-04-20" or full ISO → "Apr 20". Falls back to raw string
    /// when parsing fails so we never swallow unexpected formats.
    private static func shortDate(_ s: String) -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withFullDate]
        let date: Date?
        if let d = iso.date(from: s) {
            date = d
        } else {
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd"
            date = f.date(from: String(s.prefix(10)))
        }
        guard let date else { return s }
        let out = DateFormatter()
        out.dateFormat = "MMM d"
        return out.string(from: date)
    }

    private var greetingText: String {
        let hour = Calendar.current.component(.hour, from: Date())
        let timeOfDay: String
        switch hour {
        case 5..<12: timeOfDay = "Good morning"
        case 12..<17: timeOfDay = "Good afternoon"
        case 17..<22: timeOfDay = "Good evening"
        default:      timeOfDay = "Hello"
        }
        if let firstName = session.currentDriver?.firstName {
            return "\(timeOfDay), \(firstName)"
        }
        return timeOfDay
    }

    private var identitySubtext: String {
        guard let d = session.currentDriver else {
            return "Loading profile…"
        }
        var parts: [String] = []
        if let home = d.homeBase { parts.append(home) }
        if let lic = d.licenseClass { parts.append(lic) }
        if let yrs = d.yearsExperience { parts.append("\(yrs) yrs") }
        return parts.joined(separator: " · ")
    }

    private var avatarCircle: some View {
        let initials = session.currentDriver?.initials ?? "?"
        return ZStack {
            Circle()
                .fill(theme.primary.opacity(0.15))
                .frame(width: 52, height: 52)
            Text(initials)
                .font(.title3).bold()
                .foregroundStyle(theme.primary)
        }
        .overlay(
            Circle().strokeBorder(theme.primary.opacity(0.4), lineWidth: 1.5)
        )
    }

    // MARK: - Vehicle card

    @ViewBuilder
    /// The vehicle hero — identity, image, primary actions, and live stats in
    /// one card.
    ///
    /// Merged 2026-08-19 from `vehicleStillCard` + `vehicleCard`, which both
    /// rendered `displayTitle` in their own `SectionCard`. That produced two
    /// stacked cards with two shadows repeating the same heading, and left the
    /// vehicle with no single visual anchor on Home. The order here is
    /// deliberate: who the car is, what it looks like, what you can do with it,
    /// then the numbers. Actions sit directly under the image because that is
    /// where the eye already is after the hero, and it is the pattern travel and
    /// automotive apps have converged on.
    private var vehicleHeroCard: some View {
        let v = session.currentVehicle
        // Explicit `return` because a `let` binding precedes the single view
        // expression. This disables ViewBuilder for the property, which is
        // harmless here (one expression is returned) and produces an
        // informational warning. Adding `@ViewBuilder` instead fails: the
        // declaration above already carries one, and Swift permits only one
        // result-builder attribute per declaration.
        return SectionCard(theme: theme) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 10) {
                    Image(systemName: "car.fill")
                        .font(.title3).foregroundStyle(theme.primary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(v?.displayTitle ?? "Loading vehicle…")
                            .font(.headline)
                        if let subtitle = vehicleSubtitle(v) {
                            Text(subtitle)
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    connectionBadge
                }

                vehicleVisual

                if v != nil {
                    HomeActionRow(actions: heroActions, theme: theme)
                    Divider()
                    vehicleStatsGrid(v!)
                    // Prefer the Redis-backed live-state timestamp, which
                    // reflects the most recent connectivity check (5-min
                    // window). Fall back to the DDB `lastSeenAt` only if
                    // live-state hasn't loaded yet — that field is written
                    // by the ingestion pipeline and can lag by days, which
                    // produced the misleading "Last seen 1w ago" on the
                    // home card even when the vehicle was currently active.
                    if let live = session.liveState, let connectedAt = live.lastConnectedAt {
                        Text("Last seen \(relativeTimeString(from: connectedAt))")
                            .font(.caption2).foregroundStyle(.tertiary)
                    } else if let lastSeen = v?.lastSeenAt {
                        Text("Last seen \(relativeTimeString(from: lastSeen))")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
        }
        // Attached to the hero, not the body: the body already owns the claim-picker
        // sheet and the health card owns its own. SwiftUI honours a single
        // presentation per view, and a second one on the same view silently never
        // presents — a failure mode already documented on `upgradeOfferBanner`.
        .sheet(isPresented: $showControls) {
            if let vehicle = session.currentVehicle,
               case .signedIn(let token, _) = session.authState {
                VehicleControlsSheet(
                    vehicleId: vehicle.vehicleId,
                    theme: theme,
                    client: VSAClient(idTokenProvider: { token }),
                    isConnected: session.liveState?.isConnected
                        ?? (vehicle.connectionStatus?.lowercased() == "connected"),
                    onDone: { showControls = false }
                )
            }
        }
    }

    /// Presents the offer sheet at most once per session, and only when there is
    /// something to show.
    ///
    /// Gated on `hasLoadedInitialDashboard` rather than firing from `onAppear`: a
    /// sheet that arrives before the vehicle behind it has rendered reads as a
    /// launch interruption, which is the pattern Apple's HIG warns against and users
    /// reflexively dismiss. Waiting until the screen is populated makes it a
    /// suggestion about something visible.
    ///
    /// Permanent per-offer suppression is shared with the banner via
    /// `UpgradeOfferDismissals`, so a dismissed offer is blocked at this gate rather
    /// than producing an empty sheet.
    private func maybeAutoPresentOffer() async {
        guard !session.hasAutoPresentedOfferSheet,
              session.hasLoadedInitialDashboard,
              let offers = session.tenantConfig?.acquire?.upgradeOffers,
              !offers.isEmpty,
              // Do not present over a dismissed offer. The banner suppresses itself
              // per offer id, so without this the sheet would arrive empty — an
              // unbidden blank sheet being strictly worse than the fold problem it
              // was introduced to solve. Uses the same selection the banner uses, so
              // the gate cannot answer about a different offer than the one rendered.
              UpgradeOfferDismissals.hasUndismissedOffer(
                  among: offers,
                  ownedModelName: session.currentVehicle?.model
              )
        else { return }
        session.hasAutoPresentedOfferSheet = true
        // Brief settle so the hero has painted first; the sheet then animates in over
        // a screen the user has already seen rather than over a skeleton.
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        showOfferSheet = true
    }

    /// PROTOTYPE — the upgrade offer as a temporary sheet rather than a permanent card.
    ///
    /// `.medium` peeks about half the screen, which fits the collapsed header plus the
    /// offer row; `.large` is available for the detail. Background interaction stays
    /// enabled so the sheet reads as an arriving suggestion rather than a wall — the
    /// user can still see and scroll their vehicle behind it, which is the difference
    /// between "here is something for you" and "answer this before continuing".
    ///
    /// Renders `upgradeOfferBanner` unchanged, deliberately: it already carries the
    /// `onExplore` handler AND the `fullScreenCover` that presents `UpgradeFlow`. Had
    /// this rebuilt the content instead, removing the banner from the scroll flow
    /// would have orphaned that cover and Explore would have silently done nothing.
    @ViewBuilder
    private var offerSheet: some View {
        ScrollView {
            upgradeOfferBannerView(presentation: .sheet)
                .padding(.horizontal, 16)
                .padding(.top, 20)
        }
        .presentationDetents([.medium, .large])
        .presentationBackgroundInteraction(.enabled(upThrough: .medium))
        .presentationDragIndicator(.visible)
    }

    /// Single alert surface, from one pass over `activeDtcs`.
    ///
    /// Was two stacked banners — `criticalAlertsBanner` and `recallBanner` — each scanning
    /// the same `session.activeDtcs` independently and each rendering its own full-width
    /// card. On a vehicle with both a critical fault and an open recall the user met two
    /// alarm bars before reaching their car.
    ///
    /// Merged, not flattened: the recall keeps its own row, tint and copy because its call
    /// to action is materially different — a free repair to schedule, not a fault to review
    /// — and folding it into a severity count would lose that. What goes away is the
    /// duplicated container and the second pass over the array.
    ///
    /// Jump filters are preserved exactly, and they are not interchangeable:
    ///   critical -> .critical  so the count just read matches the rows landed on
    ///   high     -> .highPlus  because the Alerts tab's default Critical filter would
    ///                          otherwise show "no alerts" for a high-severity fault
    ///   recall   -> nil        recalls span severities, so the user's own filter stands
    @ViewBuilder
    private var vehicleAlertsBanner: some View {
        let dtcs = session.vehicleContext?.activeDtcs ?? []
        let recallCount = dtcs.filter { $0.code.uppercased().hasPrefix("RECALL") }.count
        let critical = session.activeDtcCount(minSeverityRank: 0)
        let high = session.activeDtcCount(minSeverityRank: 1) - critical

        let showsRecall = recallCount > 0 && session.layoutSegment.showsRecallBanner
        if critical > 0 || high > 0 || showsRecall {
            VStack(spacing: 8) {
                if critical > 0 {
                    alertsBannerRow(
                        icon: "exclamationmark.octagon.fill",
                        tint: .red,
                        title: "\(critical) critical vehicle alert\(critical == 1 ? "" : "s")",
                        subtitle: "Tap to review on the Alerts tab",
                        jumpFilter: .critical
                    )
                } else if high > 0 {
                    alertsBannerRow(
                        icon: "exclamationmark.triangle.fill",
                        tint: .orange,
                        title: "\(high) high-severity alert\(high == 1 ? "" : "s")",
                        subtitle: "Tap to review on the Alerts tab",
                        jumpFilter: .highPlus
                    )
                }
                // Segment gate preserved from the old `if segment.showsRecallBanner`
                // wrapper. Dropping it would surface recall messaging to segments that
                // deliberately suppress it.
                if recallCount > 0, session.layoutSegment.showsRecallBanner {
                    recallRow(count: recallCount)
                }
            }
        }
    }

    /// Open-recall row. Deliberately distinct from a severity row: the action is scheduling
    /// a free repair, not reviewing a fault.
    private func recallRow(count: Int) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.shield.fill")
                .foregroundStyle(.white)
                .font(.title3)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(count) Open Recall\(count > 1 ? "s" : "")")
                    .font(.subheadline.bold())
                    .foregroundStyle(.white)
                Text("Free repair available — tap to schedule")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.9))
            }
            Spacer()
            Image(systemName: "chevron.right")
                .foregroundStyle(.white.opacity(0.7))
        }
        .padding()
        .background(RoundedRectangle(cornerRadius: 12).fill(.orange.gradient))
        .onTapGesture { onJumpToAlerts?(nil) }
    }

    /// Plate and colour on one line, tolerating either being absent.
    ///
    /// The former `vehicleCard` interpolated `"\(license) · \(color ?? "")"`,
    /// which rendered a trailing " · " when a vehicle had a plate but no colour.
    /// Colour also used to be captioned under the image by `vehicleStillCard`;
    /// with the cards merged it belongs here next to the plate, and only once.
    private func vehicleSubtitle(_ v: VehicleInfo?) -> String? {
        guard let v else { return nil }
        let parts = [v.licensePlate, v.color]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The primary actions offered on the hero card.
    ///
    /// Every action here resolves to a capability that exists today — the health
    /// sheet, the Service tab, the assistant, and the Buy tab. Remote commands
    /// (lock/unlock/climate) are deliberately absent: iOS has no command client,
    /// and on the FWE simulation path the presence-loop sidecar disconnects
    /// immediately after subscribing to `cms/commands/<id>/request`, so a command
    /// would be accepted and silently never actuate. Adding a button for that
    /// would repeat a defect this app has shipped twice (a flag no UI read, a
    /// tile that looked tappable before its destination existed). When spec
    /// `2026-08-19-cms-vehicle-ecu-presence-resident` lands, a remote command
    /// appends here as one more `HomeAction`.
    private var heroActions: [HomeAction] {
        var actions: [HomeAction] = []
        // Controls first: remote lock/start is what an owner opens a companion app
        // for. Offered only when the commands API is configured — when it is not,
        // the action is absent rather than disabled, because a permanently dimmed
        // control with no path to enabling it is just noise.
        if VSAConfig.commandsApiUrl != nil, session.currentVehicle != nil {
            actions.append(
                HomeAction(
                    id: "controls",
                    title: "Controls",
                    systemImage: "slider.horizontal.3",
                    // Still tappable when offline: the sheet explains why nothing
                    // will reach the car, which is more useful than a dead tap.
                    unavailableReason: nil
                ) {
                    showControls = true
                }
            )
        }
        actions.append(
            HomeAction(id: "health", title: "Health", systemImage: "heart.text.square") {
                showHealthDetail = true
            }
        )
        if let onOpenService {
            actions.append(
                HomeAction(id: "service", title: "Book service", systemImage: "wrench.and.screwdriver") {
                    onOpenService()
                }
            )
        }
        if let onAskAssistant {
            actions.append(
                HomeAction(id: "ask", title: "Ask \(theme.displayName)", systemImage: "sparkles") {
                    // Carries the vehicle so the assistant does not open with a
                    // cold "which car?" turn. Same priming mechanism as the
                    // health card and ConfiguratorFlow.
                    let name = session.currentVehicle?.displayTitle ?? "my vehicle"
                    onAskAssistant("I have a question about my \(name).")
                }
            )
        }
        if let onOpenBuyTab {
            actions.append(
                HomeAction(id: "explore", title: "Explore", systemImage: "sparkle.magnifyingglass") {
                    onOpenBuyTab()
                }
            )
        }
        return actions
    }

    @ViewBuilder
    private var connectionBadge: some View {
        // Prefer Redis-backed liveState; fall back to DDB field if unloaded.
        let isConnected: Bool = {
            if let live = session.liveState { return live.isConnected }
            return (session.currentVehicle?.connectionStatus ?? "").lowercased() == "connected"
        }()
        let status = session.currentVehicle?.status ?? ""
        let color: Color = isConnected ? .green : (status.lowercased() == "active" ? .orange : .gray)
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(isConnected ? "Connected" : "Offline")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func vehicleStatsGrid(_ v: VehicleInfo) -> some View {
        let columns = [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())]
        // 2026-05-05: grid updated to match the Vehicle tab + CMS:
        // - Odometer/Fuel/Engine come from liveState when fresh, falling
        //   back to the DDB values on the vehicle record.
        // - "Battery voltage" row dropped — it was a mechanic-concern
        //   value, not driver-relevant. Energy status is already
        //   conveyed by the Fuel/Battery row via fuelType below.
        // - Fuel vs Battery row flips based on fuelType (BEV → Battery,
        //   ICE → Fuel) matching the Vehicle tab conditional.
        // - Fleet cell uses fleetName (denormalised server-side in the
        //   /drivers/me + /vehicles/{id}/context Lambdas) falling back
        //   to fleetId so ICE-era vehicles without a fleet row don't
        //   show a blank cell.
        let live = session.liveState
        let odo = (live?.odometer).map { Int($0) } ?? v.odometer ?? v.mileage
        let fuel = live?.fuelLevel ?? v.fuelLevel
        let temp = live?.engineTemp ?? v.engineTemp
        let speed = live?.speed ?? v.lastSpeed
        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            statCell("Odometer", value: odo.map { "\($0) mi" } ?? "—", systemImage: "speedometer")
            if v.isElectric {
                statCell("Battery", value: fuel.map { "\(Int($0))%" } ?? "—", systemImage: "bolt.batteryblock.fill")
            } else {
                statCell("Fuel", value: fuel.map { "\(Int($0))%" } ?? "—", systemImage: "fuelpump.fill")
            }
            // Engine temperature is an ICE signal. An EV has no engine coolant
            // loop, so the row is not "unknown" for a BEV — it is inapplicable,
            // and the record correctly carries no value. Rendering it anyway
            // produced "Engine 0°F" on a healthy electric vehicle, which reads
            // as a fault. For a BEV the slot shows pack health instead, which is
            // the equivalent long-term-condition signal.
            if v.isElectric {
                statCell("Battery health",
                         value: v.batterySoh.map { "\(Int($0))%" } ?? "—",
                         systemImage: "heart.text.square.fill")
            } else {
                statCell("Engine", value: temp.map { "\(Int($0))°F" } ?? "—", systemImage: "thermometer.high")
            }
            statCell("Speed", value: speed.map { String(format: "%.0f mph", $0) } ?? "—", systemImage: "gauge.with.needle")
            if session.layoutSegment.showsFleetStatCell {
                statCell("Fleet", value: fleetCell(v), systemImage: "building.2.fill")
            } else if let trips = v.totalTrips {
                statCell("Trips", value: "\(trips)", systemImage: "point.topleft.down.curvedto.point.bottomright.up")
            } else {
                statCell("Odometer", value: odometerCell(v), systemImage: "gauge.with.dots.needle.bottom.50percent")
            }
        }
    }




    /// Fleet cell value: prefer fleetName, fall back to fleetId, then
    /// dash. Keeps the dense 3-col grid compact by not appending the
    /// ID (Vehicle tab shows "name (id)" because it has more room).
    /// Odometer fallback for the retail stats grid when trip count is absent.
    private func odometerCell(_ v: VehicleInfo) -> String {
        // Miles, not km. `VehicleInfo.odometer` is defined in miles by the telemetry
        // contract (`signal_catalog_seed.json`: unit "miles") and rendered as `mi`
        // by the CMS web UI off the same rows. This site said "km" — and named its
        // local `km` — so one vehicle read 39,840 km here and 39,840 mi on the
        // Vehicle tab. Only the label was wrong; the value was always miles.
        guard let miles = v.odometer else { return "—" }
        return "\(miles.formatted(.number.grouping(.automatic))) mi"
    }

    private func fleetCell(_ v: VehicleInfo) -> String {
        if let name = v.fleetName, !name.isEmpty { return name }
        return v.fleetId ?? "—"
    }

    private func statCell(_ label: String, value: String, systemImage: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: systemImage)
                .font(.caption).foregroundStyle(theme.primary.opacity(0.7))
                .frame(width: 14)
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.caption2).foregroundStyle(.secondary)
                Text(value).font(.caption).bold().lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: - Upcoming service

    @ViewBuilder
    private func prettyServiceType(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        // Known mappings — preserves cases like "VSA" that title-case
        // would otherwise mangle.
        let known: [String: String] = [
            "DIAGNOSTIC_REPAIR":   "Diagnostic Repair",
            "VSA_VOICE_TRIAGE":    "Voice-triage booking",
            "STARTER_MOTOR":       "Starter Motor",
            "COOLANT_FLUSH":       "Coolant Flush",
            "OIL_CHANGE":          "Oil Change",
            "BRAKE_SERVICE":       "Brake Service",
            "TIRE_ROTATION":       "Tire Rotation",
            "INSPECTION":          "Inspection",
        ]
        if let hit = known[raw] { return hit }
        // Generic: underscores → spaces, words → title-case.
        return raw
            .split(separator: "_")
            .map { $0.prefix(1).uppercased() + $0.dropFirst().lowercased() }
            .joined(separator: " ")
    }






    // MARK: - Recent activity (trips + recent service merged, newest first)




    // MARK: - Latest alert (kept from old Home for backward visual continuity)

    @ViewBuilder
    private func latestAlertCard(triage: TriageResponse) -> some View {
        let level = AlertLevel(from: triage.classification)
        SectionCard(theme: theme) {
            HStack(spacing: 12) {
                StatusBadge(level: level)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Latest triage").font(.caption).foregroundStyle(.secondary)
                    Text(level.title).font(.subheadline).bold()
                }
                Spacer()
                Text(relativeTimeString(from: triage.decidedAt))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Rental-only cards

    /// "Trip Time Remaining" card. Shown only when the active tenant's
    /// segment is `rental`. The rental return time isn't part of the
    /// CMS vehicle record today, so we synthesize a relative window
    /// from the vehicle's purchase/enrollment timestamp ("rented for
    /// the last X days, return due Y") — good enough for the demo,
    /// real implementation would read a `rentalEndsAt` attribute.
    @ViewBuilder
    private var tripTimeRemainingCard: some View {
        SectionCard("Trip Time Remaining", theme: theme) {
            HStack(alignment: .center, spacing: 14) {
                Image(systemName: "hourglass")
                    .font(.title)
                    .foregroundStyle(theme.primary)
                    .frame(width: 44)
                VStack(alignment: .leading, spacing: 4) {
                    // Demo placeholder window: "3 days, 4 hours remaining".
                    // Hardcoded for visual; a real implementation would
                    // compute from rentalEndsAt - now.
                    Text("3 days, 4 hours")
                        .font(.title3).bold()
                    Text("Return by Friday at 3:00 PM")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
    }

    /// "Return To" card. Shown only for rental tenants. The rental
    /// pickup location lives on the seeded vehicle attributes
    /// (`rentalReturnLocation`) but isn't surfaced through the iOS
    /// VehicleContextResponse model today, so for the demo we
    /// hardcode a plausible Enterprise drop-off keyed off the
    /// driver's home base. A production implementation would
    /// surface the attributes map through `/vehicles/{id}/context`
    /// so the location reflects what's actually in CMS.
    @ViewBuilder
    private var returnToCard: some View {
        let homeBase = session.currentDriver?.homeBase ?? "your rental city"
        let returnLocation = "Enterprise — \(homeBase) Airport"
        SectionCard("Return To", theme: theme) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "mappin.and.ellipse")
                    .font(.title)
                    .foregroundStyle(theme.primary)
                    .frame(width: 44)
                VStack(alignment: .leading, spacing: 4) {
                    Text(returnLocation)
                        .font(.subheadline).bold()
                        .lineLimit(2)
                    Text("Drop the keys at the desk inside.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
    }
}


// MARK: - Vehicle-claim picker

/// Vehicle-claim picker presented when a signed-in driver has no assigned
/// vehicle. Lists the driver's fleet inventory (CMS GET /api/v1/vehicles,
/// fleet-scoped server-side) and lets them self-assign one
/// (CMS PUT /api/v1/drivers/{self}). On success the parent dismisses and the
/// Home tab re-resolves the driver → dashboard renders.
///
/// Reuses the same CMS capability the Fleet web UI's "Assign vehicle" action
/// uses; the backend constrains driver tokens to this self-service path.
/// Lives in HomeTabView.swift (not its own file) because the Xcode project
/// uses explicit file references, not file-system-synchronized groups.
struct ClaimVehicleSheet: View {
    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss

    /// Called after a successful claim so the parent can dismiss + refresh.
    var onClaimed: () -> Void

    @State private var claimingVehicleId: String?
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            Group {
                if session.claimableVehiclesLoading && session.claimableVehicles.isEmpty {
                    ProgressView("Loading vehicles…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if session.claimableVehicles.isEmpty {
                    emptyState
                } else {
                    vehicleList
                }
            }
            .navigationTitle("Claim a vehicle")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task { await session.loadClaimableVehicles() }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "car.2")
                .font(.system(size: 36))
                .foregroundStyle(.secondary)
            Text(session.claimError ?? "No vehicles available to claim in your fleet.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button("Try again") {
                Task { await session.loadClaimableVehicles() }
            }
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var vehicleList: some View {
        List {
            if let errorText {
                Section {
                    Text(errorText)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }
            Section {
                ForEach(session.claimableVehicles, id: \.vehicleId) { vehicle in
                    Button {
                        Task { await claim(vehicle) }
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "car.fill")
                                .foregroundStyle(.tint)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(vehicle.displayTitle).font(.headline)
                                Text(secondaryLine(vehicle))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if claimingVehicleId == vehicle.vehicleId {
                                ProgressView()
                            } else {
                                Image(systemName: "chevron.right")
                                    .font(.caption).foregroundStyle(.tertiary)
                            }
                        }
                    }
                    .disabled(claimingVehicleId != nil)
                }
            } header: {
                Text("Available in your fleet")
            }
        }
    }

    private func secondaryLine(_ v: VehicleInfo) -> String {
        var parts: [String] = [v.vehicleId]
        if let vin = v.vin, !vin.isEmpty { parts.append("VIN \(vin)") }
        return parts.joined(separator: " · ")
    }

    @MainActor
    private func claim(_ vehicle: VehicleInfo) async {
        guard claimingVehicleId == nil else { return }
        errorText = nil
        claimingVehicleId = vehicle.vehicleId
        defer { claimingVehicleId = nil }
        do {
            try await session.claimVehicle(vehicleId: vehicle.vehicleId)
            onClaimed()
            dismiss()
        } catch {
            errorText = error.localizedDescription
        }
    }
}
