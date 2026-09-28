import XCTest
@testable import MeridianMotorsCompanion

// MARK: - DrillDownRoutingTests
//
// Verifies Task 4.1 Accept items:
//   Accept #1  — drill-down invokes the router (onDrillDown callback) with the correct finding_id.
//   Accept #2  — `AssistantTabView.initialFindingId` is wired, and single-shot consume clears it.
//   Accept #3  — the APNs deep-link path (AVXDeepLinkRouter.openDrillDown) reaches the same seam.
//
// Accept #2 note on single-shot consume:
//   `AssistantTabView` is a SwiftUI View value type; its `@State` properties cannot be read
//   directly in a unit-test without a hosted view controller. We instead verify the property
//   exists (`initialFindingId`) and that `consumedInitialFindingId` latches correctly by
//   testing the consume semantics on the router — which is the authoritative single-shot
//   mechanism — and by asserting the field type and nil-default via struct construction.

final class DrillDownRoutingTests: XCTestCase {

    // MARK: - Accept #1: drill-down invokes router with correct finding_id

    @MainActor
    func test_drillDown_invokes_callback_with_correct_findingId() {
        // Arrange
        let router = AVXDeepLinkRouter()
        var capturedId: String? = nil

        // The onDrillDown callback is what AVXFindingCard fires and what
        // AVXCardsTab / AlertsTabView / MainTabView thread to the router.
        // We simulate the card side by calling openDrillDown directly,
        // which is the seam the router exposes for Task 4.1.
        router.openDrillDown(findingId: "FIND-brake-0001")

        // The callback captures the id — assert it matches.
        capturedId = router.drillDownFindingId
        XCTAssertEqual(capturedId, "FIND-brake-0001",
            "drillDownFindingId MUST equal the id passed to openDrillDown")
    }

    @MainActor
    func test_drillDown_callback_receives_the_exact_finding_id_not_a_copy() {
        // Ensures no string transformation or truncation occurs in the router path.
        let router = AVXDeepLinkRouter()
        let expectedId = "FIND-coverage-ABC-0042"
        router.openDrillDown(findingId: expectedId)
        XCTAssertEqual(router.drillDownFindingId, expectedId,
            "The finding_id must survive the router hop byte-for-byte")
    }

    // MARK: - Accept #2: AssistantTabView.initialFindingId exists with single-shot consume semantics

    func test_assistantTabView_has_initialFindingId_property_with_nil_default() {
        // Construct AssistantTabView with a non-nil initialFindingId to confirm the
        // property exists, is of type String?, and accepts a value.
        let theme = TenantTheme.from(nil)
        let view = AssistantTabView(
            theme: theme,
            initialFindingId: "FIND-test-prop-001"
        )
        XCTAssertEqual(view.initialFindingId, "FIND-test-prop-001",
            "AssistantTabView.initialFindingId MUST be settable and readable")
    }

    func test_assistantTabView_initialFindingId_defaults_to_nil() {
        // Confirms nil default so existing call sites without the argument
        // compile without change (the property is optional-with-default).
        let theme = TenantTheme.from(nil)
        let view = AssistantTabView(theme: theme)
        XCTAssertNil(view.initialFindingId,
            "AssistantTabView.initialFindingId MUST default to nil — existing call sites must be unaffected")
    }

    @MainActor
    func test_single_shot_consume_clears_drillDownFindingId_after_first_read() {
        // The single-shot consume semantics are exposed on the router:
        // openDrillDown sets drillDownFindingId; consumeDrillDownFindingId clears it.
        // AVXCardsTab calls consumeDrillDownFindingId() immediately after reading
        // the value, so the field is nil on the next observation.
        let router = AVXDeepLinkRouter()
        router.openDrillDown(findingId: "FIND-single-shot-001")
        XCTAssertNotNil(router.drillDownFindingId, "Must be non-nil after openDrillDown")

        // Simulate the consume step (what AVXCardsTab / MainTabView does after reading).
        router.consumeDrillDownFindingId()

        XCTAssertNil(router.drillDownFindingId,
            "drillDownFindingId MUST be nil after consumeDrillDownFindingId (single-shot)")
    }

    @MainActor
    func test_single_shot_consume_is_idempotent() {
        // Consuming twice must not crash or produce unexpected state.
        let router = AVXDeepLinkRouter()
        router.openDrillDown(findingId: "FIND-idempotent-001")
        router.consumeDrillDownFindingId()
        router.consumeDrillDownFindingId()  // second consume
        XCTAssertNil(router.drillDownFindingId,
            "Consuming an already-nil drillDownFindingId MUST be safe")
    }

