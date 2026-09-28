//
//  VSABidiClientAvxFindingIdTests.swift
//  MeridianMotorsCompanionTests
//
//  Pins the client half of the AVX drill-down pre-seed contract (AVX iOS
//  Task 4.2), unblocked 2026-09-23 when CVX Fix Group 18 shipped the server
//  reader.
//
//  WHY THESE TESTS EXIST, specifically
//  ----------------------------------
//  A rename or a casing change on either side of this contract degrades
//  SILENTLY to "no pre-seed": the server sees an absent header, opens cold, and
//  asks "which alert do you mean?" — which is indistinguishable from a session
//  that legitimately had no anchor. Nothing errors, no log line says "wrong
//  header name", and the failure is only visible as a slightly worse
//  conversation. That is precisely the class of defect that needs a test rather
//  than a comment.
//
//  The server pins its half with
//  `test_all_three_declared_casings_are_lowercase_and_distinct` plus one test
//  per accepted casing (CVX repo,
//  `agents/supervisor/tests/test_bidi_app_avx_preseed.py`).
//
//  WHAT IS AND IS NOT COVERED
//  --------------------------
//  Covered: the production header-name constant
//  `VSABidiClient.avxFindingIdHeaderName`, and its transformation through the
//  real `AgentCoreSigner.sign` into the query param the server reads.
//
//  The constant exists precisely so this file can pin it. The first version of
//  these tests built their own header dict and called the signer directly, so a
//  mutation renaming the client's inlined literal to `"AvxFindingID"` passed
//  4 of 4 — the tests verified the SIGNER, which was never in doubt, and not the
//  client. `testClientHeaderNameConstantMatchesTheServerContract` is the one
//  that actually catches a rename.
//
//  NOT covered: `VSABidiClient.connect(...)` itself, which opens a real
//  WebSocket and cannot run in a unit test — so "the constant is right" is
//  pinned here, but "connect actually puts it in the dict" is not. And end-to-end
//  delivery is additionally gated by the `requestHeaderAllowlist` in the server
//  repo's GITIGNORED `.bedrock_agentcore.yaml`; AgentCore drops unlisted headers
//  with no error, so a green suite here does not prove arrival. That is AVX iOS
//  Group 5's live-verification, and it is the only thing that can prove it.
//

import XCTest
@testable import MeridianMotorsCompanion

final class VSABidiClientAvxFindingIdTests: XCTestCase {

    /// The wire name, duplicated here ON PURPOSE.
    ///
    /// Deliberately NOT derived from the production constant: deriving it would
    /// make the assertion circular, and a rename would keep passing. The server's
    /// accepted spellings are named in the failure message so whoever breaks this
    /// learns what must change on the other side.
    private let expectedParam =
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-AvxFindingId"

    /// The short header name the client is expected to send, also hard-coded.
    private let expectedHeaderName = "AvxFindingId"

    // MARK: - The production constant itself

    /// THE load-bearing test.
    ///
    /// The other tests in this file drive `AgentCoreSigner` with a dict they
    /// build themselves, so they verify the SIGNER's transformation and cannot
    /// see a rename in `VSABidiClient`. That was a real defect in the first
    /// version of this file: a mutation renaming the client's header literal to
    /// `"AvxFindingID"` passed 4 of 4. This test is what catches it, which is why
    /// `connect()` references `Self.avxFindingIdHeaderName` instead of inlining
    /// the string.
    func testClientHeaderNameConstantMatchesTheServerContract() {
        XCTAssertEqual(
            VSABidiClient.avxFindingIdHeaderName,
            expectedHeaderName,
            """
            The AVX anchor header name changed. The server accepts ONLY these
            lowercase spellings of the delivered key:
              x-amzn-bedrock-agentcore-runtime-custom-avxfindingid
              x-amzn-bedrock-agentcore-runtime-custom-avx-finding-id
              x-amzn-bedrock-agentcore-runtime-custom-avx_finding_id
            Change AVX_FINDING_ID_QUERY_PARAMS in the CVX repo's
            agents/supervisor/bidi_app.py in the same change, or pre-seeding
            degrades silently to "no pre-seed" — nothing errors.
            """
        )
    }

    /// Guards the composition too: the constant, run through the real signer,
    /// must produce the exact param the server reads. Catches a rename of the
    /// constant AND a change to the signer's prefixing in one assertion.
    func testProductionConstantRunThroughTheRealSignerYieldsTheServersParam() throws {
        let url = try signedURL(customHeaders: [
            "User-Token": "jwt.abc",
            "Tenant-Id": "meridian-fleet",
            "Vin": "1FMCU0F70MUA12345",
            VSABidiClient.avxFindingIdHeaderName: "FIND-abc",
        ])
        XCTAssertTrue(
            (url.query ?? "").contains(expectedParam),
            "the production header constant does not sign to \(expectedParam)"
        )
    }

