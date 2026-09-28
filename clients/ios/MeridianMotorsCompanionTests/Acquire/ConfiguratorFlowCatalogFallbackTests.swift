import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `CatalogFallback.loadFixture()` (Task 3.1) and
/// `CatalogFallback.resolve(isSignedIn:fetchResult:)` (Task 3.2).
///
/// Test-first RED→GREEN pairs per tasks.md:
///   - Task 3.1 tests (content shape): go RED against the empty-arrays seam,
///     then GREEN after the 3-3-3-3-3 payload is filled in.
///   - Task 3.2 tests (resolve policy): go RED against the stub `.remote`
///     implementation of `resolve`, then GREEN after proper logic ships.
final class ConfiguratorFlowCatalogFallbackTests: XCTestCase {

    // MARK: - Fixture helpers

    private var fixture: CatalogResponse { CatalogFallback.loadFixture() }

    // MARK: - Task 3.1 — loadFixture() content tests

    /// The fixture must return a non-empty response.
    func testFixtureIsNonEmpty() {
        let f = fixture
        XCTAssertFalse(f.categories.isEmpty,
                       "loadFixture() must return at least one category; got empty array")
        XCTAssertFalse(f.models.isEmpty,
                       "loadFixture() must return at least one model; got empty array")
    }

    /// The fixture must contain a `family-suv` category (per spec 3-category requirement).
    func testFixtureHasFamilySuvCategory() {
        let cats = fixture.categories
        let familySuv = cats.first(where: { $0.categoryId == "family-suv" })
        XCTAssertNotNil(familySuv,
                        "loadFixture() must include 'family-suv' category; found: \(cats.map(\.categoryId))")
    }

    /// **Every model must offer at least one accessory.**
    ///
    /// Replaces `testFixtureHasExactlyThreeAccessoriesNamedCorrectly`, which pinned
    /// "exactly 3, all `family-suv`" — the shape that caused the defect it was
    /// supposedly guarding. `AccessoriesStep` filters on
    /// `categoryId == nil || categoryId == model's`, and only `crestwind-3row` is
    /// `family-suv`, so the **Trailwind** — the primary seeded offer — reached an
    /// EMPTY accessory step and its delivery date never moved. The count and the
    /// scoping were both incidental; this is the property that was actually wanted,
    /// and it fails against the old fixture.
    func testEveryModelHasAtLeastOneApplicableAccessory() {
        let accessories = fixture.accessories
        XCTAssertFalse(accessories.isEmpty, "fixture has no accessories at all")

        for model in fixture.models {
            // Same predicate AccessoriesStep applies.
            let applicable = accessories.filter {
                $0.categoryId == nil || $0.categoryId == model.categoryId
            }
            XCTAssertFalse(
                applicable.isEmpty,
                "\(model.modelId) (category \(model.categoryId)) has no applicable "
                + "accessories, so its accessory step renders empty and the delivery "
                + "date cannot move")
        }
    }

    /// At least one accessory must be universal, so a category the fixture has not
    /// anticipated still gets a non-empty step.
    func testAtLeastOneAccessoryIsUniversal() {
        XCTAssertTrue(fixture.accessories.contains { $0.categoryId == nil },
                      "no universal accessory; a new category would render empty")
    }

    /// Every accessory is either universal or scoped to a category the fixture
    /// actually defines — a typo'd scope would silently hide it everywhere.
    func testNoAccessoryIsScopedToAnUnknownCategory() {
        let known = Set(fixture.categories.map(\.categoryId))
        for acc in fixture.accessories {
            if let cat = acc.categoryId {
                XCTAssertTrue(known.contains(cat),
                    "accessory '\(acc.accessoryId)' is scoped to unknown category "
                    + "'\(cat)', so it can never be shown")
            }
        }
    }

    /// Line-fitted accessories must carry lead days, or selecting them cannot move
    /// the delivery estimate — which is the feature they exist to demonstrate.
    func testAccessoriesCarryLeadDays() {
        for acc in fixture.accessories {
            XCTAssertNotNil(acc.leadDays,
                            "accessory '\(acc.accessoryId)' has no leadDays")
            XCTAssertGreaterThan(acc.leadDays ?? 0, 0,
                                 "accessory '\(acc.accessoryId)' has leadDays 0")
        }
    }