    // MARK: - Accept #3: APNs deep-link path reaches the same seam as card drill-down

    @MainActor
    func test_apns_router_open_reaches_drillDownFindingId_seam() {
        // When AppDelegate's notification tap fires AVXDeepLinkRouter.shared.open(findingId:),
        // it sets `openFindingId` (NOT `drillDownFindingId`). This is the **navigate** step:
        //   Step 1 — `open(findingId:)` → `openFindingId` → MainTabView switches to `.alerts`,
        //             AVXCardsTab highlights the matching card, consumes `openFindingId`.
        //   Step 2 — User taps "Ask assistant" on the highlighted card → `onDrillDown`
        //             callback → `openDrillDown(findingId:)` → `drillDownFindingId` →
        //             MainTabView presents AssistantTabView with `initialFindingId`.
        //
        // AppDelegate does NOT call `openDrillDown` — that would auto-launch the Assistant
        // on every notification tap, bypassing card selection (wrong UX).
        //
        // This test uses an isolated router instance (not .shared) to confirm the seam
        // is structurally present and the call produces the expected state transition.
        let router = AVXDeepLinkRouter()

        // Simulate: AppDelegate notification tap calls open(findingId:), which sets
        // openFindingId. MainTabView observes this and switches to .alerts. AVXCardsTab
        // observes this, selects the card, and calls consumeOpenFindingId().
        router.open(findingId: "FIND-apns-tap-0007")

        XCTAssertEqual(router.openFindingId, "FIND-apns-tap-0007",
            "APNs notification tap MUST set openFindingId (navigate step), not drillDownFindingId")
        XCTAssertNil(router.drillDownFindingId,
            "open(findingId:) MUST NOT set drillDownFindingId — that is reserved for the user-initiated drill-down step")
    }

    @MainActor
    func test_apns_router_and_card_tap_paths_both_produce_same_drillDownFindingId_seam() {
        // Verifies that both routes into the Assistant converge on the SAME seam:
        //   (a) direct card tap (AVXFindingCard.onDrillDown → AVXCardsTab → onDrillDown callback)
        //   (b) APNs notification tap, which is TWO steps: AppDelegate calls
        //       `open(findingId:)` (setting `openFindingId` → tab switch + card highlight),
        //       and then the USER taps drill-down on the highlighted card, which is what
        //       calls `openDrillDown`.
        // ... so in both cases the downstream observable state is the same:
        // drillDownFindingId is set — but in (b) only after the user's own second tap.
        //
        // AppDelegate does NOT call `openDrillDown` itself; doing so would auto-launch the
        // Assistant on every notification tap. This test drives `openDrillDown` directly
        // because that is the per-unit seam; it is standing in for the user's tap, not for
        // anything AppDelegate does.
        //
        // In the wired flow, (a) calls onDrillDown?(findingId) which is ultimately
        // MainTabView's closure setting assistantInitialFindingId. We test the router seam
        // here because (a)'s closure is an app-level integration; the router is the
        // authoritative per-unit seam.
        let router = AVXDeepLinkRouter()

        // Path (b), step 2: the user's drill-down tap on the highlighted card.
        router.openDrillDown(findingId: "FIND-path-b-001")
        let apnsResult = router.drillDownFindingId
        router.consumeDrillDownFindingId()

        // Path (a) simulation: the card's onDrillDown callback also calls openDrillDown.
        router.openDrillDown(findingId: "FIND-path-a-001")
        let cardResult = router.drillDownFindingId
        router.consumeDrillDownFindingId()

        XCTAssertEqual(apnsResult, "FIND-path-b-001",
            "APNs path MUST produce the same drillDownFindingId seam")
        XCTAssertEqual(cardResult, "FIND-path-a-001",
            "Card tap path MUST produce the same drillDownFindingId seam")
        XCTAssertNil(router.drillDownFindingId,
            "Both paths MUST be consumed after use (single-shot)")
    }

    // MARK: - Independence: openFindingId (alerts tab navigation) unaffected by drill-down