    private func signedURL(customHeaders: [String: String]) throws -> URL {
        try AgentCoreSigner.sign(.init(
            credentials: .init(
                // Synthetic non-key placeholder — the prefix is deliberately
                // NOT `AKIA`/`ASIA` so the value doesn't shape-match the
                // scanner's aws_access_key rule. `AgentCoreSigner` uses it
                // as opaque signing material.
                accessKeyId: "TESTONLY0123456789AB",
                secretKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                sessionToken: "FAKE-SESSION-TOKEN-FOR-TEST",
                expiration: Date().addingTimeInterval(900)
            ),
            region: "us-west-2",
            runtimeArn: "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/vsa_supervisor_bidi_staging-TESTONLY",
            sessionId: "vsa-ios-\(UUID().uuidString)",
            customHeaders: customHeaders,
            expiresInSeconds: 300
        ))
    }

    // MARK: - The wire contract

    func testAvxFindingIdBecomesTheExpectedSignedQueryParam() throws {
        let findingId = "FIND-11111111-2222-3333-4444-55555555555a"
        let url = try signedURL(customHeaders: [
            "User-Token": "jwt.abc",
            "Tenant-Id": "meridian-fleet",
            "Vin": "1FMCU0F70MUA12345",
            "AvxFindingId": findingId,
        ])

        let query = url.query ?? ""
        XCTAssertTrue(
            query.contains(expectedParam),
            """
            The signed URL does not carry \(expectedParam).
            The server accepts only these lowercase spellings of the delivered key:
              x-amzn-bedrock-agentcore-runtime-custom-avxfindingid
              x-amzn-bedrock-agentcore-runtime-custom-avx-finding-id
              x-amzn-bedrock-agentcore-runtime-custom-avx_finding_id
            A different wire name degrades silently to "no pre-seed".
            Query was: \(query)
            """
        )

        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let item = components?.queryItems?.first { $0.name == expectedParam }
        XCTAssertNotNil(item, "param present in the raw query but not parseable as a query item")
        XCTAssertEqual(item?.value, findingId, "the finding id was altered in transit")
    }

    /// Absent anchor must produce NO such param — not an empty one.
    ///
    /// An empty-string value would be truthy on the wire and the server would
    /// treat it as a present-but-unresolvable anchor, logging a warning on every
    /// ordinary voice session. `connect()` guards this with
    /// `if let avxFindingId, !avxFindingId.isEmpty`.
    func testNoParamIsEmittedWhenThereIsNoAnchor() throws {
        let url = try signedURL(customHeaders: [
            "User-Token": "jwt.abc",
            "Tenant-Id": "meridian-fleet",
            "Vin": "1FMCU0F70MUA12345",
        ])

        XCTAssertFalse(
            (url.query ?? "").contains("AvxFindingId"),
            "an anchor param was emitted for a session that has no anchor"
        )
    }

    /// The anchor must not disturb the six fields already carried.
    ///
    /// Regression guard: the pre-seed work added a parameter to a `connect`
    /// signature whose other arguments are positional-by-convention, and a
    /// mis-threaded call would silently drop `VehicleId` or `DriverId` — which
    /// `book()` needs for its CMS write.
    func testAnchorDoesNotDisplaceTheExistingContextFields() throws {
        let url = try signedURL(customHeaders: [
            "User-Token": "jwt.abc",
            "Tenant-Id": "meridian-fleet",
            "Vin": "1FMCU0F70MUA12345",
            "VehicleId": "VEH-0025",
            "DriverId": "DRV-0055",
            "Latitude": "47.6062",
            "Longitude": "-122.3321",
            "AvxFindingId": "FIND-abc",
        ])

        let prefix = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-"
        let query = url.query ?? ""
        for name in ["User-Token", "Tenant-Id", "Vin", "VehicleId",
                     "DriverId", "Latitude", "Longitude", "AvxFindingId"] {
            XCTAssertTrue(
                query.contains(prefix + name),
                "\(name) is missing from the signed URL alongside the anchor"
            )
        }
    }

    // MARK: - Single-shot consume semantics on the view model

    /// The anchor is a property, not an `init` parameter, because the view model
    /// is usually the app-level PRE-WARMED session created at sign-in — long
    /// before a card is tapped. This pins that it is settable and clearable on
    /// an already-constructed instance, which is the whole reason for the
    /// design; an `init`-only anchor would compile, pass a naive test, and never
    /// fire on the common path.
    @MainActor
    func testPendingAnchorIsSettableAndClearableAfterConstruction() {
        let vm = VoiceSessionViewModel(
            tenantId: "meridian-fleet",
            vin: "1FMCU0F70MUA12345",
            jwtProvider: { nil }
        )

        XCTAssertNil(vm.pendingAvxFindingId, "a fresh view model must carry no anchor")

        vm.pendingAvxFindingId = "FIND-abc"
        XCTAssertEqual(vm.pendingAvxFindingId, "FIND-abc")

        // Assigning nil is how AssistantTabView clears a stale anchor left on
        // the shared pre-warmed VM by an earlier cover, so a plain Assistant
        // open cannot inherit a previous card's finding.
        vm.pendingAvxFindingId = nil
        XCTAssertNil(vm.pendingAvxFindingId, "the anchor could not be cleared")
    }
}
