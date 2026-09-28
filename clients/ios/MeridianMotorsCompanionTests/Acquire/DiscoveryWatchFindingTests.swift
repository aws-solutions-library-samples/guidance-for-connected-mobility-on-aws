import XCTest
@testable import MeridianMotorsCompanion

/// Tests for `DiscoveryWatchFinding` — the Tier 2 → Tier 1 artifact contract.
///
/// The contract's four properties are all correctness requirements per
/// `~/.kiro/steering/agentic-tiers.md`, not niceties, so each gets a test:
/// evidence is present, staleness is surfaced, re-runs are idempotent, and the
/// attribution does not claim an agent that is not deployed.
///
/// The last one carries the most risk here. An artifact type whose selling point is
/// "an agent did this overnight" is the easiest place in the codebase to claim a
/// capability that does not exist.
final class DiscoveryWatchFindingTests: XCTestCase {

    private let fixedNow = Date(timeIntervalSince1970: 1_777_000_000)

    private func findings(
        model: String = "Meridian Crestwind Signature",
        color: String? = "aurora-white"
    ) -> [DiscoveryWatchFinding] {
        DiscoveryWatchFinding.findings(
            savedModelName: model, savedColorId: color, now: fixedNow)
    }

    // MARK: - Attribution honesty (highest risk)

    /// Must disclose demo data and must NOT claim a scheduled agent ran.
    func testAttributionDisclosesDemoDataAndClaimsNoScheduledRun() {
        let note = DiscoveryWatchFinding.attributionNote.lowercased()
        XCTAssertTrue(note.contains("demo data"), "must disclose the findings are demo data")
        for forbidden in ["overnight scan", "ran last night", "monitored", "we detected",
                          "live inventory", "real-time"] {
            XCTAssertFalse(note.contains(forbidden),
                "attribution must not imply '\(forbidden)' — no agent is deployed")
        }
    }

    /// The agent version must be self-evidently a demo string. A plausible semver
    /// invites the reader to believe a real agent produced the finding.
    func testAgentVersionIsMarkedAsDemo() {
        XCTAssertTrue(DiscoveryWatchFinding.demoAgentVersion.contains("demo"))
        for f in findings() {
            XCTAssertEqual(f.agentVersion, DiscoveryWatchFinding.demoAgentVersion)
        }
    }

    // MARK: - Contract completeness

    /// Every artifact-contract field must be populated on every finding. A finding
    /// missing evidence is an assertion the agent cannot support.
    func testEveryFindingCarriesTheFullContract() {
        let all = findings()
        XCTAssertFalse(all.isEmpty, "fixture should produce at least one finding")
        for f in all {
            XCTAssertFalse(f.finding.isEmpty, "\(f.category) has no finding text")
            XCTAssertFalse(f.evidence.isEmpty, "\(f.category) has no evidence")
            XCTAssertFalse(f.agentVersion.isEmpty)
            XCTAssertFalse(f.inputsHash.isEmpty)
            XCTAssertTrue((0.0...1.0).contains(f.confidence))
            XCTAssertLessThanOrEqual(f.computedAt, fixedNow,
                "computedAt must not be in the future")
        }
    }

    /// Evidence must be more than a restatement of the finding.
    func testEvidenceIsNotJustTheFindingRepeated() {
        for f in findings() {
            for line in f.evidence {
                XCTAssertNotEqual(line, f.finding,
                    "\(f.category) evidence repeats the finding instead of supporting it")
            }
            XCTAssertGreaterThanOrEqual(f.evidence.count, 2,
                "\(f.category) should cite more than one observation")
        }
    }

    // MARK: - Idempotency

    /// `inputsHash` is a content address: identical inputs must produce an identical
    /// hash, which is what makes the conditional write
    /// (`attribute_not_exists(inputs_hash)`) suppress a duplicate. If this drifted to
    /// a random id, the customer would be told the same news on every agent run.
    func testInputsHashIsContentAddressedAndStable() {
        let a = findings()
        let b = findings()
        XCTAssertEqual(a.map(\.inputsHash), b.map(\.inputsHash),
            "Same inputs must yield the same hashes — a re-run must not duplicate")
    }

    func testDifferentSavedConfigurationsProduceDifferentHashes() {
        let a = findings(model: "Meridian Crestwind Signature", color: "aurora-white")
        let b = findings(model: "Meridian Windrose", color: "cascade-blue")
        XCTAssertNotEqual(Set(a.map(\.inputsHash)), Set(b.map(\.inputsHash)))
    }

