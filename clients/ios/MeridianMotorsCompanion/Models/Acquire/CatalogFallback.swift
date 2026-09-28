import Foundation

// MARK: - CatalogFallback

// [FIXTURE — NOT REAL CATALOG DATA]
//
// ## Purpose
// This fixture exists so the configurator flow renders usable content when:
//   1. The user is not signed in (no auth token available).
//   2. The `/acquire/catalog/{tenantId}` endpoint is unreachable or returns an error.
//
// ## Authoritative source
// Live catalog content comes exclusively from `GET /acquire/catalog/{tenantId}`.
// This fixture is the **degradation path** — not a cache, not a seed, not a substitute
// for the real endpoint. When the endpoint is available and the user is authenticated,
// this fixture is never consulted.
//
// ## Failure mode: degradation, not error
// Showing empty pickers (or blocking entirely) on the show floor is a worse failure than
// showing labelled fictional content. A visitor who sees "Crestwind 3-Row" in the picker
// and "FALLBACK" in the catalog version can still experience the full configuration
// journey. A visitor who sees a blank screen cannot.
//
// ## Fictional Meridian lineup only
// No real OEM brand strings, no real vendor names on accessories, no real part numbers.
// The content is self-consistent with `IvePackage.defaultFor(modelId:)` — `crestwind`
// maps to `.family`, `windrose` to `.commuter`, `trailwind` to `.sport`.

/// Client-side catalog fixture returned when the live endpoint is unavailable.
///
/// - SeeAlso: `AvailabilityContract.loadFixture()` — the same pattern applied to
///   the availability surface. `loadFixture()` here follows the same naming and
///   documentation convention established there.
enum CatalogFallback {

    // MARK: - Source enum (testability seam for Task 3.2)

    /// Where catalog content came from for the current session.
    ///
    /// `internal` and `Equatable` so the degradation policy is exercisable from
    /// `@testable import` without instantiating a view, starting a network session,
    /// or running `.task`.
    enum Source: Equatable {
        /// Content came from the live `/acquire/catalog/{tenantId}` endpoint.
        case remote
        /// Content came from the local fixture. `reason` records which failure path
        /// triggered fallback — logged via `NSLog` in `loadCatalog()` and preserved
        /// for operator diagnostics in bug reports.
        case fallback(reason: String)
    }

    // MARK: - Policy (pure, testable)

    /// Resolves catalog content given auth state and a fetch outcome.
    ///
    /// **Pure** — no side effects, no network, no view state. Both `loadCatalog()`
    /// (the thin view-side caller) and `ConfiguratorFlowCatalogFallbackTests` call
    /// this same function, so the policy is tested without a view harness and cannot
    /// drift between the test and the real path.
    ///
    /// - Parameters:
    ///   - isSignedIn: Whether the current session has a valid auth token.
    ///   - fetchResult: The result of a catalog fetch attempt, or `nil` when no fetch
    ///     was attempted (e.g. because the user was not signed in).
    /// - Returns: A `(response, source)` tuple. `source` is `.remote` only when the
    ///   fetch succeeded; otherwise `.fallback(reason:)` describes why the fixture
    ///   was used.
    static func resolve(
        isSignedIn: Bool,
        fetchResult: Result<CatalogResponse, AcquireError>?
    ) -> (response: CatalogResponse, source: Source) {
        if !isSignedIn {
            return (loadFixture(), .fallback(reason: "unsigned"))
        }
        guard let result = fetchResult else {
            // Signed in but no fetch was attempted — treat as fallback.
            return (loadFixture(), .fallback(reason: "no-fetch"))
        }
        switch result {
        case .success(let resp):
            return (resp, .remote)
        case .failure(let err) where err.isEndpointUnavailable:
            return (loadFixture(), .fallback(reason: "endpoint-unavailable"))
        case .failure:
            return (loadFixture(), .fallback(reason: "fetch-error"))
        }
    }

    // MARK: - Fixture payload

