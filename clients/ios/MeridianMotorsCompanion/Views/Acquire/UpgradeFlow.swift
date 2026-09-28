import SwiftUI

/// Three-step upgrade wizard, entered from the Home trade-in banner.
///
///   1. **Offer** — the bike, its price, and the loyalty credit with its basis
///   2. **Trade-in** — the indicative valuation of what they ride today
///   3. **Financing** — one indicative monthly figure, not an application
///   → hands off to the shopping agent
///
/// ## Why a wizard in front of the chat
///
/// Explore used to drop the customer straight into a chat, which forced the
/// agent to act as a data-entry surface — asking for things the platform
/// already knows. Front-loading the computable parts leaves the agent for
/// judgement and edge cases, which is what it is actually good at.
///
/// ## Why the handoff carries state
///
/// The final CTA writes a summary into `AppSession.pendingDiscoverPrompt`,
/// which `DiscoverFlow` consumes on appear. Without that the agent opens cold
/// and the customer re-explains everything they just entered — the same
/// amnesia that made multi-turn feel broken before the transcript replay
/// landed.
///
/// ## Deliberate scope limits
///
/// Every figure is tenant-supplied and pre-formatted (see
/// `AcquireConfig.UpgradeOffer`); nothing is computed on-device. The financing
/// step is a SUMMARY, not a calculator: the backing `finance_qualify` is a
/// deterministic mock, so tenure sliders and lender terms would invite
/// scrutiny they cannot survive. Anything beyond the indicative figure is the
/// agent's job, which is exactly where step 3 sends the customer.
struct UpgradeFlow: View {
    let offer: AcquireConfig.UpgradeOffer
    let theme: TenantTheme
    /// Hands off to the shopping agent. Caller routes to the Buy tab.
    var onTalkToAdvisor: (() -> Void)?
    /// Accepts the offer and opens the configurator with the Edition carried over.
    ///
    /// Added because the upgrade flow previously terminated at the advisor
    /// handoff only — a visitor who had decided could not proceed to order.
    /// `ConfiguratorOfferHandoff` already existed and `ConfiguratorFlow` already
    /// accepted it; nothing constructed one. This is that missing edge.
    var onConfigure: ((ConfiguratorOfferHandoff) -> Void)?

    /// Assistant channel, forwarded to the hosted configurator.
    ///
    /// Without it the configurator's `.success` step has no way to act on "Book a test
    /// drive" — and its previous nil-fallback dismissed the whole flow, so the button
    /// returned the visitor to Home. Hosting the configurator (spec
    /// `2026-08-21-cvx-upgrade-flow-continuity`) introduced a call site that did not
    /// thread this through; the standalone entries at `MainTabView` and
    /// `BuyLandingView` always did.
    var onAskAssistant: ((String) -> Void)? = nil
    /// First name captured at Beat 0, when there is one.
    ///
    /// Injected rather than read from a session: the name lives on
    /// `KioskSession.visitorFirstName` (the kiosk coordinator), not on
    /// `AppSession`, and reaching across to the coordinator from here would
    /// couple the upgrade flow to kiosk mode when it also runs outside it.
    /// The call site knows which context it is in; this view does not need to.
    var firstName: String?

    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss
    /// Injected at the app root by `.detectAdaptiveLayout()`.
    /// Used to adapt layout for compact-width contexts (iPhone portrait/landscape).
    @Environment(\.adaptiveLayoutContext) private var layoutContext

    private enum Step: Int, CaseIterable {
        case offer, tradeIn, financing, configure
        // NOTE: `configure` MUST be the last case. `advance()` is
        // `Step(rawValue: step.rawValue + 1)`, and `.configure` is terminal —
        // there is no step after it in this machine.
    }
    @State private var step: Step = .offer

    /// Handoff built by `configureAndOrder()`, stored here so the `.configure`
    /// step can pass it to the embedded `ConfiguratorFlow(chrome: .hosted, …)`.
    /// Nil until `configureAndOrder()` is called.
    @State private var configuratorOfferHandoff: ConfiguratorOfferHandoff? = nil

    /// Progress reported by the embedded `ConfiguratorFlow` via `onProgressChange`.
    /// Used to extend the progress bar from 3 capsules to `3 + progressStepCount`.
    @State private var configuratorProgress: (step: Int, total: Int)? = nil

