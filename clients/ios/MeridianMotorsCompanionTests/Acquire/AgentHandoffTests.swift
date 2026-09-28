import XCTest
@testable import MeridianMotorsCompanion

/// Regression tests for the two test-ride handoffs that reached the wrong place.
///
/// See `issues/2026-08-18-ios-upgrade-handoff-dead-ends-on-buy-landing/`.
///
/// Both defects were routing rules that existed only as prose — one in
/// `UpgradeFlow`'s docstring ("caller routes to the Buy tab", assuming the tab
/// renders `DiscoverFlow`), one in `SuccessStep`'s ("opens BookingFlow via
/// Service stage", while the call site only dismissed). Neither could fail a
/// test, because neither was expressed as code anything could call. These
/// tests exist so the rules are executable.
final class AgentHandoffTests: XCTestCase {

    // MARK: - BuyLandingView consumes a pending handoff

    /// The reported bug: `UpgradeFlow` wrote the customer's opening line into
    /// `AppSession.pendingDiscoverPrompt`, the Buy tab was selected, and the
    /// shop window rendered with the prompt stranded and no agent opened.
    func testPendingPromptWithNothingPresentedOpensAgent() {
        XCTAssertTrue(
            BuyLandingView.shouldOpenAgentForPendingHandoff(
                destination: nil,
                pendingPrompt: "I'd like to schedule a test drive for the Crestwind."),
            "a pending handoff with nothing presented must open the agent")
    }

    /// The landing view's own tiles set `destination` first and write the prompt
    /// afterwards, so firing while something is presented would fight them —
    /// and would re-present the cover that is already up.
    func testPendingPromptWhileAlreadyPresentedDoesNotOpenAgent() {
        let alreadyUp: [BuyLandingView.Destination] = [
            .chat(prompt: nil),
            .chat(prompt: "seeded by a tile"),
            .configure(handoff: ConfiguratorOfferHandoff(edition: nil,
                                                         modelName: "Crestwind",
                                                         firstName: nil)),
        ]
        for destination in alreadyUp {
            XCTAssertFalse(
                BuyLandingView.shouldOpenAgentForPendingHandoff(
                    destination: destination,
                    pendingPrompt: "a pending prompt"),
                "must not re-present over \(destination.id)")
        }
    }

    /// No handoff pending is the ordinary case — opening the Buy tab must land
    /// on the shop window, which is the whole point of that screen existing.
    func testNoPendingPromptLeavesTheShopWindowAlone() {
        XCTAssertFalse(
            BuyLandingView.shouldOpenAgentForPendingHandoff(
                destination: nil, pendingPrompt: nil),
            "a plain Buy-tab visit must not open the agent")
        XCTAssertFalse(
            BuyLandingView.shouldOpenAgentForPendingHandoff(
                destination: nil, pendingPrompt: ""),
            "an empty prompt is not a handoff")
        XCTAssertFalse(
            BuyLandingView.shouldOpenAgentForPendingHandoff(
                destination: nil, pendingPrompt: "   \n  "),
            "a whitespace-only prompt is not a handoff")
    }

    // MARK: - SuccessStep hands the test drive to the agent

    /// Warm entry: the model name arrives via the offer handoff and must be
    /// named, so the agent does not have to ask what was just ordered.
    func testTestRidePromptNamesTheModelWhenKnown() {
        let line = ConfiguratorFlow.testRidePrompt(orderNumber: "ORD-2026-08-0042",
                                                   modelName: "Meridian Crestwind Signature")
        XCTAssertTrue(line.contains("ORD-2026-08-0042"), "order number must be carried")
        XCTAssertTrue(line.contains("Meridian Crestwind Signature"),
                      "a known model must be named")
        XCTAssertTrue(line.lowercased().contains("test drive"),
                      "the ask must be a test drive")
    }