    /// Returns a clearly-labelled local fixture behind the `CatalogResponse` shape.
    ///
    /// **This is NOT real catalog data.** It exists so the configuration flow is
    /// exercisable during demo sessions before the live catalog endpoint is built.
    ///
    /// Payload is intentionally small (3-3-3-3-3):
    ///   - 3 categories: `family-suv`, `commuter`, `sport`
    ///   - 3 models: `crestwind-3row`, `windrose-city`, `trailwind-adv`
    ///   - 3 variants on `crestwind-3row`; 1 each on the other two
    ///   - 3 colors per model
    ///   - 3 accessories, all scoped to `family-suv`
    ///
    /// `catalogVersion: "fallback-2026-08-21"` — greppable in bug reports to
    /// confirm the fixture was active rather than the live endpoint.
    static func loadFixture() -> CatalogResponse {
        // [FIXTURE — NOT REAL CATALOG DATA]

        // Categories (3)
        let categories: [CatalogCategory] = [
            CatalogCategory(categoryId: "family-suv",  displayName: "Family SUV",
                            symbolName: "car.2.fill",    sortOrder: 0),
            CatalogCategory(categoryId: "commuter",    displayName: "Commuter",
                            symbolName: "car.fill",      sortOrder: 1),
            CatalogCategory(categoryId: "sport",       displayName: "Sport",
                            symbolName: "bolt.car.fill", sortOrder: 2),
        ]

        // Models (3)
        let models: [CatalogModel] = [
            CatalogModel(
                modelId:     "crestwind-3row",
                categoryId:  "family-suv",
                displayName: "Crestwind 3-Row",
                basePrice:   42990.00,
                imageKey:    nil,
                specBadges: [
                    CatalogModel.SpecBadge(label: "7 seats",       symbolName: "person.3.fill"),
                    CatalogModel.SpecBadge(label: "340 km range",  symbolName: "bolt.fill"),
                    CatalogModel.SpecBadge(label: "AWD",           symbolName: "circle.grid.cross.fill"),
                ],
                sortOrder: 0
            ),
            CatalogModel(
                modelId:     "windrose-city",
                categoryId:  "commuter",
                displayName: "Windrose City",
                basePrice:   31490.00,
                imageKey:    nil,
                specBadges: [
                    CatalogModel.SpecBadge(label: "5 seats",       symbolName: "person.2.fill"),
                    CatalogModel.SpecBadge(label: "410 km range",  symbolName: "bolt.fill"),
                ],
                sortOrder: 0
            ),
            CatalogModel(
                modelId:     "trailwind-adv",
                categoryId:  "sport",
                displayName: "Trailwind Adventurer",
                basePrice:   48750.00,
                imageKey:    nil,
                specBadges: [
                    CatalogModel.SpecBadge(label: "295 km range",   symbolName: "bolt.fill"),
                    CatalogModel.SpecBadge(label: "RWD",            symbolName: "circle.grid.cross"),
                    CatalogModel.SpecBadge(label: "0–100 in 4.2 s", symbolName: "speedometer"),
                ],
                sortOrder: 0
            ),
        ]

        // Variants — 3 for crestwind-3row, 1 each for the others
        let variants: [CatalogVariant] = [
            // Crestwind 3-Row
            CatalogVariant(variantId: "crestwind-3row-std", modelId: "crestwind-3row",
                           displayName: "Standard",   priceAdder: 0,    specOverrides: nil, sortOrder: 0),
            CatalogVariant(variantId: "crestwind-3row-lr",  modelId: "crestwind-3row",
                           displayName: "Long Range",  priceAdder: 4500, specOverrides: nil, sortOrder: 1),
            CatalogVariant(variantId: "crestwind-3row-sig", modelId: "crestwind-3row",
                           displayName: "Signature",   priceAdder: 9800, specOverrides: nil, sortOrder: 2),
            // Windrose City
            CatalogVariant(variantId: "windrose-city-std",  modelId: "windrose-city",
                           displayName: "Standard",   priceAdder: 0,    specOverrides: nil, sortOrder: 0),
            // Trailwind Adventurer
            CatalogVariant(variantId: "trailwind-adv-std",  modelId: "trailwind-adv",
                           displayName: "Standard",   priceAdder: 0,    specOverrides: nil, sortOrder: 0),
        ]

        // Colors — 3 per model, matched to each model's default variant
        let colors: [CatalogColor] = [
            // Crestwind 3-Row → default variant: crestwind-3row-std
            CatalogColor(colorId: "crestwind-deep-slate",
                         variantId: "crestwind-3row-std",
                         displayName: "Deep Slate",    hexColor: "#3D4A5C",
                         imageKey: nil, priceAdder: nil),
            CatalogColor(colorId: "crestwind-alpine-white",
                         variantId: "crestwind-3row-std",
                         displayName: "Alpine White",  hexColor: "#F4F1EA",
                         imageKey: nil, priceAdder: 500),
            CatalogColor(colorId: "crestwind-terracotta",
                         variantId: "crestwind-3row-std",
                         displayName: "Terracotta",    hexColor: "#B84A2C",
                         imageKey: nil, priceAdder: 500),
            // Windrose City → default variant: windrose-city-std
            CatalogColor(colorId: "windrose-deep-slate",
                         variantId: "windrose-city-std",
                         displayName: "Deep Slate",    hexColor: "#3D4A5C",
                         imageKey: nil, priceAdder: nil),
            CatalogColor(colorId: "windrose-alpine-white",
                         variantId: "windrose-city-std",
                         displayName: "Alpine White",  hexColor: "#F4F1EA",
                         imageKey: nil, priceAdder: 500),
            CatalogColor(colorId: "windrose-terracotta",
                         variantId: "windrose-city-std",
                         displayName: "Terracotta",    hexColor: "#B84A2C",
                         imageKey: nil, priceAdder: 500),
            // Trailwind Adventurer → default variant: trailwind-adv-std
            CatalogColor(colorId: "trailwind-deep-slate",
                         variantId: "trailwind-adv-std",
                         displayName: "Deep Slate",    hexColor: "#3D4A5C",
                         imageKey: nil, priceAdder: nil),
            CatalogColor(colorId: "trailwind-alpine-white",
                         variantId: "trailwind-adv-std",
                         displayName: "Alpine White",  hexColor: "#F4F1EA",
                         imageKey: nil, priceAdder: 500),
            CatalogColor(colorId: "trailwind-terracotta",
                         variantId: "trailwind-adv-std",
                         displayName: "Terracotta",    hexColor: "#B84A2C",
                         imageKey: nil, priceAdder: 500),
        ]

        // Accessories.
        //
        // Scoping matters more than count here. The first cut made all three
        // `family-suv`, because the original ask was phrased as "3 accessories a family
        // SUV would have" — but the demo's primary offer is the **Trailwind**, which is
        // `sport`, and `AccessoriesStep` filters on `categoryId == nil || == model's`.
        // So the headline path showed an EMPTY accessory list and the delivery date
        // never moved, which is the whole feature. Two of the three are genuinely
        // universal and are now `categoryId: nil`; the third-row bench stays family-only
        // because it does not exist on a five-seat car. Sport and commuter get one
        // fitting item each so no vehicle has an empty step.
        //
        // `leadDays` are chosen so each one individually crosses a week boundary.
        let accessories: [CatalogAccessory] = [
            CatalogAccessory(
                accessoryId: "cargo-rack",
                displayName: "Cargo Roof Rack",
                description: "Modular roof rack with 75 kg load rating. Fits all Meridian roof profiles.",
                price:       799,
                categoryId:  nil,          // universal
                imageKey:    nil,
                sortOrder:   0,
                leadDays:    3
            ),
            CatalogAccessory(
                accessoryId: "tow-package",
                displayName: "Tow Package",
                description: "Integrated tow bar rated to 2,500 kg. Includes wiring harness and hitch receiver.",
                price:       1299,
                categoryId:  nil,          // universal
                imageKey:    nil,
                sortOrder:   1,
                leadDays:    7
            ),
            CatalogAccessory(
                accessoryId: "family-third-row-seat",
                displayName: "Premium Third-Row Seat Pack",
                description: "Upgraded third-row heated seats with fold-flat mechanism and USB-C ports.",
                price:       1899,
                categoryId:  "family-suv",
                imageKey:    nil,
                sortOrder:   2,
                leadDays:    14
            ),
            CatalogAccessory(
                accessoryId: "sport-all-terrain-pack",
                displayName: "All-Terrain Wheel and Tyre Pack",
                description: "18-inch all-terrain wheel set with underbody protection for unsealed roads.",
                price:       2450,
                categoryId:  "sport",
                imageKey:    nil,
                sortOrder:   3,
                leadDays:    10
            ),
            CatalogAccessory(
                accessoryId: "commuter-city-pack",
                displayName: "City Convenience Pack",
                description: "Front and rear parking sensors with a folding cargo organiser.",
                price:       640,
                categoryId:  "commuter",
                imageKey:    nil,
                sortOrder:   4,
                leadDays:    4
            ),
        ]

        return CatalogResponse(
            tenantId:       "meridian-demo",
            catalogVersion: "fallback-2026-08-21",
            categories:     categories,
            models:         models,
            variants:       variants,
            colors:         colors,
            accessories:    accessories
        )
    }
}
