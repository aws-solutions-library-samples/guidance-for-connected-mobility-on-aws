import Foundation

// MARK: - HandoverMethod

/// The final-leg delivery method chosen by the visitor.
///
/// Named to avoid collision with `AvailabilityContract.DeliveryWindow`, which is a
/// **production slot** (standard/custom) — a different axis entirely.
///
/// ## Seed rule
/// `seedToken` is appended to the FNV-1a seed unconditionally. `centerId` is
/// deliberately **excluded** from the seed: switching dealers does not change
/// the manufacturing schedule, so it must never move the manufacturing date.
enum HandoverMethod: Equatable {
    /// Collect at the driver's preferred dealer. The vehicle already ships there;
    /// no additional final-leg transit is incurred.
    case dealerPickup(centerId: String, name: String)
    /// Final-mile delivery to the driver's address.
    case homeDelivery

    /// Token appended to the FNV-1a seed to distinguish the two handover paths.
    ///
    /// Does NOT include `centerId` — see the seed rule above.
    var seedToken: String {
        switch self {
        case .dealerPickup: return "pickup"
        case .homeDelivery:  return "delivery"
        }
    }
}

// MARK: - SupplyChainPlan

/// Client-side model for the supply-chain planning narrative shown between
/// configuration and order confirmation.
///
/// ## ⚠️ THIS IS A NARRATIVE SURFACE — IT CALLS NOTHING
///
/// Nothing in this file contacts Amazon Connect Decisions, AWS Supply Chain, or
/// any other service. The planning sequence is a **client-side transition** and
/// the delivery date is **derived locally from the chosen configuration**. This
/// is deliberate and is recorded as such in the Zone 2 design:
/// `cvx/docs/REINVENT-2026-ZONE2-WHAT-WE-DELIVER-REV3.md` § "What is real, and
/// what is narrative" states the supply-chain animation "is a client-side
/// transition; it drives nothing", and that "inventory and lead times are
/// curated demo data".
///
/// Two rules follow, and both are load-bearing rather than stylistic:
///
/// 1. **Never present this as live supply-chain data to a visitor.** The UI
///    copy must attribute the *pattern* to Amazon Connect Decisions without
///    claiming a live query happened. `attributionNote` carries the wording the
///    UI is expected to show; it is test-asserted so it cannot quietly drift
///    into an overclaim.
/// 2. **Swapping in the real service must be a data-source change, not a UI
///    change.** `SupplyChainPlan.plan(for:)` is the single seam. Replace its
///    body with a network call and every view above it is unaffected — the same
///    discipline `AvailabilityContract` follows for option gating.
///
/// This matters beyond tidiness: this portfolio has already shipped an
/// Implementation Guide callout citing a `lambdas/proactive/` directory that
/// does not exist. An overclaim in a demo surface becomes an overclaim in a
/// customer conversation.
///
/// ## Why the derivation is deterministic
///
/// The same configuration must always yield the same delivery date. A random
/// date would make snapshot baselines flaky and, worse, would let a presenter
/// re-run the same demo and get a different answer in front of an audience.
/// `deterministicSeed(for:)` uses FNV-1a rather than Swift's `hashValue`, which
/// is randomly seeded per process and therefore differs between launches.
struct SupplyChainPlan: Equatable {

    // MARK: - Planning steps (the animated sequence)

    /// One line in the planning animation. Ordered; rendered sequentially.
    ///
    /// Phrased as what an Amazon Connect Decisions AI teammate would be doing —
    /// the product is described by AWS as generating "constraint-aware supply
    /// plans" via "AI teammates", so the vocabulary is the product's own rather
    /// than invented.
    enum PlanningStep: String, CaseIterable, Identifiable {
        case contacting        = "Contacting Amazon Connect Decisions"
        case parts             = "Identifying available parts"
        case stockBuilds       = "Identifying available stock builds"
        case manufacturingTime = "Calculating manufacturing time"
        case facility          = "Selecting best manufacturing facility"
        /// The final leg to the customer. Added 2026-08-21 with the handover
        /// choice: the sequence previously ended at the plant, which left the
        /// journey one leg short of the question the customer actually has.
        case finalLeg          = "Routing the final delivery leg"

        var id: String { rawValue }

        /// SF Symbol shown beside the step.
        var symbolName: String {
            switch self {
            case .contacting:        return "antenna.radiowaves.left.and.right"
            case .parts:             return "shippingbox"
            case .stockBuilds:       return "square.stack.3d.up"
            case .manufacturingTime: return "clock.arrow.circlepath"
            case .facility:          return "building.2"
            case .finalLeg:          return "arrow.triangle.turn.up.right.diamond"
            }
        }