    /// Cold entry: `ReservationResponse` carries no model and the step machine
    /// has dropped its selections, so the name is genuinely unknown. Omitting
    /// it is correct — naming the wrong model would be worse than naming none.
    func testTestRidePromptOmitsTheModelWhenUnknown() {
        for unknown: String? in [nil, "", "  "] {
            let line = ConfiguratorFlow.testRidePrompt(orderNumber: "ORD-1", modelName: unknown)
            XCTAssertTrue(line.contains("ORD-1"), "order number must be carried")
            XCTAssertFalse(line.contains("for the "),
                           "must not emit a dangling 'for the' with no model")
            XCTAssertTrue(line.lowercased().contains("test drive"),
                          "the ask must be a test drive")
        }
    }

    /// Phrased as the customer's own words, so the persona prompt decides how
    /// to answer — the convention `UpgradeFlow.bookTestRide` already follows.
    func testTestRidePromptIsPhrasedAsTheCustomer() {
        let line = ConfiguratorFlow.testRidePrompt(orderNumber: "ORD-1", modelName: "Crestwind")
        XCTAssertTrue(line.hasPrefix("I've "),
                      "the opener must read as the customer speaking, not as an instruction")
    }

    // MARK: - The chat request carries the visitor's unwrapped words

    /// The kiosk guardrail's `PROMPT_ATTACK` filter is applied to the user
    /// prompt, and `prompt` is not purely the user's prompt — `promptWithHistory`
    /// wraps it in a vehicle-facts block and a fenced transcript, both
    /// instruction-shaped. Verified against the deployed staging guardrail on
    /// 2026-08-18: the vehicle block alone, containing no customer text, was
    /// enough to trigger it, so every Discover turn carrying vehicle context was
    /// refused before the model saw it.
    ///
    /// `userText` is the span the guardrail should judge. These tests pin that it
    /// reaches the wire, under that exact key, distinct from `prompt`.
    /// See `cvx/issues/2026-08-18-kiosk-guardrail-blocks-own-prompt-scaffolding/`.
    private func encodedRequest(prompt: String, userText: String?) throws -> [String: Any] {
        let req = AssistantChatRequest(
            prompt: prompt,
            userText: userText,
            runtimeSessionId: "disc_00000000-0000-0000-0000-000000000000",
            tenantId: "meridian",
            personaId: "consumer",
            tenantSegment: "oem",
            vehicleId: nil,
            tenantDisplayName: "Meridian",
            distanceUnit: "km",
            personaContext: nil
        )
        let data = try JSONEncoder().encode(req)
        return try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testRequestPutsUserTextOnTheWire() throws {
        let wrapped = """
        Known facts about this customer, from their connected vehicle record. \
        Treat these as established — do NOT ask the customer for anything \
        already stated here:
        - Current vehicle: Meridian Trailwind 450

        The customer says: I'd like to schedule a test drive
        """
        let raw = "I'd like to schedule a test drive"
        let json = try encodedRequest(prompt: wrapped, userText: raw)

        XCTAssertEqual(json["userText"] as? String, raw,
                       "userText must carry the visitor's unwrapped utterance")
        XCTAssertEqual(json["prompt"] as? String, wrapped,
                       "prompt must still carry the full context the model needs")
        XCTAssertNotEqual(json["userText"] as? String, json["prompt"] as? String,
                          "if these are equal the guardrail is judging the wrapper again")
    }

    /// The scaffolding is what trips the filter, so none of it may appear in the
    /// field the guardrail reads. Asserted on the substrings that tripped it
    /// live, so a regression fails for the original reason.
    func testUserTextCarriesNoneOfTheScaffolding() throws {
        let wrapped = """
        Known facts about this customer, from their connected vehicle record. \
        Treat these as established — do NOT ask the customer for anything \
        already stated here:
        - Current vehicle: Meridian Trailwind 450

        The customer says: I'd like to schedule a test drive
        """
        let json = try encodedRequest(prompt: wrapped,
                                      userText: "I'd like to schedule a test drive")
        let userText = try XCTUnwrap(json["userText"] as? String)
        for scaffold in ["Known facts about this customer",
                         "do NOT ask the customer",
                         "The customer says:",
                         "<<<TRANSCRIPT"] {
            XCTAssertFalse(userText.contains(scaffold),
                           "userText leaked scaffolding: \(scaffold)")
        }
    }

    /// `sessionId` remains the wire key (the handler 400s otherwise), and adding
    /// `userText` must not have disturbed the existing `CodingKeys` mapping.
    func testAddingUserTextDidNotDisturbTheSessionIdMapping() throws {
        let json = try encodedRequest(prompt: "p", userText: "u")
        XCTAssertNotNil(json["sessionId"], "handler requires `sessionId`")
        XCTAssertNil(json["runtimeSessionId"],
                     "`runtimeSessionId` must not reach the wire — handler 400s")
    }

    /// A caller with no visitor utterance omits the key entirely, rather than
    /// sending an empty string. Absent means "guard the whole prompt"
    /// (fail-closed); an explicit empty value would be a request to guard
    /// nothing, which is a lever no client should have.
    func testNilUserTextIsOmittedFromTheWireRatherThanSentEmpty() throws {
        let json = try encodedRequest(prompt: "app-authored narration prompt",
                                      userText: nil)
        XCTAssertNil(json["userText"],
                     "nil userText must be omitted, not encoded as \"\" — an "
                     + "empty guarded span would pass everything")
        XCTAssertNotNil(json["prompt"])
    }

    // MARK: - Markdown tables are flattened for the chat bubble

    /// The bubble parses markdown with `.inlineOnlyPreservingWhitespace`, which
    /// has no table support, so a table arrives as literal `|---|---|`. The agent
    /// reaches for one unprompted when listing dealers with slots and ratings.
    func testDealerTableIsFlattenedIntoReadableLines() {
        let table = """
        Great news — three dealers near you:

        | Dealer | Next Slots | Rating |
        |---|---|---|
        | **Meridian of Nashville** | Wed Aug 19 at 1:00 PM | 4.7 |
        | **Meridian of Brentwood** | Thu Aug 20 at 9:00 AM | 4.6 |

        Which works for you?
        """
        let out = flattenMarkdownTables(table)

        XCTAssertFalse(out.contains("|"), "no pipe should survive: \(out)")
        XCTAssertFalse(out.contains("---"), "no separator row should survive")
        // The cells are the answer — flattening must not lose them.
        XCTAssertTrue(out.contains("**Meridian of Nashville**"))
        XCTAssertTrue(out.contains("Next Slots: Wed Aug 19 at 1:00 PM"))
        XCTAssertTrue(out.contains("Rating: 4.7"))
        XCTAssertTrue(out.contains("**Meridian of Brentwood**"))
        // Surrounding prose is untouched.
        XCTAssertTrue(out.contains("Great news — three dealers near you:"))
        XCTAssertTrue(out.contains("Which works for you?"))
    }

    /// A stray pipe in ordinary prose must not be treated as a table — the
    /// separator row is what makes it one.
    func testProseContainingAPipeIsLeftAlone() {
        let prose = "Your options are test drive | service | trade-in — which?"
        XCTAssertEqual(flattenMarkdownTables(prose), prose)
    }

    func testTextWithoutTablesIsReturnedUnchanged() {
        let plain = "I found Meridian of Nashville, about 3 miles away. Book it?"
        XCTAssertEqual(flattenMarkdownTables(plain), plain)
    }

    /// Ragged rows are normal from an LLM: fewer cells than headers, empty
    /// cells, a missing leading pipe. None of these may crash or emit noise.
    func testRaggedAndEmptyCellsDegradeCleanly() {
        let ragged = """
        | Dealer | Slots | Rating |
        | --- | --- | --- |
        | Meridian of Rivergate | | 4.5 |
        Dealer Only | |
        """
        let out = flattenMarkdownTables(ragged)
        XCTAssertFalse(out.contains("---"))
        XCTAssertTrue(out.contains("Meridian of Rivergate"))
        XCTAssertTrue(out.contains("Rating: 4.5"))
        XCTAssertFalse(out.contains("Slots:"),
                       "an empty cell must not emit a dangling label")
    }
}
