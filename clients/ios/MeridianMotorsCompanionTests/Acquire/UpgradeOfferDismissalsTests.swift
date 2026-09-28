import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the persisted "never show this upgrade offer again" set.
///
/// WHY THIS EXISTS
/// ---------------
/// "Not interested in upgrading" used to record the dismissal from inside
/// `UpgradeOfferBanner`, writing the banner's own `@AppStorage`. That flipped the
/// banner's render guard immediately, so the banner returned no content — while the
/// sheet presenting it stayed on screen. The reported symptom was the drawer
/// emptying rather than closing.
///
/// The fix moves the decision to the presenter: the banner reports the decline, the
/// presenter closes the drawer, and the dismissal is persisted from the sheet's
/// `onDismiss` once the animation has finished. That relocation makes a previously
/// implicit rule load-bearing — persisting is now the ONLY thing separating an
/// explicit decline from a swipe-down, because both run the same `onDismiss`.
///
/// These tests pin the storage contract that relocation depends on. The
/// view-ordering itself is not unit-testable here; what is testable is that
/// persisting is additive, idempotent, encoding-safe, and never implied by anything
/// other than an explicit call.
final class UpgradeOfferDismissalsTests: XCTestCase {

    private var saved: String?

    override func setUp() {
        super.setUp()
        saved = UserDefaults.standard.string(forKey: UpgradeOfferDismissals.storageKey)
        UserDefaults.standard.removeObject(forKey: UpgradeOfferDismissals.storageKey)
    }

    override func tearDown() {
        if let saved {
            UserDefaults.standard.set(saved, forKey: UpgradeOfferDismissals.storageKey)
        } else {
            UserDefaults.standard.removeObject(forKey: UpgradeOfferDismissals.storageKey)
        }
        super.tearDown()
    }

    /// `id` is computed as `offerId ?? modelName`, so `offerId` is what pins it.
    private func offer(id: String, model: String = "Trailwind") -> AcquireConfig.UpgradeOffer {
        AcquireConfig.UpgradeOffer(
            offerId: id,
            modelName: model,
            rationale: nil,
            price: nil,
            imageUrl: nil,
            testRideAvailable: nil,
            finance: nil,
            specComparison: nil,
            tradeInCredit: nil,
            loyaltyDiscount: nil,
            loyaltyBasis: nil,
            priceAfterLoyalty: nil,
            monthly: nil,
            validUntil: nil,
            disclosure: nil,
            ivePackage: nil
        )
    }

    // MARK: - The write path

    func testNothingIsDismissedByDefault() {
        XCTAssertTrue(UpgradeOfferDismissals.current.isEmpty)
        XCTAssertFalse(UpgradeOfferDismissals.isDismissed("trailwind-2026"))
    }

    func testPersistRecordsTheOffer() {
        UpgradeOfferDismissals.persist(dismissing: "trailwind-2026")
        XCTAssertTrue(UpgradeOfferDismissals.isDismissed("trailwind-2026"))
    }

    /// The bug this guards: a write that replaced rather than merged would silently
    /// resurrect every previously-declined offer.
    func testPersistIsAdditiveAndDoesNotClobberEarlierDismissals() {
        UpgradeOfferDismissals.persist(dismissing: "first")
        UpgradeOfferDismissals.persist(dismissing: "second")

        XCTAssertTrue(UpgradeOfferDismissals.isDismissed("first"))
        XCTAssertTrue(UpgradeOfferDismissals.isDismissed("second"))
        XCTAssertEqual(UpgradeOfferDismissals.current.count, 2)
    }

    /// `onDismiss` can run more than once across a session; a duplicate must not
    /// corrupt the encoded form or inflate the set.
    func testPersistIsIdempotent() {
        UpgradeOfferDismissals.persist(dismissing: "same")
        UpgradeOfferDismissals.persist(dismissing: "same")

        XCTAssertEqual(UpgradeOfferDismissals.current, ["same"])
    }

    /// Offer ids fall back to `modelName`, which contains spaces and punctuation.
    /// A comma separator would split one id into two and dismiss neither.
    func testIdsContainingCommasAndSpacesRoundTrip() {
        let awkward = "Trailwind Long Range, Dual Motor"
        UpgradeOfferDismissals.persist(dismissing: awkward)

        XCTAssertEqual(UpgradeOfferDismissals.current, [awkward])
        XCTAssertTrue(UpgradeOfferDismissals.isDismissed(awkward))
    }

    func testEncodeDecodeRoundTripsMultipleIds() {
        let ids: Set<String> = ["a", "b, with comma", "c d"]
        XCTAssertEqual(UpgradeOfferDismissals.ids(in: UpgradeOfferDismissals.encode(ids)), ids)
    }

    // MARK: - Decline vs swipe

    /// The distinction the sheet's `onDismiss` flag carries. A swipe-down means "not
    /// now": the drawer closes and the offer must still be available next session.
    /// Simulated by closing WITHOUT persisting, which is what a nil `declinedOffer`
    /// produces.
    func testClosingWithoutPersistingLeavesTheOfferAvailable() {
        let offers = [offer(id: "trailwind-2026", model: "Trailwind")]

        XCTAssertTrue(
            UpgradeOfferDismissals.hasUndismissedOffer(among: offers, ownedModelName: "Trailwind"),
            "a swipe-dismissed drawer must not permanently dismiss the offer"
        )
    }

    /// The explicit decline: after persisting, the sheet gate must stop offering it,
    /// otherwise the drawer re-presents itself over a banner that has dismissed
    /// itself — the empty-sheet failure `UpgradeOfferDismissals` was extracted to
    /// prevent.
    func testPersistingStopsTheSheetGateFromOfferingItAgain() {
        let offers = [offer(id: "trailwind-2026", model: "Trailwind")]
        XCTAssertTrue(
            UpgradeOfferDismissals.hasUndismissedOffer(among: offers, ownedModelName: "Trailwind")
        )

        let top = OfferRecommendationBasis.bestMatch(among: offers, ownedModelName: "Trailwind")
        XCTAssertNotNil(top)
        UpgradeOfferDismissals.persist(dismissing: top!.offer.id)

        XCTAssertFalse(
            UpgradeOfferDismissals.hasUndismissedOffer(among: offers, ownedModelName: "Trailwind"),
            "an explicitly declined offer must not re-present"
        )
    }

    /// Declining one offer must not dismiss the rest — the button's label says
    /// "upgrading", but the stored decision is per-offer, and the gate picks the top
    /// recommendation. Pins that the remaining offer still gates open.
    func testDecliningOneOfferLeavesAnotherAvailable() {
        let declined = offer(id: "trailwind-2026", model: "Trailwind")
        let other = offer(id: "summit-2026", model: "Summit")

        UpgradeOfferDismissals.persist(dismissing: declined.id)

        XCTAssertTrue(
            UpgradeOfferDismissals.hasUndismissedOffer(
                among: [declined, other], ownedModelName: "Summit"
            ),
            "dismissing one offer must not suppress a different recommendation"
        )
    }
}
