import SwiftUI

/// Landing screen for the Buy / Acquire tab.
///
/// Previously the tab opened straight into the conversational flow. That works
/// for someone who already knows what to ask, but a prospective customer
/// dropped into an empty chat has no idea what the product can do — and an
/// OEM's shop-window is exactly where the range on offer should be visible.
/// This screen states what is available, then routes into the conversation with
/// the topic already framed.
///
/// Every route lands on an existing surface. Offers open the wizard; everything
/// else opens the conversation with a seeded opening prompt via
/// `AppSession.pendingDiscoverPrompt`, which `DiscoverFlow` already consumes on
/// appear. No new agent plumbing.
///
/// Presentation uses ONE `.fullScreenCover(item:)` keyed off an enum rather
/// than several boolean covers. SwiftUI honours only the last cover attached to
/// a view, so stacking them silently breaks every route but one — that defect
/// has already been hit once in this app.
struct BuyLandingView: View {
    let theme: TenantTheme

    /// Forwarded to `DiscoverFlow` so convergence still reaches the configurator.
    let onConverge: (DiscoverHandoff) -> Void
    let onLeadCaptured: (String) -> Void
    let onOpenAssistant: ((String, String?) -> Void)?

    /// Whether the user arrived here from a badged Buy tab, i.e. the badge promised a
    /// recommendation they had not seen.
    ///
    /// Passed in at construction rather than read from `session.buyBadgeCount` inside
    /// this view, for two reasons. The count is cleared on appear, so by first render
    /// it is already 0 — and relying on `onAppear` ordering between a parent modifier
    /// and a child is precisely the race that made a tile open the wrong assistant
    /// prompt earlier (fixed by `DiscoverFlow(initialPrompt:)`). A parameter has no
    /// ordering.
    var arrivedWithRecommendation: Bool = false

    @Environment(AppSession.self) private var session
    @State private var destination: Destination?

    /// Latches `arrivedWithRecommendation` on first appearance.
    ///
    /// Needed because the parent clears the badge as soon as this view appears, so a
    /// later re-render would pass `false` and the promoted offer would silently drop
    /// back down the page while the user was reading it.
    @State private var latchedArrival: Bool?

    private var promotesOffer: Bool { latchedArrival ?? arrivedWithRecommendation }

    /// Where a tile sends the customer.
    enum Destination: Identifiable {
        /// Conversational flow, optionally with an opening prompt.
        case chat(prompt: String?)
        /// The three-step upgrade wizard for one specific offer.
        case upgrade(offer: AcquireConfig.UpgradeOffer)
        /// The configurator, entered warm from an accepted upgrade offer.
        case configure(handoff: ConfiguratorOfferHandoff)

        var id: String {
            switch self {
            case .upgrade(let o):     return "upgrade:\(o.id)"
            case .chat(let p):        return "chat:\(p ?? "")"
            case .configure(let h):   return "configure:\(h.modelName ?? "")"
            }
        }
    }

    init(theme: TenantTheme,
         onConverge: @escaping (DiscoverHandoff) -> Void = { _ in },
         onLeadCaptured: @escaping (String) -> Void = { _ in },
         onOpenAssistant: ((String, String?) -> Void)? = nil,
         arrivedWithRecommendation: Bool = false) {
        self.theme = theme
        self.onConverge = onConverge
        self.onLeadCaptured = onLeadCaptured
        self.onOpenAssistant = onOpenAssistant
        self.arrivedWithRecommendation = arrivedWithRecommendation
    }

    // MARK: - Pending handoff

