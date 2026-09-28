import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `ConfiguratorFlow.resolvePreselection(offerModelName:models:)` —
/// the pure matching function that maps an offer's free-text model name to the
/// loaded catalog.
///
/// Task 5.2 (spec `2026-08-21-cvx-configurator-full-picker-flow`):
/// Verifies the three spec cases plus a fourth (`modelId`-only match) that proves
/// the documented fallback arm is live rather than decorative.
///
/// ## Testability rationale (architect correction 2026-08-21)
/// `preselectedCategoryId` / `preselectedModelId` are `@State private` on a
/// SwiftUI `View` and `.task` does not run in the test target.  These tests
/// assert the **pure function** `resolvePreselection` instead, which is exactly
/// what the view calls from `.task` — so the matching rule is tested without a
/// view harness, a network, or a simulator.
final class ConfiguratorFlowWarmEntryPreselectTests: XCTestCase {

    // MARK: - Fixtures

    private let crestwindModel = CatalogModel(
        modelId:     "crestwind-3row",
        categoryId:  "family-suv",
        displayName: "Crestwind 3-Row",
        basePrice:   42990, imageKey: nil, specBadges: nil, sortOrder: 0)

    private let windroseModel = CatalogModel(
        modelId:     "windrose-city",
        categoryId:  "commuter",
        displayName: "Windrose City",
        basePrice:   31490, imageKey: nil, specBadges: nil, sortOrder: 0)

    private var catalogModels: [CatalogModel] {
        [crestwindModel, windroseModel]
    }

    // MARK: - Test 1: Matching displayName resolves both ids

    /// When the offer's modelName matches a catalog model's `displayName`,
    /// both `categoryId` and `modelId` are returned.
    func testMatchingDisplayNameResolvesBothIds() {
        let result = ConfiguratorFlow.resolvePreselection(
            offerModelName: "Crestwind 3-Row",
            models: catalogModels
        )

        XCTAssertEqual(result.categoryId, "family-suv",
                       "displayName match must resolve to the model's categoryId")
        XCTAssertEqual(result.modelId, "crestwind-3row",
                       "displayName match must resolve to the model's modelId")
    }

    // MARK: - Test 2: Non-matching name yields (nil, nil)

    /// When the offer's modelName does not match any catalog model's displayName
    /// or modelId, both fields are nil — `.pickCategory` renders with no preselect,
    /// and the visitor picks from scratch.
    func testNonMatchingModelNameYieldsNilNil() {
        let result = ConfiguratorFlow.resolvePreselection(
            offerModelName: "Unknown Phantom X",
            models: catalogModels
        )

        XCTAssertNil(result.categoryId,
                     "Non-matching name must yield nil categoryId")
        XCTAssertNil(result.modelId,
                     "Non-matching name must yield nil modelId")
    }

    // MARK: - Test 3: nil offer name yields (nil, nil) — cold-entry equivalent

    /// A nil offer name (cold entry, no handoff) must return `(nil, nil)`.
    /// This is the same result as non-matching, preserving cold-entry semantics.
    func testNilOfferNameYieldsNilNil() {
        let result = ConfiguratorFlow.resolvePreselection(
            offerModelName: nil,
            models: catalogModels
        )

        XCTAssertNil(result.categoryId,
                     "Nil offer name must yield nil categoryId (cold-entry case)")
        XCTAssertNil(result.modelId,
                     "Nil offer name must yield nil modelId (cold-entry case)")
    }

    // MARK: - Test 4: modelId-only match resolves (fallback arm is live)

    /// When the offer's modelName does not match any `displayName` but matches
    /// a model's `modelId`, the match still resolves.
    ///
    /// This proves the documented second lookup arm is live rather than decorative.
    func testModelIdOnlyMatchResolves() {
        // "windrose-city" is the modelId but NOT the displayName ("Windrose City" with space).
        let result = ConfiguratorFlow.resolvePreselection(
            offerModelName: "windrose-city",
            models: catalogModels
        )

        XCTAssertEqual(result.categoryId, "commuter",
                       "modelId match must resolve to the model's categoryId")
        XCTAssertEqual(result.modelId, "windrose-city",
                       "modelId match must resolve to the model's modelId")
    }

    // MARK: - resolveEstablishedConfiguration tests (Task 2.1)
    //
    // These 6 tests exercise the pure static seam added in Group 2:
    // `ConfiguratorFlow.resolveEstablishedConfiguration(offerModelName:models:variants:categories:)`
    //
    // The function is pure and static so it is exercisable from @testable import
    // without a view, a network, or a simulator — exactly the same contract as
    // `resolvePreselection` above.

