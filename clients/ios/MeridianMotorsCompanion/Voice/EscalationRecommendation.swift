//
//  EscalationRecommendation.swift
//  MeridianMotorsCompanion
//
//  The rules behind the "Critical fault detected — we recommend speaking with an
//  agent" affordance. Pure: no UI, no networking, so every rule here is unit
//  tested in `EscalationRecommendationTests`.
//
//  CVX spec `2026-09-23-cvx-escalation-recommend-not-act`, Tasks 3.1 / 3.2, built
//  to `ios-affordance-design.md` (revised by Task 1.5b and signed off 2026-09-23).
//
//  WHAT THIS REPLACES
//  ------------------
//  Before this spec, a P0 verdict made the server open a human chat on its own.
//  Under spec D3 the handoff is RECOMMENDED: the server tells the driver what it
//  found, and the driver chooses — one tap for a person, a separate tap for
//  roadside. The server still auto-opens chat for a tenant that opts in
//  (`escalation.autoOpenHumanChatOnP0`); nothing on this side reads that flag.
//
//  THE DISCRIMINATOR
//  -----------------
//  Render iff the level is P0/P1 AND `source` is present and one of the values
//  this build recognises. Absent or unrecognised `source` means a server older
//  than the Task 1.5a contract, or a shape this client does not understand — the
//  client cannot substantiate the verdict, so it renders nothing. There is no
//  "LLM" source to exclude: after the triage-401 fix and Group 1 of the spec the
//  model cannot produce a classification at all, and inventing a value for that
//  state would imply to future readers that it can.
//

import Foundation

enum EscalationRecommendation {

    // MARK: - Protocol constants

    /// Mirrors `CLASSIFICATION_SOURCES` in the CVX supervisor
    /// (`agents/supervisor/bidi_app.py`). ONE constant for both surfaces — per
    /// the design, spreading string comparisons across views is how one surface
    /// ends up recognising a value the other rejects.
    static let knownClassificationSources: Set<String> = [
        "classifier",
        "driver-confirmed",
        "driver-unclear-default",
    ]

    /// Verdict levels the affordance is shown for. Mirrors
    /// `ESCALATION_RECOMMENDED_TIERS` on the server, which refuses a tap for
    /// anything else regardless of what this client does.
    static let recommendedLevels: Set<String> = ["P0", "P1"]

    /// What the driver tapped. The wire message carries this and nothing else.
    enum RequestIntent: String, Equatable {
        case human
        case roadside
    }

    /// The `escalation.request` message sent over the voice session's WebSocket.
    ///
    /// Carries NO severity and NO dispatch flag. The server decides both from the
    /// verdict it surfaced to this session — a level sent from here would be the
    /// client asserting a severity, which is the hole the spec closes. Pinned by
    /// `EscalationRecommendationTests.test_requestMessage_carriesNoSeverity`.
    ///
    /// Why not `POST /escalate` directly: that endpoint requires a `severity`, and
    /// nothing but the voice server can supply one without the phone asserting it.
    /// See the spec's decisions.md, "Task 3.2: the tap goes over the session".
    static func requestMessage(intent: RequestIntent) -> [String: Any] {
        [
            "type": "escalation.request",
            "intent": intent.rawValue,
        ]
    }

    // MARK: - Evaluation

    /// A verdict the client recognised and that warrants the card.
    struct Snapshot: Equatable {
        let level: String
        let source: String
        let category: String?
        let observedAt: Date

        var isCritical: Bool { level == "P0" }

        /// Roadside is offered on P0 only; the server refuses it otherwise.
        var offersRoadside: Bool { level == "P0" }

        var title: String {
            isCritical ? "Critical fault detected" : "Important issue detected"
        }

        /// Descriptive, not actionable — the card recommends, it does not act.
        var subtitle: String { "We recommend speaking with an agent." }

        /// Human-readable category badge, when the verdict carried one.
        /// `brake_failure` → "Brake failure". Absent on the ordinary classifier
        /// path, which is expected and must not suppress the card.
        var categoryLabel: String? {
            guard let category, !category.isEmpty else { return nil }
            let spaced = category.replacingOccurrences(of: "_", with: " ")
            return spaced.prefix(1).uppercased() + spaced.dropFirst()
        }
    }

    /// What one `classification` event means for the affordance.
    enum Evaluation: Equatable {
        /// Recognised P0/P1 verdict. Render.
        case recommend(Snapshot)
        /// Recognised verdict at another level: the server says this no longer
        /// warrants the card. Clears a stored recommendation.
        case clear
        /// No verdict, or a `source` this build does not recognise. Fail closed:
        /// render nothing and change nothing that was stored.
        case unsubstantiated
    }

    static func evaluate(
        level: String?,
        source: String?,
        category: String?,
        at observedAt: Date
    ) -> Evaluation {
        guard let level, let source, knownClassificationSources.contains(source) else {
            return .unsubstantiated
        }
        guard recommendedLevels.contains(level) else {
            return .clear
        }
        return .recommend(Snapshot(
            level: level,
            source: source,
            category: category,
            observedAt: observedAt
        ))
    }

    /// Convenience for the render gate. Same rule as `evaluate`.
    static func shouldRender(level: String?, source: String?) -> Bool {
        if case .recommend = evaluate(level: level, source: source, category: nil, at: Date()) {
            return true
        }
        return false
    }

    /// The Vehicle-tab recommendation outlives the conversation (the fault is on
    /// the vehicle, not in the chat), so it is stored on `AppSession` and updated
    /// by this reducer as verdicts arrive.
    static func reduce(_ current: Snapshot?, with evaluation: Evaluation) -> Snapshot? {
        switch evaluation {
        case .recommend(let snapshot): return snapshot
        case .clear: return nil
        case .unsubstantiated: return current
        }
    }

