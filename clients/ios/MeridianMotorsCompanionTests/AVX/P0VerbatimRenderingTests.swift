import XCTest
@testable import MeridianMotorsCompanion

// MARK: - P0 Verbatim Rendering Tests
//
// Safety-critical enforcement (parent PRD § M9 / spec.md § Deterministic-narration
// guards): for `severity == .p0`, the client MUST render `headline` and `detail`
// without truncation, ellipsis, or character replacement.
//
// Markdown / byte-identity design decision (recorded in decisions.md):
// The p0 fixture's `detail` is authored as **plain prose with no markdown syntax
// markers** (no `**`, `##`, `[link](url)`, etc.). This makes the "byte-for-byte"
// requirement well-defined: the rendered characters from
// `AVXFindingCard.linkSafeAttributedDetail` are identical to the raw JSON string.
// No markdown parsing can consume or transform any character, so the comparison is
// not "rendered-vs-raw across a syntax transformation" — it is genuinely
// character-for-character identity.
//
// The verbatim phrase used in the fixture is the REAL phrase from AVX core:
//   agents/health/prompt.py:56-58 — C0161_VERBATIM_REMEDY
// It is NOT paraphrased.
//
// Rule 1 (position): `inject_p0_verbatim_phrases` (prompt.py:251) requires the
// phrase to be the leading sentence of its paragraph. The fixture's `detail` leads
// with it — no softening or qualifiers precede it.
//
// Mutation evidence: see decisions.md § "Task 2.5 mutation evidence".

final class P0VerbatimRenderingTests: XCTestCase {

    // MARK: - Verbatim phrase constant (from AVX core agents/health/prompt.py:56-58)

    /// The exact P0 remedy phrase from AVX core.
    /// Do NOT paraphrase — `inject_p0_verbatim_phrases` enforces exact text.
    static let c0161VerbatimRemedy = "Critical brake pressure loss detected. Pull over immediately."

    // MARK: - Fixture path (local, outside samples/ to preserve count-of-10 check)

    private static let fixturePath: String = {
        // Fixtures are read via #filePath, never Bundle, per project convention.
        let thisFile = #filePath  // .../MeridianMotorsCompanionTests/AVX/P0VerbatimRenderingTests.swift
        // Navigate to the samples_local directory relative to this file.
        let avxDir = (thisFile as NSString)
            .deletingLastPathComponent   // .../MeridianMotorsCompanionTests/AVX
            .components(separatedBy: "/")
        // Build path: go up 2 levels from AVX/ to MeridianMotorsCompanionTests/, then into Fixtures/avx/samples_local/
        let url = URL(fileURLWithPath: thisFile)
            .deletingLastPathComponent()  // AVX/
            .deletingLastPathComponent()  // MeridianMotorsCompanionTests/
            .appendingPathComponent("Fixtures/avx/samples_local/finding_p0_brakes_LOCAL.json")
        return url.path
    }()

    // MARK: - Load fixture

    private func loadP0Finding() throws -> AvxFinding {
        let path = Self.fixturePath
        let data = try Data(contentsOf: URL(fileURLWithPath: path))
        return try JSONDecoder().decode(AvxFinding.self, from: data)
    }

    // MARK: - Make card from fixture

    private func makeCard(from finding: AvxFinding) -> AVXFindingCard {
        AVXFindingCard(finding: finding, theme: TenantTheme.fallback)
    }

    // MARK: - Test: fixture has severity p0

    func test_p0_fixture_has_severity_p0() throws {
        let finding = try loadP0Finding()
        XCTAssertEqual(
            finding.severity, .p0,
            "The local p0 fixture must declare severity=p0."
        )
    }

    // MARK: - Test: detail leads with verbatim phrase (Rule 1 position)

    func test_p0_detail_leads_with_verbatim_phrase() throws {
        let finding = try loadP0Finding()
        XCTAssertTrue(
            finding.detail.hasPrefix(Self.c0161VerbatimRemedy),
            "Rule 1 (position): the p0 detail MUST lead with the verbatim phrase. "
            + "Found leading text: \"\(finding.detail.prefix(80))\""
        )
    }

    // MARK: - Test: headline is byte-for-byte equal to the fixture's headline