    // MARK: Shared fixtures for resolveEstablishedConfiguration

    private let familySuvCategory = CatalogCategory(
        categoryId:  "family-suv",
        displayName: "Family SUV",
        symbolName:  nil,
        sortOrder:   0)

    private let commuterCategory = CatalogCategory(
        categoryId:  "commuter",
        displayName: "Commuter",
        symbolName:  nil,
        sortOrder:   1)

    // Variants for crestwindModel (modelId: "crestwind-3row")
    private let crestwindVariantStandard = CatalogVariant(
        variantId:     "crestwind-standard",
        modelId:       "crestwind-3row",
        displayName:   "Standard",
        priceAdder:    0,
        specOverrides: nil,
        sortOrder:     2)

    private let crestwindVariantLongRange = CatalogVariant(
        variantId:     "crestwind-longrange",
        modelId:       "crestwind-3row",
        displayName:   "Long Range",
        priceAdder:    3000,
        specOverrides: nil,
        sortOrder:     1)   // lower sortOrder — should be preferred

    // Variant for windroseModel (modelId: "windrose-city")
    private let windroseVariant = CatalogVariant(
        variantId:     "windrose-base",
        modelId:       "windrose-city",
        displayName:   "Base",
        priceAdder:    0,
        specOverrides: nil,
        sortOrder:     0)

    private var allCategories: [CatalogCategory] {
        [familySuvCategory, commuterCategory]
    }

    private var allVariants: [CatalogVariant] {
        [crestwindVariantStandard, crestwindVariantLongRange, windroseVariant]
    }

    // MARK: - EC Test 1: displayName match returns all three objects

    /// When offerModelName exactly matches a model's displayName,
    /// all three resolved objects (category, model, variant) are returned.
    func testEstablishedConfiguration_displayNameMatch_returnsAllThree() {
        let result = ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: "Crestwind 3-Row",
            models: catalogModels,
            variants: allVariants,
            categories: allCategories
        )
        XCTAssertNotNil(result, "displayName match must return a non-nil result")
        XCTAssertEqual(result?.model.modelId, "crestwind-3row",
                       "Resolved model must be crestwind-3row")
        XCTAssertEqual(result?.category.categoryId, "family-suv",
                       "Resolved category must be family-suv")
        XCTAssertNotNil(result?.variant,
                        "Resolved variant must not be nil")
    }

    // MARK: - EC Test 2: modelId match returns all three objects

    /// When offerModelName matches a model's modelId (not displayName),
    /// all three resolved objects are still returned.
    func testEstablishedConfiguration_modelIdMatch_returnsAllThree() {
        let result = ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: "windrose-city",   // modelId, not displayName
            models: catalogModels,
            variants: allVariants,
            categories: allCategories
        )
        XCTAssertNotNil(result, "modelId match must return a non-nil result")
        XCTAssertEqual(result?.model.modelId, "windrose-city",
                       "Resolved model must be windrose-city")
        XCTAssertEqual(result?.category.categoryId, "commuter",
                       "Resolved category must be commuter")
        XCTAssertNotNil(result?.variant,
                        "Resolved variant must not be nil")
    }

    // MARK: - EC Test 3: No model match returns nil

    /// When no model matches the offer name, the function returns nil
    /// rather than a partial tuple.
    func testEstablishedConfiguration_noMatch_returnsNil() {
        let result = ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: "Phantom X Nonexistent",
            models: catalogModels,
            variants: allVariants,
            categories: allCategories
        )
        XCTAssertNil(result,
                     "Non-matching name must return nil — not a partial tuple")
    }

    // MARK: - EC Test 4: Model with zero variants returns nil

    /// When a model matches but has no variants in the catalog,
    /// the function returns nil — a half-established configuration with
    /// a real model but no variant would put invalid data into the reservation payload.
    func testEstablishedConfiguration_modelWithNoVariants_returnsNil() {
        // Use crestwindModel but provide variants only for windroseModel
        let variantsWithoutCrestwind = [windroseVariant]
        let result = ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: "Crestwind 3-Row",
            models: catalogModels,
            variants: variantsWithoutCrestwind,
            categories: allCategories
        )
        XCTAssertNil(result,
                     "Model with zero variants must return nil, not a partial tuple")
    }

    // MARK: - EC Test 5: Lowest sortOrder wins over document order

    /// When a model has multiple variants, the one with the lowest sortOrder
    /// is selected — regardless of its position in the variants array.
    ///
    /// crestwindVariantStandard has sortOrder 2; crestwindVariantLongRange has sortOrder 1.
    /// Document order has Standard first, but Long Range must win.
    func testEstablishedConfiguration_lowestSortOrderWins() {
        // Standard (sortOrder 2) appears first in the array, Long Range (sortOrder 1) second.
        let variantsDocumentOrder = [crestwindVariantStandard, crestwindVariantLongRange]
        let result = ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: "Crestwind 3-Row",
            models: catalogModels,
            variants: variantsDocumentOrder,
            categories: allCategories
        )
        XCTAssertNotNil(result, "Must resolve to a non-nil result")
        XCTAssertEqual(result?.variant.variantId, "crestwind-longrange",
                       "Lowest sortOrder (1) must win over document order")
    }

    // MARK: - EC Test 6: Equal sortOrder resolved by variantId ascending (determinism)

    /// When two variants have the same sortOrder, the one with the lexicographically
    /// lower variantId is selected — guaranteeing a deterministic result regardless
    /// of catalog fetch order.
    func testEstablishedConfiguration_equalSortOrder_resolvedByVariantIdAscending() {
        // Both variants have sortOrder 0; "aaa-variant" < "zzz-variant" lexicographically.
        let variantA = CatalogVariant(
            variantId:     "aaa-variant",
            modelId:       "crestwind-3row",
            displayName:   "A Variant",
            priceAdder:    0,
            specOverrides: nil,
            sortOrder:     0)
        let variantZ = CatalogVariant(
            variantId:     "zzz-variant",
            modelId:       "crestwind-3row",
            displayName:   "Z Variant",
            priceAdder:    0,
            specOverrides: nil,
            sortOrder:     0)
        // Place Z before A in document order to verify sort beats document order.
        let tiedVariants = [variantZ, variantA, windroseVariant]
        let result = ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: "Crestwind 3-Row",
            models: catalogModels,
            variants: tiedVariants,
            categories: allCategories
        )
        XCTAssertNotNil(result, "Must resolve to a non-nil result")
        XCTAssertEqual(result?.variant.variantId, "aaa-variant",
                       "Equal sortOrder must resolve by variantId ascending for determinism")
    }
}

