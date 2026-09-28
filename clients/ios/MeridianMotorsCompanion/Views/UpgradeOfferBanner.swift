import SwiftUI

/// Collapsible "upgrade offer" banner for the Home tab.
///
/// Starts as a single teaser row and **pops down** shortly after appearing to
/// reveal the tenant's trade-in offers. Tapping the header toggles it; a close
/// button dismisses it for the session.
///
/// ## Where the content comes from
///
/// Entirely from `AcquireConfig.upgradeOffers`, delivered by
/// `GET /tenants/{tenantId}/config`. Nothing is computed on-device and nothing
/// is hardcoded: the copy names real models and prices, and tenant brand
/// strings are canary-forbidden in committed source. No offers means no
/// banner.
///
/// Money arrives pre-formatted from the tenant, because Indian lakh grouping
/// and Western thousands grouping disagree and picking the wrong one in front
/// of the customer looks careless.
///
/// ## Disclosure handling
///
/// `offer.disclosure` is **not** rendered here as of 2026-08-18. It is presented
/// verbatim one screen later, in `UpgradeFlow.offerStep`, which is where the offer's
/// terms are actually reviewed and where there is room to read them.
///
/// This is a de-duplication, not a removal: the rule that the disclosure is shown
/// verbatim, never truncated or paraphrased — the same rule the shopping agent
/// follows for `offers_lookup` results — still holds at that site. Do not
/// reintroduce it here; two renderings of one disclosure is how they drift, and if
/// the drawer and the flow ever disagreed the customer would be reading one figure
/// and hearing another.
struct UpgradeOfferBanner: View {
    /// Declared first so the memberwise initialiser takes it first; Swift orders
    /// init parameters by declaration order, not call order.
    var presentation: Presentation = .card
    let offers: [AcquireConfig.UpgradeOffer]
    /// Vehicle being traded in, used only for the teaser copy.
    let currentVehicleTitle: String?
    /// Owned model name, e.g. "Trailwind", so the recommender can weight a
    /// same-nameplate generational upgrade. Injected rather than read from a session
    /// because this view is prop-driven.
    var ownedModelName: String? = nil
    let theme: TenantTheme
    /// Fired when an offer's CTA is tapped, so Home can route into the Buy flow.
    var onExplore: ((AcquireConfig.UpgradeOffer) -> Void)?

    /// Called when the user declines upgrades outright, so the PRESENTER can decide
    /// what "decline" means for its surface. In the sheet that means sliding the
    /// drawer away and only then recording the dismissal.
    ///
    /// Optional, and the button falls back to dismissing locally when it is nil, so
    /// an unwired presenter (previews, snapshot tests, the inline card) keeps the
    /// old self-contained behaviour rather than rendering a dead button.
    ///
    /// Why the presenter and not this view: recording the dismissal here flips this
    /// view's own render guard, so the content disappeared out from under a sheet
    /// that was still on screen. The banner cannot both persist the decision and
    /// remain visible long enough to animate away — so it delegates.
    var onDecline: ((AcquireConfig.UpgradeOffer) -> Void)?

    @State private var isExpanded = false
    /// Offer identities the user has dismissed from Home, persisted across
    /// launches. Keyed per offer so a NEW recommendation is not suppressed by a
    /// dismissal of the previous one — "not now" applies to the offer the user
    /// actually saw, not to the category forever.
    ///
    /// Was plain `@State` until 2026-08-19, which meant a swiped-off card
    /// reappeared on the next rebuild. Dismissal that does not survive is not
    /// dismissal; it reads as the app ignoring the gesture.
    ///
    /// Dismissing here does NOT clear the Buy tab badge. The recommendation stays
    /// findable where buying happens — see `AppSession.buyBadgeCount`.
    @AppStorage(UpgradeOfferDismissals.storageKey) private var dismissedOfferIds: String = ""

    /// How this banner is hosted, which changes what chrome is appropriate.
    enum Presentation {
        /// Inline in a scroll feed: draws its own card, collapses to a header, and
        /// offers an explicit close control.
        case card
        /// Inside a sheet: no card fill or border (the sheet is the surface), always
        /// expanded (the detent is the expansion), and no chevron. Dismissal is by
        /// swipe for "not now", with a labelled control for "never" — an ✕ beside a
        /// sheet's own swipe affordance is ambiguous about which one it means.
        case sheet
    }

    @State private var hasAutoExpanded = false

    /// The single recommendation this banner presents, computed once.
    ///
    /// `bestMatch` was previously called at three separate sites in this file. It
    /// is deterministic, so they agreed — but a caller adding a fourth with a
    /// different `ownedModelName` would silently produce a header describing one
    /// offer above a row describing another.
    private var topRecommendation: (offer: AcquireConfig.UpgradeOffer,
                                    basis: OfferRecommendationBasis)? {
        OfferRecommendationBasis.bestMatch(among: offers, ownedModelName: ownedModelName)
    }

    private var dismissedIds: Set<String> {
        UpgradeOfferDismissals.ids(in: dismissedOfferIds)
    }

