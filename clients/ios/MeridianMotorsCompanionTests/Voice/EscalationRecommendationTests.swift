import XCTest
@testable import MeridianMotorsCompanion

/// Escalation-recommendation affordance — CVX spec
/// `2026-09-23-cvx-escalation-recommend-not-act`, Tasks 3.1 and 3.2.
///
/// WHAT IS PINNED
/// --------------
/// 3.1 — the render gate fails closed. The card shows iff the level is P0/P1 AND
///       `source` is present and recognised. Absent `source` is an UNKNOWN
///       protocol shape, not "classifier" (iOS used to document the reverse, and
///       the server now always sends it). The known set mirrors the server's
///       `CLASSIFICATION_SOURCES`, pinned here as a cross-repo contract.
/// 3.2 — the tap message carries only an intent: no severity, no dispatch flag.
///       Roadside is offered on P0 only. The roadside confirmation does not
///       claim a truck is coming. A P0 classification alone never opens a chat
///       (the server does that only when a tenant opts in; this client never does).
///
/// Each load-bearing assertion names the mutation it was verified against; the
/// record is in the spec's decisions.md.
@MainActor
final class EscalationRecommendationTests: XCTestCase {

    private let t0 = Date(timeIntervalSince1970: 1_000_000)

    // MARK: - Cross-repo contract

    /// Mirrors `CLASSIFICATION_SOURCES` in CVX `agents/supervisor/bidi_app.py`.
    /// If the server adds a source, this client fails closed on it until this
    /// set is deliberately extended — which is the intended degradation.
    func test_knownSources_mirrorServerEnum() {
        XCTAssertEqual(
            EscalationRecommendation.knownClassificationSources,
            ["classifier", "driver-confirmed", "driver-unclear-default"]
        )
    }

    func test_recommendedLevels_areP0AndP1Only() {
        XCTAssertEqual(EscalationRecommendation.recommendedLevels, ["P0", "P1"])
    }

    // MARK: - 3.1 Render gate — positive cases

    func test_rendersForEveryKnownSource_atP0AndP1() {
        for level in ["P0", "P1"] {
            for source in EscalationRecommendation.knownClassificationSources {
                XCTAssertTrue(
                    EscalationRecommendation.shouldRender(level: level, source: source),
                    "\(level)/\(source) must render"
                )
            }
        }
    }

    /// `category` is absent on the ordinary classifier path — the most common
    /// verdict of all. Its absence must not suppress the card.
    func test_absentCategory_doesNotSuppress() {
        let e = EscalationRecommendation.evaluate(level: "P0", source: "classifier", category: nil, at: t0)
        guard case .recommend(let s) = e else { return XCTFail("expected .recommend, got \(e)") }
        XCTAssertNil(s.categoryLabel)
        XCTAssertTrue(s.isCritical)
    }

    // MARK: - 3.1 Render gate — negative cases (fail closed)

    /// THE LOAD-BEARING NEGATIVE CASE. Absent source = unknown server shape.
    ///
    /// Mutation: treat a nil source as "classifier" (the old iOS convention) →
    /// this test FAILS.
    func test_absentSource_doesNotRender() {
        XCTAssertFalse(EscalationRecommendation.shouldRender(level: "P0", source: nil))
        XCTAssertEqual(
            EscalationRecommendation.evaluate(level: "P0", source: nil, category: nil, at: t0),
            .unsubstantiated
        )
    }

    /// Unrecognised sources — including an LLM-ish value the design once used as
    /// an example ("nova"), which no server has ever emitted.
    ///
    /// Mutation: remove the `knownClassificationSources.contains` check → FAILS.
    func test_unrecognisedSource_doesNotRender() {
        for source in ["nova", "llm", "", "Classifier", "classifier ", "model"] {
            XCTAssertFalse(
                EscalationRecommendation.shouldRender(level: "P0", source: source),
                "source \(source.debugDescription) must fail closed"
            )
        }
    }