// MARK: - Offer-name -> catalog matching (issue 2026-08-22)
//
// Authored as its own file, then folded in here because Xcode was open and
// `project.pbxproj` could not be written; a new file would not compile and its
// tests would "pass" by not existing. Suite and test names are unchanged.

/// Offer-name → catalog matching against the **real** strings both sides actually
/// carry in staging, rather than fixtures chosen to agree.
///
/// Why this suite exists: `resolveEstablishedConfiguration` originally matched on
/// exact `displayName` then exact `modelId`, and every test for it used fixtures
/// where those matched exactly. Against live data they never do — a tenant offer is
/// a marketing string ("Meridian Trailwind 2026") while a catalog model is
/// lineup-plus-trim ("Trailwind Adventurer"). So the resolver returned nil for all
/// three seeded offers, Door B fell through to the category picker, and a visitor
/// who had just accepted a Trailwind offer was asked what kind of vehicle they
/// wanted. The tests passed throughout, because they encoded the author's
/// assumption instead of the data.
///
/// The strings below are copied from ground truth, not invented:
///   - offer names: `vsa-staging-tenant-config`, tenantId=meridian, version=1.0.0,
///     `acquire.upgradeOffers[].modelName` (read 2026-08-22)
///   - catalog models: `CatalogFallback.loadFixture()`, which is what actually
///     serves today because `vsa-staging-acquire-catalog` holds no `meridian` rows
///     at all (see issues/2026-08-22-acquire-catalog-stale-tenant-and-content).
///
/// Issue: issues/2026-08-22-upgrade-drawer-suppressed-by-single-dismissal (found
/// during that investigation), spec 2026-08-21-cvx-upgrade-flow-continuity.
final class OfferToCatalogMatchTests: XCTestCase {

    /// Verbatim `modelName` values from the live staging tenant row.
    private let liveOfferNames = [
        "Meridian Trailwind 2026",
        "Meridian Crestwind Signature",
        "Meridian Azimuth Executive"
    ]

    private var fixture: CatalogResponse { CatalogFallback.loadFixture() }

