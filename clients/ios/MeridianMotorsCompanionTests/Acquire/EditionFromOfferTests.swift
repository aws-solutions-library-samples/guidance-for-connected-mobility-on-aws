import XCTest
@testable import MeridianMotorsCompanion

/// Tests for task 5.2 / F5.1: Edition arrives from the accepted offer.
///
/// Verifies:
/// - All four Edition values are carried unchanged into the order payload.
/// - `IvePackage.defaultFor(modelId:)` maps each named model to its distinct Edition.
/// - An unknown model id resolves to `.family` (the broadest, least-assertive default).
/// - A nil model id resolves to `.family`.
/// - All four named-model mappings yield four **different** Editions.
/// - The ConfiguratorOfferHandoff round-trips its edition correctly.
/// - UpgradeOffer.ivePackage decodes from JSON when present.
final class EditionFromOfferTests: XCTestCase {

    // MARK: - All four Edition values reach the order payload unchanged

    func testFamilyEditionCarriedToPayload() throws {
        let req = makeRequest(edition: .family)
        XCTAssertEqual(req.edition, "family",
                       "family Edition must reach the payload as-is")
    }

    func testExecutiveEditionCarriedToPayload() throws {
        let req = makeRequest(edition: .executive)
        XCTAssertEqual(req.edition, "executive")
    }

    func testAdventureEditionCarriedToPayload() throws {
        let req = makeRequest(edition: .adventure)
        XCTAssertEqual(req.edition, "adventure")
    }

    func testEntryUrbanEditionCarriedToPayload() throws {
        let req = makeRequest(edition: .entryUrban)
        XCTAssertEqual(req.edition, "entry-urban",
                       "entry-urban raw value must match the Zone 3 contract string")
    }

    // MARK: - Per-model default — IvePackage.defaultFor(modelId:)

    /// Crestwind is the large three-row family SUV → .family.
    func testCrestwindDefaultsToFamily() {
        XCTAssertEqual(IvePackage.defaultFor(modelId: "crestwind"),
                       .family,
                       "Crestwind (large three-row SUV) must default to .family")
    }

    /// Azimuth is the sedan → .executive.
    func testAzimuthDefaultsToExecutive() {
        XCTAssertEqual(IvePackage.defaultFor(modelId: "azimuth"),
                       .executive,
                       "Azimuth (sedan) must default to .executive")
    }

    /// Trailwind is the mid SUV → .adventure.
    func testTrailwindDefaultsToAdventure() {
        XCTAssertEqual(IvePackage.defaultFor(modelId: "trailwind"),
                       .adventure,
                       "Trailwind (mid SUV) must default to .adventure")
    }

    /// Windrose is the compact/urban vehicle → .entryUrban.
    func testWindroseDefaultsToEntryUrban() {
        XCTAssertEqual(IvePackage.defaultFor(modelId: "windrose"),
                       .entryUrban,
                       "Windrose (compact/urban) must default to .entryUrban")
    }

    /// All four named models must map to four **different** Editions.
    /// A mapping that returned the same value for all four would pass the individual
    /// tests above only if the constant happened to match — this assertion catches that.
    func testFourNamedModelsYieldFourDistinctEditions() {
        let results: Set<IvePackage> = [
            IvePackage.defaultFor(modelId: "crestwind"),
            IvePackage.defaultFor(modelId: "azimuth"),
            IvePackage.defaultFor(modelId: "trailwind"),
            IvePackage.defaultFor(modelId: "windrose"),
        ]
        XCTAssertEqual(results.count, 4,
                       "The four named models must each map to a distinct Edition")
    }

    /// Case-insensitive matching: "Crestwind LX" (mixed case with suffix) must still
    /// resolve to .family.
    func testMatchingIsCaseInsensitiveAndToleratesSuffix() {
        XCTAssertEqual(IvePackage.defaultFor(modelId: "Crestwind LX"), .family)
        XCTAssertEqual(IvePackage.defaultFor(modelId: "AZIMUTH-300"),  .executive)
        XCTAssertEqual(IvePackage.defaultFor(modelId: "Trailwind X"),  .adventure)
        XCTAssertEqual(IvePackage.defaultFor(modelId: "Windrose City"), .entryUrban)
    }

    /// An unknown model id must resolve to .family — the least specific claim.
    func testUnknownModelIdResolvesToFamily() {
        XCTAssertEqual(IvePackage.defaultFor(modelId: "unknown-model-xyz"),
                       .family,
                       "Unknown model id must resolve to .family, not .entryUrban or any other")
    }

    /// A nil model id must resolve to .family — not crash, not nil, not entryUrban.
    func testNilModelIdResolvesToFamily() {
        XCTAssertEqual(IvePackage.defaultFor(modelId: nil),
                       .family,
                       "Absent model id must resolve to .family")
    }

    /// The fallback must always be a real canonical IvePackage value.
    func testDefaultForAlwaysReturnsCanonicalValue() {
        for id in ["crestwind", "azimuth", "trailwind", "windrose", "unknown", nil] {
            let result = IvePackage.defaultFor(modelId: id)
            XCTAssertTrue(IvePackage.allCases.contains(result),
                          "defaultFor(modelId: \(id ?? "nil")) must be one of the four canonical values")
            XCTAssertFalse(result.rawValue.isEmpty,
                           "defaultFor raw value must not be empty")
        }
    }

    // MARK: - ConfiguratorOfferHandoff carries the edition correctly

    func testOfferHandoffWithExplicitEdition() {
        let handoff = ConfiguratorOfferHandoff(
            edition: .executive,
            modelName: "Meridian 400",
            firstName: nil
        )
        XCTAssertEqual(handoff.edition, .executive)
        XCTAssertEqual(handoff.modelName, "Meridian 400")
    }

    func testOfferHandoffWithNilEditionFallsBackToPerModelDefault() {
        let handoff = ConfiguratorOfferHandoff(edition: nil, modelName: "Crestwind LX", firstName: nil)
        let resolved = handoff.edition ?? IvePackage.defaultFor(modelId: handoff.modelName)
        XCTAssertEqual(resolved, .family,
                       "nil edition in a Crestwind handoff must resolve to .family via defaultFor")
    }

    // MARK: - UpgradeOffer.ivePackage decodes from JSON

    func testUpgradeOfferDecodesIvePackage() throws {
        // UpgradeOffer lives inside AcquireConfig; we decode a minimal slice.
        let json = """
        {
          "modelName": "Meridian 400 Executive",
          "ivePackage": "executive"
        }
        """.data(using: .utf8)!
        let offer = try JSONDecoder().decode(AcquireConfig.UpgradeOffer.self, from: json)
        XCTAssertEqual(offer.ivePackage, .executive)
        XCTAssertEqual(offer.modelName, "Meridian 400 Executive")
    }

    func testUpgradeOfferMissingIvePackageDecodesAsNil() throws {
        let json = """
        { "modelName": "Meridian 300" }
        """.data(using: .utf8)!
        let offer = try JSONDecoder().decode(AcquireConfig.UpgradeOffer.self, from: json)
        XCTAssertNil(offer.ivePackage,
                     "absent ivePackage must decode as nil (not a crash)")
    }

    // MARK: - Helpers

    private func makeRequest(edition: IvePackage) -> ReservationRequest {
        ReservationRequest(
            tenantId: "t", modelId: "m", variantId: "v",
            colorId: "c", selectedAccessoryIds: [],
            depositRef: nil, qualificationTier: nil,
            discoverSessionId: nil, leadId: nil,
            edition: edition.rawValue,
            interiorStyle: nil, firstName: nil,
            handoverMethod: nil, handoverCenterId: nil
        )
    }
}