    /// Whether a pending agent handoff should be honoured right now.
    ///
    /// Extracted as a static predicate so the rule is testable without standing
    /// up a SwiftUI host — the defect it guards against was a routing rule that
    /// existed only as an assumption in a docstring.
    ///
    /// False when something is already presented: this view's own tiles set
    /// `destination` first and write the prompt afterwards (in the cover's
    /// `onAppear`), so firing on a non-nil `destination` would fight them.
    static func shouldOpenAgentForPendingHandoff(destination: Destination?,
                                                 pendingPrompt: String?) -> Bool {
        guard destination == nil else { return false }
        guard let pendingPrompt,
              !pendingPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return false }
        return true
    }

    // MARK: - Derived content

    private var acquire: AcquireConfig? { session.tenantConfig?.acquire }
    private var offers: [AcquireConfig.UpgradeOffer] { acquire?.upgradeOffers ?? [] }

    /// Noun for the vehicle class, tenant-supplied so copy never hardcodes it.
    private var categoryNoun: String {
        acquire?.vehicleCategoryLabel ?? "vehicle"
    }

    /// Lowest advertised monthly across the offers, for the offers tile.
    /// Strings are pre-formatted by the tenant (grouping conventions differ by
    /// market), so this compares the digits and returns the original string.
    private var lowestMonthly: String? {
        offers.compactMap { $0.monthly }
            .min { digits($0) < digits($1) }
    }

    private func digits(_ s: String) -> Int {
        Int(s.filter(\.isNumber)) ?? Int.max
    }

    /// Highest trade-in credit on offer — the headline number, and the reason
    /// it is phrased "up to".
    private var topTradeInCredit: String? {
        offers.compactMap { $0.tradeInCredit }
            .max { digits($0) < digits($1) }
    }

    // MARK: - Body

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    // Arriving from a badged tab means the badge promised a specific
                    // recommendation, so lead with it. Landing on the owned-vehicle
                    // hero instead makes the user hunt for the thing they were just
                    // told about, which is the badge writing a cheque the screen
                    // does not honour.
                    //
                    // Unbadged (browsing deliberately), the original order stands:
                    // your car, then what the watcher noticed, then the offers.
                    // Context first is right when nothing specific was promised.
                    if promotesOffer, !offers.isEmpty {
                        offersSection
                        ownedVehicleHero
                        discoveryWatchSection
                    } else {
                        ownedVehicleHero
                        discoveryWatchSection
                        if !offers.isEmpty { offersSection }
                    }
                    tradeInTile
                    browseSection
                    testRideTile
                    advisorTile
                    disclosureFootnote
                }
                .padding(.vertical, 16)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle(session.layoutSegment.buyTabLabel)
            .onAppear {
                if latchedArrival == nil { latchedArrival = arrivedWithRecommendation }
            }
        }
        // A handoff written by a view that cannot present the agent itself is
        // consumed HERE, because this view owns `destination`.
        //
        // `UpgradeFlow` writes its opening line into
        // `AppSession.pendingDiscoverPrompt` and then asks its caller to "route
        // to the Buy tab", on the assumption that whatever the Buy tab renders
        // is `DiscoverFlow` — which consumes the prompt on appear. That stopped
        // being true when the tab started rendering this shop window instead,
        // so the Home trade-in-banner entry landed here with the customer's
        // question stranded in the session and no agent in sight (see
        // `issues/2026-08-18-ios-upgrade-handoff-dead-ends-on-buy-landing/`).
        //
        // The prompt itself is deliberately NOT passed through as
        // `.chat(prompt:)`: it is already in the session, and `DiscoverFlow`
        // must stay the single site that reads and clears it. Two writers of
        // one field is the shape of the bug this fixes.
        .onAppear {
            if Self.shouldOpenAgentForPendingHandoff(
                destination: destination,
                pendingPrompt: session.pendingDiscoverPrompt) {
                NSLog("🛒 LANDING: consuming pending handoff — opening agent")
                destination = .chat(prompt: nil)
            }
        }
        .fullScreenCover(item: $destination) { dest in
            switch dest {
            case .chat(let prompt):
                // Prompt passed DIRECTLY, not through `session.pendingDiscoverPrompt`.
                //
                // The previous version wrote the field in this view's `.onAppear`,
                // but `DiscoverFlow.autoOpenConversation()` reads it from a `.task`
                // that can run first — so the field was nil and the flow used its
                // generic opener instead. Tapping "Book a test drive" opened a
                // conversation about upgrading (2026-08-19). A parameter removes the
                // ordering assumption entirely.
                DiscoverFlow(
                    theme: theme,
                    session: session,
                    onConverge: onConverge,
                    onLeadCaptured: onLeadCaptured,
                    onOpenAssistant: onOpenAssistant,
                    onDismiss: { destination = nil },
                    initialPrompt: prompt
                )
            case .upgrade(let offer):
                UpgradeFlow(
                    offer: offer,
                    theme: theme,
                    onTalkToAdvisor: {
                        // UpgradeFlow calls dismiss() BEFORE invoking this,
                        // which drives `destination` to nil. Assigning
                        // synchronously here races that dismissal and SwiftUI
                        // can drop the incoming cover — dead-ending the flow at
                        // precisely the handoff moment. Wait for the dismissal
                        // transition to finish, then present.
                        //
                        // The summary is already in pendingDiscoverPrompt and
                        // dismissal does not clear it, so nothing is lost by
                        // waiting; DiscoverFlow consumes it on appear.
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
                            destination = .chat(prompt: nil)
                        }
                    },
                    onConfigure: { handoff in
                        // Same dismissal race as onTalkToAdvisor above, and the
                        // same 0.45s wait for the same reason — UpgradeFlow has
                        // already called dismiss() by the time this runs.
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
                            destination = .configure(handoff: handoff)
                        }
                    }
                )

            case .configure(let handoff):
                ConfiguratorFlow(
                    theme: theme,
                    handoff: nil,
                    offerHandoff: handoff,
                    onAskAssistant: onOpenAssistant.map { cb in
                        { message in cb(message, nil) }
                    }
                )
            }
        }
    }

    // MARK: - Hero

    /// The customer's own vehicle, with the trade-in headline.
    ///
    /// Leading with what they already own is the point of doing this inside a
    /// connected-vehicle product: the shop window is personalised before they
    /// type anything.
    /// Tier 1 narration of the Discovery Watch artifact.
    ///
    /// Lives on the Buy tab, not Home. Two reasons, and the first is a correctness
    /// one: Home is the landing surface for an owner doing owner things, and a
    /// "here is what changed about the car you are shopping for" card there presumes
    /// the customer is mid-purchase. The Buy tab IS the shopping context, so the
    /// finding arrives where it is already relevant.
    ///
    /// Pull-only — this renders because the customer opened the tab, not because
    /// anything was pushed. See `DiscoveryWatchFinding`.
    @ViewBuilder
    private var discoveryWatchSection: some View {
        if let top = OfferRecommendationBasis.bestMatch(among: offers, ownedModelName: session.currentVehicle?.model) {
            DiscoveryWatchCard(
                findings: DiscoveryWatchFinding.findings(
                    savedModelName: top.offer.modelName,
                    savedColorId: nil),
                watchedItemLabel: top.offer.modelName,
                theme: theme,
                onOpenBuild: { destination = .upgrade(offer: top.offer) }
            )
            // This screen has no container-level horizontal padding: the VStack
            // in `body` sets `.padding(.vertical, 16)` only, because
            // `offersSection` and `browseSection` are horizontal carousels whose
            // cards must bleed to the screen edge. So every non-carousel section
            // supplies its own margin, and this one was missing it — the card's
            // leading icon sat hard against the screen edge.
            .padding(.horizontal)
        }
    }

    @ViewBuilder
    private var ownedVehicleHero: some View {
        if let v = session.currentVehicle {
            VStack(alignment: .leading, spacing: 10) {
                Text("Your \(categoryNoun.lowercased())")
                    .font(.caption).foregroundStyle(.secondary)
                    .textCase(.uppercase)
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(v.displayTitle)
                            .font(.title3.weight(.semibold))
                            .fixedSize(horizontal: false, vertical: true)
                        if let odo = v.odometer {
                            Text("\(odo.formatted(.number.grouping(.automatic))) km"
                                 + (v.totalTrips.map { " · \($0) trips" } ?? ""))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 0)
                }
                if let credit = topTradeInCredit {
                    HStack(spacing: 6) {
                        Image(systemName: "arrow.left.arrow.right.circle.fill")
                            .foregroundStyle(theme.primary)
                        Text("Trade in for up to ")
                            .font(.subheadline)
                        + Text(credit).font(.subheadline.weight(.bold))
                    }
                    .padding(.top, 2)
                }
            }
            .padding(16)
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(theme.primary.opacity(0.08))
            )
            .padding(.horizontal)
        }
    }

    // MARK: - Tiles

    /// The offers themselves, not a link to them. A shop window should show
    /// the goods; making the customer tap through to discover that offers exist
    /// is the same mistake as opening on an empty chat.
    private var offersSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Your upgrade offers").font(.headline)
                Spacer()
                if let m = lowestMonthly {
                    Text("from \(m)/mo")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    ForEach(offers) { offer in
                        offerCard(offer)
                    }
                }
                .padding(.horizontal)
            }
        }
    }

    /// Small product shot for the offer card.
    ///
    /// Uses `offer.imageUrl` — the model being SOLD, never the owned vehicle's
    /// 360 sequence. On failure it falls back to a themed glyph rather than any
    /// substitute image: showing the wrong vehicle on a card selling a
    /// different one is far worse than showing none.
    ///
    /// These assets are WebP served as `application/octet-stream`. iOS decodes
    /// the container by sniffing rather than by MIME type or file extension, so
    /// no format handling is needed here — and adding an extension-based check
    /// would break them, since the CDN also serves WebP under `.png` paths.
    /// Bundled art for the offered model, else a glyph.
    ///
    /// `offer.imageUrl` is deliberately nil on every seeded offer, so without this
    /// tier the customer never saw the vehicle being sold to them. Resolution is by
    /// model name and returns nil for an unknown model rather than substituting
    /// another body style.
    @ViewBuilder
    private func offerBundledOrGlyph(_ offer: AcquireConfig.UpgradeOffer) -> some View {
        if let name = VehicleSweepView.bundledStaticImageName(
            forOfferModelName: offer.modelName) {
            Image(name).resizable().scaledToFit().padding(4)
        } else {
            Image(systemName: "bolt.car")
                .font(.system(size: 22))
                .foregroundStyle(theme.primary.opacity(0.7))
        }
    }

    @ViewBuilder
    private func offerThumbnail(_ offer: AcquireConfig.UpgradeOffer) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.tertiarySystemGroupedBackground))
            if let raw = offer.imageUrl, let url = URL(string: raw) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFit().padding(4)
                    case .failure:
                        offerBundledOrGlyph(offer)
                    default:
                        ProgressView().controlSize(.mini).tint(theme.primary)
                    }
                }
            } else {
                offerBundledOrGlyph(offer)
            }
        }
        .frame(height: 68)
        .frame(maxWidth: .infinity)
        .accessibilityHidden(true)
    }

    private func offerCard(_ offer: AcquireConfig.UpgradeOffer) -> some View {
        Button {
            open(.upgrade(offer: offer), label: "offer")
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                offerThumbnail(offer)
                Text(offer.modelName)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color(.label))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                if let r = offer.rationale {
                    Text(r).font(.caption2).foregroundStyle(.secondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 2)
                if let after = offer.priceAfterLoyalty ?? offer.price {
                    Text(after).font(.subheadline.weight(.bold))
                        .foregroundStyle(theme.primary)
                }
                if let m = offer.monthly {
                    Text("\(m)/month").font(.caption2).foregroundStyle(.secondary)
                }
            }
            .frame(width: 176, height: 208, alignment: .leading)
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.secondarySystemGroupedBackground))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(theme.primary.opacity(0.25), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Upgrade offer: \(offer.modelName)")
    }

    private var tradeInTile: some View {
        tile(
            icon: "indianrupeesign.circle.fill",
            title: "What's my \(categoryNoun.lowercased()) worth?",
            subtitle: "An indicative range from your service history and usage",
            badge: nil,
            action: {
                open(.chat(prompt:
                    "What is my current \(categoryNoun.lowercased()) worth as a "
                    + "trade-in? Please explain how you arrived at the range."),
                     label: "trade-in")
            }
        )
    }

    /// Browse by category. Categories are generic classes, not model names, so
    /// nothing brand-specific is hardcoded here.
    private var browseSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Browse the range")
                .font(.headline)
                .padding(.horizontal)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    categoryCard("Commuter", "figure.walk.motion",
                                 "Everyday economy")
                    categoryCard("Electric", "bolt.fill",
                                 "Zero fuel, low running cost")
                    categoryCard("Adventure", "mountain.2.fill",
                                 "Touring and rough roads")
                }
                .padding(.horizontal)
            }
        }
    }

    private func categoryCard(_ name: String,
                              _ icon: String,
                              _ blurb: String) -> some View {
        Button {
            open(.chat(prompt:
                "Show me your \(name.lowercased()) options and tell me which "
                + "would suit how I currently drive."),
                 label: "browse-\(name.lowercased())")
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: icon)
                    .font(.title2).foregroundStyle(theme.primary)
                Text(name).font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color(.label))
                Text(blurb).font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(width: 132, alignment: .leading)
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.secondarySystemGroupedBackground))
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Browse \(name) options")
    }

    private var testRideTile: some View {
        tile(
            icon: "key.fill",
            title: "Book a test drive",
            subtitle: "Find an authorised dealer near your \(categoryNoun.lowercased())",
            badge: nil,
            action: {
                open(.chat(prompt:
                    "I'd like to book a test drive. Which authorised dealers are "
                    + "nearest to me, and what slots do they have?"),
                     label: "test-drive")
            }
        )
    }

    private var advisorTile: some View {
        tile(
            icon: "bubble.left.and.bubble.right.fill",
            title: "Ask anything",
            subtitle: "Talk it through, or ask for a human advisor",
            badge: nil,
            action: { open(.chat(prompt: nil), label: "open-chat") }
        )
    }

    private func tile(icon: String,
                      title: String,
                      subtitle: String,
                      badge: String?,
                      action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10)
                        .fill(theme.primary.opacity(0.15))
                        .frame(width: 40, height: 40)
                    Image(systemName: icon)
                        .foregroundStyle(theme.primary)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Color(.label))
                    Text(subtitle)
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
                if let b = badge {
                    Text(b)
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .background(Capsule().fill(theme.primary))
                }
                Image(systemName: "chevron.right")
                    .font(.caption).foregroundStyle(.tertiary)
            }
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.secondarySystemGroupedBackground))
            )
            .padding(.horizontal)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title). \(subtitle)")
        // Every tile built here gets the screen margin, rather than each caller
        // remembering it. `tradeInTile`, `testRideTile` and `advisorTile` all
        // render through this helper and all three were flush to the screen edge
        // — one missing modifier, three broken rows, which is the argument for
        // owning it here. A future tile inherits the margin instead of
        // reintroducing the bug.
        //
        // Not hoisted to the VStack in `body`: `offersSection` and
        // `browseSection` are horizontal carousels that pad their header and
        // inner HStack separately so cards bleed to the edge. Container padding
        // would inset the scroll region and double the inner padding.
        .padding(.horizontal)
    }

    private var disclosureFootnote: some View {
        Text("Prices and trade-in figures are indicative and subject to "
             + "inspection and credit approval.")
            .font(.caption2).foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal)
            .padding(.top, 4)
    }

    private func open(_ dest: Destination, label: String) {
        NSLog("🛒 BUY: landing route=%@", label)
        destination = dest
    }
}
