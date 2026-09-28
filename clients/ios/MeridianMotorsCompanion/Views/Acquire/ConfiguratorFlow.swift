import SwiftUI

/// Multi-step vehicle configuration wizard.
///
/// Modeled on `BookingFlow.swift` — linear step machine, no NavigationStack
/// churn (the stack is created once at `:315` in `.standalone` chrome and never
/// pushed onto; steps swap content inside it), `dismiss()` exits at any point.
/// The floating mic FAB overlay is preserved (it lives in `MainTabView`'s
/// overlay layer above all content).
///
/// ## Step machine — 12 cases
///
/// The sequence is:
/// ```
/// pickCategory → pickModel → pickVariant → showEditionSummary → pickColor
/// → pickInteriorStyle → pickAccessories → pickHandover → supplyChainPlanning (auto-advance)
/// → deliveryProposal → confirm → success
/// ```
///
/// The Edition is displayed, not chosen — it arrives via the offer handoff
/// (`offerHandoff?.edition`) or defaults to `IvePackage.defaultFor(modelId:)`.
/// Accessories are optional; handover determines delivery (pickup costs 0 days,
/// home delivery 2–4 days). `supplyChainPlanning` auto-advances without visitor input.
/// See spec `2026-08-21-cvx-upgrade-flow-continuity` § Decision A for the two entry doors.
///
/// ## Chrome — standalone vs. hosted
///
/// - **`.standalone`** (default): owns a `NavigationStack`, the nav title, and
///   the close button. Used by the Buy tab, Home's configure action, and the
///   Discover hand-off — each of which presents this view itself. Requires no
///   changes to existing call sites.
/// - **`.hosted`**: renders step content only, with no `NavigationStack`, no
///   title, and no toolbar. `UpgradeFlow` supplies the chrome so that
///   configuring reads as later steps of the upgrade, not a second wizard.
///   Fires `onNavTitleChange` so the host title stays in sync with the inner step.
///   See spec `2026-08-21-cvx-upgrade-flow-continuity` § Decision B.
///
/// ## Entry doors — Door A (cold) and Door B (warm)
///
/// - **Door A (cold)**: `offerHandoff = nil`. Opens at `.pickCategory` with no
///   pre-selections. Used by Buy tab, Home configure action.
/// - **Door B (warm)**: `offerHandoff = ConfiguratorOfferHandoff` (accepted upgrade).
///   Resolves the offer's `modelName` against the loaded catalog and opens at
///   `.showEditionSummary` over real catalog objects. If resolution fails, gracefully
///   falls back to `.pickCategory` as a first-class path, not an error. A "Change vehicle"
///   affordance on the summary step keeps pickers one tap away.
/// 
/// Both doors produce identical final results; they differ only in where the
/// configuration starts.
///
/// ## Demo-scope constraint (decisions.md 2026-07-28 "Demo scope")
/// The `/acquire/*` REST routes are not provisioned server-side in this window.
/// `AcquireCatalogClient` calls return `AcquireError.endpointUnavailable` — the
/// flow renders from in-session state (empty catalog with placeholder content)
/// and from `handoff`-supplied pre-selections. Steps degrade gracefully; the
/// flow never crashes or hangs.
///
/// ## Init signature
/// ```swift
/// ConfiguratorFlow(theme: TenantTheme, chrome: Chrome = .standalone, 
///                   handoff: DiscoverHandoff? = nil, offerHandoff: ConfiguratorOfferHandoff? = nil)
/// ```
struct ConfiguratorFlow: View {
    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss
    /// Injected at the app root by `.detectAdaptiveLayout()`.
    /// Used to adapt layout for compact-width contexts (iPhone portrait/landscape).
    @Environment(\.adaptiveLayoutContext) private var layoutContext

    let theme: TenantTheme
    /// Optional handoff from a preceding Discover session. When non-nil,
    /// pre-selects category and model (Step 1 / Step 2 auto-advance).
    let handoff: DiscoverHandoff?
    /// Optional handoff from an accepted upgrade offer (task 5.2 / Zone 2 Rev 3).
    /// When non-nil, the Edition is taken from the offer and the flow opens
    /// directly at `showEditionSummary`, skipping category/model/variant pickers.
    var offerHandoff: ConfiguratorOfferHandoff? = nil
    /// Optional callback for primed-prompt assistant entry.
    var onAskAssistant: ((String) -> Void)? = nil
    /// Optional callback fired on every step transition with the current progress.
    ///
    /// Receives `(progressStep, progressStepCount)` so the host can render a
    /// unified progress bar spanning the upgrade flow and the configurator.
    /// One-way — the host observes, it does not drive the step machine.
    var onProgressChange: ((Int, Int) -> Void)? = nil
    /// Optional callback fired on every step transition with the current nav title.
    ///
    /// Allows a hosting view (`UpgradeFlow` with `chrome: .hosted`) to keep its
    /// own nav bar title in sync with the inner step without owning the step machine.
    /// One-way — the host observes, it does not drive. Nil on all existing call sites.
    var onNavTitleChange: ((String) -> Void)? = nil

    // MARK: - Chrome mode (Task 3.1)

    /// Controls whether the configurator supplies its own navigation chrome or
    /// defers to a host.
    ///
    /// - `.standalone` (default): owns a `NavigationStack`, the nav title, and the
    ///   close button. All existing call sites use this mode without any change.
    /// - `.hosted`: renders step content only — no `NavigationStack`, no title, no
    ///   toolbar. The host (`UpgradeFlow`) owns the chrome so that configuring reads
    ///   as later steps of the upgrade, not a second wizard. See spec
    ///   `2026-08-21-cvx-upgrade-flow-continuity` § Decision B.
    enum Chrome {
        case standalone
        case hosted
    }
    /// Chrome mode for this instance. Defaults to `.standalone` so every existing
    /// call site (`MainTabView`, `HomeTabView`, `BuyLandingView`) is unaffected.
    var chrome: Chrome = .standalone

    // MARK: - Step machine

    /// Promoted from `private` to `internal` (task 5.1) so `@testable import`
    /// in `ConfiguratorFlowStepMachineTests` can exercise step transitions directly.
    /// No wider than `internal` — this is not a public API.
    ///
    /// ## Zone 2 Revision 3 step machine (tasks 5.2 / 5.3 / 5.4 — Accessories)
    ///
    /// The Edition is no longer a picker step — it arrives via the accepted offer
    /// (`offerHandoff?.edition`) and is DISPLAYED, not chosen.  A no-offer fallback
    /// yields `IvePackage.defaultFor(modelId:)` — never nil, because the Edition is the
    /// payload on the visitor's NFC key card.
    ///
    /// Step sequence (light configuration path):
    ///   pickCategory → pickModel → pickVariant
    ///   → showEditionSummary → pickColor → pickInteriorStyle
    ///   → pickAccessories → supplyChainPlanning → deliveryProposal
    ///   → confirm → success
    ///
    /// Warm entry (`offerHandoff != nil`) pre-selects category and model from the offer,
    /// opening at `.pickCategory` with highlights showing the offer's suggested configuration.
    /// Pickers are visible and changeable — the visitor can see the pre-selection and modify it.
    /// The Edition still arrives from the offer and is displayed on `showEditionSummary`.
    ///
    /// `supplyChainPlanning` auto-advances and costs no tap — see `Step.isAutoAdvancing`.
    enum Step {
        case pickCategory
        case pickModel(CatalogCategory)
        case pickVariant(CatalogCategory, CatalogModel)
        /// Edition is shown with a "what's inside" summary; it is not a picker.
        case showEditionSummary(IvePackage, CatalogModel, CatalogVariant)
        case pickColor(IvePackage, CatalogModel, CatalogVariant)
        case pickInteriorStyle(IvePackage, CatalogModel, CatalogVariant, CatalogColor)
        /// Choose optional accessories for the configured vehicle.
        /// Placed before supplyChainPlanning so the plan can include accessory lead days.
        case pickAccessories(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, [CatalogAccessory])
        /// Choose how the vehicle reaches the customer: dealer pickup or home delivery.
        /// Placed BEFORE supplyChainPlanning so the plan reads the chosen handover method.
        case pickHandover(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, Set<String>)
        /// Animated planning sequence. Auto-advances; no visitor input.
        case supplyChainPlanning(IvePackage, CatalogModel, CatalogVariant,
                                 CatalogColor, InteriorStyle, Set<String>, HandoverMethod)
        /// Proposed delivery date and the reasoning behind it.
        case deliveryProposal(IvePackage, CatalogModel, CatalogVariant,
                              CatalogColor, InteriorStyle, SupplyChainPlan)
        case confirm(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, Set<String>, HandoverMethod)
        case success(ReservationResponse)