        /// Dwell time before this step marks complete and the next begins.
        ///
        /// Total is deliberately ~5.2s. The Zone 2 journey budget is 3 minutes at
        /// 60–80 visitors/hour, so the sequence has to read as substantial without
        /// becoming the reason a queue forms.
        var dwellSeconds: Double {
            switch self {
            case .contacting:        return 1.1
            case .parts:             return 1.0
            case .stockBuilds:       return 1.0
            case .manufacturingTime: return 1.1
            case .facility:          return 1.0
            // 1.0 brings the total to 6.2s, inside the 8.0s bound that
            // `testTotalDwellStaysWithinJourneyBudget` pins against the Zone 2
            // 3-minute journey budget.
            case .finalLeg:          return 1.0
            }
        }
    }

    /// Total dwell for the whole sequence, for callers that need to size a timeout.
    static var totalDwellSeconds: Double {
        PlanningStep.allCases.reduce(0) { $0 + $1.dwellSeconds }
    }

    // MARK: - Manufacturing facility

    struct Facility: Equatable {
        let facilityId: String
        let displayName: String
        /// Region label shown to the visitor. Deliberately generic — see the
        /// naming constraint below.
        let regionLabel: String
        /// Why this facility was selected, phrased as a planning rationale.
        let selectionRationale: String
        /// Invented plant coordinates, for the order-tracker map.
        ///
        /// **These are deliberately open rural points, not real plant sites.** The naming
        /// constraint below forbids a real OEM plant name or locality because those are one
        /// step removed from identifying a real customer — a map pin is a *locality*, so it
        /// carries exactly the same risk as the label. Chosen to sit in the right region for
        /// `regionLabel` and nowhere near a real automotive plant.
        let latitude: Double
        let longitude: Double
    }

    /// Fictional Meridian facilities.
    ///
    /// **Naming constraint**: these are invented and navigation/nature-themed to
    /// match the fictional Meridian lineup (Windrose / Trailwind / Crestwind /
    /// Azimuth). Do NOT substitute a real OEM plant name or a real
    /// plant locality — the portfolio carries an open row (`Rebrand regional
    /// residual`) precisely because region and plant labels are one step removed
    /// from identifying a real customer.
    static let facilities: [Facility] = [
        Facility(
            facilityId: "aurora",
            displayName: "Meridian Aurora Plant",
            regionLabel: "Northern assembly region",
            selectionRationale: "Shortest queue for your trim",
            latitude: 46.95, longitude: -102.40
        ),
        Facility(
            facilityId: "cascade",
            displayName: "Meridian Cascade Works",
            regionLabel: "Western assembly region",
            selectionRationale: "Holds the interior trim in stock, avoiding a supplier wait",
            latitude: 44.05, longitude: -121.95
        ),
        Facility(
            facilityId: "solstice",
            displayName: "Meridian Solstice Assembly",
            regionLabel: "Central assembly region",
            selectionRationale: "Existing stock build matches your configuration most closely",
            latitude: 39.10, longitude: -96.60
        )
    ]

    // MARK: - Plan fields

    /// Facility selected for this build.
    let facility: Facility
    /// Estimated build duration in days, before transit.
    ///
    /// This leg covers the factory build — from order slot to vehicle-complete,
    /// before it leaves the plant.
    let manufacturingDays: Int
    /// Estimated transit duration in days: factory → dealer.
    ///
    /// This leg is incurred for **both** handover methods — the vehicle ships to
    /// the dealer regardless of whether the customer picks it up there or has it
    /// delivered onward.
    let transitDays: Int
    /// Final-leg days: dealer → customer (home delivery) or 0 (dealer pickup).
    ///
    /// - `.dealerPickup`: 0 — the vehicle is already at the dealer.
    /// - `.homeDelivery`: 2–4 days, deterministically derived from the seed.
    let finalLegDays: Int
    /// Handover method chosen for this plan.
    let handover: HandoverMethod
    /// The proposed delivery date. Derived, never fetched.
    let proposedDeliveryDate: Date
    /// Whether a matching stock build was found (shortens the timeline).
    let matchedStockBuild: Bool
    /// Number of long-lead parts flagged during planning.
    let longLeadPartCount: Int
    /// Delivery window this plan assumes.
    let deliveryWindow: AvailabilityContract.DeliveryWindow

    /// The attribution wording the UI must show.
    ///
    /// Test-asserted so it cannot drift into claiming a live query. It credits
    /// the pattern without asserting a call happened.
    static let attributionNote =
        "Planning pattern powered by Amazon Connect Decisions. "
        + "Dates and facility selection are demo data for this experience."

