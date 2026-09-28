import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the vehicle-health breakdown.
///
/// The load-bearing property is that **the score is narrated, never recomputed**.
/// `VehicleContextResponse.healthScore` is documented as the "single source of truth"
/// rendered verbatim by both this app and the CMS web UI, and iOS deliberately
/// stopped computing it so the two cannot disagree. These tests pin that, plus the
/// deduction→remedy table and the self-serve boundary.
final class VehicleHealthBreakdownTests: XCTestCase {

    private func deduction(_ reason: String, _ amount: Int) -> HealthScoreBreakdown.Deduction {
        HealthScoreBreakdown.Deduction(reason: reason, amount: amount)
    }

    // MARK: - Remedy table covers the server's closed vocabulary

    /// Every reason `_compute_health_score` can emit must map to a remedy. The
    /// vocabulary is closed — DTC severities plus two fixed strings — so a gap here
    /// means a real deduction renders with no guidance.
    func testRemedyExistsForEveryServerEmittedReason() {
        let reasons = [
            "DTC P0299 CRITICAL", "DTC P0420 HIGH", "DTC P0171 MEDIUM", "DTC B1004 LOW",
            "Scheduled service overdue", "Vehicle disconnected",
        ]
        for r in reasons {
            XCTAssertNotNil(HealthDeductionRemedy.remedy(for: r),
                            "no remedy mapped for server reason '\(r)'")
        }
    }

    /// Matching must tolerate the variable DTC code and casing, since the reason
    /// embeds a code rather than being a fixed string.
    func testDtcMatchingIsCodeAgnosticAndCaseInsensitive() {
        for r in ["DTC P0420 MEDIUM", "dtc p0420 medium", "DTC C0035 MEDIUM", "DTC MEDIUM"] {
            XCTAssertNotNil(HealthDeductionRemedy.remedy(for: r), "failed on '\(r)'")
        }
    }

    /// Severity must be read from the reason, not re-derived — otherwise the client
    /// could disagree with the server about how serious a fault is.
    func testSeverityDrivesDistinctGuidance() {
        let critical = HealthDeductionRemedy.remedy(for: "DTC P0299 CRITICAL")
        let medium = HealthDeductionRemedy.remedy(for: "DTC P0171 MEDIUM")
        XCTAssertNotEqual(critical?.action, medium?.action,
            "critical and medium faults must not give identical guidance")
        XCTAssertTrue(critical!.action.lowercased().contains("before further driving"),
            "a critical fault should advise against continued driving")
    }

    /// An unrecognised reason must return nil so the view can say it has no guidance,
    /// rather than being silently dropped — dropping it would make the on-screen
    /// arithmetic stop adding up.
    func testUnknownReasonReturnsNilRatherThanAGuess() {
        XCTAssertNil(HealthDeductionRemedy.remedy(for: "Tyre pressure anomaly"))
        XCTAssertNil(HealthDeductionRemedy.remedy(for: ""))
    }

    // MARK: - Self-serve boundary

    /// Only owner-actionable deductions may be counted as recoverable. Promising
    /// points that need a workshop is the kind of number that erodes trust the moment
    /// it does not materialise.
    func testOnlySelfServeDeductionsCountAsRecoverable() {
        let ds = [
            deduction("Scheduled service overdue", 10),   // self-serve → counts
            deduction("Vehicle disconnected", 5),          // self-serve → counts
            deduction("DTC P0420 MEDIUM", 8),              // needs service → excluded
            deduction("DTC P0299 CRITICAL", 30),           // needs service → excluded
        ]
        XCTAssertEqual(HealthDeductionRemedy.selfServeRecoverablePoints(in: ds), 15)
    }