    /// Hashes must be unique within one batch, or two findings would collide and one
    /// would be silently dropped by the conditional write.
    func testHashesAreUniqueWithinABatch() {
        let all = findings()
        XCTAssertEqual(Set(all.map(\.inputsHash)).count, all.count,
            "Two findings share a content address; one would be lost on write")
    }

    /// `id` must be the hash, so SwiftUI's diffing and the dedup key agree.
    func testIdentifiableIdIsTheContentAddress() {
        for f in findings() { XCTAssertEqual(f.id, f.inputsHash) }
    }

    // MARK: - Ordering

    /// Newest first, matching the reader convention in the deployed
    /// `health_findings.py` (newest per category by `max(computed_at)`).
    func testFindingsAreNewestFirst() {
        let all = findings()
        for (a, b) in zip(all, all.dropFirst()) {
            XCTAssertGreaterThanOrEqual(a.computedAt, b.computedAt,
                "findings must be ordered newest first")
        }
    }

    // MARK: - Staleness is surfaced, not hidden

    /// A stale finding must report itself stale — and must still be returned, so the
    /// customer is not shown an empty state that implies "nothing changed" when the
    /// truth is "nothing was checked".
    func testStalenessIsDetectedAtTheThreshold() {
        let f = DiscoveryWatchFinding(
            category: .leadTimeImproved, finding: "x", evidence: ["a", "b"],
            computedAt: fixedNow.addingTimeInterval(-8 * 86_400),
            confidence: 0.8, agentVersion: "t", inputsHash: "h")
        XCTAssertTrue(f.isStale(now: fixedNow), "8 days old should be stale at a 7-day threshold")

        let fresh = DiscoveryWatchFinding(
            category: .leadTimeImproved, finding: "x", evidence: ["a", "b"],
            computedAt: fixedNow.addingTimeInterval(-2 * 86_400),
            confidence: 0.8, agentVersion: "t", inputsHash: "h")
        XCTAssertFalse(fresh.isStale(now: fixedNow))
    }

    func testFreshnessLabelIsPopulatedAndRelative() {
        for f in findings() {
            let label = f.freshnessLabel(now: fixedNow)
            XCTAssertTrue(label.lowercased().hasPrefix("checked"))
            XCTAssertFalse(label.isEmpty)
        }
    }

    /// The fixture must not produce findings that are already stale — a demo opening
    /// on "may be out of date" undercuts the whole point.
    func testFixtureFindingsAreFresh() {
        for f in findings() {
            XCTAssertFalse(f.isStale(now: fixedNow),
                "\(f.category) is stale on generation; the demo would open on a caveat")
        }
    }

    // MARK: - Content relevance

    /// Lead time is always reported — a customer who saved a build cares most about
    /// when they get it, so it is the one finding that should never be absent.
    func testLeadTimeFindingIsAlwaysPresent() {
        for model in ["Meridian Crestwind Signature", "Meridian Trailwind",
                      "Meridian Windrose", "Meridian Azimuth Executive"] {
            let all = findings(model: model)
            XCTAssertTrue(all.contains { $0.category == .leadTimeImproved },
                "\(model) produced no lead-time finding")
        }
    }

    /// The saved colour must be named when an availability finding fires, otherwise
    /// "the option you wanted is available" is unverifiable by the customer.
    func testOptionFindingNamesTheSavedColour() {
        // Search the model space for a configuration that produces the option
        // finding, rather than assuming one does.
        for model in ["Meridian Crestwind Signature", "Meridian Trailwind",
                      "Meridian Windrose", "Meridian Azimuth Executive"] {
            let all = DiscoveryWatchFinding.findings(
                savedModelName: model, savedColorId: "racing-red", now: fixedNow)
            if let opt = all.first(where: { $0.category == .optionOpened }) {
                XCTAssertTrue(opt.finding.lowercased().contains("racing red"),
                    "option finding must name the saved colour; got: \(opt.finding)")
                return
            }
        }
        // Not a failure if no configuration triggers it — the finding is conditional.
    }

    /// A nil saved colour must not render a placeholder or crash.
    func testNilColourDegradesGracefully() {
        let all = findings(color: nil)
        for f in all {
            XCTAssertFalse(f.finding.contains("nil"))
            XCTAssertFalse(f.finding.contains("Optional"))
        }
    }
}
