import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `VehicleSweepView`'s bundled-asset registry and rotation modes.
///
/// The property that matters here is that **each asset gets the semantics it
/// actually supports**. Wrapping a partial arc snaps across the width of the car
/// on every cycle; ping-ponging a full turntable discards half the vehicle while
/// the label claims a rotation. Both are visible on screen, and neither would be
/// caught by a build.
final class VehicleSweepRegistryTests: XCTestCase {

    // MARK: - Rotation mode copy is derived, not passed

    /// The accessibility string is derived from the mode, so a partial arc cannot
    /// be described as a 360.
    ///
    /// The visible "Drag to rotate" / "Drag to look around" hint was REMOVED
    /// 2026-08-18 when the view switched to continuous automatic rotation — the
    /// motion is now the affordance. The accessibility string survives and carries
    /// the same honesty requirement: a VoiceOver user must not be promised a view
    /// the asset does not contain.
    func testPartialArcNeverClaims360() {
        let mode = VehicleSweepView.RotationMode.partialArc
        XCTAssertFalse(mode.accessibilityDescription.contains("360"),
            "A ~95° arc must not describe itself as 360 to a VoiceOver user")
    }

    func testFullTurntableAdvertises360() {
        let mode = VehicleSweepView.RotationMode.fullTurntable
        XCTAssertTrue(mode.accessibilityDescription.contains("360"))
    }

    /// Both modes rotate automatically now, so both must say so — a static-sounding
    /// description would misrepresent a moving image.
    func testBothModesDescribeThemselvesAsRotating() {
        for mode in [VehicleSweepView.RotationMode.fullTurntable, .partialArc] {
            XCTAssertTrue(mode.accessibilityDescription.lowercased().contains("rotating"),
                "\(mode) rotates automatically and should say so")
            XCTAssertFalse(mode.accessibilityDescription.isEmpty)
        }
    }

    // MARK: - Registry: known models

    func testCrestwindIsAPartialArc() {
        let sweep = VehicleSweepView.bundledSweep(make: "Meridian", model: "Crestwind")
        XCTAssertEqual(sweep?.subdirectory, "MeridianCrestwindSweep")
        XCTAssertEqual(sweep?.frameCount, 24)
        XCTAssertEqual(sweep?.rotationMode, .partialArc,
            "Crestwind art is a ~95° arc; wrapping it snaps across the car")
    }

    func testTrailwindIsAFullTurntable() {
        let sweep = VehicleSweepView.bundledSweep(make: "Meridian", model: "Trailwind")
        XCTAssertEqual(sweep?.subdirectory, "MeridianTrailwindSweep")
        XCTAssertEqual(sweep?.frameCount, 32)
        XCTAssertEqual(sweep?.rotationMode, .fullTurntable,
            "Trailwind art is a verified full revolution; clamping it discards half the vehicle")
    }

    func testWindroseIsAPartialArc() {
        let sweep = VehicleSweepView.bundledSweep(make: "Meridian", model: "Windrose")
        XCTAssertEqual(sweep?.subdirectory, "MeridianWindroseSweep")
        XCTAssertEqual(sweep?.frameCount, 32)
        XCTAssertEqual(sweep?.rotationMode, .partialArc,
            "Windrose art is a ~140° arc reaching the rear but not closing; "
            + "wrapping it would jump ~180° from rear to front")
    }

    func testAzimuthIsAPartialArc() {
        let sweep = VehicleSweepView.bundledSweep(make: "Meridian", model: "Azimuth")
        XCTAssertEqual(sweep?.subdirectory, "MeridianAzimuthSweep")
        XCTAssertEqual(sweep?.frameCount, 24)
        XCTAssertEqual(sweep?.rotationMode, .partialArc,
            "Azimuth art is a ~85° arc pivoting through the front; it has no rear view")
    }

    /// Matching is case-insensitive and tolerates a suffix, mirroring
    /// `IvePackage.defaultFor(modelId:)`. Catalog model ids arrive in several
    /// shapes ("Trailwind", "trailwind-sport") and must resolve the same art.
    func testModelMatchingIsCaseInsensitiveAndToleratesSuffixes() {
        for variant in ["Trailwind", "trailwind", "TRAILWIND", "trailwind-sport",
                        "Meridian Trailwind Signature"] {
            XCTAssertEqual(
                VehicleSweepView.bundledSweep(make: "Meridian", model: variant)?.subdirectory,
                "MeridianTrailwindSweep",
                "\(variant) should resolve to the Trailwind sweep")
        }
    }

    // MARK: - Generation resolution

    /// A pre-redesign Trailwind must resolve the 2023 assets.
    func testPreRedesignTrailwindResolvesOlderGeneration() {
        for year in [2021, 2022, 2023, 2024] {
            XCTAssertEqual(
                VehicleSweepView.bundledSweep(make: "Meridian", model: "Trailwind", year: year)?.subdirectory,
                "MeridianTrailwind2023Sweep", "\(year) should use the 2023 sweep")
            XCTAssertEqual(
                VehicleSweepView.bundledStaticImageName(make: "Meridian", model: "Trailwind", year: year),
                "MeridianTrailwind2023", "\(year) should use the 2023 hero")
        }
    }