        // MARK: - Forward-advance seam (F5.2)

        /// Minimal selections needed to drive the warm-entry path forward one step.
        ///
        /// Only the fields needed for the offer-accepted → success path are exposed;
        /// earlier steps (pickCategory, pickModel, pickVariant) are not part of the
        /// warm-entry tap budget and are not tested here.
        struct ForwardSelection {
            let color: CatalogColor
            let interiorStyle: InteriorStyle
            let reservationResponse: ReservationResponse
            /// Plan produced by the supply-chain planning sequence.
            ///
            /// Defaulted so existing callers and tests that predate the
            /// supply-chain steps keep compiling; the default is a real derived
            /// plan rather than a sentinel, because `deliveryProposal` must always
            /// carry a usable plan — the same reasoning that makes
            /// `IvePackage.defaultFor(modelId:)` non-optional.
            var supplyChainPlan: SupplyChainPlan = SupplyChainPlan.plan(
                modelId: "unknown",
                variantId: "unknown",
                colorId: "unknown",
                interiorStyle: .classic,
                deliveryWindow: .standard
            )
            /// Accessory ids the visitor toggled on during `.pickAccessories`.
            /// Defaulted to empty so predating tests keep compiling.
            var selectedAccessoryIds: Set<String> = []
            /// Handover method chosen by the visitor at `.pickHandover`.
            /// Defaulted to `.homeDelivery` so predating tests keep compiling.
            var handover: HandoverMethod = .homeDelivery
        }

        /// Advances the step machine forward one step from the current state,
        /// using `selection` to supply the minimal user inputs required.
        ///
        /// Encodes the same transition logic as the SwiftUI onContinue / onSelect
        /// closures, so a test that breaks when the machine grows proves that the
        /// production sequence is what it claims to be.
        ///
        /// Returns `nil` when the step does not have a forward transition
        /// (e.g. `.success`, or steps that require catalog data not available here).
        ///
        /// Access: `internal` — available to `@testable import` in the test target.
        /// Not wider than `internal` per F5.2 constraints.
        func advance(using selection: ForwardSelection) -> Step? {
            switch self {
            case .showEditionSummary(let edition, let model, let variant):
                return .pickColor(edition, model, variant)
            case .pickColor(let edition, let model, let variant):
                return .pickInteriorStyle(edition, model, variant, selection.color)
            case .pickInteriorStyle(let edition, let model, let variant, let color):
                // Advances to pickAccessories with an empty accessory list;
                // the real accessor list comes from catalog state at the call site.
                return .pickAccessories(edition, model, variant, color,
                                        selection.interiorStyle, [])
            case .pickAccessories(let edition, let model, let variant, let color, let style, _):
                // Advances to pickHandover, passing the visitor's selected ids.
                // pickHandover MUST come before supplyChainPlanning — the plan reads
                // the handover choice from the transition.
                return .pickHandover(edition, model, variant, color, style,
                                     selection.selectedAccessoryIds)
            case .pickHandover(let edition, let model, let variant, let color, let style, let accessoryIds):
                // Advances to supplyChainPlanning, carrying the handover choice.
                return .supplyChainPlanning(edition, model, variant, color, style,
                                            accessoryIds, selection.handover)
            case .supplyChainPlanning(let edition, let model, let variant, let color, let style, _, _):
                // Auto-advance — no visitor tap. See `isAutoAdvancing`.
                return .deliveryProposal(edition, model, variant, color, style,
                                         selection.supplyChainPlan)
            case .deliveryProposal(let edition, let model, let variant, let color, let style, _):
                return .confirm(edition, model, variant, color, style,
                                selection.selectedAccessoryIds, selection.handover)
            case .confirm:
                return .success(selection.reservationResponse)

            // Steps with no forward transition through this seam. Spelled out
            // rather than collapsed into `default:` (review cycle 1, S2): the
            // ordering `.pickAccessories -> .pickHandover -> .supplyChainPlanning`
            // is load-bearing, and with a `default:` arm a step inserted into that
            // chain would silently return nil here instead of failing to compile.
            // The step machine grew twice in two days; this switch should notice.
            case .pickCategory, .pickModel, .pickVariant:
                // Need catalog data this seam does not carry. Driven by the view.
                return nil
            case .success:
                // Terminal.
                return nil
            }
        }

        /// True for steps that advance themselves without visitor input.
        ///
        /// The tap budget counts *visitor actions*, not transitions, so the
        /// budget test subtracts these. Keeping the distinction in the production
        /// type rather than in the test means the test cannot drift from the
        /// machine's actual behaviour.
        var isAutoAdvancing: Bool {
            if case .supplyChainPlanning = self { return true }
            return false
        }

        // MARK: - Progress step (Task 1.4)

        /// 1-based progress step for the combined progress bar.
        ///
        /// Rules:
        /// - `.supplyChainPlanning` shares its step with `.deliveryProposal` — it
        ///   auto-advances, so the bar must not tick without the visitor acting.
        /// - `.success` shares its step with `.confirm` — same reasoning.
        ///
        /// **Exhaustive switch with no `default` and no `@unknown default`.**
        /// When a new case is added to `Step`, this switch is a compile error until
        /// an arm is added here. That is the property the spec requires: a new step
        /// is a compile error rather than a silently wrong progress bar.
        /// Progress relative to the step the visitor actually ENTERED at.
        ///
        /// Door B (accepted upgrade offer) opens at `.showEditionSummary`, skipping
        /// category / model / trim — so an absolute `progressStep` over the full machine
        /// both overstates position and inflates the total with three steps that will
        /// never be visited. Entering at step 4 of 10 rendered as "7 of 13 done" the
        /// instant the configurator appeared.
        ///
        /// `entryProgressStep` is clamped to the current step so a visitor who goes back
        /// BELOW the entry point (via "Change vehicle") cannot produce a negative index;
        /// the caller persists the widened entry so the bar does not re-narrow on the way
        /// forward again.
        static func progressReport(step: Step,
                                   entryProgressStep: Int) -> (index: Int, total: Int) {
            let entry = min(max(1, entryProgressStep), step.progressStep)
            return (step.progressStep - entry + 1, progressStepCount - entry + 1)
        }

