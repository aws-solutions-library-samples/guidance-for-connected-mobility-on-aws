import XCTest
@testable import MeridianMotorsCompanion

// MARK: - Link safety tests for AVXFindingCard

/// Security-requirement test (carried from Group 1 security review Cycle 1,
/// Suggestion 3).
///
/// `AVXFindingCard` renders `Finding.detail` as Markdown. `detail` is
/// LLM-generated content arriving over the network. SwiftUI Markdown performs
/// automatic link detection, so a crafted or hallucinated URL would become a
/// tappable link on a safety card.
///
/// This test asserts that `AVXFindingCard.linkSafeAttributedDetail` strips all
/// `.link` attributes from the Markdown-parsed `AttributedString` before
/// rendering, so no URL in `detail` becomes tappable, regardless of what the
/// API delivers.
///
/// Critical distinction: the rendered CHARACTERS are preserved verbatim —
/// the safety of this mechanism is that it is text-preserving, not
/// text-replacing. Task 2.5's byte-identity assertion tests verbatim character
/// rendering; this test guards the orthogonal link-tap property.
final class AvxFindingCardLinkSafetyTests: XCTestCase {

    // MARK: - Helpers

    private func makeCard(detail: String) -> AVXFindingCard {
        AVXFindingCard(
            finding: AvxFinding(
                findingId: "FIND-link-test-001",
                findingKey: "health.brakes.test",
                agentId: .agent2VehicleHealth,
                agentVersion: "1.0.0-test",
                scope: AvxScope(vin: "TESTVIN0000000001", ownerId: nil, fleetId: nil, dealershipId: nil, districtId: nil),
                severity: .attention,
                computedAt: "2026-09-18T00:00:00Z",
                expiresAt: "2026-09-25T00:00:00Z",
                confidence: .high,
                findingKind: .healthBrakes,
                headline: "Test Finding",
                detail: detail,
                evidence: [],
                inputsHash: "abc123",
                tokensUsed: 42,
                costUsd: 0.001,
                disclosureTxt: nil,
                auditTrail: []
            ),
            theme: TenantTheme.fallback
        )
    }

    // MARK: - Test: URLs in detail are NOT rendered as tappable links

    /// Asserts that `linkSafeAttributedDetail` produces an `AttributedString`
    /// with zero runs carrying a non-nil `.link` attribute, even when `detail`
    /// contains well-formed URLs.
    ///
    /// This is the **primary security assertion** for this requirement.
    ///
    /// **Mutation test (run manually per tasks.md § Verify):**
    /// To verify this test is load-bearing, remove the `attributed[run.range].link = nil`
    /// line in `AVXFindingCard.linkSafeAttributedDetail`. The test will fail
    /// because at least one run will carry a non-nil `.link` attribute.
    /// Restore the line and the test returns to PASS.
    func test_detail_markdown_links_are_not_tappable() {
        // A crafted detail string containing URLs in two forms that SwiftUI
        // Markdown and AttributedString recognise as link-bearing:
        // 1. Explicit Markdown link syntax: [label](url)
        // 2. An inline URL that Markdown autodetects as a link
        let detailWithLinks = """
        Brake wear detected. For guidance, see [Meridian safety page](https://example.com/safety). \
        Also note: https://example.com/brake-guide — follow these steps.
        """

        let card = makeCard(detail: detailWithLinks)
        let attributed = card.linkSafeAttributedDetail

        // Assert: no run in the attributed string has a link attribute.
        // This is the property under test — a URL may appear as characters
        // but must not be a tappable href.
        var linksFound = 0
        for run in attributed.runs {
            if run.link != nil {
                linksFound += 1
            }
        }

        XCTAssertEqual(
            linksFound, 0,
            "SECURITY: linkSafeAttributedDetail must strip ALL link attributes. "
            + "Found \(linksFound) run(s) with non-nil .link in: \"\(detailWithLinks)\". "
            + "A crafted or hallucinated URL in Finding.detail must NOT become a "
            + "tappable href on a safety card."
        )
    }

    // MARK: - Test: characters are preserved verbatim (text is not altered)