    // MARK: - Derived display helpers

    /// Total days from order to dealer (manufacturing + transit: factory → dealer).
    ///
    /// **Note**: this covers factory → dealer only. For the full factory → customer
    /// duration, add `finalLegDays`. `proposedDeliveryDate` reflects all three legs.
    ///
    /// - `manufacturingDays`: order slot → vehicle-complete at plant.
    /// - `transitDays`: plant → dealer (incurred for both pickup and home delivery).
    /// - `finalLegDays`: dealer → customer address. Zero for pickup.
    ///
    /// All three legs, because this is what `weeksRangeLabel` and the proposal's
    /// total-days row are derived from, and both sit on the same screen as
    /// `proposedDeliveryDate` — which has always included every leg. Excluding the
    /// final leg here made those two figures disagree by 2-4 days on any home
    /// delivery.
    var totalDays: Int { manufacturingDays + transitDays + finalLegDays }

    /// Formatted delivery date, e.g. "12 October 2026".
    func formattedDeliveryDate(locale: Locale = .current) -> String {
        let fmt = DateFormatter()
        fmt.locale = locale
        fmt.dateFormat = "d MMMM yyyy"
        return fmt.string(from: proposedDeliveryDate)
    }

    /// Human range label, e.g. "6–8 weeks".
    var weeksRangeLabel: String {
        let weeks = max(1, Int((Double(totalDays) / 7.0).rounded()))
        return "\(weeks)–\(weeks + 2) weeks"
    }

    // MARK: - The single data-source seam

    /// Produces a plan for the given configuration.
    ///
    /// **Replace this body with a network call when the Amazon Connect Decisions
    /// integration exists.** Every view above this function reads `SupplyChainPlan`
    /// and is unaffected by where the data came from — that is the whole reason
    /// this indirection exists.
    ///
    /// - Parameters:
    ///   - modelId: chosen model identifier.
    ///   - variantId: chosen trim identifier.
    ///   - colorId: chosen exterior colour identifier.
    ///   - interiorStyle: chosen interior style.
    ///   - deliveryWindow: standard or custom, from `AvailabilityContract`.
    ///   - selectedAccessoryIds: accessory ids the visitor has toggled on. Sorted
    ///     before joining into the deterministic seed so `["b","a"]` and `["a","b"]`
    ///     produce the same date. Defaults to `[]` so existing callers compile unchanged.
    ///   - accessoryLeadDays: map from accessory id → additional manufacturing lead days.
    ///     Each selected accessory's days are summed into `mfgDays` AFTER the matched-stock
    ///     divide and long-lead multiplier, so accessories always move the date forward and
    ///     are never divided out by the stock-build shortcut. Fitting a tow package to a
    ///     rolling stock build still costs those days. Defaults to `[:]`.
    ///   - handover: how the vehicle reaches the customer. Defaults to `.homeDelivery`.
    ///     The default is `.homeDelivery` — NOT `.dealerPickup` — so that the seed token
    ///     appended here is consistent with what callers that do not yet pass `handover`
    ///     will receive once they are updated. `centerId` is never included in the seed:
    ///     choosing a different dealer must not silently move the manufacturing date.
    ///   - now: injectable clock, so tests are not date-dependent.
    static func plan(
        modelId: String,
        variantId: String,
        colorId: String,
        interiorStyle: InteriorStyle,
        deliveryWindow: AvailabilityContract.DeliveryWindow,
        selectedAccessoryIds: [String] = [],
        accessoryLeadDays: [String: Int] = [:],
        handover: HandoverMethod = .homeDelivery,
        now: Date = Date()
    ) -> SupplyChainPlan {
        // [DEMO DERIVATION — NOT A SUPPLY-CHAIN QUERY]
        // The accessory element is appended only when the selection is non-empty.
        // An empty selection must reproduce the pre-delta date exactly (back-compat
        // for existing callers that never pass accessories) — appending "" would
        // change the FNV-1a input and alter all pre-existing derived dates.
        //
        // handover.seedToken does NOT enter the main seed (which drives facility,
        // mfgDays, and transitDays). Those fields represent the factory→dealer
        // journey, which is identical regardless of how the customer receives the
        // vehicle. Only finalLegDays uses the handover token, via a separate seed,
        // so that choosing delivery vs pickup deterministically changes the date
        // without scrambling the manufacturing/transit derivation. centerId is also
        // never in any seed: switching dealers must not move the manufacturing date.
        var seedComponents = [modelId, variantId, colorId, interiorStyle.rawValue, deliveryWindow.rawValue]
        if !selectedAccessoryIds.isEmpty {
            seedComponents.append(selectedAccessoryIds.sorted().joined(separator: ","))
        }
        let seed = deterministicSeed(for: seedComponents)

        let facility = facilities[Int(seed % UInt64(facilities.count))]

        // A matching stock build is found for roughly a third of configurations,
        // deterministically per configuration.
        let matchedStock = (seed / 7) % 3 == 0

        // Long-lead parts: 0-2, more likely on a custom window.
        let baseLongLead = Int((seed / 13) % 3)
        let longLead = deliveryWindow == .custom ? baseLongLead : max(0, baseLongLead - 1)

        // Manufacturing days. Standard window builds faster; a matched stock
        // build skips most of the queue; each long-lead part adds time.
        var mfgDays: Int
        switch deliveryWindow {
        case .standard: mfgDays = 28 + Int((seed / 17) % 15)   // 28-42
        case .custom:   mfgDays = 56 + Int((seed / 19) % 29)   // 56-84
        }
        if matchedStock { mfgDays = max(7, mfgDays / 3) }
        mfgDays += longLead * 7

        // Accessory lead days — added AFTER the matched-stock divide and long-lead
        // multiplier so that line-side accessory installation always adds real time,
        // regardless of whether the base build matched stock. Fitting a tow package
        // to a rolling stock build still costs those days.
        let accessoryLeadTotal = selectedAccessoryIds
            .compactMap { accessoryLeadDays[$0] }
            .reduce(0, +)
        mfgDays += accessoryLeadTotal

        let transit = 5 + Int((seed / 23) % 6)                 // 5-10

        // Final-leg days: dealer pickup costs nothing extra (vehicle is already
        // there); home delivery adds 2–4 days deterministically.
        //
        // The final-leg seed includes handover.seedToken (so choosing pickup vs
        // delivery deterministically changes the proposed date) but does NOT include
        // deliveryWindow (the manufacturing schedule is independent of the handover
        // method) or centerId (switching dealers must not move the date).
        let finalLeg: Int
        switch handover {
        case .dealerPickup:
            finalLeg = 0
        case .homeDelivery:
            let finalLegSeed = deterministicSeed(for: [
                modelId, variantId, colorId, interiorStyle.rawValue, handover.seedToken
            ])
            finalLeg = 2 + Int((finalLegSeed / 29) % 3)       // 2-4
        }

        let delivery = Calendar.current.date(
            byAdding: .day, value: mfgDays + transit + finalLeg, to: now
        ) ?? now

        return SupplyChainPlan(
            facility: facility,
            manufacturingDays: mfgDays,
            transitDays: transit,
            finalLegDays: finalLeg,
            handover: handover,
            proposedDeliveryDate: delivery,
            matchedStockBuild: matchedStock,
            longLeadPartCount: longLead,
            deliveryWindow: deliveryWindow
        )
    }