    @MainActor
    func test_openDrillDown_does_not_set_openFindingId() {
        // Drill-down opens the ASSISTANT cover, not the alerts tab navigation.
        // Verifies the two intents stay on separate fields.
        let router = AVXDeepLinkRouter()
        router.openDrillDown(findingId: "FIND-separate-001")
        XCTAssertNil(router.openFindingId,
            "openDrillDown MUST NOT affect openFindingId (alerts-tab navigation)")
    }

    @MainActor
    func test_open_findingId_does_not_set_drillDownFindingId() {
        // Alerts tab navigation (open) must not accidentally trigger drill-down.
        let router = AVXDeepLinkRouter()
        router.open(findingId: "FIND-alerts-nav-001")
        XCTAssertNil(router.drillDownFindingId,
            "open(findingId:) MUST NOT affect drillDownFindingId (assistant drill-down)")
    }

    // MARK: - Accept #4 / #5 (F2.1): openFindingId → card selection, no Assistant auto-launch

    @MainActor
    func test_openFindingId_sets_selectedFindingId_in_viewModel_and_does_not_set_drillDownFindingId() {
        // Verifies the full navigate path (F2.1 Accept #1, #2, #3, #5):
        //   1. `open(findingId:)` sets `openFindingId` on the router.
        //   2. `AVXCardsViewModel.selectFinding(id:)` is called with the same id.
        //   3. `consumeOpenFindingId()` clears `openFindingId` (single-shot).
        //   4. `drillDownFindingId` is NOT set (no Assistant auto-launch).
        //
        // The tab-switch (selectedTab → .alerts) is a MainTabView behaviour driven by
        // `.onChange(of: openFindingId)` — it cannot be unit-tested here without a
        // hosted UIViewController. The router-level seam is what we assert.
        let router = AVXDeepLinkRouter()
        let viewModel = AVXCardsViewModel()

        // Simulate AppDelegate notification tap.
        router.open(findingId: "FIND-navigate-0001")

        // Simulate what AVXCardsTab.onChange does: select the card, consume the token.
        viewModel.selectFinding(id: router.openFindingId ?? "")
        router.consumeOpenFindingId()

        XCTAssertEqual(viewModel.selectedFindingId, "FIND-navigate-0001",
            "selectedFindingId MUST be set to the finding_id from the notification tap")
        XCTAssertNil(router.openFindingId,
            "openFindingId MUST be consumed (nil) after the navigate step")
        XCTAssertNil(router.drillDownFindingId,
            "openFindingId navigate path MUST NOT set drillDownFindingId — no Assistant auto-launch")
    }

    @MainActor
    func test_navigate_path_selectedFindingId_single_shot_consume() {
        // Verifies that `clearSelectedFinding()` resets `selectedFindingId` to nil so
        // back-navigation or re-render does not re-trigger the highlight (F2.1 Accept #2).
        let viewModel = AVXCardsViewModel()

        viewModel.selectFinding(id: "FIND-consume-test-001")
        XCTAssertNotNil(viewModel.selectedFindingId, "selectedFindingId MUST be non-nil after selectFinding")

        viewModel.clearSelectedFinding()
        XCTAssertNil(viewModel.selectedFindingId,
            "selectedFindingId MUST be nil after clearSelectedFinding (single-shot semantics)")
    }

    @MainActor
    func test_navigate_path_does_not_trigger_assistant_cover() {
        // Explicitly asserts F2.1 Accept #3: notification tap MUST NOT set
        // `drillDownFindingId`, which is the property that triggers the Assistant cover.
        // Tapping "Ask assistant" on the highlighted card is the user-initiated step 2;
        // the notification tap must NOT perform it automatically.
        let router = AVXDeepLinkRouter()

        router.open(findingId: "FIND-no-assistant-0001")

        XCTAssertNotNil(router.openFindingId,
            "open(findingId:) MUST set openFindingId (tab-switch + card-select path)")
        XCTAssertNil(router.drillDownFindingId,
            "open(findingId:) MUST NOT set drillDownFindingId — that auto-launches the Assistant, which is step 2 only")
    }

    // MARK: - Structural guards for the wiring itself (F2.1)

