import XCTest
@testable import MeridianMotorsCompanion

/// Conformance tests for the AVX Swift models against the byte-identical vendored
/// fixtures (Task 1.4).
///
/// `FixtureHashesTests` proves the *schema* has not drifted. This file proves the
/// *Swift types* still match that schema — the two are independent failures: a schema
/// can stay byte-identical while a Swift property is renamed, and a Swift type can be
/// correct against a schema that has silently moved.
final class AvxModelsTests: XCTestCase {

    // MARK: - Fixture plumbing

    /// Read from the source tree via `#filePath`, matching the convention used by
    /// `FixtureHashesTests` and `Snapshots/__Snapshots__` (fixtures are deliberately
    /// not bundle resources).
    private static var samplesDirectory: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("Fixtures/avx/samples")
    }

    private func loadFixture(_ name: String) throws -> Data {
        try Data(contentsOf: Self.samplesDirectory.appendingPathComponent(name))
    }

    private func fixtureNames(prefix: String) throws -> [String] {
        try FileManager.default
            .contentsOfDirectory(atPath: Self.samplesDirectory.path)
            .filter { $0.hasPrefix(prefix) && $0.hasSuffix(".json") }
            .sorted()
    }

    /// Decode → encode → compare as parsed JSON objects.
    ///
    /// Comparing parsed objects rather than raw strings is what makes this a
    /// *semantic* round-trip: `NSDictionary.isEqual` ignores key ordering and
    /// whitespace, both of which `JSONEncoder` is free to change, while still
    /// catching a dropped field, a renamed key, or an integer that became a double.
    private func assertRoundTrips<T: Codable>(
        _ type: T.Type, fixture name: String, file: StaticString = #filePath, line: UInt = #line
    ) throws {
        let original = try loadFixture(name)
        let decoded = try JSONDecoder().decode(type, from: original)
        let reEncoded = try JSONEncoder().encode(decoded)

        let lhs = try JSONSerialization.jsonObject(with: original) as? NSDictionary
        let rhs = try JSONSerialization.jsonObject(with: reEncoded) as? NSDictionary

        XCTAssertNotNil(lhs, "\(name): original is not a JSON object", file: file, line: line)
        XCTAssertNotNil(rhs, "\(name): re-encoded is not a JSON object", file: file, line: line)

        if lhs != rhs {
            // Name the differing keys rather than dumping two blobs — a round-trip
            // failure is almost always one field, and the diff is the whole message.
            let lk = Set((lhs?.allKeys as? [String]) ?? [])
            let rk = Set((rhs?.allKeys as? [String]) ?? [])
            var detail = ""
            if lk != rk {
                detail += " Keys lost: \(lk.subtracting(rk).sorted())."
                detail += " Keys added: \(rk.subtracting(lk).sorted())."
            } else {
                let changed = lk.filter { k in
                    !((lhs?[k] as? NSObject)?.isEqual(rhs?[k] as? NSObject) ?? false)
                }
                detail += " Same key set; differing values at: \(changed.sorted())."
            }
            XCTFail(
                "\(name) did not round-trip losslessly.\(detail)", file: file, line: line
            )
        }
    }

    // MARK: - Enum shape (Task 1.4 Accept #3)

    func test_action_status_enum_has_all_8_values() {
        XCTAssertEqual(
            AvxActionStatus.allCases.count, 8,
            """
            AvxActionStatus must carry all 8 contract values (addenda D4 + D5).
            Found: \(AvxActionStatus.allCases.map(\.rawValue).sorted())
            """
        )

        // Pin the raw values too. A count-only assertion passes if a case is renamed
        // or its raw value edited, which is a wire-level break the count cannot see.
        XCTAssertEqual(
            Set(AvxActionStatus.allCases.map(\.rawValue)),
            [
                "pending_execution", "executed", "in_progress", "completed",
                "cancelled", "declined", "dismissed", "failed",
            ]
        )
    }

    func test_dismissed_and_cancelled_are_distinct_cases() {
        // Addendum D4. Guards the model layer; the UI-layer equivalent is Task 2.4.
        XCTAssertNotEqual(AvxActionStatus.dismissed, AvxActionStatus.cancelled)
        XCTAssertNotEqual(
            AvxActionStatus.dismissed.rawValue, AvxActionStatus.cancelled.rawValue
        )
    }

    func test_failed_and_declined_are_distinct_cases() {
        // Addendum D5.
        XCTAssertNotEqual(AvxActionStatus.failed, AvxActionStatus.declined)
        XCTAssertNotEqual(
            AvxActionStatus.failed.rawValue, AvxActionStatus.declined.rawValue
        )
    }

    func test_target_system_enum_has_all_6_values() {
        XCTAssertEqual(
            AvxTargetSystem.allCases.count, 6,
            "target_system has 6 contract values including ios-owner. "
            + "Found: \(AvxTargetSystem.allCases.map(\.rawValue).sorted())"
        )
        XCTAssertEqual(AvxTargetSystem.iosOwner.rawValue, "ios-owner")
        XCTAssertEqual(AvxTargetSystem.vehicleCommand.rawValue, "vehicle-command")
    }

    func test_severity_enum_includes_p0_and_flags_it_verbatim() {
        XCTAssertEqual(AvxSeverity.allCases.count, 5)
        XCTAssertEqual(AvxSeverity.p0.rawValue, "p0")
        // The safety predicate Task 2.5's rendering guard keys off.
        XCTAssertTrue(AvxSeverity.p0.requiresVerbatimRendering)
        for s in AvxSeverity.allCases where s != .p0 {
            XCTAssertFalse(
                s.requiresVerbatimRendering,
                "\(s.rawValue) must not claim verbatim-rendering status"
            )
        }
    }

    // MARK: - Round-trip (Task 1.4 Accept #2)

    func test_all_finding_fixtures_round_trip() throws {
        let names = try fixtureNames(prefix: "finding_")
        XCTAssertEqual(names.count, 4, "Expected 4 vendored finding fixtures, got \(names)")
        for name in names {
            try assertRoundTrips(AvxFinding.self, fixture: name)
        }
    }

    func test_all_action_fixtures_round_trip() throws {
        let names = try fixtureNames(prefix: "action_")
        XCTAssertEqual(names.count, 6, "Expected 6 vendored action fixtures, got \(names)")
        for name in names {
            try assertRoundTrips(AvxAction.self, fixture: name)
        }
    }

    // MARK: - Specific contract properties worth naming

    /// `evidence[].value` is schema-untyped and one vendored fixture carries an
    /// explicit `null` inside it (`/evidence[1]/value/covered` in
    /// `finding_health_brakes_mocked_provenance.json`).
    ///
    /// That null is load-bearing: it means "warranty coverage unknown — the DMS seam
    /// is not live yet", which is NOT the same claim as `covered: false` and NOT the
    /// same as the key being absent. A model that mapped JSON null onto a Swift
    /// optional would drop it on re-encode and silently convert *unknown* into
    /// *not applicable* — the provenance dishonesty parent PRD § M14 exists to prevent.
    func test_explicit_null_inside_untyped_evidence_value_is_preserved() throws {
        let name = "finding_health_brakes_mocked_provenance.json"
        let finding = try JSONDecoder().decode(AvxFinding.self, from: loadFixture(name))

        let carriesExplicitNull = finding.evidence.contains { entry in
            guard case .object(let fields) = entry.value else { return false }
            return fields.values.contains(.null)
        }
        XCTAssertTrue(
            carriesExplicitNull,
            "Expected an explicit null inside an evidence value; the fixture's "
            + "'covered: null' must survive decoding as .null, not collapse to absent."
        )

        // And it must survive the return trip.
        try assertRoundTrips(AvxFinding.self, fixture: name)
    }

    /// Integers must not degrade to doubles. `tokens_used: 1124` re-encoding as
    /// `1124.0` would still decode fine and would still "look right" in a debugger,
    /// but it is a wire-shape change.
    func test_integer_fields_do_not_degrade_to_double() throws {
        let finding = try JSONDecoder().decode(
            AvxFinding.self, from: loadFixture("finding_health_tires_valid.json")
        )
        XCTAssertEqual(finding.tokensUsed, 847)
        XCTAssertEqual(finding.costUsd, 0.00423, accuracy: 1e-12)

        let re = try JSONEncoder().encode(finding)
        let obj = try JSONSerialization.jsonObject(with: re) as? [String: Any]
        XCTAssertTrue(
            obj?["tokens_used"] is Int || (obj?["tokens_used"] as? NSNumber)?.stringValue == "847",
            "tokens_used must re-encode as an integer, got \(String(describing: obj?["tokens_used"]))"
        )
    }

    /// The three addenda fixtures are the only instances exercising the statuses the
    /// UI must not collapse, so assert they decode to exactly those statuses.
    func test_addenda_fixtures_decode_to_their_distinct_statuses() throws {
        let expected: [String: AvxActionStatus] = [
            "action_dismissed_addendum_d4_valid.json": .dismissed,
            "action_declined_4xx_addendum_d5_valid.json": .declined,
            "action_failed_5xx_exhausted_addendum_d5_valid.json": .failed,
        ]
        for (name, status) in expected {
            let action = try JSONDecoder().decode(AvxAction.self, from: loadFixture(name))
            XCTAssertEqual(
                action.status, status,
                "\(name) must decode to .\(status.rawValue)"
            )
        }
    }

    /// Strict decoding: an unknown status must fail loudly rather than fall back.
    /// A silent fallback would render an unrecognised terminal state as something
    /// else on a card the owner reads to decide what to do.
    func test_unknown_action_status_fails_decoding_rather_than_defaulting() throws {
        let original = try loadFixture("action_dms_book_visit_valid.json")
        guard var obj = try JSONSerialization.jsonObject(with: original) as? [String: Any] else {
            return XCTFail("fixture is not a JSON object")
        }
        obj["status"] = "quantum_superposition"
        let mutated = try JSONSerialization.data(withJSONObject: obj)

        XCTAssertThrowsError(
            try JSONDecoder().decode(AvxAction.self, from: mutated),
            "An unrecognised Action.status must throw, not silently default."
        )
    }
}