    /// Asserts that stripping link attributes does NOT alter the visible
    /// character content of the rendered string.
    ///
    /// This is the complementary assertion to `test_detail_markdown_links_are_not_tappable`:
    /// the safety mechanism is text-preserving. Task 2.5's P0 verbatim test
    /// relies on this — if the sanitizer altered characters, Task 2.5 would fail,
    /// which is correct behaviour. This test asserts the mechanism is sound for
    /// the non-P0 case.
    func test_detail_characters_are_preserved_after_link_stripping() {
        let plainDetail = "Brake pads show 20% remaining. Schedule service within 2 weeks."
        let card = makeCard(detail: plainDetail)
        let attributed = card.linkSafeAttributedDetail

        // The string content of the attributed string must equal the input.
        // For plain text with no Markdown syntax, this is byte-identical.
        XCTAssertEqual(
            String(attributed.characters),
            plainDetail,
            "linkSafeAttributedDetail must preserve all characters verbatim. "
            + "The link-stripping mechanism must not alter visible text."
        )
    }

    // MARK: - Test: link-syntax text nodes are preserved as characters

    /// Asserts that the *text* of a Markdown link (the label) survives stripping —
    /// only the URL tap-action is removed, not the label characters.
    ///
    /// For example, `[Meridian safety page](https://example.com/safety)` should
    /// render the characters "Meridian safety page" with no tappable URL.
    func test_markdown_link_label_characters_are_preserved() {
        let detailWithLink = "See [the safety guide](https://example.com/safety) for steps."
        let card = makeCard(detail: detailWithLink)
        let attributed = card.linkSafeAttributedDetail

        let rendered = String(attributed.characters)

        // The label text "the safety guide" must be present in the output.
        XCTAssertTrue(
            rendered.contains("the safety guide"),
            "Link label characters ('the safety guide') must be preserved after link stripping. "
            + "Rendered output: \"\(rendered)\""
        )

        // The URL itself as a visible character sequence may or may not appear
        // (Markdown link syntax hides it); the key requirement is no tappable link.
        for run in attributed.runs {
            XCTAssertNil(
                run.link,
                "No run should carry a .link attribute after stripping. "
                + "Found non-nil link in rendered output: \"\(rendered)\""
            )
        }
    }

    // MARK: - Test: empty detail does not crash

    func test_empty_detail_is_safe() {
        let card = makeCard(detail: "")
        let attributed = card.linkSafeAttributedDetail
        XCTAssertEqual(String(attributed.characters), "")
    }

    // MARK: - Test: P0 severity detail with a URL — links stripped, characters intact

    /// Exercises the full intersection: a P0 finding whose detail contains a URL.
    /// Both the verbatim-rendering invariant (characters intact) and the link-safety
    /// invariant (no tappable URL) must hold simultaneously.
    func test_p0_detail_with_url_strips_links_preserves_text() {
        // P0 verbatim phrase from agents/health/prompt.py:56-58
        let p0Detail = "Critical brake pressure loss detected. Pull over immediately. "
            + "See https://meridian.example/emergency for assistance."

        let card = AVXFindingCard(
            finding: AvxFinding(
                findingId: "FIND-p0-link-test",
                findingKey: "health.brakes.p0",
                agentId: .agent2VehicleHealth,
                agentVersion: "1.0.0-test",
                scope: AvxScope(vin: "TESTVIN0000000001", ownerId: nil, fleetId: nil, dealershipId: nil, districtId: nil),
                severity: .p0,
                computedAt: "2026-09-18T00:00:00Z",
                expiresAt: "2026-09-25T00:00:00Z",
                confidence: .high,
                findingKind: .healthBrakes,
                headline: "Critical brake pressure loss detected. Pull over immediately.",
                detail: p0Detail,
                evidence: [],
                inputsHash: "p0abc123",
                tokensUsed: 55,
                costUsd: 0.002,
                disclosureTxt: nil,
                auditTrail: []
            ),
            theme: TenantTheme.fallback
        )

        let attributed = card.linkSafeAttributedDetail
        let rendered = String(attributed.characters)

        // Characters preserved
        XCTAssertTrue(
            rendered.contains("Critical brake pressure loss detected."),
            "P0 verbatim phrase must be present after link stripping."
        )
        XCTAssertTrue(
            rendered.contains("Pull over immediately."),
            "P0 verbatim phrase continuation must be present after link stripping."
        )

        // No tappable links
        for run in attributed.runs {
            XCTAssertNil(
                run.link,
                "No link attribute should survive stripping in a P0 detail with a URL."
            )
        }
    }
}

/// Finding-card content fixes from UAT 2026-09-25 (Alerts tab, `meridian.driver`):
/// the detail repeated the headline, evidence chips showed internal field paths
/// (and the same chip three times), and a routine "No open issues" finding
/// offered Approve and Dismiss.
///
/// Mutations verified (each fails at least one test here):
///   1. `showsDetail` drops the P0 exemption.
///   2. `showsDetail` always true (duplicate shown again).
///   3. `offersActions` true for routine.
///   4. `label(for:)` returns `entry.kind` unchanged.
///   5. `items(for:)` ids taken from `sourceRef` (collide within one run).
final class AVXFindingCardContentTests: XCTestCase {