    /// Records a permanent, per-offer dismissal. Storage details live in
    /// `UpgradeOfferDismissals` so this view and the sheet gate cannot disagree.
    ///
    /// Writes through `UpgradeOfferDismissals.persist(dismissing:)` rather than
    /// assigning `dismissedOfferIds` directly, so there is one writer of the encoded
    /// form. `@AppStorage` still observes the key, so this view re-renders either way.
    private func dismiss(_ offer: AcquireConfig.UpgradeOffer) {
        UpgradeOfferDismissals.persist(dismissing: offer.id)
    }

    /// Sheet mode is always expanded: the detent already performs the reveal the
    /// chevron used to, and a collapsed card inside a half-height sheet wastes the
    /// space the sheet just claimed.
    private var effectiveExpanded: Bool {
        presentation == .sheet ? true : isExpanded
    }

    var body: some View {
        if let top = topRecommendation, !dismissedIds.contains(top.offer.id) {
            VStack(spacing: 0) {
                header(top.offer)
                if effectiveExpanded {
                    Divider().padding(.horizontal, 12)
                    VStack(spacing: 10) {
                        // ONE curated recommendation, not the whole list — see
                        // `OfferRecommendationBasis.bestMatch(among:)`. Showing every
                        // offer turns this into a brochure and puts the choosing back
                        // on the customer, which demonstrates no reasoning at all.
                        offerRow(top.offer)
                        if presentation == .sheet {
                            // Distinguishes "never show this offer again" from the
                            // swipe, which only means "not now". Labelled rather than
                            // an icon because the two outcomes are materially
                            // different and an ✕ cannot say which it performs.
                            //
                            // Delegates to `onDecline` so the sheet closes. Recording
                            // the dismissal inline flipped the render guard above and
                            // emptied the sheet in place, leaving the drawer sitting
                            // open with nothing in it.
                            Button("Not interested in upgrading") {
                                if let onDecline {
                                    onDecline(top.offer)
                                } else {
                                    dismiss(top.offer)
                                }
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.top, 4)
                        }
                    }
                    .padding(12)
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
            }
            // Card fill/border only when inline. Inside a sheet the sheet IS the
            // surface, and a bordered card within it reads as a card-in-a-card.
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(presentation == .card ? theme.primary.opacity(0.08) : .clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(presentation == .card ? theme.primary.opacity(0.35) : .clear,
                            lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .animation(.spring(response: 0.42, dampingFraction: 0.82), value: isExpanded)
            .task {
                // Pop down on its own once, so the offers are seen without
                // the user having to discover the tap target. Delayed rather
                // than immediate so it reads as an arriving notification
                // instead of a static block.
                guard presentation == .card else { return }
                guard !hasAutoExpanded else { return }
                hasAutoExpanded = true
                try? await Task.sleep(nanoseconds: 900_000_000)
                await MainActor.run { isExpanded = true }
            }
        }
    }

    /// Takes the recommended offer so the dismiss button knows *what* it is
    /// dismissing — dismissal is now per-offer and persisted, so it cannot be
    /// keyed on "the banner" as a whole.
    private func header(_ offer: AcquireConfig.UpgradeOffer) -> some View {
        Button {
            isExpanded.toggle()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "arrow.triangle.2.circlepath.circle.fill")
                    .font(.title3)
                    .foregroundStyle(theme.primary)
                VStack(alignment: .leading, spacing: 3) {
                    // Say what it is, then why it was chosen.
                    //
                    // This previously read "Curated for how you drive" — named for
                    // the reasoning rather than the transaction, to avoid sounding
                    // like a promotion pushed at the customer. That instinct was
                    // right but overshot: the heading named the *existence* of
                    // reasoning and dropped the noun, so a collapsed card gave no
                    // clue it concerned an upgrade at all. The reasoning still
                    // leads the eye — it is the line directly beneath, and the
                    // full evidence is on the first screen after Explore — but it
                    // is no longer doing the job of telling the user what this is.
                    HStack(spacing: 6) {
                        Text("Upgrade offer")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Color(.label))
                        // Validity in the COLLAPSED header, not only in the
                        // expanded row. `validUntil` is tenant-supplied and real
                        // (staging: "Valid till Aug 31"), and it was previously
                        // rendered only inside `offerRow` — visible after
                        // expanding, which is exactly when a time limit has
                        // already stopped being useful. Rendered verbatim: an
                        // expiry is an offer term, so it is presented as given
                        // and never reformatted into a countdown or an urgency
                        // phrase this app invented.
                        if let validity = offer.validUntil {
                            Text(validity)
                                .font(.caption2.weight(.medium))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(
                                    Capsule().fill(theme.primary.opacity(0.15))
                                )
                                .foregroundStyle(theme.primary)
                        }
                    }
                    Text(teaser)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                Spacer()
                // Inline: a chevron for collapse/expand and an ✕ for permanent
                // dismissal. In a sheet both are wrong — the detent performs the
                // expansion, and an ✕ sitting beside the sheet's own swipe-to-dismiss
                // is ambiguous about whether it means "not now" or "never". Sheet mode
                // therefore says which one it means, in words, at the bottom.
                if presentation == .card {
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(theme.primary)
                    Button {
                        dismiss(offer)
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.tertiary)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Dismiss recommendation")
                }
            }
            .padding(12)
        }
        .buttonStyle(.plain)
        .accessibilityHint(isExpanded ? "Collapses the offer list"
                                      : "Expands the offer list")
    }

    /// One-line teaser shown collapsed.
    ///
    /// Leads with the *reasoning* rather than the trade-in figure. The money is
    /// visible the moment the row expands; what the collapsed line has to earn is the
    /// tap, and "we looked at how you drive" earns it in a way "trade in your car"
    /// does not. The old default — "2 offers on a new model" — also revealed that the
    /// surface was a list, which it no longer is.
    private var teaser: String {
        guard let top = OfferRecommendationBasis.bestMatch(among: offers, ownedModelName: ownedModelName) else {
            return "Matched to your driving"
        }
        let signals = top.basis.evidence.count
        if let title = currentVehicleTitle {
            return "Based on \(signals) signals from your \(title) — "
                + "the \(top.offer.modelName) fits how you drive"
        }
        return "Based on \(signals) signals from your driving — "
            + "the \(top.offer.modelName) fits how you drive"
    }

    private func offerRow(_ offer: AcquireConfig.UpgradeOffer) -> some View {
        HStack(alignment: .top, spacing: 10) {
            offerThumbnail(offer)

            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text(offer.modelName)
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    if let price = offer.price {
                        Text(price).font(.subheadline.weight(.medium))
                    }
                }
                if let rationale = offer.rationale {
                    Text(rationale)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: 8) {
                    if let credit = offer.tradeInCredit {
                        pill("Trade-in \(credit)", filled: true)
                    }
                    if let monthly = offer.monthly {
                        pill("\(monthly)/mo", filled: false)
                    }
                    Spacer()
                    if onExplore != nil {
                        Button("Explore") { onExplore?(offer) }
                            .font(.caption.weight(.semibold))
                            .buttonStyle(.borderless)
                            .foregroundStyle(theme.primary)
                    }
                }
                // Validity is NOT repeated here. It moved to the header capsule
                // (2026-08-19), which is visible whether the card is collapsed or
                // expanded — rendering it in both places showed the same term
                // twice on expand. Same call as the `disclosure` removed from this
                // drawer earlier: one authoritative site per term.

                // The reasoning (recommendation evidence + energy savings) and the
                // offer disclosure deliberately do NOT appear here — they moved to
                // `UpgradeFlow.offerStep`, the first screen after Explore.
                //
                // This drawer is a peek on a crowded Home tab: three stacked
                // explanation blocks at caption2 made the case for the upgrade in the
                // one place with no room to read it. The argument belongs on a full
                // screen. The disclosure was also a duplicate — `UpgradeFlow` already
                // renders it verbatim, so nothing was dropped, only de-duplicated
                // away from the cramped surface.
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    /// Thumbnail of the vehicle being offered.
    ///
    /// Precedence: `offer.imageUrl` (tenant-supplied, offer-specific) → bundled art
    /// resolved from the model name → a themed glyph.
    ///
    /// The bundled tier exists because `imageUrl` is **deliberately nil** on every
    /// seeded offer, so before this the customer never saw the vehicle being sold to
    /// them anywhere in the offer flow. Bundled art fills that in with no network
    /// dependency and no risk of a broken remote image.
    ///
    /// Never falls back to the *owned* vehicle's art: this row sells a different
    /// vehicle, and rendering the trade-in here is the exact confusion
    /// `UpgradeFlow.heroImage` was written to avoid.
    @ViewBuilder
    private func offerThumbnail(_ offer: AcquireConfig.UpgradeOffer) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(.tertiarySystemGroupedBackground))

            if let raw = offer.imageUrl, let url = URL(string: raw) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFit()
                    case .failure:
                        bundledOrGlyph(offer)
                    default:
                        ProgressView().tint(theme.primary).controlSize(.small)
                    }
                }
            } else {
                bundledOrGlyph(offer)
            }
        }
        .frame(width: 76, height: 52)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityHidden(true)   // the model name is already announced
    }

    @ViewBuilder
    private func bundledOrGlyph(_ offer: AcquireConfig.UpgradeOffer) -> some View {
        if let name = VehicleSweepView.bundledStaticImageName(
            forOfferModelName: offer.modelName) {
            Image(name).resizable().scaledToFit()
        } else {
            Image(systemName: "car.fill")
                .font(.callout)
                .foregroundStyle(theme.primary.opacity(0.7))
        }
    }

    private func pill(_ text: String, filled: Bool) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                Capsule().fill(filled ? theme.primary.opacity(0.18)
                                      : Color(.tertiarySystemGroupedBackground))
            )
            .foregroundStyle(filled ? theme.primary : Color(.secondaryLabel))
    }
}
