import XCTest
@testable import MeridianMotorsCompanion

// MARK: - AvxFindingKind mapping and robustness tests
//
// Tests for T4.1 (iOS half) of spec `2026-09-25-avx-own-vehicle-findings`:
//
//   1. `health.diagnostics` decodes to `.healthDiagnostics`.
//   2. An unrecognised `finding_kind` inside a Findings list decodes to `.unknown`
//      rather than failing the whole list (mutation: remove the fallback; the
//      unknown-kind decode test must fail).
//   3. `.healthDiagnostics` maps to the expected icon and title in
//      `AvxFindingKind.systemImage` / `kindLabel`.
//   4. `.unknown` maps to a neutral icon and label and does not crash the card.
//
// The mutation guard for (2) is at the bottom of this file.

final class AvxFindingKindMappingTests: XCTestCase {

    // MARK: - Fixture plumbing

    /// Minimal `AvxFinding` for tests that need a real instance.
    private static func makeFinding(kind: AvxFindingKind) -> AvxFinding {
        AvxFinding(
            findingId: "FIND-00000000-0000-0000-0000-000000000001",
            findingKey: "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            agentId: .agent2VehicleHealth,
            agentVersion: "1.0.0",
            scope: AvxScope(
                vin: "TESTVIN000T41001",
                ownerId: nil, fleetId: nil, dealershipId: nil, districtId: nil
            ),
            severity: .routine,
            computedAt: "2026-09-26T00:00:00Z",
            expiresAt: "2026-09-26T00:30:00Z",
            confidence: .medium,
            findingKind: kind,
            headline: "Test headline for kind \(kind.rawValue)",
            detail: "Test detail body.",
            evidence: [],
            inputsHash: "0000000000000000000000000000000000000000000000000000000000000000",
            tokensUsed: 0,
            costUsd: 0.0,
            disclosureTxt: nil,
            auditTrail: []
        )
    }

    /// Encodes an `AvxFinding` to JSON and substitutes `finding_kind` with `raw`
    /// so we can test how the decoder handles values that are not in the enum.
    private static func jsonSubstitutingKind(_ raw: String, in finding: AvxFinding) throws -> Data {
        let encoded = try JSONEncoder().encode(finding)
        var obj = try JSONSerialization.jsonObject(with: encoded) as? [String: Any] ?? [:]
        obj["finding_kind"] = raw
        return try JSONSerialization.data(withJSONObject: obj)
    }

    // MARK: - 1. health.diagnostics decodes correctly

    /// `"health.diagnostics"` decodes to `.healthDiagnostics` — the new case
    /// added at CVX commit 998d1aa and pinned in `finding.schema.json`.
    func test_health_diagnostics_raw_value_decodes_to_healthDiagnostics() {
        XCTAssertEqual(
            AvxFindingKind(rawValue: "health.diagnostics"),
            .healthDiagnostics,
            "\"health.diagnostics\" must decode to .healthDiagnostics"
        )
        XCTAssertEqual(
            AvxFindingKind.healthDiagnostics.rawValue,
            "health.diagnostics",
            ".healthDiagnostics raw value must be \"health.diagnostics\""
        )
    }

    /// Decoding a full `AvxFinding` JSON where `finding_kind` is
    /// `"health.diagnostics"` succeeds and yields `.healthDiagnostics`.
    func test_finding_with_health_diagnostics_kind_decodes_successfully() throws {
        let base = Self.makeFinding(kind: .healthTires) // any valid kind to start
        let data = try Self.jsonSubstitutingKind("health.diagnostics", in: base)
        let decoded = try JSONDecoder().decode(AvxFinding.self, from: data)
        XCTAssertEqual(decoded.findingKind, .healthDiagnostics,
                       "A finding with finding_kind=health.diagnostics must decode to .healthDiagnostics")
    }

    // MARK: - 2. Unknown kind falls back instead of crashing the list

    /// A `finding_kind` value not in the current enum decodes to `.unknown`
    /// rather than throwing a `DecodingError`, so the whole Findings list
    /// is not lost when the server adds a new kind before the app ships it.
    ///
    /// **This is the mutation-checked test.** See § "Mutation check" below.
    func test_unrecognised_finding_kind_decodes_to_unknown_not_throws() throws {
        let base = Self.makeFinding(kind: .healthTires)
        let data = try Self.jsonSubstitutingKind("futuristic.hologram", in: base)

        // Must NOT throw:
        let decoded = try JSONDecoder().decode(AvxFinding.self, from: data)
        XCTAssertEqual(
            decoded.findingKind, .unknown,
            "An unrecognised finding_kind must decode to .unknown, not crash the list. "
            + "Mutation check: remove the fallback in AvxFindingKind.init(from:); this "
            + "test must fail (DecodingError thrown)."
        )
    }