    private let brakeLine =
        "Most recent brake service (2018-07-09) completed successfully with outcome 'resolved'. No open issues."

    private func finding(severity: AvxSeverity, headline: String, detail: String,
                         evidence: [AvxEvidence] = []) -> AvxFinding {
        AvxFinding(
            findingId: "FIND-uat-001", findingKey: "health.brakes.uat",
            agentId: .agent2VehicleHealth, agentVersion: "0.1.0",
            scope: AvxScope(vin: "MRDN0000000000013", ownerId: nil, fleetId: nil,
                            dealershipId: nil, districtId: nil),
            severity: severity, computedAt: "2026-09-24T19:45:41Z",
            expiresAt: "2026-10-02T19:45:41Z", confidence: .high,
            findingKind: .healthBrakes, headline: headline, detail: detail,
            evidence: evidence, inputsHash: "h", tokensUsed: 0, costUsd: 0,
            disclosureTxt: nil, auditTrail: [])
    }

    private func card(_ f: AvxFinding) -> AVXFindingCard {
        AVXFindingCard(finding: f, theme: TenantTheme.fallback)
    }

    private func ev(_ kind: String, _ value: AvxJSONValue) -> AvxEvidence {
        AvxEvidence(kind: kind, source: kind, sourceRef: "agent-2-health-run", value: value,
                    computedAt: "2026-09-24T19:45:41Z", provenance: .live, vin: nil)
    }

    // MARK: 1. Duplicate headline / detail

    func test_detailHidden_whenItRepeatsTheHeadline() {
        XCTAssertFalse(card(finding(severity: .routine, headline: brakeLine, detail: brakeLine)).showsDetail)
        XCTAssertFalse(card(finding(severity: .attention, headline: brakeLine, detail: brakeLine + "  ")).showsDetail)
    }

    func test_detailShown_whenItDiffers() {
        XCTAssertTrue(card(finding(severity: .routine, headline: "Brakes OK", detail: brakeLine)).showsDetail)
    }

    func test_p0_alwaysShowsDetail_evenWhenIdentical() {
        XCTAssertTrue(card(finding(severity: .p0, headline: brakeLine, detail: brakeLine)).showsDetail)
    }

    // MARK: 4. Nothing to act on

    func test_routineAndInformational_offerNoApproveOrDismiss() {
        XCTAssertFalse(card(finding(severity: .routine, headline: "a", detail: "b")).offersActions)
        XCTAssertFalse(card(finding(severity: .informational, headline: "a", detail: "b")).offersActions)
    }

    func test_actionableSeverities_offerApproveAndDismiss() {
        for s in [AvxSeverity.p0, .attention, .soon] {
            XCTAssertTrue(card(finding(severity: s, headline: "a", detail: "b")).offersActions, "\(s)")
        }
    }

    // MARK: 3. Evidence chips

    /// The live brakes finding's evidence: three entries, one shared source_ref.
    private var liveBrakesEvidence: [AvxEvidence] {
        [ev("adp.brakes.outcome", .string("resolved")),
         ev("adp.brakes.service_date", .string("2018-07-09")),
         ev("adp.brakes.dtc_codes", .string("[]"))]
    }

    func test_chipLabels_areReadable_andCarryShortValues() {
        let labels = AVXEvidenceChips.items(for: liveBrakesEvidence).map(\.label)
        XCTAssertEqual(labels, ["Brakes · outcome: resolved",
                                "Brakes · service date: 2018-07-09",
                                "Brakes · DTC codes: none"])
        for label in labels { XCTAssertFalse(label.contains("adp."), label) }
    }

    func test_chipIds_areUnique_whenEntriesShareASourceRef() {
        let ids = AVXEvidenceChips.items(for: liveBrakesEvidence).map(\.id)
        XCTAssertEqual(Set(ids).count, 3, "three evidence entries must draw three distinct chips")
    }

    func test_longOrStructuredValues_areLeftOff() {
        let long = ev("adp.brakes.note", .string(String(repeating: "x", count: 40)))
        let obj = ev("adp.brakes.detail", .object(["a": .integer(1)]))
        XCTAssertEqual(AVXEvidenceChips.items(for: [long, obj]).map(\.label),
                       ["Brakes · note", "Brakes · detail"])
    }
}