    /// Mutation: widen `recommendedLevels` → FAILS.
    func test_lowerLevels_doNotRender() {
        for level in ["P2", "P3", "", "p0", "P4"] {
            XCTAssertFalse(
                EscalationRecommendation.shouldRender(level: level, source: "classifier"),
                "level \(level.debugDescription) must not render"
            )
        }
        XCTAssertFalse(EscalationRecommendation.shouldRender(level: nil, source: "classifier"))
    }

    func test_recognisedLowerLevel_clears_butUnknownShapeDoesNot() {
        XCTAssertEqual(
            EscalationRecommendation.evaluate(level: "P3", source: "classifier", category: nil, at: t0),
            .clear
        )
        XCTAssertEqual(
            EscalationRecommendation.evaluate(level: "P3", source: "nova", category: nil, at: t0),
            .unsubstantiated
        )
    }

    // MARK: - 3.1 Vehicle-tab snapshot reducer

    func test_reducer() {
        let rec = EscalationRecommendation.Snapshot(level: "P0", source: "classifier", category: nil, observedAt: t0)
        XCTAssertEqual(EscalationRecommendation.reduce(nil, with: .recommend(rec)), rec)
        XCTAssertNil(EscalationRecommendation.reduce(rec, with: .clear))
        // An unrecognised shape neither stores nor clears.
        XCTAssertEqual(EscalationRecommendation.reduce(rec, with: .unsubstantiated), rec)
        XCTAssertNil(EscalationRecommendation.reduce(nil, with: .unsubstantiated))
    }

    // MARK: - 3.1 Through the real view model

    private func makeVM() -> VoiceSessionViewModel {
        VoiceSessionViewModel(
            tenantId: "test-tenant",
            vin: "1HGBH41JXMN000046",
            jwtProvider: { nil }
        )
    }

    /// The assistant card's ONLY gate is `vm.escalationRecommendation`. Drive it
    /// with real wire events so a view model that bypasses the pure rule is
    /// caught, not just the rule itself.
    func test_viewModel_rendersOnlyForRecognisedVerdict() async {
        let vm = makeVM()
        XCTAssertNil(vm.escalationRecommendation, "no verdict yet")

        await vm.handle(event: .classification("P0", source: nil, category: nil))
        XCTAssertNil(vm.escalationRecommendation, "absent source must fail closed")

        await vm.handle(event: .classification("P0", source: "nova", category: nil))
        XCTAssertNil(vm.escalationRecommendation, "unrecognised source must fail closed")

        await vm.handle(event: .classification("P0", source: "classifier", category: nil))
        XCTAssertEqual(vm.escalationRecommendation?.level, "P0")

        await vm.handle(event: .classification("P3", source: "classifier", category: nil))
        XCTAssertNil(vm.escalationRecommendation, "a lower verdict clears the card")
    }

    func test_viewModel_dismissIsRearmedByANewVerdict() async {
        let vm = makeVM()
        await vm.handle(event: .classification("P1", source: "driver-unclear-default", category: "brake_failure"))
        XCTAssertNotNil(vm.escalationRecommendation)
        vm.dismissEscalationRecommendation()
        XCTAssertNil(vm.escalationRecommendation)
        await vm.handle(event: .classification("P0", source: "driver-confirmed", category: "brake_failure"))
        XCTAssertEqual(vm.escalationRecommendation?.level, "P0")
        XCTAssertEqual(vm.escalationRecommendation?.categoryLabel, "Brake failure")
    }

    // MARK: - 3.1 Vehicle-tab "as of" label

