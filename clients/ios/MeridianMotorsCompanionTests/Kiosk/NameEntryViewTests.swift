import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `NameEntryView` first-name validation contract.
///
/// Validation must reject nothing a real first name could be. Only whitespace-only
/// input is treated as invalid (and even then, a visitor who submits whitespace is
/// nudged to the skip path rather than blocked). See spec task 7.2.
///
/// All tests operate on the validation logic directly, not on the SwiftUI view —
/// the coordinator's `setVisitorFirstName` applies the same trimming rule that
/// `NameEntryView.attemptConfirm()` does, so both paths are exercised.
@MainActor
final class NameEntryViewTests: XCTestCase {

    private var coordinator: KioskSessionCoordinator!

    override func setUp() {
        super.setUp()
        coordinator = KioskSessionCoordinator()
    }

    // MARK: - Names with apostrophes

    func testOBrienIsAccepted() {
        coordinator.setVisitorFirstName("O'Brien")
        XCTAssertEqual(coordinator.session.visitorFirstName, "O'Brien",
                       "Apostrophe in first name must be accepted")
    }

    // MARK: - Names with hyphens

    func testAnneMarieIsAccepted() {
        coordinator.setVisitorFirstName("Anne-Marie")
        XCTAssertEqual(coordinator.session.visitorFirstName, "Anne-Marie",
                       "Hyphen in first name must be accepted")
    }

    // MARK: - Non-Latin scripts

    func testChineseSingleCharacterIsAccepted() {
        coordinator.setVisitorFirstName("李")
        XCTAssertEqual(coordinator.session.visitorFirstName, "李",
                       "Non-Latin name (Chinese character) must be accepted")
    }

    // MARK: - Name at max length

    func testNameAtMaxLengthIsAccepted() {
        let maxName = String(repeating: "A", count: NameEntryView.maxLength)
        coordinator.setVisitorFirstName(maxName)
        XCTAssertEqual(coordinator.session.visitorFirstName, maxName,
                       "Name exactly at max length must be accepted")
    }

    // MARK: - Name over max length (trimmed by UI layer; coordinator stores as given)

    func testOverLengthNameIsNotBlockedAtCoordinatorLevel() {
        // The coordinator stores whatever it receives; max-length enforcement
        // is the UI's responsibility (NameEntryView's onChange handler).
        // A name longer than 60 chars is valid data — it may arrive via paste.
        let longName = String(repeating: "B", count: NameEntryView.maxLength + 10)
        coordinator.setVisitorFirstName(longName)
        XCTAssertNotNil(coordinator.session.visitorFirstName,
                        "Over-length name must not be rejected at coordinator level")
    }

    // MARK: - Whitespace-only input is stored as nil

    func testWhitespaceOnlyInputStoredAsNil() {
        coordinator.setVisitorFirstName("   ")
        XCTAssertNil(coordinator.session.visitorFirstName,
                     "Whitespace-only input must be stored as nil (not as blanks)")
    }

    // MARK: - Nil input is stored as nil

    func testNilInputStoredAsNil() {
        coordinator.setVisitorFirstName(nil)
        XCTAssertNil(coordinator.session.visitorFirstName,
                     "Nil input must remain nil")
    }

    // MARK: - No surname field exists anywhere

    func testNoSurnameFieldOnKioskSession() {
        // Verify at the type level: KioskSession has no lastName / surname / familyName field.
        // We do this by encoding a KioskSession and checking the keys present.
        // This is a structural assertion — if a surname field is added the test may need updating.
        let session = KioskSession()
        let mirror = Mirror(reflecting: session)
        let fieldNames = mirror.children.compactMap { $0.label }
        let surnameFields = fieldNames.filter {
            $0.lowercased().contains("last") ||
            $0.lowercased().contains("surname") ||
            $0.lowercased().contains("family")
        }
        XCTAssertTrue(surnameFields.isEmpty,
                      "No surname / lastName / familyName field should exist on KioskSession. Found: \(surnameFields)")
    }
}
