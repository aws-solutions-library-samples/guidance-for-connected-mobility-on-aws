import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `PreferredDealer` — the stored "this is my dealer" choice.
///
/// Every test uses its own `UserDefaults` suite so nothing touches the simulator's real
/// defaults and cases cannot see each other's writes.
final class PreferredDealerTests: XCTestCase {

    private var defaults: UserDefaults!
    private var suiteName: String!

    override func setUp() {
        super.setUp()
        suiteName = "PreferredDealerTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    // MARK: - Fixtures

    private func center(
        _ id: String,
        name: String = "Meridian Northside",
        type: String = "dealer",
        address: String = "1400 N Wells St, Chicago, IL 60610",
        phone: String? = "(312) 555-0143",
        distance: Double? = 4.2,
        slots: [String] = ["Tuesday May 19 at 10:00 AM"]
    ) -> ServiceCenter {
        ServiceCenter(
            centerId: id,
            name: name,
            type: type,
            address: address,
            phone: phone,
            distanceMiles: distance,
            rating: 4.6,
            averageWaitDays: 2,
            fleetDiscount: false,
            brandsServiced: ["Meridian"],
            nextAvailableSlots: slots,
            hours: nil
        )
    }

    // MARK: - Round trip

    func testSetThenReadReturnsTheSameDealer() {
        PreferredDealer.set(center("c1"), for: "DRV-1", defaults: defaults)
        let got = PreferredDealer.current(for: "DRV-1", defaults: defaults)
        XCTAssertEqual(got?.centerId, "c1")
        XCTAssertEqual(got?.name, "Meridian Northside")
        XCTAssertEqual(got?.phone, "(312) 555-0143")
    }

    func testNoPreferenceReturnsNil() {
        XCTAssertNil(PreferredDealer.current(for: "DRV-1", defaults: defaults))
    }

    func testClearRemovesThePreference() {
        PreferredDealer.set(center("c1"), for: "DRV-1", defaults: defaults)
        PreferredDealer.clear(for: "DRV-1", defaults: defaults)
        XCTAssertNil(PreferredDealer.current(for: "DRV-1", defaults: defaults))
    }

    // MARK: - Per-driver isolation

    /// The defect this guards is a preference leaking between personas on a shared demo
    /// device — one driver seeing another's dealer.
    func testPreferenceIsIsolatedPerDriver() {
        PreferredDealer.set(center("c1", name: "Northside"), for: "DRV-1", defaults: defaults)
        PreferredDealer.set(center("c2", name: "Southgate"), for: "DRV-2", defaults: defaults)

        XCTAssertEqual(PreferredDealer.current(for: "DRV-1", defaults: defaults)?.name, "Northside")
        XCTAssertEqual(PreferredDealer.current(for: "DRV-2", defaults: defaults)?.name, "Southgate")
    }

    /// Absent a driver id there must be NO preference at all. A shared fallback slot is
    /// precisely how the leak above would happen, so this asserts the write is refused
    /// rather than redirected.
    func testNilDriverIdStoresNothing() {
        XCTAssertNil(PreferredDealer.key(for: nil))
        XCTAssertNil(PreferredDealer.key(for: ""))
        XCTAssertNil(PreferredDealer.set(center("c1"), for: nil, defaults: defaults))
        XCTAssertNil(PreferredDealer.current(for: nil, defaults: defaults))
        // And nothing was written under any key.
        XCTAssertTrue(
            defaults.dictionaryRepresentation().keys
                .filter { $0.hasPrefix(PreferredDealer.storageKeyPrefix) }
                .isEmpty
        )
    }

    // MARK: - What is deliberately NOT cached

    /// Slot availability must not survive into the stored snapshot. A cached
    /// "Tuesday at 10" becomes a lie within a day, and a confidently wrong appointment time
    /// is worse than no time at all.
    func testStoredSnapshotDropsMomentInTimeFields() {
        let stored = PreferredDealer.Stored(from: center("c1", distance: 4.2, slots: ["Tuesday at 10"]))
        // The type has no slot or distance storage at all — assert via the encoded form so
        // this fails if either is ever added back.
        let json = PreferredDealer.encode(stored) ?? ""
        XCTAssertFalse(json.contains("Tuesday"))
        XCTAssertFalse(json.contains("distance"))
        XCTAssertFalse(json.contains("4.2"))
    }

    // MARK: - isPreferred

    func testIsPreferredMatchesOnIdNotName() {
        PreferredDealer.set(center("c1", name: "Meridian Northside"), for: "DRV-1", defaults: defaults)
        // Same display name, different branch. Matching by name would be a bug waiting for
        // two branches to share one.
        let sameNameDifferentBranch = center("c9", name: "Meridian Northside")
        XCTAssertFalse(PreferredDealer.isPreferred(sameNameDifferentBranch, for: "DRV-1", defaults: defaults))
        XCTAssertTrue(PreferredDealer.isPreferred(center("c1"), for: "DRV-1", defaults: defaults))
    }

    // MARK: - Pinning

    func testPreferredDealerIsPinnedFirstAndOthersKeepServerOrder() {
        PreferredDealer.set(center("c3"), for: "DRV-1", defaults: defaults)
        let list = [center("c1"), center("c2"), center("c3"), center("c4")]

        let pinned = PreferredDealer.pinPreferredFirst(list, for: "DRV-1", defaults: defaults)

        XCTAssertEqual(pinned.map(\.centerId), ["c3", "c1", "c2", "c4"])
    }

    /// A preference is a default, not a cage: pinning reorders, it never filters. The nearest
    /// bay must remain reachable, because it is the right answer when the car is broken.
    func testPinningNeverDropsAlternatives() {
        PreferredDealer.set(center("c3"), for: "DRV-1", defaults: defaults)
        let list = [center("c1"), center("c2"), center("c3")]
        let pinned = PreferredDealer.pinPreferredFirst(list, for: "DRV-1", defaults: defaults)
        XCTAssertEqual(pinned.count, list.count)
        XCTAssertEqual(Set(pinned.map(\.centerId)), Set(list.map(\.centerId)))
    }

    func testPinningIsANoOpWithoutAPreference() {
        let list = [center("c1"), center("c2")]
        XCTAssertEqual(
            PreferredDealer.pinPreferredFirst(list, for: "DRV-1", defaults: defaults).map(\.centerId),
            ["c1", "c2"]
        )
    }

    /// The dealer being absent from a result set means "not returned for this capability near
    /// this location" — emphatically not "no longer my dealer".
    func testPinningIsANoOpWhenThePreferredDealerIsAbsent() {
        PreferredDealer.set(center("c99"), for: "DRV-1", defaults: defaults)
        let list = [center("c1"), center("c2")]
        XCTAssertEqual(
            PreferredDealer.pinPreferredFirst(list, for: "DRV-1", defaults: defaults).map(\.centerId),
            ["c1", "c2"]
        )
    }

    // MARK: - Refresh

    func testRefreshUpdatesStaleFieldsButKeepsChosenAt() {
        PreferredDealer.set(center("c1", name: "Old Name", phone: "(312) 555-0000"), for: "DRV-1", defaults: defaults)
        let original = PreferredDealer.current(for: "DRV-1", defaults: defaults)!

        let live = [center("c1", name: "Meridian Northside", phone: "(312) 555-0143")]
        let refreshed = PreferredDealer.refresh(from: live, for: "DRV-1", defaults: defaults)

        XCTAssertEqual(refreshed?.name, "Meridian Northside")
        XCTAssertEqual(refreshed?.phone, "(312) 555-0143")
        XCTAssertEqual(refreshed?.chosenAt, original.chosenAt, "chosenAt records when the driver chose, not when we last saw them")
    }

    func testRefreshLeavesThePreferenceAloneWhenAbsentFromTheList() {
        PreferredDealer.set(center("c1", name: "Keep Me"), for: "DRV-1", defaults: defaults)
        let refreshed = PreferredDealer.refresh(from: [center("c2")], for: "DRV-1", defaults: defaults)
        XCTAssertEqual(refreshed?.name, "Keep Me")
        XCTAssertEqual(PreferredDealer.current(for: "DRV-1", defaults: defaults)?.name, "Keep Me")
    }

    func testRefreshIsANoOpWithoutAPreference() {
        XCTAssertNil(PreferredDealer.refresh(from: [center("c1")], for: "DRV-1", defaults: defaults))
    }

    // MARK: - Robustness

    /// A corrupt value should cost the driver one re-pick, not the launch.
    func testCorruptStoredValueDecodesAsNoPreference() {
        defaults.set("{not json", forKey: PreferredDealer.key(for: "DRV-1")!)
        XCTAssertNil(PreferredDealer.current(for: "DRV-1", defaults: defaults))
    }

    // MARK: - Switching

    /// Switching must be a plain overwrite — no history, no second slot, no confirmation
    /// state to get stuck in. The first cut of this feature made changing a dealer require
    /// completing a real booking; these assert the store itself imposes no such ceremony.
    func testSwitchingDealerIsAPlainOverwrite() {
        PreferredDealer.set(center("c1", name: "Northside"), for: "DRV-1", defaults: defaults)
        PreferredDealer.set(center("c2", name: "Southgate"), for: "DRV-1", defaults: defaults)

        XCTAssertEqual(PreferredDealer.current(for: "DRV-1", defaults: defaults)?.centerId, "c2")
        // Exactly one stored preference for this driver, not two.
        let keys = defaults.dictionaryRepresentation().keys.filter { $0.hasPrefix(PreferredDealer.storageKeyPrefix) }
        XCTAssertEqual(keys.count, 1)
    }

    /// `chosenAt` must advance on a switch — it records when THIS dealer was chosen, so a
    /// switch is a new choice, not an edit of the old one. (Contrast `refresh`, which
    /// preserves it, because seeing a dealer again is not choosing them again.)
    func testSwitchingResetsChosenAt() throws {
        PreferredDealer.set(center("c1"), for: "DRV-1", defaults: defaults)
        let first = try XCTUnwrap(PreferredDealer.current(for: "DRV-1", defaults: defaults))
        // ISO8601 encoding is second-granular, so a sub-second switch can legitimately share
        // a timestamp. Assert not-earlier rather than strictly-later.
        PreferredDealer.set(center("c2"), for: "DRV-1", defaults: defaults)
        let second = try XCTUnwrap(PreferredDealer.current(for: "DRV-1", defaults: defaults))
        XCTAssertGreaterThanOrEqual(second.chosenAt, first.chosenAt)
        XCTAssertEqual(second.centerId, "c2")
    }

    /// Switching away and back leaves no residue from the intermediate choice.
    func testSwitchingBackIsClean() {
        PreferredDealer.set(center("c1", name: "Northside"), for: "DRV-1", defaults: defaults)
        PreferredDealer.set(center("c2", name: "Southgate"), for: "DRV-1", defaults: defaults)
        PreferredDealer.set(center("c1", name: "Northside"), for: "DRV-1", defaults: defaults)

        let got = PreferredDealer.current(for: "DRV-1", defaults: defaults)
        XCTAssertEqual(got?.centerId, "c1")
        XCTAssertEqual(got?.name, "Northside")
    }

    // MARK: - Segment policy

    /// Only the OEM owner is offered a dealer of their own. A fleet driver does not choose
    /// where the van is serviced and a renter has no relationship at all.
    func testOnlyTheOemSegmentSurfacesAPreferredDealer() {
        XCTAssertTrue(LayoutSegment.oem.showsPreferredDealer)
        XCTAssertFalse(LayoutSegment.fleet.showsPreferredDealer)
        XCTAssertFalse(LayoutSegment.rental.showsPreferredDealer)
    }
}