    // MARK: - Copy

    /// Shown once a roadside request has been recorded (`rsaDispatched`).
    ///
    /// CVX RSA spec `2026-09-23-cvx-rsa-stub-dispatch` Task 4.1, wording chosen
    /// by the user on 2026-09-24: a faked acknowledgement that roadside has been
    /// notified and a vehicle will follow. Phase A's Option B ("not yet
    /// available") became false once the simulated provider and lifecycle shipped.
    ///
    /// The dispatch behind it is SIMULATED (fake provider, `SIM-` references), and
    /// the copy says so, so a demo audience is never shown a real truck that does
    /// not exist (spec R4, Task 4.1 Accept 3).
    /// The marker is hard-coded because every dispatch is simulated today (the
    /// real provider raises). Once a real provider can dispatch, this copy must
    /// read `simulated` from the dispatch record instead of always saying so.
    ///
    /// Known limit, recorded in the spec's decisions.md: the ack is shown when the
    /// request is RECORDED. The server can still fail the dispatch afterwards (for
    /// example with no fresh vehicle position), and this client does not read the
    /// dispatch status.
    static let roadsideRequestedNotice =
        "Roadside assistance has been notified. A vehicle will be dispatched "
        + "shortly. (Simulated dispatch for this demo.)"

    /// Shown when the driver asked for roadside but the server could not record
    /// the request. The human chat still opened; the agent is the fallback.
    static let roadsideNotRecordedNotice =
        "We couldn't record the roadside request. Ask the agent in the chat."

    /// Shown in place of the roadside button once a chat is open — a second
    /// request would open a second chat, so the agent handles it from here.
    static let roadsideViaAgentNotice =
        "Ask the agent in the chat about roadside assistance."

    // MARK: - Roadside-first wording (user decision 2026-09-25)

    /// Card copy when a P0 leads with roadside instead of an agent.
    static let roadsideFirstTitle = "Critical issue detected"
    static let roadsideFirstSubtitle =
        "We recommend roadside assistance. You can also talk to an agent."

    /// DTC codes whose AVX spoken response tells the driver to pull over or
    /// stop, with no instruction to call emergency services. Mirrors every code
    /// that CVX `agents/supervisor/dtc_response_catalog.py` maps to
    /// `pullover_stop_engine`, `pullover_safe_location` or `brake_emergency`.
    ///
    /// An allowlist on purpose. Fire (`B0001_FIRE`, exit the vehicle) and crash
    /// (`U3000_CRASH`, call 911) are left out, and so is any code this build
    /// does not know, so those keep the default "speak with an agent" wording.
    /// A card that recommends roadside under a spoken "call 911" would weaken
    /// the spoken instruction. Pinned by
    /// `EscalationRecommendationTests.test_roadsideFirstCodes_mirrorServerCatalog`.
    static let roadsideFirstCodes: Set<String> = ["P0217", "P0A80", "P0299", "C1201", "C1234"]

    /// Whether a P0 card leads with roadside assistance rather than an agent.
    ///
    /// True only for an ordinary classifier P0 (no driver-confirmed category)
    /// where every urgent active fault is in `roadsideFirstCodes`, and there is
    /// at least one. "Urgent" is any severity other than MEDIUM or LOW, so a
    /// row with a missing or unrecognised severity counts and must be on the
    /// list. Everything else keeps the default wording.
    ///
    /// Known limit: `activeDtcs` is the app's vehicle context, loaded when the
    /// assistant opens. A fault raised mid-session is not in it. The server's
    /// spoken response is primed at session start too, so the two stay
    /// consistent, but neither sees the new fault.
    static func leadsWithRoadside(_ snapshot: Snapshot, activeDtcs: [ActiveDtc]) -> Bool {
        guard snapshot.offersRoadside,
              snapshot.source == "classifier",
              snapshot.category == nil else { return false }
        let urgent = activeDtcs.filter {
            !["MEDIUM", "LOW"].contains(($0.severity ?? "").uppercased())
        }
        return !urgent.isEmpty
            && urgent.allSatisfy { roadsideFirstCodes.contains($0.code.uppercased()) }
    }

    /// Vehicle-tab caption. The Vehicle tab cannot connect a chat itself — the
    /// voice session that surfaced the verdict has ended — so its button opens
    /// the assistant, which re-checks the vehicle before offering the handoff.
    static let vehicleTabCaption =
        "Open the assistant and describe what's happening. It will re-check "
        + "your vehicle before connecting you."

    /// "As of today, 10:42 AM" / "As of yesterday, 10:42 AM" / "As of Sep 21,
    /// 10:42 AM". The Vehicle-tab card outlives the conversation, so a
    /// time-only label ("As of 10:42 AM") made a days-old verdict read as
    /// current (review Cycle 3). The day is always stated.
    static func asOfText(
        _ date: Date,
        now: Date = Date(),
        calendar: Calendar = .current,
        locale: Locale = .current
    ) -> String {
        var timeStyle = Date.FormatStyle(date: .omitted, time: .shortened)
        timeStyle.locale = locale
        timeStyle.calendar = calendar
        timeStyle.timeZone = calendar.timeZone
        let time = date.formatted(timeStyle)
        if calendar.isDate(date, inSameDayAs: now) {
            return "As of today, \(time)"
        }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(date, inSameDayAs: yesterday) {
            return "As of yesterday, \(time)"
        }
        var dayStyle = Date.FormatStyle(date: .abbreviated, time: .omitted)
        dayStyle.locale = locale
        dayStyle.calendar = calendar
        dayStyle.timeZone = calendar.timeZone
        return "As of \(date.formatted(dayStyle)), \(time)"
    }
}