    /// A post-redesign Trailwind, or one with no year at all, must resolve current-gen.
    func testCurrentTrailwindAndUnknownYearResolveCurrentGeneration() {
        for year in [2025, 2026, 2027] {
            XCTAssertEqual(
                VehicleSweepView.bundledSweep(make: "Meridian", model: "Trailwind", year: year)?.subdirectory,
                "MeridianTrailwindSweep", "\(year) should use the current sweep")
        }
        // No year → current generation, never the outgoing car.
        XCTAssertEqual(
            VehicleSweepView.bundledSweep(make: "Meridian", model: "Trailwind", year: nil)?.subdirectory,
            "MeridianTrailwindSweep")
    }

    /// The two generations must be genuinely different assets, or the split is
    /// decorative — which was the whole point of adding a second render.
    func testTrailwindGenerationsResolveDistinctAssets() {
        let old = VehicleSweepView.bundledSweep(make: "Meridian", model: "Trailwind", year: 2023)
        let new = VehicleSweepView.bundledSweep(make: "Meridian", model: "Trailwind", year: 2026)
        XCTAssertNotEqual(old?.subdirectory, new?.subdirectory)
        XCTAssertNotEqual(
            VehicleSweepView.bundledStaticImageName(make: "Meridian", model: "Trailwind", year: 2023),
            VehicleSweepView.bundledStaticImageName(make: "Meridian", model: "Trailwind", year: 2026))
    }

    /// A model with only ONE generation bundled must fall back to it for ANY year,
    /// not return nil. An old Crestwind should show the Crestwind we have; trading a
    /// slightly-wrong model year for no vehicle at all is the worse outcome.
    func testModelsWithOneGenerationFallBackForOldYears() {
        for model in ["Crestwind", "Windrose", "Azimuth"] {
            for year in [2019, 2023, 2026] {
                XCTAssertNotNil(
                    VehicleSweepView.bundledSweep(make: "Meridian", model: model, year: year),
                    "\(model) \(year) must fall back to its only generation, not vanish")
            }
            // And must NOT pick up the Trailwind's 2023 suffix.
            XCTAssertEqual(
                VehicleSweepView.bundledStaticImageName(make: "Meridian", model: model, year: 2023),
                "Meridian\(model)",
                "\(model) must not inherit the Trailwind's generation suffix")
        }
    }

    /// Offers carry the year inside the marketing name. A 2026 offer must show the
    /// new car; an offer with no year must also show the new car, because an offer is
    /// for a NEW vehicle.
    func testOfferNameYearSelectsGeneration() {
        XCTAssertEqual(
            VehicleSweepView.bundledStaticImageName(forOfferModelName: "Meridian Trailwind 2026"),
            "MeridianTrailwind")
        XCTAssertEqual(
            VehicleSweepView.bundledStaticImageName(forOfferModelName: "Meridian Trailwind"),
            "MeridianTrailwind",
            "An offer with no year is for a new vehicle and must not show the outgoing car")
        XCTAssertEqual(
            VehicleSweepView.bundledStaticImageName(forOfferModelName: "Meridian Trailwind 2023"),
            "MeridianTrailwind2023",
            "An explicitly-2023 offer name should resolve the older art")
    }

    /// A number that is not a plausible model year must not be read as one.
    func testNonYearNumbersInOfferNamesAreIgnored() {
        XCTAssertEqual(
            VehicleSweepView.bundledStaticImageName(forOfferModelName: "Meridian Trailwind GT 500"),
            "MeridianTrailwind")
        XCTAssertEqual(
            VehicleSweepView.bundledStaticImageName(forOfferModelName: "Meridian Trailwind 1200 Sport"),
            "MeridianTrailwind")
    }

    // MARK: - Registry coverage in both directions

    /// The full Meridian lineup. Kept as one list so the two coverage tests below
    /// cannot drift apart, and so adding a fifth model forces a decision about its
    /// art rather than silently inheriting a glyph.
    private static let lineup = ["Crestwind", "Trailwind", "Windrose", "Azimuth"]

    /// Every model in the lineup has art as of 2026-08-18, so every one must
    /// resolve. This is the direction that catches a model whose art shipped but
    /// which was never registered — it would render a glyph and nothing would fail.
    func testEveryLineupModelResolves() {
        for model in Self.lineup {
            XCTAssertNotNil(VehicleSweepView.bundledSweep(make: "Meridian", model: model),
                "\(model) is in the lineup and its art ships; it must resolve")
            XCTAssertNotNil(VehicleSweepView.bundledStaticImageName(make: "Meridian", model: model),
                "\(model) static fallback must resolve")
        }
    }