        /// Report for a visitor who has just entered at `entry` and not yet moved.
        ///
        /// **Not expressible via `progressReport`**, and that is the whole reason this
        /// exists. At `.onAppear` the machine is still on its initial `.pickCategory`
        /// while the entry point is already known to be `.showEditionSummary` — and
        /// `progressReport` clamps `entry` down to the current step, so it would answer
        /// "1 of 10" for a path that has 7 steps. Reporting 10 and then 7 a moment later
        /// is exactly the flicker the seeding was added to remove; the clamp defeated it.
        ///
        /// The clamp is right for `progressReport`, whose job is to stay in range for
        /// whatever step the visitor is on. It is wrong here, where the step has not
        /// caught up with the entry yet.
        static func progressReportAtEntry(_ entry: Int) -> (index: Int, total: Int) {
            let e = min(max(1, entry), progressStepCount)
            return (1, progressStepCount - e + 1)
        }

        /// The entry step for a given door. One definition, so the view cannot presume
        /// one value while the report computes another.
        static func entryStep(hasOfferHandoff: Bool) -> Int {
            hasOfferHandoff ? Kind.showEditionSummary.progressStep
                            : Kind.pickCategory.progressStep
        }

        /// What the view publishes at `.onAppear`.
        ///
        /// Exists so the **choice of formula** is under test, not just the formulae. The
        /// previous fix made both formulae correct and left the view free to call the
        /// wrong one — which is precisely the bug that shipped: `.onAppear` routed through
        /// `progressReport`, whose clamp answered for the still-initial step. A mutation
        /// test confirmed the arithmetic tests could not see that, because they never
        /// asked which function the view used.
        static func progressReportOnAppear(hasOfferHandoff: Bool)
            -> (entry: Int, index: Int, total: Int) {
            let entry = entryStep(hasOfferHandoff: hasOfferHandoff)
            let r = progressReportAtEntry(entry)
            return (entry, r.index, r.total)
        }

        /// A `Step` with its payload removed.
        ///
        /// Exists so progress arithmetic can name a case **without constructing one**.
        /// `progressStepCount` previously fabricated a dummy `ReservationResponse` purely
        /// to read a number off `.success`, and the host needs
        /// `showEditionSummary`'s progress value before any catalog has loaded — at which
        /// point no `CatalogModel` exists to build one with. Fabricating placeholders to
        /// ask a question about a case is what put `"offer-model"` into a reservation
        /// payload once already.
        enum Kind: CaseIterable {
            case pickCategory, pickModel, pickVariant, showEditionSummary
            case pickColor, pickInteriorStyle, pickAccessories, pickHandover
            case supplyChainPlanning, deliveryProposal, confirm, success

            /// 1-based position in the progress bar. **One capsule per screen.**
            ///
            /// Previously `.supplyChainPlanning` shared with `.deliveryProposal` and
            /// `.success` shared with `.confirm`, on the reasoning that an auto-advancing
            /// step must not tick the bar without the visitor acting. **User decision
            /// 2026-08-22 reverses that**: *"i'd rather have the right number of steps
            /// that we don't skip any"*. Four screens collapsing into two capsules means
            /// the visitor passes through screens the bar ignores, which reads as the bar
            /// skipping — and a bar that under-counts the journey is worse than one that
            /// ticks during a six-second animation the visitor is watching anyway.
            ///
            /// Removing the sharing also fixed a latent defect: `onProgressChange` and
            /// `onNavTitleChange` fire from `.onChange(of: step.progressStep)`, so with
            /// shared values neither fired on `.supplyChainPlanning -> .deliveryProposal`
            /// or `.confirm -> .success`. The hosted nav title was stale across both
            /// transitions.
            var progressStep: Int {
                switch self {
                case .pickCategory:        return 1
                case .pickModel:           return 2
                case .pickVariant:         return 3
                case .showEditionSummary:  return 4
                case .pickColor:           return 5
                case .pickInteriorStyle:   return 6
                case .pickAccessories:     return 7
                case .pickHandover:        return 8
                case .supplyChainPlanning: return 9
                case .deliveryProposal:    return 10
                case .confirm:             return 11
                case .success:             return 12
                }
            }
        }

        /// This step's payload-free kind.
        ///
        /// **Exhaustive, no `default`.** This is the compiler guard: adding a case to
        /// `Step` fails to build until it is mapped here, which in turn forces a
        /// `Kind.progressStep` arm.
        var kind: Kind {
            switch self {
            case .pickCategory:        return .pickCategory
            case .pickModel:           return .pickModel
            case .pickVariant:         return .pickVariant
            case .showEditionSummary:  return .showEditionSummary
            case .pickColor:           return .pickColor
            case .pickInteriorStyle:   return .pickInteriorStyle
            case .pickAccessories:     return .pickAccessories
            case .pickHandover:        return .pickHandover
            case .supplyChainPlanning: return .supplyChainPlanning
            case .deliveryProposal:    return .deliveryProposal
            case .confirm:             return .confirm
            case .success:             return .success
            }
        }

        var progressStep: Int { kind.progressStep }

        /// Total distinct progress steps — derived from `Kind`, never a literal, and no
        /// longer needing a fabricated payload to compute.
        static let progressStepCount: Int =
            Kind.allCases.map(\.progressStep).max() ?? 1
    }

    @State private var step: Step = .pickCategory

    /// True once `.task` has decided the entry step.
    ///
    /// `.task` is tied to view lifetime, and in `.hosted` chrome this view sits inside
    /// another presentation — so a sheet opened from a step (the dealer picker on
    /// `.pickHandover`) can make the content disappear and reappear, re-firing `.task`
    /// and clobbering `step` back to the entry state. That reads to the visitor as
    /// "choosing a dealer sent me to the beginning of the flow".
    ///
    /// Entry resolution is a one-shot decision, so it is guarded by a flag rather than
    /// made re-entrant. Loading the catalog stays idempotent and is deliberately NOT
    /// guarded — re-running it is harmless and recovers from a failed first load.
    @State private var hasResolvedEntryStep = false

    /// Progress step the visitor entered the configurator at — 1 on Door A, 4 on an
    /// established Door B. Monotonically widens (never narrows) if they navigate back
    /// past it, so the bar can grow but not shrink.
    ///
    /// **Seeded synchronously on appear, before the catalog loads.** Otherwise the host
    /// renders one bar length, then a second when the first report arrives — the visitor
    /// sees the step count change right after tapping "Configure & order". Door B's entry
    /// is *presumed* from `offerHandoff != nil` rather than waiting for resolution to
    /// confirm it; if resolution then fails and the pickers open, the bar widens through
    /// the same monotone path as "Change vehicle". Presuming the common case and widening
    /// on the rare one is stable where waiting for certainty is not.
    @State private var entryProgressStep: Int = 1

    /// Plan accepted at `deliveryProposal`, retained so the post-order routing
    /// view can explain the chain with the same numbers the visitor was shown.
    /// Nil on a cold path that never ran planning.
    @State private var lastPlan: SupplyChainPlan?

    /// Order id being tracked, set when the visitor taps "Track your order" on
    /// `.success`. Non-nil swaps `.success`'s content for `OrderTrackerView` **in place**.
    ///
    /// Not a `.sheet`: this view already owns one (order routing), it can be hosted three
    /// presentations deep inside `UpgradeFlow`, and adding another presentation here is
    /// exactly what broke the dealer picker. Swapping content adds no presentation level.
    @State private var trackingOrderId: String? = nil
    @State private var showOrderRouting = false

    // MARK: - Catalog state (populated from AcquireCatalogClient; empty when endpoint absent)