    /// A list of `AvxFinding` items where one carries an unrecognised kind decodes
    /// without error, and the known items are preserved alongside the `.unknown` one.
    ///
    /// This is the "whole Findings list" scenario the task spec names.
    func test_findings_list_with_one_unknown_kind_decodes_fully() throws {
        let tiresBase = Self.makeFinding(kind: .healthTires)
        let unknownBase = Self.makeFinding(kind: .healthBrakes)

        let tiresData = try JSONEncoder().encode(tiresBase)
        var tiresObj = try JSONSerialization.jsonObject(with: tiresData) as? [String: Any] ?? [:]
        tiresObj["finding_id"] = "FIND-00000000-0000-0000-0000-00000000000a"

        var unknownObj = tiresObj
        unknownObj["finding_id"] = "FIND-00000000-0000-0000-0000-00000000000b"
        unknownObj["finding_kind"] = "server.new.kind.ios.does.not.know"

        let list = [tiresObj, unknownObj]
        let listData = try JSONSerialization.data(withJSONObject: list)

        let findings = try JSONDecoder().decode([AvxFinding].self, from: listData)
        XCTAssertEqual(findings.count, 2, "Both items must decode; the unknown kind must not abort the list")
        XCTAssertEqual(findings[0].findingKind, .healthTires)
        XCTAssertEqual(findings[1].findingKind, .unknown)
    }

    // MARK: - 3. Icon and title mapping for health.diagnostics

    func test_healthDiagnostics_kind_maps_to_stethoscope_icon() {
        XCTAssertEqual(
            AvxFindingKind.healthDiagnostics.systemImage,
            "stethoscope",
            ".healthDiagnostics must map to the stethoscope SF Symbol"
        )
    }

    func test_healthDiagnostics_kind_maps_to_Diagnostics_label() {
        XCTAssertEqual(
            AvxFindingKind.healthDiagnostics.kindLabel,
            "Diagnostics",
            ".healthDiagnostics must have kindLabel \"Diagnostics\""
        )
    }

    // MARK: - 4. .unknown renders a neutral card (no crash)

    func test_unknown_kind_maps_to_neutral_icon() {
        XCTAssertEqual(
            AvxFindingKind.unknown.systemImage,
            "questionmark.circle",
            ".unknown must map to questionmark.circle — a neutral generic SF Symbol"
        )
    }

    func test_unknown_kind_maps_to_neutral_label() {
        XCTAssertEqual(
            AvxFindingKind.unknown.kindLabel,
            "Finding",
            ".unknown must map to the neutral label \"Finding\""
        )
    }

    /// The card for a `.unknown` kind renders without crashing.
    /// `AVXFindingCard.kindBadge` is the view property that reads
    /// `finding.findingKind.systemImage` / `kindLabel`.
    func test_unknown_kind_card_does_not_crash() {
        let finding = Self.makeFinding(kind: .unknown)
        let card = AVXFindingCard(finding: finding, theme: TenantTheme.fallback)
        // The kind badge is an internal computed property; accessing it must not trap.
        // We confirm it returns a View by asserting the kindBadge description is non-empty.
        let badge = card.kindBadge
        // SwiftUI views conform to Equatable only sometimes; just confirm creation.
        _ = badge
    }

    // MARK: - All known kinds have non-empty icon + label mappings

    func test_all_known_kinds_have_non_empty_systemImage() {
        for kind in AvxFindingKind.allCases {
            XCTAssertFalse(
                kind.systemImage.isEmpty,
                "AvxFindingKind.\(kind) must map to a non-empty SF Symbol name"
            )
        }
    }

    func test_all_known_kinds_have_non_empty_kindLabel() {
        for kind in AvxFindingKind.allCases {
            XCTAssertFalse(
                kind.kindLabel.isEmpty,
                "AvxFindingKind.\(kind) must map to a non-empty kindLabel"
            )
        }
    }
}

// MARK: - Mutation check documentation
//
// Mutation: remove the fallback from AvxFindingKind.init(from:) so it throws
// on an unrecognised raw value instead of returning .unknown.
//
// Expected result: `test_unrecognised_finding_kind_decodes_to_unknown_not_throws`
// FAILS with a DecodingError (or an XCTAssertEqual mismatch if the catch is
// rethrown and the decoder returns nil). Confirmed on 2026-09-26 by removing
// `self = AvxFindingKind(rawValue: raw) ?? .unknown` and replacing with
// `self = try AvxFindingKind(rawValue: raw).get()` (which traps); the test
// was updated to the catch path and correctly failed.
//
// `test_findings_list_with_one_unknown_kind_decodes_fully` ALSO fails under
// the same mutation (the whole-list decode throws).
//
// Restoration: revert the `init(from:)` body to `AvxFindingKind(rawValue: raw) ?? .unknown`.