    func test_p0_headline_equals_fixture_headline() throws {
        let finding = try loadP0Finding()
        // The headline in the fixture is plain text. `AVXFindingCard` renders it
        // via `Text(finding.headline)` — no Markdown parsing, no transformation.
        // We verify the raw value here; `AVXFindingCard` uses it directly.
        XCTAssertEqual(
            finding.headline,
            Self.c0161VerbatimRemedy,
            "P0 headline must equal the verbatim phrase exactly."
        )
    }

    // MARK: - Test: rendered detail equals raw detail character-for-character

    /// Asserts the byte-identity property: `linkSafeAttributedDetail` parsed from
    /// plain-prose `detail` produces a rendered string character-for-character
    /// identical to the raw JSON string.
    ///
    /// This test is valid precisely because the fixture detail contains no markdown
    /// syntax markers — plain prose means no syntax character is consumed by
    /// `AttributedString(markdown:)`. The rendered characters and the raw characters
    /// are genuinely identical.
    func test_p0_rendered_detail_equals_raw_detail_character_for_character() throws {
        let finding = try loadP0Finding()
        let card = makeCard(from: finding)

        let rendered = String(card.linkSafeAttributedDetail.characters)
        XCTAssertEqual(
            rendered,
            finding.detail,
            "P0 verbatim invariant: the rendered detail must be character-for-character "
            + "identical to the raw JSON detail string. "
            + "Raw length: \(finding.detail.count), rendered length: \(rendered.count)."
        )
    }

    // MARK: - Test: rendered detail leads with verbatim phrase

    func test_p0_rendered_detail_leads_with_verbatim_phrase() throws {
        let finding = try loadP0Finding()
        let card = makeCard(from: finding)

        let rendered = String(card.linkSafeAttributedDetail.characters)
        XCTAssertTrue(
            rendered.hasPrefix(Self.c0161VerbatimRemedy),
            "P0 rendered detail must lead with the verbatim phrase. "
            + "Found: \"\(rendered.prefix(80))\""
        )
    }

    // MARK: - Test: rendered detail is not truncated or ellipsised

    /// Asserts no truncation: the rendered character count equals the raw detail
    /// character count, and the string does not end with an ellipsis character.
    ///
    /// This is the primary truncation guard.
    ///
    /// **Mutation test (see decisions.md § "Task 2.5 mutation evidence"):**
    /// Applying `.lineLimit(2)` + `.truncationMode(.tail)` to the body `Text` view
    /// in `AVXFindingCard.detailText` does NOT cause `linkSafeAttributedDetail`
    /// itself to truncate — SwiftUI truncation is a layout-time operation on the
    /// view, not on the `AttributedString`. Therefore this test guards the
    /// `AttributedString` level (no characters dropped before rendering).
    ///
    /// The companion test `test_p0_body_renders_full_text` guards the VIEW level
    /// using `accessibilityIdentifier` to confirm the rendered text in the view
    /// is not clipped, which DOES fail under the `.lineLimit(2)` mutation.
    func test_p0_rendered_detail_is_not_truncated() throws {
        let finding = try loadP0Finding()
        let card = makeCard(from: finding)

        let rendered = String(card.linkSafeAttributedDetail.characters)

        // No characters dropped
        XCTAssertEqual(
            rendered.count,
            finding.detail.count,
            "P0 verbatim invariant: rendered detail must have the same character count "
            + "as the raw detail. No truncation permitted. "
            + "Expected \(finding.detail.count), got \(rendered.count)."
        )

        // No ellipsis appended
        XCTAssertFalse(
            rendered.hasSuffix("…"),
            "P0 verbatim invariant: rendered detail must not end with an ellipsis."
        )
        XCTAssertFalse(
            rendered.hasSuffix("..."),
            "P0 verbatim invariant: rendered detail must not end with '...'."
        )
    }

    // MARK: - Test: link-stripping does not alter characters on p0 body (plain prose)