    @State private var categories: [CatalogCategory] = []
    @State private var models: [CatalogModel] = []
    @State private var variants: [CatalogVariant] = []
    @State private var colors: [CatalogColor] = []
    /// Accessories loaded from the catalog (or fallback). Filtered per-model before
    /// passing into `.pickAccessories`. Populated in `loadCatalog()`.
    @State private var accessories: [CatalogAccessory] = []

    @State private var catalogLoading: Bool = false
    @State private var catalogError: AcquireError? = nil

    /// Accessory ids the visitor has toggled on during `.pickAccessories`.
    ///
    /// Cleared by `.onAppear { selectedAccessoryIds = [] }` on the `.pickCategory`
    /// case view — entering the category picker restarts the configuration, so the
    /// selection resets. `Step` is not `Equatable`, so `.onChange(of: step)` is not
    /// available and must not be reintroduced without adding that conformance.
    ///
    /// Deliberately NOT cleared on back-navigation that stops short of
    /// `.pickCategory` (e.g. `.pickAccessories` → `.pickInteriorStyle`, or back out
    /// of `.deliveryProposal`): the visitor is amending a configuration, not
    /// starting a new one, and losing their accessory picks there would be a bug.
    /// Dismiss-and-reopen needs no explicit clear — the sheet's view hierarchy is
    /// destroyed, so all `@State` returns to its initial value.
    @State private var selectedAccessoryIds: Set<String> = []

    /// Live-computed plan updated each time `selectedAccessoryIds` changes on
    /// the `.pickAccessories` step.  `nil` until interior style is chosen.
    ///
    /// Populated via `SupplyChainPlan.plan(selectedAccessoryIds:accessories:)`
    /// — the same convenience overload that `.supplyChainPlanning.onComplete` uses —
    /// so the date on the chip is byte-identical to the date the visitor sees on
    /// `.deliveryProposal`.  That equality is guaranteed by construction
    /// (one function, called twice) rather than by two call sites agreeing.
    @State private var provisionalPlan: SupplyChainPlan? = nil

    /// Category id to highlight in `CategoryStep` when the flow was entered from
    /// an offer handoff.  Populated after catalog loads in `.task`.
    @State private var preselectedCategoryId: String? = nil

    /// Model id to highlight in `ModelStep` when the flow was entered from an
    /// offer handoff.  Populated after catalog loads in `.task`.
    @State private var preselectedModelId: String? = nil

    /// Category retained when Door B establishes a configuration from the offer.
    ///
    /// Populated in `.task` alongside the warm-entry `showEditionSummary` step.
    /// Required so `EditionSummaryStep.onBack` and the "Change vehicle" action can
    /// navigate to `.pickVariant(category, model)` without re-searching the catalog.
    /// Nil on Door A (cold entry / Discover handoff) — back-navigation from
    /// `.showEditionSummary` falls back to the `categories` state search.
    @State private var resolvedOfferCategory: CatalogCategory? = nil
    // MARK: - Offer-name -> catalog matching

    /// Lowercased alphanumeric tokens of a name.
    ///
    /// Splits on anything that is not a letter or number, so `"Crestwind 3-Row"`
    /// and `"crestwind-3row"` both contribute `crestwind`.
    static func nameTokens(_ s: String) -> Set<String> {
        Set(
            s.lowercased()
                .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
                .map(String.init)
        )
    }

    /// Matches a tenant offer's free-text model name to a catalog model.
    ///
    /// **Why this is not string equality.** An offer's `modelName` is a tenant
    /// marketing string and a catalog model's `displayName` is lineup-plus-trim.
    /// In staging they are `"Meridian Trailwind 2026"` and `"Trailwind Adventurer"`
    /// — no exact match exists, and the original exact-only implementation therefore
    /// resolved **nothing** for any of the three seeded offers. A visitor who had
    /// just accepted a Trailwind was asked what kind of vehicle they wanted.
    ///
    /// **Why it is safe to loosen.** A token only decides the match if it is
    /// *discriminating* — present in exactly one model across the whole catalog. So
    /// the shared brand word `"Meridian"` decides nothing, and if two models both
    /// carried `"Trailwind"` neither would be chosen. When the offer yields no
    /// discriminating token, or points at more than one model, this returns nil and
    /// the caller falls back to the pickers. Declining is always available; guessing
    /// would put a vehicle the visitor never chose into the reservation payload.
    ///
    /// Deliberately derived from the catalog rather than a hardcoded lineup list, so
    /// a tenant with a different lineup needs no code change here.
    static func matchModel(offerModelName name: String,
                           models: [CatalogModel]) -> CatalogModel? {
        // Exact first — correct, and cheap, when a tenant's names do line up.
        if let m = models.first(where: { $0.displayName == name }) { return m }
        if let m = models.first(where: { $0.modelId == name }) { return m }

        let offerTokens = nameTokens(name)
        guard !offerTokens.isEmpty else { return nil }

        var owners: [String: Set<String>] = [:]
        for m in models {
            for t in nameTokens(m.displayName).union(nameTokens(m.modelId)) {
                owners[t, default: []].insert(m.modelId)
            }
        }

        let candidates = Set(offerTokens.compactMap { t -> String? in
            guard let o = owners[t], o.count == 1 else { return nil }
            return o.first
        })
        guard candidates.count == 1, let id = candidates.first else { return nil }
        return models.first { $0.modelId == id }
    }


    // MARK: - Preselect resolution (Task 5.2)

    /// Resolves an offer's free-text model name against the loaded catalog.
    ///
    /// `internal` and pure (no side effects, no view state) so the matching rule
    /// is exercisable from `@testable import` without a view, a network, or `.task`.
    ///
    /// Matching order:
    ///   1. Exact `displayName` match.
    ///   2. Exact `modelId` match (offers sometimes carry the raw id rather than
    ///      the display name).
    ///   3. `(nil, nil)` — caller renders `.pickCategory` with no preselect.
    ///
    /// - Parameters:
    ///   - offerModelName: the `modelName` field from `ConfiguratorOfferHandoff`,
    ///     or `nil` for cold entry.
    ///   - models: the catalog (or fallback) model list.
    /// - Returns: `(categoryId, modelId)` for the matched model, or `(nil, nil)`.
    static func resolvePreselection(
        offerModelName: String?,
        models: [CatalogModel]
    ) -> (categoryId: String?, modelId: String?) {
        guard let name = offerModelName, !name.isEmpty else {
            return (nil, nil)
        }
        // Shares `matchModel` with `resolveEstablishedConfiguration` so the two
        // cannot disagree about which model an offer names — one of them deciding
        // "Trailwind" while the other declines would highlight one row and
        // establish another.
        guard let match = Self.matchModel(offerModelName: name, models: models) else {
            return (nil, nil)
        }
        return (match.categoryId, match.modelId)
    }