    // MARK: - Deterministic seed

    /// FNV-1a over the joined configuration components.
    ///
    /// Swift's `Hashable.hashValue` is randomly seeded per process, so it is
    /// unusable here: the same configuration would produce a different delivery
    /// date on every app launch, breaking both snapshot baselines and demo
    /// repeatability. FNV-1a is stable across launches, platforms and Swift
    /// versions, which is the only property that matters for this use.
    static func deterministicSeed(for components: [String]) -> UInt64 {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325     // FNV-1a 64-bit offset basis
        let prime: UInt64 = 0x1000_0000_01b3          // FNV-1a 64-bit prime
        for byte in components.joined(separator: "|").utf8 {
            hash ^= UInt64(byte)
            hash = hash &* prime
        }
        return hash
    }
}

// MARK: - Convenience overload (accessories-aware, single call-site for chip + planning)

extension SupplyChainPlan {
    /// Convenience overload that takes the catalog's accessories directly,
    /// so the lead-day map is built in exactly one place.
    ///
    /// Both the "Estimated delivery" chip on `.pickAccessories` and the
    /// `.supplyChainPlanning` planning sequence call this function — which
    /// is what makes the spec's byte-identity guarantee true **by construction**
    /// rather than by two call sites coincidentally agreeing.  If they each built
    /// their own lead-day map, date equality would be maintained by hand and
    /// could quietly drift.
    ///
    /// `internal` so `LiveDeliveryEstimateTests` can exercise the overload via
    /// `@testable import` without a view, a network, or a simulator.
    ///
    /// - Parameters:
    ///   - modelId: chosen model identifier.
    ///   - variantId: chosen trim identifier.
    ///   - colorId: chosen exterior colour identifier.
    ///   - interiorStyle: chosen interior style.
    ///   - deliveryWindow: standard or custom, from `AvailabilityContract`.
    ///   - selectedAccessoryIds: the `Set<String>` of toggled accessory ids.
    ///   - accessories: the catalog (or fallback) accessory list; lead-day map is
    ///     built here from `accessory.leadDays ?? 0`, so callers never maintain a
    ///     separate map.
    ///   - handover: how the vehicle reaches the customer. Defaults to `.homeDelivery`.
    ///   - now: injectable clock for deterministic tests.
    static func plan(
        modelId: String,
        variantId: String,
        colorId: String,
        interiorStyle: InteriorStyle,
        deliveryWindow: AvailabilityContract.DeliveryWindow,
        selectedAccessoryIds: Set<String>,
        accessories: [CatalogAccessory],
        handover: HandoverMethod = .homeDelivery,
        now: Date = Date()
    ) -> SupplyChainPlan {
        // Build the lead-day map from the accessory list — one place, no drift.
        let leadDayMap = accessories.reduce(into: [String: Int]()) {
            $0[$1.accessoryId] = $1.leadDays ?? 0
        }
        return plan(
            modelId: modelId,
            variantId: variantId,
            colorId: colorId,
            interiorStyle: interiorStyle,
            deliveryWindow: deliveryWindow,
            selectedAccessoryIds: Array(selectedAccessoryIds),
            accessoryLeadDays: leadDayMap,
            handover: handover,
            now: now
        )
    }
}