    private var utcCalendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }

    /// Mutation: restore the time-only label → the older-day cases FAIL.
    func test_asOfText_alwaysStatesTheDay() {
        let cal = utcCalendar
        let en = Locale(identifier: "en_US")
        let now = cal.date(from: DateComponents(year: 2026, month: 9, day: 24, hour: 15, minute: 0))!
        let sameDay = cal.date(from: DateComponents(year: 2026, month: 9, day: 24, hour: 10, minute: 42))!
        let yesterday = cal.date(from: DateComponents(year: 2026, month: 9, day: 23, hour: 10, minute: 42))!
        let older = cal.date(from: DateComponents(year: 2026, month: 9, day: 21, hour: 10, minute: 42))!

        let today = EscalationRecommendation.asOfText(sameDay, now: now, calendar: cal, locale: en)
        XCTAssertTrue(today.hasPrefix("As of today, "), today)
        XCTAssertTrue(today.contains("10:42"), today)

        let yday = EscalationRecommendation.asOfText(yesterday, now: now, calendar: cal, locale: en)
        XCTAssertTrue(yday.hasPrefix("As of yesterday, "), yday)

        let old = EscalationRecommendation.asOfText(older, now: now, calendar: cal, locale: en)
        XCTAssertTrue(old.contains("Sep 21"), old)
        XCTAssertTrue(old.contains("10:42"), old)
        XCTAssertFalse(old.contains("today") || old.contains("yesterday"), old)
    }

    // MARK: - 3.2 The tap message

    /// Mutation: add `"severity": ...` (or `"dispatchRoadside": ...`) to
    /// `requestMessage` → FAILS.
    func test_requestMessage_carriesNoSeverity() {
        for intent in [EscalationRecommendation.RequestIntent.human, .roadside] {
            let msg = EscalationRecommendation.requestMessage(intent: intent)
            XCTAssertEqual(Set(msg.keys), ["type", "intent"], "only type + intent may be sent")
            XCTAssertNil(msg["severity"])
            XCTAssertNil(msg["dispatchRoadside"])
            XCTAssertEqual(msg["type"] as? String, "escalation.request")
            XCTAssertEqual(msg["intent"] as? String, intent.rawValue)
        }
    }

    /// The server matches on these literals (`ESCALATION_REQUEST_INTENTS`).
    func test_intentWireValues_matchServer() {
        XCTAssertEqual(EscalationRecommendation.RequestIntent.human.rawValue, "human")
        XCTAssertEqual(EscalationRecommendation.RequestIntent.roadside.rawValue, "roadside")
    }

    /// The encoded bytes, not just the dictionary.
    func test_requestMessage_serialisesWithoutSeverity() throws {
        let data = try JSONSerialization.data(
            withJSONObject: EscalationRecommendation.requestMessage(intent: .roadside)
        )
        let text = String(decoding: data, as: UTF8.self)
        XCTAssertFalse(text.contains("severity"))
        XCTAssertFalse(text.contains("dispatch"))
    }

    // MARK: - 3.2 Roadside is a separate, P0-only tap

    /// Mutation: make `offersRoadside` true for P1 → FAILS.
    func test_roadsideOfferedOnP0Only() {
        let p0 = EscalationRecommendation.Snapshot(level: "P0", source: "classifier", category: nil, observedAt: t0)
        let p1 = EscalationRecommendation.Snapshot(level: "P1", source: "classifier", category: nil, observedAt: t0)
        XCTAssertTrue(p0.offersRoadside)
        XCTAssertFalse(p1.offersRoadside)
    }

    /// CVX RSA spec Task 4.1: the user's chosen ack (2026-09-24). Pinned exactly,
    /// so any rewording goes through review.
    ///
    /// Mutations: restore Phase A's Option B; drop the "(Simulated dispatch …)"
    /// marker; restore "Roadside assistance has been dispatched to your
    /// location." → each FAILS.
    func test_roadsideNotice_isTheChosenSimulatedAck() {
        XCTAssertEqual(
            EscalationRecommendation.roadsideRequestedNotice,
            "Roadside assistance has been notified. A vehicle will be dispatched "
                + "shortly. (Simulated dispatch for this demo.)"
        )
    }

    /// The simulated marker must survive to the UI (spec R4, Task 4.1 Accept 3),
    /// and the ack must stay a promise of a dispatch to come, never a claim that
    /// a vehicle is already on its way. Phase A's "not yet available" sentence
    /// is gone because it became false.
    func test_roadsideNotice_isMarkedSimulatedAndClaimsNothingInProgress() {
        let notice = EscalationRecommendation.roadsideRequestedNotice.lowercased()
        XCTAssertTrue(notice.contains("simulated"), "the simulated marker must be visible")
        XCTAssertFalse(notice.contains("not yet available"), "Option B's sentence is false now and must be gone")
        for forbidden in ["has been dispatched", "on its way", "on the way", "en route", "is coming", "arriving", "eta", "minutes"] {
            XCTAssertFalse(notice.contains(forbidden), "notice must not claim \(forbidden.debugDescription)")
        }
    }

    // MARK: - 3.2 Taps through the view model

    /// No card, no request — the tap is gated on the same rule as the render.
    ///
    /// Mutation: remove the `escalationRecommendation` guard in
    /// `requestEscalation` → handoffState flips to .failed("Not connected") → FAILS.
    func test_viewModel_tapWithoutCardIsANoop() async {
        let vm = makeVM()
        await vm.handle(event: .classification("P0", source: nil, category: nil))
        await vm.requestEscalation(.human)
        XCTAssertEqual(vm.handoffState, .none)
        XCTAssertNil(vm.escalationRequestInFlight)
    }

    /// Roadside on a P1 card is refused client-side too.
    func test_viewModel_roadsideOnP1IsANoop() async {
        let vm = makeVM()
        await vm.handle(event: .classification("P1", source: "classifier", category: nil))
        await vm.requestEscalation(.roadside)
        XCTAssertEqual(vm.handoffState, .none)
        XCTAssertNil(vm.lastEscalationIntent)
    }

    /// With a card but no live session, the tap reports it rather than failing
    /// silently — and this is also the positive control proving the two no-op
    /// tests above can detect a tap that got through.
    func test_viewModel_tapWithCardButNoSession_reportsNotConnected() async {
        let vm = makeVM()
        await vm.handle(event: .classification("P0", source: "classifier", category: nil))
        await vm.requestEscalation(.human)
        guard case .failed(let message) = vm.handoffState else {
            return XCTFail("expected .failed, got \(vm.handoffState)")
        }
        XCTAssertTrue(message.contains("Not connected"))
    }

    // MARK: - 3.2 Accept 4: no auto-open from a verdict alone

    /// A P0 verdict renders a card and does NOTHING else. The chat opens only on
    /// an `escalation` event, which the server sends on a tap or when a tenant
    /// has opted in (`escalation.autoOpenHumanChatOnP0`). The control half
    /// proves the assertion can see a handoff-state change.
    func test_p0Verdict_doesNotOpenChat() async {
        let vm = makeVM()
        await vm.handle(event: .classification("P0", source: "classifier", category: nil))
        XCTAssertNotNil(vm.escalationRecommendation)
        XCTAssertEqual(vm.handoffState, .none, "a verdict must never open a chat on its own")

        // Control: the escalation event DOES move handoff state.
        await vm.handle(event: .escalation(.init(
            status: "failed", severity: "P0", contactId: nil, participantId: nil,
            participantToken: nil, connectionExpiry: nil, rsaDispatched: false,
            rsaActionId: nil, message: "refused", chatDiagnosis: nil
        )))
        XCTAssertEqual(vm.handoffState, .failed(message: "refused"))
    }

    // MARK: - Roadside-first wording (user decision 2026-09-25)

    private func dtc(_ code: String, _ severity: String?) -> ActiveDtc {
        ActiveDtc(dtcId: nil, code: code, status: "ACTIVE", severity: severity, system: nil,
                  description: nil, timestamp: nil, firstSeenAt: nil, source: nil,
                  serviceRequired: nil)
    }

    private func snap(_ level: String = "P0", source: String = "classifier",
                      category: String? = nil) -> EscalationRecommendation.Snapshot {
        .init(level: level, source: source, category: category, observedAt: t0)
    }

    /// VEH-MRDN-0015 as read 2026-09-25: P0217 CRITICAL, P0299 HIGH, and four
    /// MEDIUM/LOW faults.
    private var meridianFaults: [ActiveDtc] {
        [dtc("P0562", "LOW"), dtc("P0001", "MEDIUM"), dtc("P0299", "HIGH"),
         dtc("P0217", "CRITICAL"), dtc("B1234", "MEDIUM"), dtc("P0420", "MEDIUM")]
    }

    /// Mirrors every code CVX `dtc_response_catalog.py` maps to a pull-over
    /// template. Mutation: add B0001_FIRE or U3000_CRASH → FAILS.
    func test_roadsideFirstCodes_mirrorServerCatalog() {
        XCTAssertEqual(EscalationRecommendation.roadsideFirstCodes,
                       ["P0217", "P0A80", "P0299", "C1201", "C1234"])
        XCTAssertFalse(EscalationRecommendation.roadsideFirstCodes.contains("B0001_FIRE"))
        XCTAssertFalse(EscalationRecommendation.roadsideFirstCodes.contains("U3000_CRASH"))
    }

    func test_leadsWithRoadside_forClassifierP0WithPullOverFaults() {
        XCTAssertTrue(EscalationRecommendation.leadsWithRoadside(snap(), activeDtcs: meridianFaults))
    }

    /// The spoken response for fire and crash says to call 911, so the card
    /// must not lead with roadside. Mutation: `allSatisfy` → `contains(where:)`
    /// → FAILS.
    func test_fireOrCrash_keepsDefaultWording() {
        for code in ["B0001_FIRE", "U3000_CRASH"] {
            XCTAssertFalse(
                EscalationRecommendation.leadsWithRoadside(
                    snap(), activeDtcs: meridianFaults + [dtc(code, "CRITICAL")]),
                "\(code) must keep the default wording")
        }
    }

    /// A code this build does not know keeps the default, including a row with
    /// no severity. Mutation: count only CRITICAL/HIGH as urgent → FAILS.
    func test_unknownUrgentFault_keepsDefaultWording() {
        XCTAssertFalse(EscalationRecommendation.leadsWithRoadside(
            snap(), activeDtcs: meridianFaults + [dtc("U0100", "CRITICAL")]))
        XCTAssertFalse(EscalationRecommendation.leadsWithRoadside(
            snap(), activeDtcs: meridianFaults + [dtc("B0020", nil)]))
    }

    /// No positive evidence, no roadside lead. Mutation: drop `!urgent.isEmpty`
    /// → FAILS.
    func test_noUrgentFaults_keepsDefaultWording() {
        XCTAssertFalse(EscalationRecommendation.leadsWithRoadside(snap(), activeDtcs: []))
        XCTAssertFalse(EscalationRecommendation.leadsWithRoadside(
            snap(), activeDtcs: [dtc("P0420", "MEDIUM"), dtc("P0562", "LOW")]))
    }

    /// Only an ordinary classifier P0. A driver-confirmed verdict carries a
    /// category (it may be a fire or a crash the vehicle has not reported).
    /// Mutations: drop the source check or the category check → FAILS.
    func test_onlyAnOrdinaryClassifierP0_leadsWithRoadside() {
        XCTAssertFalse(EscalationRecommendation.leadsWithRoadside(snap("P1"), activeDtcs: meridianFaults))
        XCTAssertFalse(EscalationRecommendation.leadsWithRoadside(
            snap(source: "driver-confirmed"), activeDtcs: meridianFaults))
        XCTAssertFalse(EscalationRecommendation.leadsWithRoadside(
            snap(category: "fire_smoke"), activeDtcs: meridianFaults))
    }

    func test_roadsideFirstCopy_isPinned() {
        XCTAssertEqual(EscalationRecommendation.roadsideFirstTitle, "Critical issue detected")
        XCTAssertEqual(EscalationRecommendation.roadsideFirstSubtitle,
                       "We recommend roadside assistance. You can also talk to an agent.")
    }
}
