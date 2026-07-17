#if DEBUG
import Foundation

/// One-shot integration test for the voice auth + SigV4 signing path.
///
/// Runs against the deployed AgentCore runtime. Opens a signed WebSocket,
/// sends a `session.start` fallback, waits up to 10 seconds for any server
/// event, logs the outcome, and closes cleanly.
///
/// ## How to run
///
/// 1. In Xcode, add `-DVSA_RUN_INTEGRATION_TEST` to the scheme's Swift
///    compiler flags (Edit Scheme -> Run -> Arguments -> pass to invocation,
///    OR Build Settings -> Other Swift Flags).
/// 2. Sign in to the app normally.
/// 3. The runner fires automatically ~2 seconds after sign-in (see
///    `VoiceIntegrationTestHook` call site, currently in `MainTabView.task`).
/// 4. Watch Xcode's console.
///
/// ## What to look for
///
/// - "INTEGRATION TEST: connected" = WebSocket upgrade succeeded, SigV4
///   signing works. If this line appears, chunk 4 (the signer) is verified.
/// - "INTEGRATION TEST: got event <type>" = server is emitting events,
///   end-to-end path works.
/// - "INTEGRATION TEST: handshake failed <error>" = SigV4 rejected; inspect
///   the error. Common causes: clock skew, custom header mis-encoding,
///   wrong signing scope.
///
/// ## When to remove
///
/// After the integration test passes, drop the `-DVSA_RUN_INTEGRATION_TEST`
/// flag so normal runs don't fire it. The file itself can stay as a
/// regression harness for future changes to the signer.
enum VoiceIntegrationTest {

    /// Timeout for the first server event.
    private static let firstEventTimeoutSeconds: UInt64 = 10

    /// Run the test with a fresh JWT. Logs everything; does not throw.
    static func run(jwt: String) async {
        print("🧪 INTEGRATION TEST: starting")
        print("🧪 INTEGRATION TEST: jwt length \(jwt.count)")

        let provider = AwsCredentialProvider(
            region: VSAConfig.awsRegion,
            identityPoolId: VSAConfig.defaultPool.identityPoolId,
            userPoolId: VSAConfig.defaultPool.userPoolId,
            idTokenProvider: { jwt }
        )

        // Prove the credential path works before we even touch the WebSocket.
        let creds: AwsCredentialProvider.Credentials
        do {
            creds = try await provider.credentials()
            print("🧪 INTEGRATION TEST: got AWS creds (access key \(creds.accessKeyId.prefix(6))…, expires \(creds.expiration))")
        } catch {
            print("🧪 INTEGRATION TEST: credentials failed: \(error.localizedDescription)")
            return
        }

        // Sign the URL ourselves first so we can log it before handing to
        // URLSession. If the signer throws, we see why; if URLSession fails
        // later, we can paste this URL into a shell and curl -I to diagnose.
        let sessionId = "vsa-ios-intg-\(UUID().uuidString.prefix(8))"
        do {
            let previewUrl = try AgentCoreSigner.sign(.init(
                credentials: creds,
                region: VSAConfig.awsRegion,
                runtimeArn: VSAConfig.agentCoreBidiRuntimeArn,
                sessionId: sessionId,
                customHeaders: [
                    "User-Token": jwt,
                    "Tenant-Id": VSAConfig.defaultTenantId,
                    "Vin": VSAConfig.demoVin,
                ],
                expiresInSeconds: 300
            ))
            print("🧪 INTEGRATION TEST: signed URL length \(previewUrl.absoluteString.count)")
            // Print the URL in 400-char chunks to keep console readable.
            let full = previewUrl.absoluteString
            var i = full.startIndex
            var n = 0
            while i < full.endIndex {
                let end = full.index(i, offsetBy: 400, limitedBy: full.endIndex) ?? full.endIndex
                print("🧪 URL[\(n)]: \(full[i..<end])")
                i = end
                n += 1
            }
        } catch {
            print("🧪 INTEGRATION TEST: signer threw: \(error.localizedDescription)")
            return
        }

        let client = VSABidiClient(backend: .agentcoreBidi(
            agentRuntimeArn: VSAConfig.agentCoreBidiRuntimeArn,
            region: VSAConfig.awsRegion
        ))

        let stream: AsyncStream<VSABidiClient.Event>
        do {
            stream = try await client.connect(
                tenantId: VSAConfig.defaultTenantId,
                vin: VSAConfig.demoVin,
                jwt: jwt,
                credentialProvider: provider
            )
            print("🧪 INTEGRATION TEST: connected")
        } catch {
            print("🧪 INTEGRATION TEST: handshake failed: \(error.localizedDescription)")
            return
        }

        // Consume events until we see one or time out. We're just checking
        // that the wire is alive — we don't send audio because that would
        // require the full capture pipeline.
        let timeoutTask = Task<Void, Never> {
            try? await Task.sleep(nanoseconds: firstEventTimeoutSeconds * 1_000_000_000)
        }
        let receiveTask = Task<String?, Never> {
            for await event in stream {
                return "\(event)"
            }
            return nil
        }

        let winner = await withTaskGroup(of: (String, String?).self) { group -> String? in
            group.addTask {
                _ = await timeoutTask.value
                return ("timeout", nil)
            }
            group.addTask {
                let result = await receiveTask.value
                return ("event", result)
            }
            if let first = await group.next() {
                group.cancelAll()
                return first.0 == "timeout" ? nil : first.1
            }
            return nil
        }

        timeoutTask.cancel()
        receiveTask.cancel()

        if let evt = winner {
            print("🧪 INTEGRATION TEST: got event \(evt)")
        } else {
            print("🧪 INTEGRATION TEST: no event within \(firstEventTimeoutSeconds)s (connection opened but server silent)")
        }

        await client.disconnect()
        print("🧪 INTEGRATION TEST: done, disconnected cleanly")
    }
}

/// Hook for invoking the integration test from app lifecycle code. Wrapped
/// so the call site stays a one-liner and the flag check lives here.
enum VoiceIntegrationTestHook {
    /// Call this after sign-in completes. Internally checks the build flag
    /// and no-ops if disabled; delays 2 seconds so the auth state settles
    /// before the test fires.
    static func fireAfterSignIn(idTokenProvider: @escaping @Sendable () -> String?) {
        #if VSA_RUN_INTEGRATION_TEST
        Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard let jwt = idTokenProvider(), !jwt.isEmpty else {
                print("🧪 INTEGRATION TEST: skipped — no JWT available")
                return
            }
            await VoiceIntegrationTest.run(jwt: jwt)
        }
        #endif
    }
}
#endif