// MARK: - Post-order routing (what the OEM does next)

/// The order-routing narrative shown after the order is placed.
///
/// Same constraint as `SupplyChainPlan`: this describes how an OEM would
/// typically route an order, rendered from local data. It queries nothing.
struct OrderRoutingStage: Equatable, Identifiable {
    var id: String { key }
    let key: String
    let title: String
    let detail: String
    let symbolName: String
    /// Which system an OEM would typically own this step in.
    let systemLabel: String

    /// The routing chain, in order.
    ///
    /// Sequenced to match how a real order actually propagates — order intake,
    /// then planning, then material commitment, then scheduling, then build,
    /// then logistics, then dealer handover. A visitor sees their order leave
    /// the app and enter an operational chain, which is the point.
    static func chain(for plan: SupplyChainPlan) -> [OrderRoutingStage] {
        [
            OrderRoutingStage(
                key: "intake",
                title: "Order accepted",
                detail: "Order record created and validated against the configuration you chose.",
                symbolName: "checkmark.seal",
                systemLabel: "Order management"
            ),
            OrderRoutingStage(
                key: "planning",
                title: "Demand signal published",
                detail: "Your build joins the consensus forecast, so planning sees it alongside "
                    + "every other order competing for the same parts.",
                symbolName: "chart.line.uptrend.xyaxis",
                systemLabel: "Amazon Connect Decisions — demand planning"
            ),
            OrderRoutingStage(
                key: "material",
                title: "Parts committed",
                detail: plan.longLeadPartCount > 0
                    ? "\(plan.longLeadPartCount) long-lead part(s) reserved ahead of the build slot."
                    : "All parts for your configuration are available from current inventory.",
                symbolName: "shippingbox.fill",
                systemLabel: "Amazon Connect Decisions — supply planning"
            ),
            OrderRoutingStage(
                key: "scheduling",
                title: "Build slot scheduled",
                detail: plan.matchedStockBuild
                    ? "Matched to an in-progress stock build at \(plan.facility.displayName), "
                        + "which is why your date is earlier than a full custom build."
                    : "Slotted into the production schedule at \(plan.facility.displayName).",
                symbolName: "calendar.badge.clock",
                systemLabel: "Manufacturing execution"
            ),
            OrderRoutingStage(
                key: "build",
                title: "Manufacturing",
                detail: "Approximately \(plan.manufacturingDays) days on the line, "
                    + "including paint and final assembly.",
                symbolName: "wrench.and.screwdriver",
                systemLabel: "Manufacturing execution"
            ),
            OrderRoutingStage(
                key: "logistics",
                title: "Outbound logistics",
                detail: "Approximately \(plan.transitDays) days in transit from "
                    + "\(plan.facility.regionLabel.lowercased()).",
                symbolName: "truck.box",
                systemLabel: "Transportation management"
            ),
            OrderRoutingStage(
                key: "handover",
                title: "Dealer handover",
                detail: "Pre-delivery inspection, then your handover appointment.",
                symbolName: "key.horizontal",
                systemLabel: "Dealer management"
            )
        ]
    }
}
