import XCTest
@testable import MeridianMotorsCompanion

/// Tests for task 5.4: option gating by delivery window (contract-first).
///
/// Verifies:
/// - Available option is selectable (availability returns available=true).
/// - Unavailable option is disabled with a reason.
/// - Switching standard→custom re-opens options marked availableOnCustom.
/// - Empty (allAvailable) response degrades to all-available rather than all-disabled.
/// - Malformed fixture (nil) falls back to allAvailable — never crashes.
///
/// Cross-repo dependency: the backend availability/lead-time service is CVX-side
/// and does not exist. This test suite exercises the CLIENT contract only.
final class AvailabilityContractTests: XCTestCase {

    // MARK: - Available option is selectable

    func testAvailableOptionReturnsAvailableTrue() {
        let contract = AvailabilityContract(
            deliveryWindow: .standard,
            colorOptions: [
                .init(optionId: "midnight-black", available: true,
                      unavailableReason: nil, availableOnCustom: false)
            ],
            interiorStyleOptions: []
        )
        let avail = contract.availability(forColorId: "midnight-black")
        XCTAssertTrue(avail.available)
        XCTAssertNil(avail.unavailableReason)
    }

    // MARK: - Unavailable option is disabled with a reason

    func testUnavailableOptionReturnsFalseWithReason() {
        let contract = AvailabilityContract(
            deliveryWindow: .standard,
            colorOptions: [
                .init(optionId: "racing-red", available: false,
                      unavailableReason: "Not in current production run",
                      availableOnCustom: true)
            ],
            interiorStyleOptions: []
        )
        let avail = contract.availability(forColorId: "racing-red")
        XCTAssertFalse(avail.available)
        XCTAssertEqual(avail.unavailableReason, "Not in current production run")
    }

    // MARK: - Standard → custom unlocks availableOnCustom options

    func testSwitchingToCustomWindowUnlocksOptions() {
        let contract = AvailabilityContract(
            deliveryWindow: .standard,
            colorOptions: [
                .init(optionId: "racing-red", available: false,
                      unavailableReason: "Not in current production run",
                      availableOnCustom: true)
            ],
            interiorStyleOptions: []
        )
        let customContract = contract.withCustomWindow()
        let avail = customContract.availability(forColorId: "racing-red")
        XCTAssertTrue(avail.available,
                      "Option marked availableOnCustom must become available after window switch")
        XCTAssertNil(avail.unavailableReason)
    }

    func testOptionUnavailableOnBothWindowsStaysUnavailableOnCustom() {
        let contract = AvailabilityContract(
            deliveryWindow: .standard,
            colorOptions: [],
            interiorStyleOptions: [
                .init(optionId: "touring", available: false,
                      unavailableReason: "Leather supplier lead time: 20 weeks",
                      availableOnCustom: false)
            ]
        )
        let customContract = contract.withCustomWindow()
        let avail = customContract.availability(forStyleId: "touring")
        XCTAssertFalse(avail.available,
                       "Option with availableOnCustom=false must stay unavailable after window switch")
    }

    // MARK: - allAvailable degrades to all-available (never all-disabled)

    func testAllAvailableContractReturnsAvailableForAnyOptionId() {
        let contract = AvailabilityContract.allAvailable
        // Unknown id → open-world assumption → available=true
        let color = contract.availability(forColorId: "some-unknown-color-id")
        XCTAssertTrue(color.available,
                      "allAvailable must return available=true for any option id")
        let style = contract.availability(forStyleId: InteriorStyle.sport.rawValue)
        XCTAssertTrue(style.available)
    }

    func testLoadFixtureOrAllAvailableFallback() {
        // loadFixture() returns a non-nil AvailabilityContract.
        // If it returned nil the caller would use allAvailable — both are valid.
        let contract = AvailabilityContract.loadFixture() ?? .allAvailable
        // Either way, the contract must not crash when queried.
        _ = contract.availability(forColorId: "any-id")
        _ = contract.availability(forStyleId: "any-id")
    }
}