    /// Nav title reported by the embedded `ConfiguratorFlow` via `onNavTitleChange`.
    /// Used to keep the single navigation bar title in sync with the inner step.
    @State private var configuratorNavTitle: String = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                progressBar
                if step == .configure {
                    // The hosted configurator fills the available space directly —
                    // NOT wrapped in a ScrollView. Each configurator step manages its
                    // own scrolling, and nesting a scroll-view list inside UpgradeFlow's
                    // ScrollView would produce a non-scrollable double-stack.
                    stepContent
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: layoutContext.isCompact ? 12 : 16) {
                            stepContent
                        }
                        .padding(layoutContext.isCompact ? 12 : 16)
                    }
                    // The footer (primary action + back) and test-ride row are hidden on
                    // `.configure` — the embedded `ConfiguratorFlow` supplies its own
                    // primary action, and two competing primary buttons is exactly the
                    // defect this task exists to remove. Test-ride is also inappropriate
                    // mid-configuration.
                    VStack(spacing: 0) {
                        testRideRow
                        footer
                    }
                }
            }
            .background(Color(.systemGroupedBackground))
            // The condition score reads completed service history. Home does not
            // always have it loaded by the time the wizard opens, and a missing
            // array silently costs two factors — so ask for it here.
            .task {
                // Only when absent — Home usually has it, but the wizard can be
                // reached before that load completes.
                guard session.serviceHistoryLoadedAt == nil,
                      case .signedIn(let token, _) = session.authState else { return }
                await session.loadServiceHistory(
                    client: VSAClient(idTokenProvider: { token }))
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { dismiss() } label: { Image(systemName: "xmark") }
                        .accessibilityLabel("Close")
                }
            }
        }
    }

    private var title: String {
        switch step {
        case .offer:     return "Your upgrade"
        case .tradeIn:   return "Your trade-in"
        case .financing: return "Financing"
        case .configure:
            // Track the embedded configurator's current step title so the single
            // navigation bar stays in sync with the inner step. Falls back to the
            // last known title (set the moment the configurator first renders) or
            // a neutral string before the first `onNavTitleChange` fires.
            return configuratorNavTitle.isEmpty ? "Configure" : configuratorNavTitle
        }
    }

    // MARK: - Progress

    /// Number of UpgradeFlow steps that precede the embedded configurator.
    ///
    /// Derived from `Step.allCases` minus `.configure` so a new pre-configure step
    /// automatically increases this count without touching the progress bar.
    /// Hard-coding `3` here (or in `progressBar`) is forbidden by spec § Risks R3.
    private static var upgradeStepCount: Int {
        Step.allCases.filter { $0 != .configure }.count
    }

    private var progressBar: some View {
        let upgradeCount = Self.upgradeStepCount
        // The configurator reports its own total, which is entry-relative: on an
        // accepted-offer entry it skips category/model/trim, so the static count would
        // pad the bar with three steps the visitor will never see. Falls back to the
        // static count only before the first report arrives.
        // Fallback is the WARM-entry total, not the full machine: `.configure` is only
        // reachable from an accepted offer, so the full count was never the right guess
        // here and produced a visible jump in the number of capsules the moment the first
        // report arrived. The configurator seeds its report on appear, so this fallback
        // should now be unobservable — it is the right value regardless.
        let configuratorCount = configuratorProgress?.total
            ?? (ConfiguratorFlow.Step.progressStepCount
                - ConfiguratorFlow.Step.Kind.showEditionSummary.progressStep + 1)
        let totalCount = upgradeCount + configuratorCount

        // Number of filled capsules depends on which machine is active.
        let filledCount: Int
        switch step {
        case .offer, .tradeIn, .financing:
            // rawValue is 0-based; add 1 for 1-based filled count.
            filledCount = step.rawValue + 1
        case .configure:
            // Upgrade steps (all 3) are complete, plus however far the configurator has come.
            filledCount = upgradeCount + (configuratorProgress?.step ?? 0)
        }

        return HStack(spacing: 6) {
            ForEach(0..<totalCount, id: \.self) { index in
                Capsule()
                    .fill(index < filledCount ? theme.primary : Color(.systemGray4))
                    .frame(height: 4)
            }
        }
        .padding(.horizontal)
        .padding(.top, 8)
        // Animate on step change and on configurator progress updates.
        .animation(.easeInOut(duration: 0.25), value: step)
        .animation(.easeInOut(duration: 0.25), value: configuratorProgress?.step)
    }

    // MARK: - Steps

    @ViewBuilder
    private var stepContent: some View {
        switch step {
        case .offer:     offerStep
        case .tradeIn:   tradeInStep
        case .financing: financingStep
        case .configure:
            // The configurator is hosted inside this wizard's presentation.
            // `chrome: .hosted` removes the configurator's own NavigationStack,
            // title, and close button — this view's shell supplies all of those.
            // `onProgressChange` and `onNavTitleChange` let this view keep its
            // combined progress bar and nav title up to date.
            if let handoff = configuratorOfferHandoff {
                ConfiguratorFlow(
                    theme: theme,
                    handoff: nil,
                    offerHandoff: handoff,
                    // Forwarded so `.success`'s "Book a test drive" has somewhere to go.
                    onAskAssistant: onAskAssistant,
                    onProgressChange: { progressStep, progressTotal in
                        configuratorProgress = (step: progressStep, total: progressTotal)
                    },
                    onNavTitleChange: { title in
                        configuratorNavTitle = title
                    },
                    chrome: .hosted
                )
            }
        }
    }

    private var offerStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            heroImage

            Text(offer.modelName).font(.title2.bold())
            if let rationale = offer.rationale {
                Text(rationale)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            card {
                if let price = offer.price {
                    priceRow("Ex-showroom", price, strikethrough: offer.priceAfterLoyalty != nil)
                }
                if let discount = offer.loyaltyDiscount {
                    priceRow("Loyalty credit", "− \(discount)", accent: true)
                }
                if let after = offer.priceAfterLoyalty {
                    Divider()
                    priceRow("Your price", after, emphasised: true)
                }
            }

            specComparisonBlock

            // Why this vehicle, and what it saves — moved here from the Home drawer
            // 2026-08-18. This is the first screen after Explore and the first place
            // with room to make the argument; the drawer rendered the same content at
            // caption2 inside a collapsed peek, which is where it was least readable.
            recommendationBasisBlock
            energySavingsBlock

            // The basis is the point. A bare discount looks arbitrary; the
            // inputs make it earned, and they are all real platform data.
            if let basis = offer.loyaltyBasis {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundStyle(theme.primary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Why you qualify").font(.caption.weight(.semibold))
                        Text(basis)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .padding(12)
                // Match `card`'s width so this block lines up with the price,
                // recommendation and savings blocks above it. Without this the HStack
                // sized to its content and rendered at roughly 75% width, which read
                // as a misaligned box rather than a deliberate variant. Modifier order
                // mirrors `card`: pad the content, expand, then fill.
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 12)
                    .fill(theme.primary.opacity(0.08)))
            }
        }
    }

    /// Builds the composite from whatever the session actually holds.
    /// Missing inputs are omitted rather than guessed — a vehicle with no
    /// service history scores on the factors we have and says so.
    /// Current vehicle versus this offer, row by row.
    ///
    /// Rows favouring the CURRENT bike are shown, not filtered out. An electric
    /// option may genuinely have a lower top speed than the petrol bike it
    /// replaces, and take hours to charge rather than minutes to refuel at a
    /// pump. Hiding that would be the
    /// kind of omission an OEM audience spots instantly, and it would undermine
    /// the rows that genuinely favour the upgrade.
    @ViewBuilder
    private var specComparisonBlock: some View {
        if let rows = offer.specComparison, !rows.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("How it compares")
                    .font(.subheadline.weight(.semibold))

                card {
                    // Column headers naming BOTH vehicles.
                    //
                    // Previously the two columns were unlabelled — two values either side
                    // of an arrow, with only a caption2 "vs your <model>" tucked in the
                    // section header. Nothing said which figure was which car, so the
                    // reader had to infer direction from the arrow. Naming the columns is
                    // the whole fix; the arrow is now confirmation rather than the only
                    // signal.
                    HStack(alignment: .bottom, spacing: 8) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text("YOURS")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.tertiary)
                            Text(ownedVehicleLabel)
                                .font(.footnote.weight(.medium))
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)

                        // Spacer matching the arrow column so headers align with values.
                        Image(systemName: "arrow.left.and.right")
                            .font(.caption2)
                            .opacity(0)

                        VStack(alignment: .leading, spacing: 1) {
                            Text("UPGRADE")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(theme.primary.opacity(0.7))
                            Text(offer.modelName)
                                .font(.footnote.weight(.medium))
                                .foregroundStyle(theme.primary)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Divider()

                    ForEach(rows) { row in
                        VStack(alignment: .leading, spacing: 5) {
                            // Was .caption/secondary — the label read as a footnote rather
                            // than the name of the thing being compared.
                            Text(row.label)
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(Color(.label))
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                // Values were .caption (~12pt), which is below the size at
                                // which a spec figure is scannable. .body is 17pt.
                                Text(row.current ?? "—")
                                    .font(.body)
                                    .foregroundStyle(row.favours == "current"
                                                     ? Color(.label) : .secondary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Image(systemName: arrowName(row.favours))
                                    .font(.footnote)
                                    .foregroundStyle(arrowColour(row.favours))
                                Text(row.new ?? "—")
                                    .font(row.favours == "new"
                                          ? .body.weight(.semibold) : .body)
                                    .foregroundStyle(row.favours == "new"
                                                     ? theme.primary : Color(.label))
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        if row.id != rows.last?.id { Divider() }
                    }
                }

                // Provenance only.
                //
                // The second sentence — "Rows where your current bike leads are shown
                // too." — was removed 2026-08-21. The BEHAVIOUR it described is retained
                // and is the honest part: rows the current vehicle wins are still included
                // (staging shows Cargo 780 L vs 755 L favouring the current car). But as
                // the manufacturer we do not need to narrate our own even-handedness, and
                // the sentence also still said "bike", from before this was a car app.
                Text("Figures from the manufacturer's published specifications.")
                    .font(.caption).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Label for the customer's own vehicle in the comparison header.
    ///
    /// Prefers the full `displayTitle` ("2023 Meridian Trailwind") so the year is visible —
    /// which is what makes a same-nameplate upgrade legible as a generational step rather
    /// than a like-for-like swap. Falls back to the model alone, then to a neutral phrase;
    /// the old fallback was "current bike".
    private var ownedVehicleLabel: String {
        if let v = session.currentVehicle {
            let title = v.displayTitle
            if !title.isEmpty { return title }
            if let m = v.model, !m.isEmpty { return m }
        }
        return "Your current vehicle"
    }

    private func arrowName(_ favours: String?) -> String {
        switch favours {
        case "new":     return "arrow.right.circle.fill"
        case "current": return "arrow.left.circle"
        default:        return "arrow.left.and.right"
        }
    }

    private func arrowColour(_ favours: String?) -> Color {
        switch favours {
        case "new":     return theme.primary
        case "current": return Color(.systemOrange)
        default:        return .secondary
        }
    }

    private var tradeInScore: TradeInScore {
        let v = session.currentVehicle
        // `completedService`, not `scheduledService`. AppSession splits the
        // service-history response into two arrays; filtering the *scheduled*
        // one for status == COMPLETED matched nothing, which is why the service
        // and repair factors never appeared.
        let completed = session.completedService
        let scheduled = completed.filter {
            ($0.category ?? "").uppercased() == "SCHEDULED"
        }.count
        let repairs = completed.count - scheduled

        var owned: Date? = nil
        if let raw = v?.purchaseDate, !raw.isEmpty {
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            owned = iso.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
            if owned == nil {
                let df = DateFormatter()
                df.dateFormat = "yyyy-MM-dd"
                owned = df.date(from: String(raw.prefix(10)))
            }
        }

        return TradeInScore(
            healthScore: session.vehicleContext?.healthScore,
            odometerKm: v?.odometer ?? v?.mileage,
            totalTrips: v?.totalTrips,
            ownedSince: owned,
            completedScheduledServices: scheduled,
            completedRepairs: max(repairs, 0),
            now: Date()
        )
    }

    private var tradeInStep: some View {
        let s = tradeInScore
        return VStack(alignment: .leading, spacing: 14) {
            if let v = session.currentVehicle {
                Text("We valued your \(v.displayTitle)").font(.title3.bold())
            } else {
                Text("We valued your current vehicle").font(.title3.bold())
            }

            // The score, and immediately its band — a bare number invites
            // "compared to what?".
            HStack(alignment: .center, spacing: 14) {
                ZStack {
                    Circle()
                        .stroke(Color(.systemGray5), lineWidth: 8)
                    Circle()
                        .trim(from: 0, to: CGFloat(s.score) / 100)
                        .stroke(theme.primary,
                                style: StrokeStyle(lineWidth: 8, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    VStack(spacing: 0) {
                        Text("\(s.score)")
                            // Display face — the trade-in score ring, matching the vehicle-health
                            // numeral. Ultra-light geometric rather than bold rounded.
                            .font(theme.displayFont(size: 30))
                        Text("/100").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .frame(width: 82, height: 82)

                VStack(alignment: .leading, spacing: 3) {
                    Text("Trade-in condition").font(.caption).foregroundStyle(.secondary)
                    Text(s.band.rawValue)
                        .font(.title3.bold())
                        .foregroundStyle(theme.primary)
                    Text(s.band.blurb)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground)))

            // Every factor names its evidence, so the number is auditable
            // rather than something to be trusted.
            Text("What went into it").font(.subheadline.weight(.semibold))
            card {
                ForEach(s.factors) { f in
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(f.label).font(.subheadline)
                            Text(f.detail)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 8)
                        Text(f.points >= 0 ? "+\(f.points)" : "\(f.points)")
                            .font(.caption.weight(.semibold).monospacedDigit())
                            .foregroundStyle(f.points >= 0 ? theme.primary : .secondary)
                    }
                    if f.id != s.factors.last?.id { Divider() }
                }
                if s.factors.isEmpty {
                    Text("Not enough connected data yet to score this vehicle.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            noteRow("Different from Vehicle Health on your home screen — that "
                    + "is a live diagnostic snapshot of how the vehicle is running "
                    + "today. This adds age, distance, service record and "
                    + "repair history, which is what affects resale.")

            // Range, never a point estimate. The spread is the honesty.
            if let credit = offer.tradeInCredit, let base = Self.rupees(credit) {
                let r = s.indicativeRange(baseCredit: base)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Indicative trade-in range")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("\(formatMoney(r.low)) – \(formatMoney(r.high))")
                        // Display face. This is a money figure, so only its TYPE changes — the
                        // value and its formatting come from the deterministic offer seam
                        // and are untouched.
                        .font(theme.displayFont(size: 32))
                        .foregroundStyle(theme.primary)
                    Text("Condition score applied to a "
                         + "\(formatMoney(base)) base for this model")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(RoundedRectangle(cornerRadius: 12)
                    .fill(Color(.secondarySystemGroupedBackground)))
            }

            noteRow(s.disclaimer)
            noteRow("Final value depends on physical inspection, service "
                    + "history and cosmetic condition.")
        }
    }

    /// Parse a pre-formatted rupee string back to an Int so the score can
    /// adjust it. Tenant strings are authoritative for DISPLAY; this is only
    /// used to derive the range, and returns nil rather than guessing if the
    /// shape is unexpected.
    private static func rupees(_ s: String) -> Int? {
        let digits = s.filter(\.isNumber)
        return digits.isEmpty ? nil : Int(digits)
    }

    /// Tenant currency symbol, never a hardcoded glyph.
    ///
    /// `AcquireConfig.currencySymbol` exists precisely so "the Swift UI never
    /// hardcodes currency glyphs" (see its doc comment in `Api/Models.swift`).
    /// This view previously hardcoded the rupee glyph, which meant a USD tenant
    /// rendered rupee prefixes in front of dollar amounts. Falls back to the ISO
    /// code, then to an empty string — showing a bare number is wrong, but showing
    /// the WRONG currency is worse.
    private var currencySymbol: String {
        let acquire = session.tenantConfig?.acquire
        return acquire?.currencySymbol ?? acquire?.currency ?? ""
    }

    /// Digit-grouping locale for derived money figures, derived from the tenant's
    /// ISO currency code rather than pinned to `en_IN`.
    ///
    /// Indian lakh grouping (1,17,000) and Western thousands grouping (117,000)
    /// disagree, and using the wrong one in front of the customer looks careless —
    /// which is exactly why this must follow the tenant rather than a constant.
    private var groupingLocale: Locale {
        switch (session.tenantConfig?.acquire?.currency ?? "").uppercased() {
        case "INR": return Locale(identifier: "en_IN")
        case "":    return .current
        default:    return .current
        }
    }

    /// Format a DERIVED money figure using the tenant's symbol and grouping.
    ///
    /// Only for values computed on-device (an indicative range, a monthly delta).
    /// Tenant-supplied money fields on `UpgradeOffer` are pre-formatted strings by
    /// design — "so the client never has to decide on grouping, separators, or
    /// currency placement" — and must be rendered verbatim, never re-formatted
    /// through here.
    private func formatMoney(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = groupingLocale
        return currencySymbol + (f.string(from: NSNumber(value: n)) ?? "\(n)")
    }

    private var financingStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Indicative financing").font(.title3.bold())

            card {
                if let after = offer.priceAfterLoyalty ?? offer.price {
                    detail("Your price", after)
                }
                if let credit = offer.tradeInCredit {
                    detail("Less trade-in", "− \(credit)")
                }
                if let monthly = offer.monthly {
                    Divider()
                    HStack {
                        Text("Estimated monthly").font(.subheadline)
                        Spacer()
                        Text(monthly)
                            .font(.title3.bold())
                            .foregroundStyle(theme.primary)
                    }
                }
            }

            financeComparison

            if let validity = offer.validUntil {
                noteRow(validity)
            }
            // Verbatim, never paraphrased — the same rule the agent follows for
            // offers_lookup results, so figures never diverge between the
            // screen and the conversation.
            if let disclosure = offer.disclosure {
                Text(disclosure)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(12)
                    .background(RoundedRectangle(cornerRadius: 10)
                        .fill(Color(.secondarySystemGroupedBackground)))
            }

            noteRow("An advisor can confirm approval, tenure options and "
                    + "exchange paperwork.")
        }
    }

    /// Side-by-side of what they pay today versus what they would pay.
    ///
    /// This is where the loyalty argument becomes concrete: a repeat customer
    /// with a clean payment record is WHY the new rate is lower, so the rate
    /// benefit is shown as a number rather than asserted as goodwill. Hidden
    /// entirely for first-time buyers — a comparison against blanks would be
    /// worse than no comparison.
    @ViewBuilder
    private var financeComparison: some View {
        if let existing = session.tenantConfig?.acquire?.existingFinance,
           let now = offer.finance {
            VStack(alignment: .leading, spacing: 10) {
                Text("Compared with your current finance")
                    .font(.subheadline.weight(.semibold))

                card {
                    HStack(alignment: .top) {
                        financeColumn(
                            title: existing.lender ?? "Current",
                            rate: existing.rate,
                            term: existing.termMonths.map { "\($0) months" },
                            monthly: existing.monthly,
                            subdued: true)
                        Divider().frame(height: 74)
                        financeColumn(
                            title: "New",
                            rate: now.rate,
                            term: now.termMonths.map { "\($0) months" },
                            monthly: now.monthly,
                            subdued: false)
                    }

                    if let benefit = now.loyaltyRateBenefit {
                        Divider()
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.down.right.circle.fill")
                                .foregroundStyle(theme.primary)
                            Text("Your rate is \(benefit) — because you have "
                                 + "financed with us before")
                                .font(.caption)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let paid = existing.onTimePayments, paid > 0 {
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(theme.primary)
                            Text("\(paid) on-time payments on record")
                                .font(.caption)
                        }
                    }
                    if let delta = monthlyDelta(from: existing.monthly, to: now.monthly) {
                        Divider()
                        HStack {
                            Text("Change in monthly").font(.caption)
                            Spacer()
                            Text(delta).font(.caption.weight(.semibold))
                        }
                    }
                }

                if let remaining = existing.monthsRemaining,
                   let outstanding = existing.outstanding {
                    noteRow("\(remaining) months and \(outstanding) remaining on your "
                            + "current loan. " + (existing.note ?? ""))
                }
            }
        }
    }

    private func financeColumn(title: String, rate: String?, term: String?,
                               monthly: String?, subdued: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(subdued ? .secondary : theme.primary)
            if let monthly {
                Text(monthly)
                    .font(.title3.bold())
                    .foregroundStyle(subdued ? Color(.secondaryLabel) : Color(.label))
                Text("per month").font(.caption2).foregroundStyle(.tertiary)
            }
            if let rate { Text(rate).font(.caption2).foregroundStyle(.secondary) }
            if let term { Text(term).font(.caption2).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Signed difference between two pre-formatted rupee strings. Nil when
    /// either cannot be parsed — better to omit the row than to show a wrong
    /// delta next to correct figures.
    private func monthlyDelta(from a: String?, to b: String?) -> String? {
        guard let a, let b, let x = Self.rupees(a), let y = Self.rupees(b) else { return nil }
        let d = y - x
        guard d != 0 else { return "no change" }
        return (d > 0 ? "+" : "−") + formatMoney(abs(d)) + " / month"
    }

    // MARK: - Footer

    private var footer: some View {
        VStack(spacing: 8) {
            HStack(spacing: 12) {
                if step != .offer {
                    Button("Back") { back() }
                        .buttonStyle(.bordered)
                }
                // Spacer rather than `.frame(maxWidth: .infinity)` on the button.
                // Stretching the primary action made its centred label sit in the
                // middle of the bar, which reads as neither left- nor right-aligned;
                // pushing it to the trailing edge puts the forward action where the
                // thumb expects it and keeps Back at the leading edge.
                Spacer(minLength: 12)
                // On the final step the primary action is to order. A visitor who has
                // walked offer → trade-in → financing has decided; making the advisor
                // handoff primary there left the flow with no path to an order at all,
                // which is the gap this closes.
                Button(step == .financing ? "Configure & order" : "Continue") {
                    step == .financing ? configureAndOrder() : advance()
                }
                .buttonStyle(.borderedProminent)
                .tint(theme.primary)
            }
            // Advisor stays reachable, demoted rather than removed — it is the
            // right action for an undecided visitor and the agent holds context
            // the configurator does not.
            if step == .financing {
                Button("Talk to an advisor first") { handOff() }
                    .font(.subheadline)
                    .foregroundStyle(theme.primary)
            }
        }
        .padding(.horizontal)
        .padding(.top, 12)
        .padding(.bottom, 4)
        .background(.bar)
    }

    /// Accepts the offer and advances into the embedded configurator step.
    ///
    /// Previously called `dismiss()` and fired `onConfigure` so the parent could
    /// re-present `ConfiguratorFlow` in its own `fullScreenCover`. That produced
    /// two NavigationStacks, two progress indicators, two X buttons, and a modal
    /// dismiss/present hop mid-purchase (spec § Context, Defect 2).
    ///
    /// Now: the handoff is stored in `configuratorOfferHandoff`, then
    /// `step = .configure` advances into the configurator inline — one presentation,
    /// no modal transition.
    ///
    /// `onConfigure` is **retained** — `UpgradeFlow` is not the only place that
    /// opens the configurator. The Discover path and `BuyLandingView` still cross
    /// a presentation boundary and still need it. On this path it is simply not
    /// invoked. See spec § Decision B and tasks.md 3.2 constraints.
    ///
    /// Carries the offer's `ivePackage` through as the Edition, same reasoning as
    /// the original comment below.
    private func configureAndOrder() {
        let handoff = ConfiguratorOfferHandoff(
            edition: offer.ivePackage,
            modelName: offer.modelName,
            firstName: firstName
        )
        NSLog("🛒 UPGRADE: configure handoff — hosting inline (model=%@ edition=%@)",
              offer.modelName, offer.ivePackage?.rawValue ?? "default")
        configuratorOfferHandoff = handoff
        withAnimation {
            step = .configure
        }
        // onConfigure is intentionally NOT invoked here — this path hosts the
        // configurator inline rather than crossing a presentation boundary.
    }

    /// Test drive is offered on every step, not just at the end.
    ///
    /// Someone convinced by the photo on step 1 should not have to walk through
    /// financing to ask for a ride. It hands off to the agent, which already
    /// holds the `book` tool for this persona, rather than introducing a second
    /// booking path that would have to be kept in step with it.
    @ViewBuilder
    private var testRideRow: some View {
        if offer.testRideAvailable == true {
            Button {
                bookTestRide()
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "calendar.badge.plus")
                    Text("Schedule a test drive")
                }
                .font(.subheadline.weight(.medium))
                // `.buttonStyle(.plain)` on a bare label makes the hit area the
                // glyph and text only — roughly 20pt tall, under Apple's 44pt
                // minimum. The frame gives the row a real tap target, and
                // `contentShape` makes the whole of it tappable rather than just
                // the ink.
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.primary)
            .padding(.horizontal)
            .padding(.top, 12)
            .padding(.bottom, 10)
        }
    }

    private func bookTestRide() {
        var line = "I'd like to schedule a test drive for the \(offer.modelName)."
        if let v = session.currentVehicle {
            line += " I currently drive a \(v.displayTitle)."
        }
        line += " What slots are available near me, and what should I bring?"
        session.pendingDiscoverPrompt = line
        NSLog("🛒 UPGRADE: test-drive handoff (model=%@)", offer.modelName)
        dismiss()
        onTalkToAdvisor?()
    }

    private func advance() {
        withAnimation {
            if let next = Step(rawValue: step.rawValue + 1) { step = next }
        }
    }

    private func back() {
        withAnimation {
            if let prev = Step(rawValue: step.rawValue - 1) { step = prev }
        }
    }

    /// Write the collected state into the handoff channel, then let the caller
    /// switch tabs. Phrased as the customer's own opening line so the agent's
    /// persona prompt decides how to answer it.
    private func handOff() {
        var parts: [String] = []
        parts.append("I've been looking at the \(offer.modelName) in the app.")
        if let after = offer.priceAfterLoyalty ?? offer.price {
            parts.append("It showed my price as \(after)")
            if let d = offer.loyaltyDiscount {
                parts.append("after a \(d) loyalty credit")
            }
        }
        let sc = tradeInScore
        if let credit = offer.tradeInCredit, let base = Self.rupees(credit) {
            let r = sc.indicativeRange(baseCredit: base)
            parts.append("with an indicative trade-in of "
                + "\(formatMoney(r.low)) to \(formatMoney(r.high)) "
                + "on a condition score of \(sc.score) out of 100 (\(sc.band.rawValue))")
        }
        if let monthly = offer.monthly {
            parts.append("and about \(monthly) a month")
        }
        let summary = parts.joined(separator: " ")
            + ". Can you talk me through whether this is the right choice and what happens next?"

        session.pendingDiscoverPrompt = summary
        NSLog("🛒 UPGRADE: handoff to advisor (model=%@ len=%d)",
              offer.modelName, summary.count)
        dismiss()
        onTalkToAdvisor?()
    }

    // MARK: - Small pieces

    /// Hero shot of the model being OFFERED.
    ///
    /// Uses `offer.imageUrl`, never the owned vehicle's 360 sequence — an
    /// earlier version rendered a frame of the vehicle being TRADED IN on the
    /// page selling a different one. Falls back to a themed glyph when the
    /// tenant supplied no image: showing the WRONG vehicle is far worse than
    /// showing none.
    @ViewBuilder
    private var heroImage: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground))
            if let raw = offer.imageUrl, let url = URL(string: raw) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFit().padding(8)
                    case .failure:
                        heroBundledOrGlyph
                    default:
                        ProgressView().tint(theme.primary)
                    }
                }
            } else {
                heroBundledOrGlyph
            }
        }
        .frame(height: layoutContext.isCompact ? 140 : 170)
        .frame(maxWidth: .infinity)
        .accessibilityLabel(offer.modelName)
    }

    /// Bundled art for the offered model, else the themed glyph.
    ///
    /// This preserves the rule stated above — never show the wrong vehicle — because
    /// `bundledStaticImageName(forOfferModelName:)` requires the brand token AND a
    /// known model, and returns nil otherwise. What it changes is that a *correct*
    /// bundled image now beats the glyph, which matters because `offer.imageUrl` is
    /// deliberately nil on every seeded offer, so this hero was always a glyph.
    @ViewBuilder
    private var heroBundledOrGlyph: some View {
        if let name = VehicleSweepView.bundledStaticImageName(
            forOfferModelName: offer.modelName) {
            Image(name).resizable().scaledToFit().padding(8)
        } else {
            Image(systemName: "bolt.car")
                .font(.system(size: 40))
                .foregroundStyle(theme.primary.opacity(0.8))
        }
    }

    /// Why this vehicle is being recommended, with the evidence behind it.
    ///
    /// Expanded by default here, unlike the collapsed treatment it had in the Home
    /// drawer. On a full screen the argument is the content, so hiding it behind a
    /// disclosure triangle would be defensive about the thing this screen exists to
    /// say.
    ///
    /// Observation and inference stay visually distinct so the customer can accept a
    /// fact and still reject the conclusion drawn from it. See
    /// `OfferRecommendationBasis`.
    @ViewBuilder
    private var recommendationBasisBlock: some View {
        let basis = OfferRecommendationBasis.basis(
            offerId: offer.id, modelName: offer.modelName, rationale: offer.rationale,
            ownedModelName: session.currentVehicle?.model)

        card {
            HStack(spacing: 8) {
                Image(systemName: "sparkles").foregroundStyle(theme.primary)
                Text("Why we're suggesting this")
                    .font(.subheadline.weight(.semibold))
                Spacer(minLength: 0)
            }
            Text(basis.touchpointSummary)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                Text(basis.confidenceLabel)
                Text("·")
                Text(basis.freshnessLabel())
            }
            .font(.caption2)
            .foregroundStyle(.tertiary)

            Divider().padding(.vertical, 2)

            ForEach(basis.evidence) { item in
                HStack(alignment: .top, spacing: 9) {
                    Image(systemName: item.touchpoint.symbolName)
                        .font(.caption)
                        .foregroundStyle(theme.primary)
                        .frame(width: 16)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.detail)
                            .font(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(item.inference)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.bottom, 2)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(item.touchpoint.rawValue). \(item.detail). \(item.inference)")
            }

            Text(OfferRecommendationBasis.attributionNote)
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Projected running-cost saving, for an electric current vehicle only.
    ///
    /// Assumptions and the verbatim disclosure are rendered with the figure, always —
    /// a savings number without its inputs cannot be checked, and money claims plus
    /// their disclosures are a deterministic seam per
    /// `~/.kiro/steering/agentic-tiers.md`. See `EnergySavingsProjection`.
    @ViewBuilder
    private var energySavingsBlock: some View {
        if let v = session.currentVehicle,
           let p = EnergySavingsProjection.projection(
                currentFuelType: v.fuelType,
                odometerMiles: v.odometer,
                yearsOwned: Self.yearsOwned(modelYear: v.year),
                offerId: offer.id,
                modelName: offer.modelName,
                currencySymbol: currencySymbol) {
            card {
                HStack(spacing: 8) {
                    Image(systemName: "bolt.fill").foregroundStyle(theme.primary)
                    Text("Save about \(p.savingLabel) on charging")
                        .font(.subheadline.weight(.semibold))
                    Spacer(minLength: 0)
                }
                Text("\(p.efficiencyGainPercent)% more efficient than your current vehicle")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Divider().padding(.vertical, 2)

                ForEach(p.assumptions, id: \.self) { line in
                    HStack(alignment: .top, spacing: 6) {
                        Text("·").font(.caption2).foregroundStyle(.tertiary)
                        Text(line)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                // Verbatim. See EnergySavingsProjection.disclosure.
                Text(EnergySavingsProjection.disclosure)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 2)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Estimated saving \(p.savingLabel) on charging. "
                + p.assumptions.joined(separator: ". ") + ". "
                + EnergySavingsProjection.disclosure)
        }
    }

    /// Ownership duration proxy from model year.
    ///
    /// The client has no purchase date, so vehicle age stands in. Floored at 1 so the
    /// annual-distance estimate cannot divide by zero, capped at 12 so a very old
    /// model year does not imply an implausibly small annual distance.
    private static func yearsOwned(modelYear: Int?) -> Int {
        guard let modelYear, modelYear > 1980 else { return 3 }
        let current = Calendar.current.component(.year, from: Date())
        return min(12, max(1, current - modelYear))
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) { content() }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12)
                .fill(Color(.secondarySystemGroupedBackground)))
    }

    private func priceRow(_ label: String, _ value: String,
                          strikethrough: Bool = false,
                          accent: Bool = false,
                          emphasised: Bool = false) -> some View {
        HStack {
            Text(label)
                .font(emphasised ? .subheadline.weight(.semibold) : .subheadline)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .font(emphasised ? .title3.bold() : .subheadline)
                .strikethrough(strikethrough, color: .secondary)
                .foregroundStyle(accent ? theme.primary
                                        : (emphasised ? Color(.label) : Color(.secondaryLabel)))
        }
    }

    private func detail(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.subheadline).foregroundStyle(.secondary)
            Spacer()
            Text(value).font(.subheadline)
        }
    }

    private func noteRow(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "info.circle").font(.caption2)
            Text(text).font(.caption)
        }
        .foregroundStyle(.secondary)
        .fixedSize(horizontal: false, vertical: true)
    }
}