    private func resolve(_ offerName: String)
        -> (category: CatalogCategory, model: CatalogModel, variant: CatalogVariant)? {
        ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: offerName,
            models: fixture.models,
            variants: fixture.variants,
            categories: fixture.categories
        )
    }

    // MARK: - The reported defect

    /// The exact case the user hit: accept the Trailwind offer, land on the picker.
    func testLiveTrailwindOfferResolvesToTheTrailwindModel() {
        let resolved = resolve("Meridian Trailwind 2026")
        XCTAssertNotNil(resolved,
            "The primary seeded offer must establish a vehicle. Returning nil here is "
            + "what sent a visitor who had already chosen a Trailwind to 'what kind of "
            + "vehicle are you looking for'.")
        XCTAssertEqual(resolved?.model.modelId, "trailwind-adv")
    }

    func testLiveCrestwindOfferResolvesToTheCrestwindModel() {
        let resolved = resolve("Meridian Crestwind Signature")
        XCTAssertNotNil(resolved)
        XCTAssertEqual(resolved?.model.modelId, "crestwind-3row")
    }

    /// The offer names its trim, and the catalog has that trim, so use it rather
    /// than defaulting to lowest `sortOrder`.
    func testOfferTrimWordSelectsTheMatchingVariant() {
        let resolved = resolve("Meridian Crestwind Signature")
        XCTAssertEqual(resolved?.variant.variantId, "crestwind-3row-sig",
            "\"Signature\" appears in both the offer name and a variant displayName; "
            + "establishing Standard instead would silently downgrade the visitor's offer.")
    }

    // MARK: - The property that must survive the loosening

    /// An offer for a model the catalog does not carry must still return nil.
    ///
    /// `Azimuth` has no fixture model, so this is a live gap rather than a
    /// hypothetical — and falling through to the picker is the correct handling.
    /// Guessing would put a vehicle the visitor never chose into the payload.
    func testOfferWithNoCorrespondingModelStillReturnsNil() {
        XCTAssertNil(resolve("Meridian Azimuth Executive"),
            "No Azimuth in the catalog. Loosening the match must not make this guess.")
    }

    /// The brand word is shared by every offer and must not decide anything.
    func testSharedBrandWordAloneNeverMatches() {
        XCTAssertNil(resolve("Meridian"),
            "\"Meridian\" is not discriminating; matching on it would resolve every "
            + "offer to whichever model happened to be first.")
    }

    /// A token common to two models cannot resolve either.
    func testAmbiguousTokenReturnsNil() {
        let twoTrailwinds = [
            CatalogModel(modelId: "trailwind-adv", categoryId: "sport",
                         displayName: "Trailwind Adventurer", basePrice: 1,
                         imageKey: nil, specBadges: nil, sortOrder: 0),
            CatalogModel(modelId: "trailwind-city", categoryId: "commuter",
                         displayName: "Trailwind City", basePrice: 1,
                         imageKey: nil, specBadges: nil, sortOrder: 1)
        ]
        let resolved = ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: "Meridian Trailwind 2026",
            models: twoTrailwinds,
            variants: fixture.variants,
            categories: fixture.categories
        )
        XCTAssertNil(resolved,
            "\"trailwind\" matches two models, so it is not discriminating and the "
            + "resolver must decline rather than pick one.")
    }

    /// Exact equality must keep working — it is the correct answer when a tenant's
    /// offer names and catalog names do line up.
    func testExactDisplayNameStillMatches() {
        let resolved = resolve("Trailwind Adventurer")
        XCTAssertEqual(resolved?.model.modelId, "trailwind-adv")
    }

    func testEmptyAndNilOfferNamesReturnNil() {
        XCTAssertNil(resolve(""))
        XCTAssertNil(ConfiguratorFlow.resolveEstablishedConfiguration(
            offerModelName: nil, models: fixture.models,
            variants: fixture.variants, categories: fixture.categories))
    }

    /// Every seeded offer either establishes a vehicle or declines — never a
    /// half-resolved tuple, and never a crash.
    func testAllLiveOffersAreEitherResolvedOrCleanlyDeclined() {
        for name in liveOfferNames {
            if let r = resolve(name) {
                XCTAssertEqual(r.model.categoryId, r.category.categoryId,
                               "\(name): category must belong to the resolved model")
                XCTAssertEqual(r.variant.modelId, r.model.modelId,
                               "\(name): variant must belong to the resolved model")
            }
        }
    }
}