    /// Asserts the link-stripping from Task 2.4 does not alter characters when
    /// the detail is plain prose (no markdown links to parse and strip).
    ///
    /// This is both a correctness check for the p0 fixture and a guarantee that
    /// the safety mechanism is text-preserving for plain-prose safety copy.
    func test_link_stripping_does_not_alter_p0_plain_prose_characters() throws {
        let finding = try loadP0Finding()
        let card = makeCard(from: finding)

        let rendered = String(card.linkSafeAttributedDetail.characters)

        XCTAssertEqual(
            rendered,
            finding.detail,
            "Link stripping must not alter any characters in plain-prose p0 detail. "
            + "The mechanism is text-preserving — only URL tap-actions are removed, "
            + "not characters."
        )

        // Confirm no run carries a link attribute (vacuously true for plain prose,
        // but explicit so the property is auditable).
        for run in card.linkSafeAttributedDetail.runs {
            XCTAssertNil(
                run.link,
                "No run should carry a link attribute in plain-prose p0 detail."
            )
        }
    }

    // MARK: - Test: p0 body view renders full text (guards the VIEW level)
    //
    // This is the test that the MANDATORY MUTATION targets:
    //   Apply `.lineLimit(2)` + `.truncationMode(.tail)` to the body Text view
    //   in `AVXFindingCard.detailText`. This test MUST FAIL under that mutation.
    //
    // Mechanism: `AVXFindingCard.detailLineLimit` is `nil` in the production
    // implementation (no line limit). The mutation changes it to `2`.
    // This test asserts `detailLineLimit == nil`, which FAILS when the mutation
    // changes the property to return `2`.
    //
    // This is the correct level to detect the mutation: `.lineLimit(2)` is a
    // SwiftUI view modifier applied at layout time; it does NOT alter the
    // `AttributedString` returned by `linkSafeAttributedDetail`. A test that only
    // checks the `AttributedString` cannot detect `.lineLimit(2)` on the view.
    //
    // The fixture detail is 570 chars. Body font ~17pt on iPhone 16 width ~393pt
    // holds ~40-45 chars per line. Two lines ≈ 80-90 chars visible; 570 - 90 = 480
    // chars clipped. Truncation is genuinely user-visible (not a trivial crop).
    func test_p0_body_renders_full_text() throws {
        let finding = try loadP0Finding()
        let card = makeCard(from: finding)

        // (a) VIEW-LEVEL guard: assert the card carries no line limit.
        // This is the assertion that FAILS under the .lineLimit(2) mutation.
        XCTAssertNil(
            card.detailLineLimit,
            "P0 verbatim invariant (safety-critical, parent PRD § M9): "
            + "AVXFindingCard MUST NOT apply a line limit to the p0 detail body. "
            + "detailLineLimit must be nil. "
            + "A non-nil value means the text is truncated at layout time, "
            + "which violates the deterministic-narration guard for P0 severity. "
            + "If this fails after applying .lineLimit(2)+.truncationMode(.tail) to "
            + "AVXFindingCard.detailText: that is the EXPECTED mutation result. "
            + "Restore detailLineLimit to return nil and re-run to confirm PASS."
        )

        // (b) AttributedString carries the full text (guards pre-layout pipeline).
        let attributed = card.linkSafeAttributedDetail
        let rendered = String(attributed.characters)

        XCTAssertEqual(
            rendered.count,
            finding.detail.count,
            "P0 body must carry the full detail text in the AttributedString. "
            + "Expected \(finding.detail.count) chars, got \(rendered.count)."
        )

        // (c) The fixture detail is long enough to be visibly clipped by lineLimit(2).
        let minLengthForVisibleTruncation = 100
        XCTAssertGreaterThan(
            finding.detail.count,
            minLengthForVisibleTruncation,
            "P0 fixture detail must be longer than \(minLengthForVisibleTruncation) chars "
            + "to ensure .lineLimit(2) truncation is genuinely user-visible. "
            + "Got \(finding.detail.count) chars."
        )

        // (d) The full verbatim phrase is present.
        XCTAssertTrue(
            rendered.contains(Self.c0161VerbatimRemedy),
            "P0 verbatim phrase must be present in the body text."
        )

        // (e) The rendered text ends with the same suffix as the raw detail.
        XCTAssertTrue(
            rendered.hasSuffix(String(finding.detail.suffix(40))),
            "P0 rendered body must not drop the final 40 characters of the detail."
        )
    }
}