    /// Resolves a fully-establishable configuration from an offer's model name.
    ///
    /// Used by Door B (offer-accepted entry) to arrive at `.showEditionSummary`
    /// carrying **real catalog objects** — not placeholder ids — so the reservation
    /// payload contains valid model, variant, and category identifiers.
    ///
    /// `internal` and pure (no side effects, no view state) so the matching rule
    /// is exercisable from `@testable import` without a view, a network, or `.task`.
    ///
    /// Matching order (mirrors `resolvePreselection`):
    ///   1. Exact `displayName` match.
    ///   2. Exact `modelId` match (offers sometimes carry the raw id).
    ///   3. `nil` — caller falls back to `.pickCategory` via `resolvePreselection`.
    ///
    /// Variant selection: lowest `sortOrder`, with ties broken by `variantId`
    /// ascending so the result is deterministic regardless of catalog fetch order.
    ///
    /// Returns `nil` — never a partial tuple — when the model matches but has no
    /// variants, or when the model's category cannot be resolved.  A real model
    /// paired with a wrong variant is worse than no establishment, because it would
    /// put a valid-looking but incorrect variant into the reservation payload.
    ///
    /// - Parameters:
    ///   - offerModelName: the `modelName` field from `ConfiguratorOfferHandoff`.
    ///   - models: the loaded catalog model list.
    ///   - variants: the loaded catalog variant list.
    ///   - categories: the loaded catalog category list.
    /// - Returns: `(category, model, variant)` when fully resolvable; `nil` otherwise.
    static func resolveEstablishedConfiguration(
        offerModelName: String?,
        models: [CatalogModel],
        variants: [CatalogVariant],
        categories: [CatalogCategory]
    ) -> (category: CatalogCategory, model: CatalogModel, variant: CatalogVariant)? {
        guard let name = offerModelName, !name.isEmpty else { return nil }

        guard let model = matchModel(offerModelName: name, models: models) else {
            return nil
        }

        // Resolve the model's category — nil category means a half-established config.
        guard let category = categories.first(where: { $0.categoryId == model.categoryId }) else {
            return nil
        }

        // Find all variants for this model, sorted by sortOrder ascending,
        // with ties broken by variantId ascending for determinism.
        let modelVariants = variants
            .filter { $0.modelId == model.modelId }
            .sorted {
                let sortA = $0.sortOrder ?? Int.max
                let sortB = $1.sortOrder ?? Int.max
                if sortA != sortB { return sortA < sortB }
                return $0.variantId < $1.variantId
            }

        // No variants → return nil, not a partial tuple.
        guard let lowestSort = modelVariants.first else { return nil }

        // Prefer a trim the offer actually names. "Meridian Crestwind Signature"
        // and a "Signature" variant is not a coincidence — establishing Standard
        // instead would silently downgrade the offer the visitor accepted. Only
        // when exactly one variant is named, so an ambiguous descriptor falls back
        // to the deterministic lowest-sortOrder choice rather than guessing.
        let offerTokens = Self.nameTokens(name)
        let named = modelVariants.filter {
            !Self.nameTokens($0.displayName).isDisjoint(with: offerTokens)
        }
        let bestVariant = named.count == 1 ? named[0] : lowestSort

        return (category: category, model: model, variant: bestVariant)
    }

    /// The Edition resolved from the accepted offer for the given model.
    ///
    /// Uses the offer's edition when present; otherwise falls back to the
    /// per-model default via `IvePackage.defaultFor(modelId:)`.  Never nil.
    private func resolvedEdition(for modelId: String?) -> IvePackage {
        offerHandoff?.edition ?? IvePackage.defaultFor(modelId: modelId)
    }

    // MARK: - Availability (task 5.4)

    /// Loaded once on appear; degrades to all-available on any failure.
    @State private var availability: AvailabilityContract = .allAvailable

    // MARK: - Convenience accessors from config

    private var acquireConfig: AcquireConfig? { session.tenantConfig?.acquire }
    private var currencySymbol: String { acquireConfig?.currencySymbol ?? "$" }
    /// ISO 4217 code, distinct from the glyph. Needed where a currency must be
    /// named rather than rendered (e.g. the deposit on a finance qualification).
    private var currencyCode: String { acquireConfig?.currency ?? "USD" }
    private var vehicleCategoryLabel: String { acquireConfig?.vehicleCategoryLabel ?? "Vehicle" }

    // MARK: - Test-ride handoff

    /// Opening line handed to the agent from the success step.
    ///
    /// Phrased as the customer's own words so the persona prompt decides how to
    /// answer it — the same convention as `UpgradeFlow.bookTestRide`.
    ///
    /// The order number is always available and is the most useful context the
    /// agent can have here; the model name is not. `ReservationResponse` carries
    /// no model, and the step machine has dropped its selections by the time
    /// `.success` renders, so the name comes from the offer handoff when the
    /// visitor arrived warm and is omitted when they did not. Omitting it is
    /// correct: naming the wrong model would be worse than naming none.
    static func testRidePrompt(orderNumber: String, modelName: String?) -> String {
        let opener: String
        if let modelName, !modelName.trimmingCharacters(in: .whitespaces).isEmpty {
            opener = "I've just placed order #\(orderNumber) for the \(modelName)."
        } else {
            opener = "I've just placed order #\(orderNumber)."
        }
        return opener
            + " I'd like to book a test drive before delivery."
            + " What slots are available near me, and what should I bring?"
    }

    // MARK: - Body

    var body: some View {
        chromedContent
            // Every step transition, named. This path had zero instrumentation, which
            // is why diagnosing "the drawer never opened" and "choosing a dealer sent
            // me back to the start" each required reading DynamoDB, Cognito and a
            // simulator plist. One line makes the next one a log capture.
            .onChange(of: step.progressStep) { _, _ in
                NSLog("🛒 CONFIG: step -> %@ (progress %d/%d)",
                      String(describing: step), step.progressStep,
                      Step.progressStepCount)
            }
            .task {
                // Both entry modes load the catalog first — warm entry used to skip
                // this and jump to showEditionSummary with placeholder content, but
                // that prevented the visitor from seeing or changing the base vehicle.
                // Now we always load, then either establish over real catalog objects
                // (Door B) or populate preselect fields for highlighted pickers (fallback).
                await loadCatalog()
                await loadAvailability()
                guard !hasResolvedEntryStep else {
                    NSLog("🛒 CONFIG: .task re-fired; entry step already resolved, leaving step=%@",
                          String(describing: step))
                    return
                }
                hasResolvedEntryStep = true
                if let oh = offerHandoff {
                    // Door B: attempt to fully establish the vehicle from the offer.
                    if let established = ConfiguratorFlow.resolveEstablishedConfiguration(
                        offerModelName: oh.modelName,
                        models: models,
                        variants: variants,
                        categories: categories
                    ) {
                        // Retain the category so back-navigation and "Change vehicle"
                        // can reach .pickVariant(category, model) without re-searching.
                        resolvedOfferCategory = established.category
                        let edition = resolvedEdition(for: established.model.modelId)
                        NSLog("🛒 CONFIG: door B established model=%@ variant=%@ from offer=%@",
                              established.model.modelId, established.variant.variantId,
                              oh.modelName ?? "nil")
                        // Entry was already seeded to this value on appear; assert the
                        // presumption held rather than recomputing it.
                        entryProgressStep = Step.entryStep(hasOfferHandoff: true)
                        step = .showEditionSummary(edition, established.model, established.variant)
                    } else {
                        // Graceful fallback: catalog didn't resolve fully (e.g. new model
                        // not yet seeded).  Open at pickCategory with preselection
                        // highlighting — visitor sees the offer's suggested row but can
                        // confirm or change.  Not an error path; this is the designed
                        // degradation when the catalog is unavailable or out of date.
                        NSLog("🛒 CONFIG: door B could NOT establish from offer=%@ "
                              + "(models=%d) — falling back to pickCategory",
                              oh.modelName ?? "nil", models.count)
                        let resolved = ConfiguratorFlow.resolvePreselection(
                            offerModelName: oh.modelName,
                            models: models
                        )
                        preselectedCategoryId = resolved.categoryId
                        preselectedModelId    = resolved.modelId
                        step = .pickCategory
                    }
                }
            }
    }

