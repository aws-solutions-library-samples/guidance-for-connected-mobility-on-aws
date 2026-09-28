import XCTest
@testable import MeridianMotorsCompanion

// MARK: - Tests

/// Red-phase skeleton for `AvxActionStatusBadge` (Task 2.1).
///
/// Wave B (CONTRACT-COUPLED): tests are LIVE — no XCTSkipIf predicate.
/// They will fail until Task 2.4 implements the badge rendering that produces
/// distinct labels and accessibility identifiers for all 8 status values.
///
/// **Why these tests are structural rather than view-rendering tests**: per the
/// task constraint, `label` and `accessibilityIdentifier` are declared `var`
/// (intentionally public) on `AvxActionStatusBadge` so distinctness is assertable
/// without SwiftUI rendering machinery. The tests assert on those computed
/// properties directly, making them lightweight and deterministic across Simulator
/// configurations.
///
/// Each test name is EXACT per tasks.md Task 2.1 Accept #2.
final class AvxActionStatusBadgeTests: XCTestCase {

    // MARK: - Test (a): all 8 statuses render distinct accessible labels

    /// For each of the 8 `AvxActionStatus` values, the badge must produce a
    /// distinct `label`. No two values may share a label.
    ///
    /// Correctness-critical (Addenda D4 + D5):
    /// - `dismissed` ≠ `cancelled` (Addendum D4)
    /// - `failed` ≠ `declined` (Addendum D5)
    ///
    /// **Why this is red**: Task 2.2 scaffolded `AvxActionStatusBadge` with the
    /// correct label strings per the contract. This test confirms they are distinct.
    /// It goes red if any two labels collide — including the two Addendum-guarded pairs.
    func test_all_8_statuses_render_distinctly() {
        // Arrange: create a badge for every status
        let badges = AvxActionStatus.allCases.map { status in
            (status: status, badge: AvxActionStatusBadge(status: status))
        }

        // Assert: enum size precondition
        XCTAssertEqual(
            AvxActionStatus.allCases.count, 8,
            "AvxActionStatus must have exactly 8 cases (addenda D4 + D5). "
            + "If the count has changed, update this test and the contract."
        )

        // Collect labels and accessibility identifiers
        let labels = badges.map(\.badge.label)
        let identifiers = badges.map(\.badge.accessibilityIdentifier)

        // Assert: all labels are unique
        let uniqueLabels = Set(labels)
        XCTAssertEqual(
            uniqueLabels.count, 8,
            "All 8 AvxActionStatus values must render with DISTINCT labels. "
            + "Duplicate labels found. Labels: \(labels.sorted()). "
            + "This includes the Addendum D4 requirement that 'dismissed' ≠ 'cancelled' "
            + "and the Addendum D5 requirement that 'failed' ≠ 'declined'."
        )

        // Assert: all accessibility identifiers are unique
        let uniqueIdentifiers = Set(identifiers)
        XCTAssertEqual(
            uniqueIdentifiers.count, 8,
            "All 8 AvxActionStatus values must render with DISTINCT accessibilityIdentifiers. "
            + "Duplicate identifiers found. Identifiers: \(identifiers.sorted())"
        )

        // Assert: no label is empty
        for (status, badge) in badges {
            XCTAssertFalse(
                badge.label.isEmpty,
                "Label for .\(status.rawValue) must not be empty"
            )
            XCTAssertFalse(
                badge.accessibilityIdentifier.isEmpty,
                "accessibilityIdentifier for .\(status.rawValue) must not be empty"
            )
        }
    }

    // MARK: - Test (b): dismissed ≠ cancelled (Addendum D4)

    /// Asserts that the rendered label for `.dismissed` is distinct from the
    /// rendered label for `.cancelled`.
    ///
    /// Addendum D4 extended the `Action.status` enum with `dismissed` to
    /// represent an owner-initiated dismissal via `POST /actions/{id}/dismiss`,
    /// semantically separate from `cancelled` (which an owner applies to an
    /// already-approved action). Collapsing the two labels to the same string
    /// makes the status ladder ambiguous on the card the owner reads to
    /// understand what happened.
    ///
    /// **Why this is red**: Task 2.2 scaffolded the correct labels; Task 2.4
    /// wires them into the rendered view. This test guards the contract at the
    /// property level.
    func test_dismissed_distinct_from_cancelled_label() {
        let dismissedBadge = AvxActionStatusBadge(status: .dismissed)
        let cancelledBadge = AvxActionStatusBadge(status: .cancelled)

        XCTAssertNotEqual(
            dismissedBadge.label, cancelledBadge.label,
            "Addendum D4: .dismissed and .cancelled must have DISTINCT labels. "
            + "dismissed='\(dismissedBadge.label)' vs cancelled='\(cancelledBadge.label)'. "
            + "An owner who dismissed a finding (via /actions/{id}/dismiss) must see "
            + "a different status from an owner who cancelled an approved action."
        )

        XCTAssertNotEqual(
            dismissedBadge.accessibilityIdentifier,
            cancelledBadge.accessibilityIdentifier,
            "Addendum D4: .dismissed and .cancelled must have DISTINCT accessibilityIdentifiers."
        )

        // Smoke-check that the label strings themselves are non-empty
        XCTAssertFalse(dismissedBadge.label.isEmpty, ".dismissed label must not be empty")
        XCTAssertFalse(cancelledBadge.label.isEmpty, ".cancelled label must not be empty")
    }

    // MARK: - Test (c): failed ≠ declined (Addendum D5)

    /// Asserts that the rendered label for `.failed` is distinct from the
    /// rendered label for `.declined`.
    ///
    /// Addendum D5 extended the `Action.status` enum with `failed` (executor
    /// exhausted retries after a 5xx) and `declined` (target system returned a
    /// terminal 4xx). Both are terminal states but carry distinct diagnostic
    /// information: `failed` may be retried after an operator intervenes;
    /// `declined` means the target actively refused and a different action path
    /// is needed. Collapsing them hides actionable diagnostic information.
    ///
    /// **Why this is red**: same as `test_dismissed_distinct_from_cancelled_label`.
    func test_failed_distinct_from_declined_label() {
        let failedBadge  = AvxActionStatusBadge(status: .failed)
        let declinedBadge = AvxActionStatusBadge(status: .declined)

        XCTAssertNotEqual(
            failedBadge.label, declinedBadge.label,
            "Addendum D5: .failed and .declined must have DISTINCT labels. "
            + "failed='\(failedBadge.label)' vs declined='\(declinedBadge.label)'. "
            + "A .failed action (5xx exhausted retries) has a different recovery path "
            + "from a .declined action (target refused with 4xx)."
        )

        XCTAssertNotEqual(
            failedBadge.accessibilityIdentifier,
            declinedBadge.accessibilityIdentifier,
            "Addendum D5: .failed and .declined must have DISTINCT accessibilityIdentifiers."
        )

        // Smoke-check that the label strings themselves are non-empty
        XCTAssertFalse(failedBadge.label.isEmpty, ".failed label must not be empty")
        XCTAssertFalse(declinedBadge.label.isEmpty, ".declined label must not be empty")
    }
}
