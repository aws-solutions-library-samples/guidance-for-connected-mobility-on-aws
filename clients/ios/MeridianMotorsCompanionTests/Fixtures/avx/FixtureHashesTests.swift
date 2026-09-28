import XCTest
import CryptoKit

/// Contract-drift tripwire for the AVX Finding + Action schemas.
///
/// These schemas are **owned by AVX core** (CVX repo,
/// `agents/tier2/schemas/{finding,action}.schema.json`) and vendored here
/// byte-identically. This spec MUST NOT re-declare them — see
/// `.kiro/specs/2026-09-18-avx-ios-cards/spec.md` § Constraints and § Risks R2.
///
/// The pins below are `static let` constants rather than a file path **on
/// purpose** (Task 1.3 Accept #2): if a rebase updates the CVX-side schema and
/// re-copies it here without also updating the pin in this file, the test fails.
/// Storing the expected digest in a sidecar file that is copied alongside the
/// schema would make the check self-satisfying — the very drift it exists to
/// catch would update both sides and stay green.
///
/// Rebase pipeline when AVX core changes a schema (all three, same commit):
///   1. update AVX core,
///   2. re-copy into `Fixtures/avx/`,
///   3. update the matching pin below.
/// Any of the three missing is drift.
///
/// Digests verified against AVX core's own tripwire at
/// `tests/schemas/test_contract_fixtures_byte_identical.py:208-209`, so both
/// repos assert the same bytes from opposite sides.
final class FixtureHashesTests: XCTestCase {

    // MARK: - Pins (freeze time: 2026-09-18, AVX core commit 9205785)

    /// SHA-256 of `finding.schema.json`. Matches AVX core's `FINDING_SCHEMA` pin.
    private static let findingSchemaSHA256 =
        "855351e29743093cd819ef777827fcd0c98fac28e72ef0f7225239779acf20d9"

    /// SHA-256 of `action.schema.json`. Matches AVX core's `ACTION_SCHEMA` pin.
    private static let actionSchemaSHA256 =
        "510fdbc7940f90acdbce2f6accc63e6738f159d54b2dce1476132c3cde54f24d"

    /// The canonical `Action.status` set — eight values, per AVX core contract
    /// addenda D4 (`dismissed`) and D5 (`declined`, `failed`).
    ///
    /// Asserted as an exact set: no additions, no removals (Task 1.3 Accept #3).
    /// `dismissed` is NOT a synonym for `cancelled` and `failed` is NOT a synonym
    /// for `declined`; the UI renders all eight distinctly (Tasks 2.4 / 2.1).
    private static let expectedActionStatuses: Set<String> = [
        "pending_execution",
        "executed",
        "in_progress",
        "completed",
        "cancelled",
        "declined",
        "dismissed",
        "failed",
    ]

    // MARK: - Fixture location

    /// Resolves the vendored fixture directory relative to this source file.
    ///
    /// Uses `#filePath` rather than `Bundle(for:)` deliberately: the fixtures are
    /// not registered as bundle resources in `project.pbxproj`. This mirrors the
    /// convention already established in this test target by
    /// `Snapshots/__Snapshots__`, whose reference PNGs are likewise read from the
    /// source tree and are absent from the project file.
    private static var fixtureDirectory: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
    }

    private func sha256Hex(ofFileNamed name: String) throws -> String {
        let url = Self.fixtureDirectory.appendingPathComponent(name)
        let data = try Data(contentsOf: url)
        return SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    // MARK: - Byte-identity

    func test_finding_schema_is_byte_identical_to_avx_core() throws {
        let actual = try sha256Hex(ofFileNamed: "finding.schema.json")
        XCTAssertEqual(
            actual,
            Self.findingSchemaSHA256,
            """
            finding.schema.json has drifted from AVX core's canonical copy.
            Expected \(Self.findingSchemaSHA256), got \(actual).
            Do NOT "fix" this by editing the pin alone — re-copy the schema from
            AVX core (agents/tier2/schemas/finding.schema.json) and update the pin
            in the same commit, or the two repos are silently out of contract.
            """
        )
    }

    func test_action_schema_is_byte_identical_to_avx_core() throws {
        let actual = try sha256Hex(ofFileNamed: "action.schema.json")
        XCTAssertEqual(
            actual,
            Self.actionSchemaSHA256,
            """
            action.schema.json has drifted from AVX core's canonical copy.
            Expected \(Self.actionSchemaSHA256), got \(actual).
            """
        )
    }

    // MARK: - Status enum shape

    func test_action_status_enum_is_exactly_the_eight_canonical_values() throws {
        let url = Self.fixtureDirectory.appendingPathComponent("action.schema.json")
        let data = try Data(contentsOf: url)

        guard
            let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
            let properties = root["properties"] as? [String: Any],
            let status = properties["status"] as? [String: Any],
            let values = status["enum"] as? [String]
        else {
            return XCTFail(
                "Could not read properties.status.enum from action.schema.json. "
                + "The schema's shape changed, which is itself contract drift."
            )
        }

        XCTAssertEqual(
            Set(values),
            Self.expectedActionStatuses,
            """
            Action.status enum drift.
            Missing from schema: \(Self.expectedActionStatuses.subtracting(values).sorted())
            Unexpected in schema: \(Set(values).subtracting(Self.expectedActionStatuses).sorted())
            """
        )

        // Guards the count independently of the set comparison, so a duplicated
        // entry in the schema (which collapses in a Set) is still caught.
        XCTAssertEqual(
            values.count, 8,
            "Expected exactly 8 status values, found \(values.count): \(values)"
        )
    }

    // MARK: - Sample fixtures

    /// The vendored sample instances are what Task 1.4's round-trip test decodes.
    /// Asserted here so a partial vendoring (schemas copied, samples forgotten) is
    /// caught at the contract layer rather than surfacing as a confusing decode
    /// failure in a downstream test.
    func test_vendored_sample_fixtures_are_present_and_valid_json() throws {
        let samples = Self.fixtureDirectory.appendingPathComponent("samples")
        let contents = try FileManager.default.contentsOfDirectory(
            at: samples, includingPropertiesForKeys: nil
        ).filter { $0.pathExtension == "json" }

        XCTAssertEqual(
            contents.count, 10,
            "Expected 10 vendored sample fixtures from AVX core, found \(contents.count)."
        )

        for url in contents {
            let data = try Data(contentsOf: url)
            XCTAssertNoThrow(
                try JSONSerialization.jsonObject(with: data),
                "\(url.lastPathComponent) is not valid JSON"
            )
        }
    }

    /// The three addenda-specific fixtures must survive vendoring by name — they
    /// are the only instances that exercise `dismissed` (D4), `declined` and
    /// `failed` (D5), which are exactly the values the UI must not collapse.
    func test_addenda_fixtures_survive_vendoring() throws {
        let samples = Self.fixtureDirectory.appendingPathComponent("samples")
        for name in [
            "action_dismissed_addendum_d4_valid.json",
            "action_declined_4xx_addendum_d5_valid.json",
            "action_failed_5xx_exhausted_addendum_d5_valid.json",
        ] {
            XCTAssertTrue(
                FileManager.default.fileExists(
                    atPath: samples.appendingPathComponent(name).path
                ),
                "Addendum fixture \(name) is missing from the vendored samples."
            )
        }
    }
}