    /// The chrome-aware container.
    ///
    /// `.standalone` (default): wraps `innerContent` in a `NavigationStack` with the
    /// nav title and close button — identical to the pre-Group-3 body. Every existing
    /// call site uses this path without any modification.
    ///
    /// `.hosted`: renders `innerContent` directly, with no `NavigationStack`, no title,
    /// and no toolbar. `UpgradeFlow` owns the chrome and wires `navTitle` directly into
    /// its own nav bar.
    ///
    /// The step-content switch is written **once** inside `innerContent`; neither branch
    /// duplicates it. See spec § Risks and tasks.md Group 3 constraint.
    @ViewBuilder
    private var chromedContent: some View {
        switch chrome {
        case .standalone:
            NavigationStack {
                innerContent
                    .navigationTitle(navTitle)
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Close") { dismiss() }
                        }
                    }
            }
        case .hosted:
            innerContent
        }
    }

    /// The shared step content, used by both chrome modes.
    ///
    /// Contains the catalog-loading indicator, `stepContent`, common modifiers
    /// (background, adaptive layout environment, and the progress-change observer).
    /// Written once here; `chromedContent` wraps it conditionally.
    @ViewBuilder
    private var innerContent: some View {
        VStack(spacing: 0) {
            if catalogLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: 2)
                    .tint(theme.primary)
            }
            stepContent
        }
        .background(Color(.systemGroupedBackground).ignoresSafeArea())
        .environment(\.adaptiveLayoutContext, layoutContext)
        // Fire onProgressChange (and onNavTitleChange for hosted chrome) whenever
        // the step's 1-based progress value changes. step.progressStep is an Int
        // (Equatable), so SwiftUI re-evaluates it whenever the @State step changes
        // — no Equatable conformance on Step needed.
        .onAppear {
            // Seed and publish before any await, so the host never renders a
            // provisional length. `.onChange` alone cannot do this: it fires only on
            // change, and the initial render is not a change.
            // Single call: the entry value and the published report come from one place,
            // so they cannot disagree. See `progressReportOnAppear`.
            let report = Step.progressReportOnAppear(hasOfferHandoff: offerHandoff != nil)
            entryProgressStep = report.entry
            onProgressChange?(report.index, report.total)
            onNavTitleChange?(navTitle)
        }
        .onChange(of: step.progressStep) { _, newProgressStep in
            // Widen (never narrow) the entry point if the visitor has gone back past it.
            if newProgressStep < entryProgressStep { entryProgressStep = newProgressStep }
            let report = Step.progressReport(step: step,
                                             entryProgressStep: entryProgressStep)
            onProgressChange?(report.index, report.total)
            onNavTitleChange?(navTitle)
        }
    }

    // MARK: - Step content

    @ViewBuilder
    private var stepContent: some View {
        switch step {

        case .pickCategory:
            CategoryStep(
                categories: categories,
                preselectedCategoryId: preselectedCategoryId ?? handoff?.preferredCategory,
                vehicleCategoryLabel: vehicleCategoryLabel,
                theme: theme
            ) { cat in
                step = .pickModel(cat)
            }
            .onAppear { selectedAccessoryIds = [] }
            .safeAreaInset(edge: .bottom) {
                if let onAskAssistant {
                    assistantPrimedPromptBar(
                        message: "help me pick a vehicle for daily driving",
                        onAsk: onAskAssistant
                    )
                }
            }

        case .pickModel(let cat):
            ModelStep(
                category: cat,
                models: models,
                preselectedModelId: preselectedModelId ?? handoff?.preferredModelId,
                currencySymbol: currencySymbol,
                assetBaseUrl: acquireConfig?.assetBaseUrl,
                theme: theme,
                onSelect: { model in
                    step = .pickVariant(cat, model)
                },
                onBack: {
                    step = .pickCategory
                }
            )

        case .pickVariant(let cat, let model):
            VariantStep(
                model: model,
                variants: variants,
                basePrice: model.basePrice,
                currencySymbol: currencySymbol,
                theme: theme,
                onSelect: { variant in
                    let edition = resolvedEdition(for: model.modelId)
                    step = .showEditionSummary(edition, model, variant)
                },
                onBack: {
                    step = .pickModel(cat)
                }
            )
            .safeAreaInset(edge: .bottom) {
                if let onAskAssistant {
                    assistantPrimedPromptBar(
                        message: "compare Sport and Long-Range for me",
                        onAsk: onAskAssistant
                    )
                }
            }

        case .showEditionSummary(let edition, let model, let variant):
            // Tap 1 of N from offer-accepted to order-placed.
            EditionSummaryStep(
                edition: edition,
                model: model,
                theme: theme,
                onContinue: {
                    step = .pickColor(edition, model, variant)
                },
                onBack: {
                    // Back to pickVariant — works for both cold and warm entry.
                    // Prefer the retained category from Door B resolution; fall
                    // back to searching the loaded catalog when absent (cold entry).
                    let cat = resolvedOfferCategory
                        ?? categories.first(where: { $0.categoryId == model.categoryId })
                    if let cat {
                        step = .pickVariant(cat, model)
                    } else {
                        step = .pickCategory
                    }
                },
                // "Change vehicle" is wired only on Door B (offer handoff present).
                // Nil on Door A — the button is absent and Door A's layout is untouched.
                // Navigates to .pickCategory so the visitor can pick a different
                // model entirely; pickers are one tap away.
                onChangeVehicle: offerHandoff != nil ? {
                    step = .pickCategory
                } : nil,
                // Prefer the offer's marketing name — it carries the model year, and the
                // Trailwind has two generations bundled, so the catalog displayName alone
                // could illustrate a NEW configuration with the outgoing car. Falls back
                // to the catalog name, where a nil year correctly yields current-generation.
                vehicleImageName: VehicleSweepView.bundledStaticImageName(
                    forOfferModelName: offerHandoff?.modelName
                ) ?? VehicleSweepView.bundledStaticImageName(
                    make: "Meridian", model: model.displayName
                )
            )

        case .pickColor(let edition, let model, let variant):
            // Tap 2 of 5.
            ColorStep(
                variant: variant,
                colors: colors,
                basePrice: (model.basePrice ?? 0) + (variant.priceAdder ?? 0),
                currencySymbol: currencySymbol,
                theme: theme,
                onSelect: { color in
                    step = .pickInteriorStyle(edition, model, variant, color)
                },
                onBack: {
                    step = .showEditionSummary(edition, model, variant)
                }
            )

        case .pickInteriorStyle(let edition, let model, let variant, let color):
            // Tap 3 of N — now leads to accessories picker.
            InteriorStyleStep(
                availability: availability,
                theme: theme,
                onSelect: { style in
                    step = .pickAccessories(edition, model, variant, color, style,
                                            accessoriesFor(categoryId: model.categoryId))
                },
                onBack: {
                    step = .pickColor(edition, model, variant)
                }
            )

        // MARK: Task 4.2 + 5.1: AccessoriesStep with live delivery estimate chip
        case .pickAccessories(let edition, let model, let variant, let color, let style, let accList):
            AccessoriesStep(
                categoryId: model.categoryId,
                accessories: accList,
                currencySymbol: currencySymbol,
                theme: theme,
                selectedAccessoryIds: $selectedAccessoryIds,
                onContinue: {
                    step = .pickHandover(edition, model, variant, color, style,
                                         selectedAccessoryIds)
                },
                onBack: {
                    step = .pickInteriorStyle(edition, model, variant, color)
                }
            )
            // Live delivery estimate chip — stays visible while the visitor toggles.
            // Note: the estimate here uses .homeDelivery default because handover is
            // not yet chosen. The estimate legitimately moves once handover is chosen
            // on the next step; byte-identity is guaranteed between the LAST chip the
            // visitor saw (post-handover, on the proposal) and the proposal itself.
            .safeAreaInset(edge: .top) {
                if let plan = provisionalPlan {
                    HStack(spacing: 8) {
                        Image(systemName: "calendar.badge.clock")
                            .foregroundStyle(theme.primary)
                            .font(.caption.bold())
                        VStack(alignment: .leading, spacing: 1) {
                            Text("Estimated delivery: \(plan.formattedDeliveryDate())")
                                .font(.caption.bold())
                                .foregroundStyle(theme.primary)
                            Text("Updates as you add options")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(.ultraThinMaterial)
                    .transition(.opacity.combined(with: .move(edge: .top)))
                    .accessibilityLabel("Estimated delivery: \(plan.formattedDeliveryDate()). Updates as you add options.")
                }
            }
            .onChange(of: selectedAccessoryIds) { _, newIds in
                provisionalPlan = provisionalPlanFor(
                    model: model, variant: variant, color: color,
                    style: style, selectedIds: newIds, accList: accList
                )
            }
            .onAppear {
                provisionalPlan = provisionalPlanFor(
                    model: model, variant: variant, color: color,
                    style: style, selectedIds: selectedAccessoryIds, accList: accList
                )
            }

        case .pickHandover(let edition, let model, let variant, let color, let style, let accessoryIds):
            HandoverStep(
                driverId: session.vehicleContext?.driver?.driverId,
                theme: theme,
                onSelect: { method in
                    step = .supplyChainPlanning(edition, model, variant, color, style,
                                                accessoryIds, method)
                },
                onBack: {
                    step = .pickAccessories(edition, model, variant, color, style,
                                            accessoriesFor(categoryId: model.categoryId))
                }
            )

        case .supplyChainPlanning(let edition, let model, let variant, let color, let style, let accessoryIds, let handoverMethod):
            // No tap — auto-advances when the planning sequence completes.
            SupplyChainPlanningView(
                theme: theme,
                onComplete: {
                    // ONE call site for the plan, shared with the chip via
                    // `provisionalPlanFor`. Guarantees the proposal date equals the
                    // last chip date the visitor saw — by construction, not by two
                    // call sites happening to agree.
                    let plan = provisionalPlanFor(
                        model: model, variant: variant, color: color,
                        style: style, selectedIds: accessoryIds,
                        accList: accessoriesFor(categoryId: model.categoryId),
                        handover: handoverMethod
                    )
                    step = .deliveryProposal(edition, model, variant, color, style, plan)
                }
            )

        case .deliveryProposal(let edition, let model, let variant, let color, let style, let plan):
            // Tap 4 of 5.
            DeliveryProposalView(
                plan: plan,
                model: model,
                theme: theme,
                onAccept: {
                    // Retained so the post-order routing view can explain the
                    // chain using the same plan the visitor just accepted.
                    lastPlan = plan
                    // plan.handover carries the method chosen at pickHandover —
                    // forward it to confirm so the reservation payload includes it.
                    step = .confirm(edition, model, variant, color, style,
                                    selectedAccessoryIds, plan.handover)
                },
                onBack: {
                    // Skips `.pickAccessories` deliberately: back from a planned
                    // result returns to the last *input* the visitor chose. The
                    // accessory selection is preserved in `selectedAccessoryIds`
                    // (only `.pickCategory` clears it), so re-advancing shows the
                    // same picks. Review cycle 1, S1.
                    step = .pickInteriorStyle(edition, model, variant, color)
                }
            )

        case .confirm(let edition, let model, let variant, let color, let style, let confirmAccessoryIds, let handoverMethod):
            // Confirm and submit order.
            LightConfirmStep(
                edition: edition,
                model: model,
                variant: variant,
                color: color,
                interiorStyle: style,
                currencySymbol: currencySymbol,
                tenantId: session.activeTenantId,
                discoverSessionId: handoff?.discoverSessionId,
                leadId: handoff?.leadId,
                firstName: offerHandoff?.firstName,
                selectedAccessoryIds: confirmAccessoryIds.sorted(),
                handoverMethod: handoverMethod,
                theme: theme,
                onSuccess: { resp in
                    step = .success(resp)
                },
                onBack: {
                    // Skips `.pickAccessories` deliberately: back from a planned
                    // result returns to the last *input* the visitor chose. The
                    // accessory selection is preserved in `selectedAccessoryIds`
                    // (only `.pickCategory` clears it), so re-advancing shows the
                    // same picks. Review cycle 1, S1.
                    step = .pickInteriorStyle(edition, model, variant, color)
                }
            )

        case .success(let resp):
            if let trackingOrderId {
                OrderTrackerView(
                    orderId: trackingOrderId,
                    theme: theme,
                    initialStage: .orderPlaced,
                    // The plant the build was assigned to during planning. `lastPlan` is
                    // retained from `.deliveryProposal` for exactly this kind of
                    // post-order narration.
                    facility: lastPlan?.facility,
                    // This flow already supplies navigation chrome in both chrome modes.
                    showsOwnNavigationStack: false
                )
                .safeAreaInset(edge: .bottom) {
                    Button {
                        self.trackingOrderId = nil
                    } label: {
                        Text("Back to your order")
                            .frame(maxWidth: .infinity, minHeight: 32)
                    }
                    .buttonStyle(.bordered)
                    .tint(theme.primary)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
                }
            } else {
            SuccessStep(
                reservation: resp,
                vehicleCategoryLabel: vehicleCategoryLabel,
                theme: theme,
                // Was `{ _ in dismiss() }` — the orderId was discarded and the button
                // merely closed the flow, so "Track your order" went nowhere while a
                // complete 384-line `OrderTrackerView` sat with zero call sites.
                onOpenOrderTracker: { orderId in
                    NSLog("🛒 CONFIG: open order tracker (order=%@ facility=%@)",
                          orderId, lastPlan?.facility.facilityId ?? "unknown")
                    trackingOrderId = orderId
                },
                // Nil when no assistant is plumbed, which HIDES the button rather than
                // having it dismiss the flow. See `SuccessStep.onBookTestRide`.
                onBookTestRide: onAskAssistant == nil ? nil : {
                    // A test ride is a sales action, and the agent already holds
                    // the `book` tool for this persona — so this hands off to the
                    // agent rather than opening `BookingFlow`, which is the
                    // SERVICE-appointment sheet. Same reasoning as
                    // `UpgradeFlow.testRideRow`: a second booking path would
                    // have to be kept in step with the first.
                    //
                    // Uses `onAskAssistant` rather than
                    // `AppSession.pendingDiscoverPrompt` on purpose. This flow is
                    // presented from two places that dismiss to DIFFERENT views,
                    // so a session-channel handoff would depend on which view
                    // happens to be underneath — exactly the coupling that
                    // stranded the upgrade handoff. The assistant cover is
                    // presented from `MainTabView` and needs nothing from us.
                    //
                    // Deliberately does NOT dismiss first: the primed-prompt bars
                    // elsewhere in this flow already open the assistant on top of
                    // the current step, and closing it returns the customer to
                    // their confirmation with "Track your order" still reachable.
                    guard let onAskAssistant else { return }
                    let line = Self.testRidePrompt(orderNumber: resp.orderNumber,
                                                   modelName: offerHandoff?.modelName)
                    NSLog("🛒 CFG: test-drive handoff (order=%@)", resp.orderNumber)
                    onAskAssistant(line)
                },
                onDone: { dismiss() }
            )
            // Opt-in rather than automatic: a visitor in a hurry takes the
            // confirmation and leaves; one who asks "what happens now?" gets the
            // operational chain. Only offered when a plan exists — on a cold path
            // that skipped planning there is nothing honest to show.
            .safeAreaInset(edge: .bottom) {
                if lastPlan != nil {
                    Button {
                        showOrderRouting = true
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                                .font(.caption.bold())
                            Text("See what happens next")
                                .font(.caption.bold())
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.right")
                                .font(.caption2)
                        }
                        .foregroundStyle(theme.primary)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(.ultraThinMaterial)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("See what happens next with your order")
                }
            }
            .sheet(isPresented: $showOrderRouting) {
                if let plan = lastPlan {
                    OrderRoutingView(
                        plan: plan,
                        orderNumber: resp.orderNumber,
                        theme: theme,
                        onDone: { showOrderRouting = false }
                    )
                }
            }
            }   // else (trackingOrderId == nil)
        }
    }

    // MARK: - Primed-prompt assistant bar

    /// Thin bar that surfaces one primed prompt chip per step.
    /// Tapping fires `onAsk` so the assistant opens with a focused seed message
    /// without the user having to type it. Pattern mirrors `onAskAboutDtc` /
    /// `onBookService` in MainTabView: the callback is lifted to the call site
    /// (ConfiguratorFlow → MainTabView) so no assistant plumbing is duplicated.
    @ViewBuilder
    private func assistantPrimedPromptBar(message: String, onAsk: @escaping (String) -> Void) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkles")
                .foregroundStyle(theme.primary)
                .font(.caption.bold())
            Button {
                onAsk(message)
            } label: {
                Text("\"\(message)\"")
                    .font(.caption.bold())
                    .foregroundStyle(theme.primary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .buttonStyle(.plain)
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .foregroundStyle(theme.primary.opacity(0.5))
                .font(.caption2)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .accessibilityLabel("Ask assistant: \(message)")
    }

    // MARK: - Navigation title

    /// Navigation title for the current step.
    ///
    /// `internal` (not `private`) so the hosted chrome path (`UpgradeFlow`) can
    /// read it and display it in its own nav bar, keeping the single nav bar in
    /// sync with the inner step.
    var navTitle: String {
        switch step {
        case .pickCategory:          return "Choose category"
        case .pickModel:             return "Choose model"
        case .pickVariant:           return "Choose trim"
        case .showEditionSummary:    return "Your Edition"
        case .pickColor:             return "Choose colour"
        case .pickInteriorStyle:     return "Interior style"
        case .pickAccessories:       return "Add accessories"
        case .pickHandover:          return "Delivery method"
        case .supplyChainPlanning:   return "Planning your build"
        case .deliveryProposal:      return "Estimated delivery"
        case .confirm:               return "Confirm order"
        case .success:               return "Order placed"
        }
    }

    // MARK: - Delivery-estimate derivation (single call site)

    /// Accessories visible for a category: universal ones plus that category's own.
    ///
    /// One definition, used both to build the `.pickAccessories` case payload and to
    /// derive the plan, so the filtered list cannot diverge between the two.
    private func accessoriesFor(categoryId: String?) -> [CatalogAccessory] {
        accessories.filter { $0.categoryId == nil || $0.categoryId == categoryId }
    }

    /// The single place a delivery estimate is derived in this view.
    ///
    /// Spec § "Live-updating delivery estimate" requires the date on
    /// `.deliveryProposal` to be identical to the last chip the visitor saw while
    /// picking. Routing the chip's baseline compute, its per-toggle recompute, and
    /// the planning step's final derivation through this one function makes that
    /// identity structural rather than a property three call sites must maintain.
    ///
    /// It also fixes a live asymmetry found in review cycle 1: the planning step was
    /// passing the **unfiltered** `accessories` state while the chip passed the
    /// category-filtered list. Those agreed only because the lead-day map is keyed by
    /// id and the selected ids are necessarily a subset of the filtered list — true
    /// today, but a chain of reasoning rather than a guarantee, and it would break the
    /// moment two categories shared an accessory id with different `leadDays`.
    private func provisionalPlanFor(
        model: CatalogModel,
        variant: CatalogVariant,
        color: CatalogColor,
        style: InteriorStyle,
        selectedIds: Set<String>,
        accList: [CatalogAccessory],
        handover: HandoverMethod = .homeDelivery
    ) -> SupplyChainPlan {
        SupplyChainPlan.plan(
            modelId: model.modelId,
            variantId: variant.variantId,
            colorId: color.colorId,
            interiorStyle: style,
            deliveryWindow: availability.deliveryWindow,
            selectedAccessoryIds: selectedIds,
            accessories: accList,
            handover: handover
        )
    }

    // MARK: - Catalog load

    /// Thin caller: gathers `isSignedIn` + a fetch `Result`, delegates to the pure
    /// `CatalogFallback.resolve(isSignedIn:fetchResult:)` policy, assigns the four
    /// catalog arrays, and logs the reason when degradation is engaged.
    ///
    /// The branching logic lives entirely in `CatalogFallback.resolve` — this method
    /// has no `if/guard/catch` of its own beyond the fetch attempt.  That separation
    /// is what makes the policy unit-testable without a view, a network, or a simulator.
    private func loadCatalog() async {
        catalogLoading = true
        defer { catalogLoading = false }

        // Determine auth state once; the policy key is boolean isSignedIn.
        let isSignedIn: Bool
        var token: String? = nil
        if case .signedIn(let t, _) = session.authState {
            isSignedIn = true
            token = t
        } else {
            isSignedIn = false
        }

        // Attempt a live fetch only when we have a token.
        var fetchResult: Result<CatalogResponse, AcquireError>? = nil
        if let token {
            let client = AcquireCatalogClient(idTokenProvider: { token })
            do {
                let resp = try await client.fetchCatalog(tenantId: session.activeTenantId)
                fetchResult = .success(resp)
            } catch let err as AcquireError where err.isEndpointUnavailable {
                // Keep the error observable for operators; degradation handles the UX.
                catalogError = err
                fetchResult = .failure(err)
            } catch {
                let acquireErr = AcquireError.network(error)
                catalogError = acquireErr
                fetchResult = .failure(acquireErr)
            }
        }

        // Delegate to the pure policy — returns (response, source).
        let (resp, source) = CatalogFallback.resolve(
            isSignedIn: isSignedIn,
            fetchResult: fetchResult
        )

        // Log when fallback is engaged so bug reports are greppable.
        if case .fallback(let reason) = source {
            NSLog("ConfiguratorFlow: catalog fallback engaged (%@)", reason)
        }

        await MainActor.run {
            categories  = resp.categories
            models      = resp.models
            variants    = resp.variants
            colors      = resp.colors
            accessories = resp.accessories
        }
    }

    // MARK: - Availability load (task 5.4)

    /// Loads option availability from the local fixture behind `AvailabilityContract`.
    /// Degrades to all-available on any failure — failing closed would grey out the
    /// whole configurator on the show floor.
    private func loadAvailability() async {
        availability = AvailabilityContract.loadFixture() ?? .allAvailable
    }
}