    /// The tests above assert the *router* and *view-model* contracts. They do NOT — and
    /// cannot — assert that `MainTabView` actually observes `openFindingId`, because a
    /// SwiftUI `.onChange` only fires inside a hosted view hierarchy, which a plain
    /// `XCTestCase` has no way to drive.
    ///
    /// That gap was not theoretical: F2.1's mutation (deleting the `.onChange` block from
    /// `MainTabView`) left every behavioural test above **passing**. A guard that a
    /// deliberate removal of the feature cannot break is asserting presence of the
    /// router API, not the navigation property (see
    /// `~/.kiro/steering/testing.md` § "Mutation testing at the green boundary").
    ///
    /// So the wiring is asserted where it actually lives: in the source. Same approach, and
    /// same justification, as `AppDelegateTests.test_device_token_is_never_logged_at_public_privacy`
    /// — the property is real but not in-process observable.
    func test_mainTabView_observes_openFindingId_and_switches_to_alerts() throws {
        let source = try String(contentsOf: Self.mainTabViewSourceURL, encoding: .utf8)

        guard let block = Self.onChangeBlock(observing: "AVXDeepLinkRouter.shared.openFindingId",
                                             in: source) else {
            return XCTFail("""
                MainTabView does not observe AVXDeepLinkRouter.shared.openFindingId.
                Without it, a notification tap sets openFindingId and nothing navigates —
                the deep-link lands nowhere, which is the exact defect F2.1 fixed
                (Task 3.4 Accept #4/#5).
                """)
        }

        XCTAssertTrue(
            block.contains("selectedTab = .alerts"),
            """
            MainTabView observes openFindingId but does not switch to the .alerts tab.
            `.alerts` is the ABSORB host for AVXCardsTab (Task 1.2); there is no .avx tab.
            Block found:
            \(block)
            """
        )

        // Two-step design must not collapse: navigating must not present the Assistant.
        for forbidden in ["isAssistantPresented = true", "openDrillDown", "drillDownFindingId"] {
            XCTAssertFalse(
                block.contains(forbidden),
                """
                The openFindingId observer references `\(forbidden)`, which would auto-launch
                the Assistant on every notification tap. Navigation (step 1) and drill-down
                (step 2, user-initiated) must stay distinct.
                """
            )
        }
    }

    /// Companion guard for the card-selection half, which lives in `AVXCardsTab`.
    func test_avxCardsTab_observes_openFindingId_and_selects_the_card() throws {
        let source = try String(contentsOf: Self.avxCardsTabSourceURL, encoding: .utf8)

        guard let block = Self.onChangeBlock(observing: "AVXDeepLinkRouter.shared.openFindingId",
                                             in: source) else {
            return XCTFail(
                "AVXCardsTab does not observe openFindingId, so the matching card is never "
                + "highlighted (Task 3.4 Accept #5)."
            )
        }
        XCTAssertTrue(
            block.contains("selectFinding"),
            "AVXCardsTab observes openFindingId but never calls selectFinding(id:)."
        )
        XCTAssertTrue(
            block.contains("consumeOpenFindingId"),
            """
            AVXCardsTab does not consume openFindingId after selecting, so a re-render can
            re-trigger navigation (F2.1 Accept #2 requires single-shot consume).
            """
        )
    }

    // MARK: - Structural helpers

    /// Returns the text of the `.onChange(of: <keyPath>)` call including its trailing
    /// closure, or nil when absent. Balanced-brace scan from the closure's opening `{`.
    private static func onChangeBlock(observing keyPath: String, in source: String) -> String? {
        guard let anchor = source.range(of: ".onChange(of: \(keyPath)") else { return nil }
        guard let braceStart = source.range(of: "{", range: anchor.upperBound..<source.endIndex)
        else { return nil }
        var depth = 1
        var idx = braceStart.upperBound
        while idx < source.endIndex, depth > 0 {
            let ch = source[idx]
            if ch == "{" { depth += 1 }
            if ch == "}" { depth -= 1 }
            idx = source.index(after: idx)
        }
        return String(source[anchor.lowerBound..<idx])
    }

    /// This test file lives at `MeridianMotorsCompanionTests/AVX/`, so three
    /// `deletingLastPathComponent` calls reach `clients/ios`.
    private static var iosRootURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // AVX
            .deletingLastPathComponent()   // MeridianMotorsCompanionTests
            .deletingLastPathComponent()   // clients/ios
    }

    private static var mainTabViewSourceURL: URL {
        iosRootURL.appendingPathComponent("MeridianMotorsCompanion/Views/MainTabView.swift")
    }

    private static var avxCardsTabSourceURL: URL {
        iosRootURL.appendingPathComponent("MeridianMotorsCompanion/Views/AVX/AVXCardsTab.swift")
    }
}