    /// A model NOT in the lineup must return nil rather than borrow another body
    /// style. Rendering an SUV for a sedan — or vice versa — is the "wrong vehicle"
    /// failure this codebase treats as worse than showing none (see
    /// `UpgradeFlow.heroImage`).
    ///
    /// Every lineup member had art by 2026-08-18, so this now guards the *next*
    /// model rather than a current gap. That is deliberate: the assertion that
    /// used to list Azimuth and Windrose would have kept passing while being
    /// wrong once their art landed.
    func testModelOutsideTheLineupReturnsNilRatherThanBorrowingABody() {
        for model in ["Zephyr", "Meridian Zephyr GT", "Sirocco", "unknown-model"] {
            XCTAssertNil(VehicleSweepView.bundledSweep(make: "Meridian", model: model),
                "\(model) is not in the lineup and must not inherit another model's art")
            XCTAssertNil(VehicleSweepView.bundledStaticImageName(make: "Meridian", model: model),
                "\(model) static image must not fall back either")
        }
    }

    /// No two models may share a frame directory. A copy-paste in the registry
    /// that pointed two models at one sweep would show the same vehicle for both,
    /// and every other test here would still pass.
    func testNoTwoModelsShareASweepDirectory() {
        var seen: [String: String] = [:]
        for model in Self.lineup {
            guard let dir = VehicleSweepView.bundledSweep(make: "Meridian", model: model)?.subdirectory
            else { continue }
            if let existing = seen[dir] {
                XCTFail("\(model) and \(existing) both point at \(dir) — one of them shows the wrong vehicle")
            }
            seen[dir] = model
        }
        XCTAssertEqual(seen.count, Self.lineup.count, "Each model needs its own sweep directory")
    }

    func testUnknownModelReturnsNil() {
        XCTAssertNil(VehicleSweepView.bundledSweep(make: "Meridian", model: "Zephyr"))
        XCTAssertNil(VehicleSweepView.bundledSweep(make: "Meridian", model: nil))
        XCTAssertNil(VehicleSweepView.bundledSweep(make: "Meridian", model: ""))
    }

    // MARK: - Only fictional brands may be bundled

    /// Real-OEM brand strings are canary-forbidden in committed source; their
    /// imagery must arrive at runtime via `VSA_VEHICLE_360_BASE_URL`. Only the
    /// fictional Meridian brand resolves here.
    func testOnlyMeridianResolves() {
        for make in ["Chevrolet", "Toyota", "Meridian Motors", "meridian", ""] {
            XCTAssertNil(VehicleSweepView.bundledSweep(make: make, model: "Trailwind"),
                "\(make) must not resolve bundled art")
        }
        XCTAssertNil(VehicleSweepView.bundledSweep(make: nil, model: "Trailwind"))
        // Exact match only.
        XCTAssertNotNil(VehicleSweepView.bundledSweep(make: "Meridian", model: "Trailwind"))
    }

    // MARK: - Sweep and static fallback must agree

    /// If a model has a sweep, its static fallback must depict the SAME vehicle.
    /// A sweep and a static image disagreeing means the hero changes vehicle when
    /// frames fail to load.
    func testStaticFallbackAgreesWithSweepForEveryKnownModel() {
        let cases = [("Crestwind", "MeridianCrestwind"),
                     ("Trailwind", "MeridianTrailwind"),
                     ("Windrose", "MeridianWindrose"),
                     ("Azimuth", "MeridianAzimuth")]
        for (model, expectedImage) in cases {
            XCTAssertNotNil(VehicleSweepView.bundledSweep(make: "Meridian", model: model))
            XCTAssertEqual(
                VehicleSweepView.bundledStaticImageName(make: "Meridian", model: model),
                expectedImage,
                "\(model)'s static fallback must depict the same vehicle as its sweep")
        }
    }

    // MARK: - Declared frame counts match what actually ships

    /// `frameCount` higher than the shipped file count silently shortens the arc
    /// rather than failing, so a stale count is invisible at runtime. This asserts
    /// the registry against the real bundle.
    func testDeclaredFrameCountsMatchBundledFiles() {
        for model in Self.lineup {
            guard let sweep = VehicleSweepView.bundledSweep(make: "Meridian", model: model) else {
                return XCTFail("\(model) should have a registry entry")
            }
            var found = 0
            for i in 1...sweep.frameCount {
                if Bundle(for: Self.self).url(forResource: "\(i)", withExtension: "jpg",
                                              subdirectory: sweep.subdirectory) != nil
                    || Bundle.main.url(forResource: "\(i)", withExtension: "jpg",
                                       subdirectory: sweep.subdirectory) != nil {
                    found += 1
                }
            }
            XCTAssertEqual(found, sweep.frameCount,
                "\(model): registry declares \(sweep.frameCount) frames but \(found) are in the bundle")
        }
    }
}