    // MARK: - Task 3.1 — guard assertions (from task Verify block)

    /// The file header must carry the [FIXTURE — NOT REAL CATALOG DATA] marker.
    ///
    /// This asserts on the catalogVersion field, which encodes the fixture marker
    /// indirectly. The file-level grep from the Verify command confirms the docstring
    /// marker; the in-test assertion confirms the data path is consistent.
    func testFixtureCatalogVersionCarriesFallbackMarker() {
        let version = fixture.catalogVersion ?? ""
        XCTAssertTrue(version.lowercased().contains("fallback"),
                      "catalogVersion should contain 'fallback' to be greppable in bug reports; got '\(version)'")
    }

    /// The fixture must contain zero real OEM brand strings.
    ///
    /// Checks a selection of commonly-referenced OEM names that would indicate
    /// real production data has leaked into the fixture.
    func testFixtureContainsNoRealOEMBrandStrings() {
        let f = fixture
        // Build a flat string of all user-visible text in the fixture.
        var parts: [String] = [f.tenantId, f.catalogVersion ?? ""]
        for c in f.categories { parts += [c.categoryId, c.displayName] }
        for m in f.models     { parts += [m.modelId, m.displayName] }
        for v in f.variants   { parts += [v.variantId, v.displayName] }
        for c in f.colors     { parts += [c.colorId, c.displayName] }
        for a in f.accessories { parts += [a.accessoryId, a.displayName, a.description ?? ""] }
        let allText = parts.joined(separator: " ").lowercased()

        let forbidden = ["ford", "toyota", "honda", "hyundai", "bmw",
                         "nissan", "chevrolet", "volkswagen",
                         "audi", "mercedes", "tesla", "rivian", "stellantis"]
        for brand in forbidden {
            XCTAssertFalse(allText.contains(brand),
                           "Fixture contains real OEM brand string '\(brand)' — fixture must use fictional Meridian names only")
        }
    }

    // MARK: - Task 3.2 — resolve(isSignedIn:fetchResult:) policy tests

    private func makeSuccessResult() -> Result<CatalogResponse, AcquireError> {
        .success(fixture)
    }
    private func makeEndpointUnavailableResult() -> Result<CatalogResponse, AcquireError> {
        .failure(.endpointUnavailable("/acquire/catalog/test"))
    }

    /// Unsigned caller → fallback with reason "unsigned".
    func testResolveUnsignedReturnsFallback() {
        let (_, source) = CatalogFallback.resolve(isSignedIn: false, fetchResult: nil)
        XCTAssertEqual(source, .fallback(reason: "unsigned"),
                       "resolve(isSignedIn: false) must return .fallback(reason: 'unsigned')")
    }

    /// Signed + endpoint-unavailable → fallback with reason "endpoint-unavailable".
    func testResolveSignedEndpointUnavailableReturnsFallback() {
        let (_, source) = CatalogFallback.resolve(
            isSignedIn: true,
            fetchResult: makeEndpointUnavailableResult()
        )
        XCTAssertEqual(source, .fallback(reason: "endpoint-unavailable"),
                       "resolve with isEndpointUnavailable error must return .fallback(reason: 'endpoint-unavailable')")
    }

    /// Signed + successful fetch → `.remote` with the response passed through unchanged.
    func testResolveSignedSuccessReturnsRemote() {
        let remoteResponse = fixture  // any well-formed response
        let (response, source) = CatalogFallback.resolve(
            isSignedIn: true,
            fetchResult: .success(remoteResponse)
        )
        XCTAssertEqual(source, .remote,
                       "resolve with .success fetchResult must return .remote")
        XCTAssertEqual(response.catalogVersion, remoteResponse.catalogVersion,
                       "resolve must pass the remote response through unchanged")
        XCTAssertEqual(response.categories.count, remoteResponse.categories.count,
                       "resolve must pass the remote categories through unchanged")
    }
}