    func testFaultsAreNotSelfServeAndServiceItemsAre() {
        XCTAssertEqual(HealthDeductionRemedy.remedy(for: "Scheduled service overdue")?.isSelfServe, true)
        XCTAssertEqual(HealthDeductionRemedy.remedy(for: "Vehicle disconnected")?.isSelfServe, true)
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"] {
            XCTAssertEqual(HealthDeductionRemedy.remedy(for: "DTC P0420 \(sev)")?.isSelfServe, false,
                           "\(sev) fault must not be presented as owner-fixable")
        }
    }

    /// Unknown reasons contribute nothing rather than defaulting to recoverable.
    func testUnknownReasonsAreNotCountedAsRecoverable() {
        XCTAssertEqual(
            HealthDeductionRemedy.selfServeRecoverablePoints(in: [deduction("Mystery signal", 12)]),
            0)
    }

    func testNoDeductionsMeansNothingRecoverable() {
        XCTAssertEqual(HealthDeductionRemedy.selfServeRecoverablePoints(in: []), 0)
    }

    // MARK: - The primed prompt carries the facts on screen

    /// The agent must be seeded with the score AND the verbatim reasons, so it
    /// reasons from what the owner is looking at instead of re-deriving it.
    func testPrimedPromptCarriesScoreAndVerbatimReasons() {
        let bd = HealthScoreBreakdown(
            score: 79,
            deductions: [deduction("DTC P0420 MEDIUM", 8),
                         deduction("Scheduled service overdue", 10),
                         deduction("Vehicle disconnected", 5)],
            computedAt: "2026-08-19T09:00:00Z")
        let view = VehicleHealthDetailView(
            score: 79, breakdown: bd, theme: .fallback, onDone: {})

        let p = view.primedPrompt
        XCTAssertTrue(p.contains("79"), "prompt must state the score")
        for r in ["DTC P0420 MEDIUM", "Scheduled service overdue", "Vehicle disconnected"] {
            XCTAssertTrue(p.contains(r), "prompt must carry the verbatim reason '\(r)'")
        }
        XCTAssertTrue(p.lowercased().contains("first"),
                      "prompt should ask for prioritisation — the judgement half")

        // The seed must NOT provoke a tool cascade. Asking a voice agent to research
        // and book broke the Nova session on 2026-08-19 (triage alone took 2385 ms
        // against a ~1 s budget), so these two constraints are load-bearing rather
        // than stylistic. See `primedPrompt`.
        XCTAssertTrue(p.lowercased().contains("do not look anything up"),
                      "seed must tell the agent not to perform lookups")
        XCTAssertFalse(p.lowercased().contains("book anything that needs"),
                       "seed must not ask the agent to book — that provoked the cascade")
        XCTAssertTrue(p.lowercased().contains("do not book"),
                      "seed must explicitly defer booking")

        // The facts must be IN the seed, so no lookup is needed to answer.
        XCTAssertTrue(p.contains("−8") || p.contains("-8"),
                      "seed must carry the deduction amounts so the agent need not fetch them")
        XCTAssertTrue(p.lowercased().contains("service visit")
                      || p.lowercased().contains("myself"),
                      "seed should say who can fix each item, so the agent need not infer it")
    }

    /// With no deductions the prompt must still be coherent and must not fabricate
    /// items to talk about.
    func testPrimedPromptWithNoDeductionsDoesNotInventItems() {
        let view = VehicleHealthDetailView(
            score: 100, breakdown: nil, theme: .fallback, onDone: {})
        let p = view.primedPrompt
        XCTAssertTrue(p.contains("100"))
        XCTAssertFalse(p.contains("deductions are:"),
                       "must not claim deductions when there are none")
    }

    // MARK: - Score is narrated, not recomputed

    /// When the server's deductions do not sum to its score, the SERVER's score is
    /// what shows. The client must not substitute its own total — that is precisely
    /// the divergence iOS stopped computing the score to avoid.
    func testServerScoreWinsWhenDeductionsDisagree() {
        // Deductions total 20, which would imply 80, but the server says 79.
        let bd = HealthScoreBreakdown(
            score: 79,
            deductions: [deduction("DTC P0420 MEDIUM", 8),
                         deduction("Scheduled service overdue", 10),
                         deduction("DTC B1004 LOW", 2)],
            computedAt: "2026-08-19T09:00:00Z")
        let view = VehicleHealthDetailView(
            score: 79, breakdown: bd, theme: .fallback, onDone: {})
        XCTAssertEqual(view.score, 79,
            "the rendered score must be the server's, regardless of the deduction sum")
    }
}
