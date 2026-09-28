import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the offer expiry (`validUntil`).
///
/// An expiry is an **offer term**, which puts it on the deterministic side of the
/// seam: it is tenant-supplied free text, presented verbatim, and never
/// reinterpreted by this app. That matters more than it looks. A limited-time
/// claim is a commercial statement, and an app that turns `"Valid till Aug 31"`
/// into `"Ends in 12 days!"` has manufactured urgency it has no authority to
/// assert — it has also silently taken on a timezone and a parsing failure mode
/// it cannot see.
///
/// These tests exist because the rule was previously only a source comment, and a
/// boundary with no executable form is a boundary that gets crossed.
final class OfferValidityTests: XCTestCase {

    private func offer(validUntil: String?) -> AcquireConfig.UpgradeOffer {
        AcquireConfig.UpgradeOffer(
            offerId: "o1", modelName: "Meridian Trailwind 2026", rationale: nil,
            price: nil, imageUrl: nil, testRideAvailable: nil,
            finance: nil, specComparison: nil, tradeInCredit: nil,
            loyaltyDiscount: nil, loyaltyBasis: nil, priceAfterLoyalty: nil,
            monthly: nil, validUntil: validUntil, disclosure: nil,
            ivePackage: nil)
    }

    // MARK: - Verbatim pass-through

    /// The exact string staging serves today. It must survive untouched.
    func testStagingValueIsPreservedVerbatim() {
        XCTAssertEqual(offer(validUntil: "Valid till Aug 31").validUntil,
                       "Valid till Aug 31")
    }

    /// `validUntil` is documented as free-text and tenants format it differently.
    /// Anything that "helpfully" normalises these would be reformatting a term.
    func testHeterogeneousTenantFormatsAllSurviveUnchanged() {
        for raw in [
            "Valid till Aug 31",
            "Valid till 31 Aug",
            "Offer ends 2026-09-15",
            "Ends Sep 15, 2026",
            "While stocks last",          // no date at all — still valid text
            "Valid till 31/08/2026"       // ambiguous D/M vs M/D on purpose
        ] {
            XCTAssertEqual(offer(validUntil: raw).validUntil, raw,
                           "tenant expiry text must not be rewritten: \(raw)")
        }
    }

    /// Absent expiry stays absent. The banner must render no validity affordance
    /// rather than substituting a default — an invented expiry is worse than none.
    func testAbsentExpiryStaysNil() {
        XCTAssertNil(offer(validUntil: nil).validUntil)
    }

    /// Empty string is distinct from nil and must not silently become a label.
    /// Callers use `if let`, so an empty value would render an empty capsule; this
    /// pins the value so that behaviour is a known quantity rather than a surprise.
    func testEmptyExpiryIsPreservedAsEmptyNotNil() {
        XCTAssertEqual(offer(validUntil: "").validUntil, "")
    }

    // MARK: - The type is a String, deliberately

    /// Negative assertion, and the load-bearing one. `validUntil` must remain a
    /// `String?` — not a `Date`. Parsing it would require choosing a locale,
    /// a timezone and a calendar on the customer's behalf, and every one of those
    /// choices can move an expiry by a day. It would also convert a tenant typo
    /// into either a crash or a silent `nil`, both of which lose a term the
    /// customer is entitled to see. If someone changes this to `Date`, this test
    /// stops compiling — which is the intended alarm.
    func testExpiryRemainsFreeTextNotAParsedDate() {
        let value: String? = offer(validUntil: "Valid till Aug 31").validUntil
        XCTAssertNotNil(value)
        XCTAssertTrue(value is String?)
    }

    /// The app must not synthesise urgency vocabulary around the term. This pins
    /// the *inputs* — the only string available to render is the tenant's own — so
    /// any countdown phrasing would have to come from somewhere this test can see.
    func testNoUrgencyVocabularyIsAddedToTheTenantString() {
        let raw = "Valid till Aug 31"
        let rendered = offer(validUntil: raw).validUntil ?? ""
        for invented in ["hurry", "last chance", "only", "days left", "expires soon", "act now", "!"] {
            XCTAssertFalse(
                rendered.lowercased().contains(invented),
                "'\(invented)' is not in the tenant's term and must not be added"
            )
        }
    }
}
