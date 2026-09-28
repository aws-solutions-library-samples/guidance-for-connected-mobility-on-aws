import Foundation

/// Static configuration bundled into the app. Not secrets — these are Cognito
/// pool/client IDs and public API endpoints. User credentials live in Keychain.
///
/// Environment selection is driven by xcconfig files:
///   - `Staging.xcconfig` → wired to the **Debug** build configuration
///     (Debug scheme hits us-west-2 staging stacks)
///   - `Release.xcconfig` → wired to the **Release** build configuration
///     (Release scheme hits us-east-1 prod stacks)
///
/// Each xcconfig defines `VSA_*` build-setting variables. `Info.plist`
/// references those variables via `$(VSA_*)` substitution. This file
/// reads the substituted values via `plistString(_:)` with hardcoded
/// prod values as fallback — so any failure in the xcconfig pipeline
/// (missing key, unfilled placeholder, empty string) degrades gracefully
/// to a working prod build rather than `fatalError()`.
///
/// Spec: `2026-05-28-staging-auto-deploy/spec.md` in CVX repo.
enum VSAConfig {
    /// The tenant this build is skinned for. Multi-tenant switching is a v2 feature.
    static let defaultTenantId = "fleet"

    // MARK: - Info.plist helper

    /// Read a string value from `Info.plist`. Returns `nil` for missing
    /// keys, empty values, unfilled `$(VSA_*)` build-setting placeholders,
    /// and literal placeholder tokens shipped in the tracked xcconfig files
    /// (`<REPLACE_...>`, `YOUR_*`, synthetic all-zero UUIDs). Any of these
    /// triggers the Swift-fallback code path in the callers below.
    ///
    /// The literal-token filters exist because
    /// `Staging.xcconfig`/`Release.xcconfig` ship with fail-loud placeholder
    /// values per spec `2026-07-17-ios-tenant-canary-migration` — the real
    /// values come from gitignored `*.local.xcconfig` overrides included
    /// via `#include?` at the bottom of each tracked xcconfig. When the
    /// local override is missing, `plistString` MUST return nil so the
    /// caller can emit an `assertionFailure` in Debug rather than hand the
    /// placeholder string to Cognito (which then fails with an opaque
    /// `ResourceNotFoundException`).
    private static func plistString(_ key: String) -> String? {
        guard let s = Bundle.main.object(forInfoDictionaryKey: key) as? String,
              !s.isEmpty,
              !s.hasPrefix("$("),                          // unfilled build-setting substitution
              !s.hasPrefix("<REPLACE_"),                   // <REPLACE_WITH_USER_POOL_ID> style
              !s.hasPrefix("YOUR_"),                       // YOUR_COGNITO_APP_CLIENT_ID style
              !s.contains("00000000-0000-0000-0000-000000000000")  // synthetic all-zero UUID
        else {
            return nil
        }
        return s
    }

    // MARK: - Environment-driven endpoints

    /// AWS region. Falls back to `us-east-1` if the xcconfig pipeline
    /// failed to populate the Info.plist substitution.
    static var awsRegion: String {
        plistString("VSAAwsRegion") ?? "us-east-1"
    }

    /// AWS region of the Amazon Connect instance used for live-agent
    /// escalation (Participant Service endpoint + chat WebSocket). This is
    /// INDEPENDENT of `awsRegion`: the Connect instance lives in us-east-1
    /// while the app's Cognito identity pool and AgentCore runtime are in
    /// us-west-2. The ParticipantToken minted by StartChatContact is
    /// region-bound, so the chat client MUST target the instance's region —
    /// using `awsRegion` here hits participant.connect.<wrong-region> and
    /// the token is rejected with HTTP 403
    /// (issue 2026-06-22-ios-chat-connect-region). Falls back to us-east-1;
    /// override via the `VSAConnectRegion` Info.plist/xcconfig key if a
    /// tenant's Connect instance ever moves regions.
    static var connectRegion: String {
        plistString("VSAConnectRegion") ?? "us-east-1"
    }

    /// Deployed REST API endpoint (from `vsa-${stage}-api` stack output `RestApiUrl`).
    /// Base URL of a numbered 360-frame sequence for the owned vehicle
    /// (`<base>/1.png` … `<base>/<N>.png`), or nil when unset.
    ///
    /// Sourced from `VSA_VEHICLE_360_BASE_URL`, which lives ONLY in the
    /// gitignored `Staging.local.xcconfig` — a real value is tenant-branded
    /// and tenant brand strings are canary-forbidden in committed source.
    /// Nil disables the turntable and the Vehicle tab falls back to a glyph.
    static var vehicle360BaseUrl: String? {
        guard let s = plistString("VSAVehicle360BaseUrl"),
              !s.isEmpty, s.hasPrefix("http") else { return nil }
        return s
    }

    /// Number of frames in the sequence. 0 (or unset) disables the turntable.
    static var vehicle360FrameCount: Int {
        Int(plistString("VSAVehicle360Frames") ?? "") ?? 0
    }

    static var restApiUrl: URL {
        if let s = plistString("VSARestApiUrl"), let u = URL(string: s) {
            return u
        }
        return URL(string: "https://jsy5rf5c2b.execute-api.us-east-1.amazonaws.com/prod")!
    }

    /// Deployed WebSocket API endpoint (from `vsa-${stage}-api` stack output `WsApiUrl`).
    static var wsApiUrl: URL {
        if let s = plistString("VSAWsApiUrl"), let u = URL(string: s) {
            return u
        }
        return URL(string: "wss://4n0yg5h3tb.execute-api.us-east-1.amazonaws.com/prod")!
    }

    /// CMS UI main API base (cms-${stage}-ui-api). Used by the driver
    /// self-vehicle-claim flow (GET /api/v1/vehicles, PUT /api/v1/drivers/{id}).
    /// Authenticated with the VSA-pool id-token; the CMS authorizer trusts the
    /// VSA pool and main_api constrains driver tokens to a self-service allowlist.
    /// Optional: nil when `VSA_CMS_REST_API_URL` is unset/empty (e.g. prod until
    /// wired) — callers hide the claim affordance rather than crash.
    static var cmsRestApiUrl: URL? {
        guard let s = plistString("VSACmsRestApiUrl"), let u = URL(string: s) else {
            return nil
        }
        return u
    }

    /// Remote-commands API base (`cms-${stage}-commands-api`).
    ///
    /// A SEPARATE API Gateway from `cmsRestApiUrl` — do not conflate them. Its
    /// Cognito authorizer trusts the same pool the app already signs into
    /// (`cms-staging-ui-users`), which is why no additional federation was needed
    /// to call it; the token in hand is already accepted.
    ///
    /// Optional, and callers MUST hide the controls affordance when nil rather
    /// than surfacing a disabled button with no explanation — same contract as
    /// `cmsRestApiUrl`. Unset is the expected state anywhere the commands stack
    /// is not deployed.
    static var commandsApiUrl: URL? {
        guard let s = plistString("VSACommandsApiUrl"), let u = URL(string: s) else {
            return nil
        }
        return u
    }

    /// CMS telemetry WebSocket (ws-fanout service). Pushes real-time vehicle
    /// telemetry and alerts. Connect with ?fleetId=X&token=JWT.
    static var telemetryWsUrl: URL {
        if let s = plistString("VSATelemetryWsUrl"), let u = URL(string: s) {
            return u
        }
        return URL(string: "wss://f350hn08d9.execute-api.us-east-1.amazonaws.com/live")!
    }

    /// Deployed AgentCore BidiAgent runtime hosting `bidi_app.py`. Used by
    /// `VSABidiClient` for voice sessions. The WebSocket URL is derived from
    /// this ARN at connect time; auth is SigV4.
    static var agentCoreBidiRuntimeArn: String {
        plistString("VSAAgentCoreRuntimeArn")
            ?? "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/YOUR_RUNTIME_ID"
    }

    /// Demo identity — update these after running `make bootstrap-demo` in the
    /// CMS repo and `make seed-staging` (or equivalent seed script) in the CVX repo.
    /// These should match a seeded driver/vehicle pair in your CMS deployment.
    static let demoVin = "1HGBH41JXMN000024"
    static let demoVehicleId = "VEH-0025"
    static let demoDriverId = "DRV-0055"

    // MARK: - Cognito tenant pools

    /// Per-tenant Cognito pool and app client IDs.
    struct TenantPool {
        let tenantId: String
        let userPoolId: String
        let clientId: String
        let displayName: String
        /// Cognito Identity Pool ID (e.g. "us-east-1:abc-..."), federated with
        /// `userPoolId`. Used by `AwsCredentialProvider` to exchange the user's
        /// IdToken for temporary AWS credentials that can SigV4-sign AgentCore
        /// WebSocket handshakes. Deployed via the `TenantPool` CDK construct
        /// in `infrastructure/lib/tenant-pool.ts` of the VFO repo.
        let identityPoolId: String
    }

    /// All known tenant pools. Today only `fleet` is wired; pools for
    /// `oem` and `enterprise` (rental) personas reuse the fleet pool
    /// for the demo (see `demoPersonas` below).
    ///
    /// User pool / client / identity-pool IDs are read from Info.plist
    /// (`VSAUserPoolId`, `VSAUserPoolClientId`, `VSAIdentityPoolId`) so
    /// Debug-scheme builds use staging pools and Release-scheme builds
    /// use prod pools. Fallback values are the prod IDs.
    static var tenantPools: [String: TenantPool] {
        [
            "fleet": TenantPool(
                tenantId: "fleet",
                userPoolId: plistString("VSAUserPoolId") ?? "<REPLACE_WITH_USER_POOL_ID>",
                clientId: plistString("VSAUserPoolClientId") ?? "r4qee39vt3v0dtp6noaiup1ce",
                displayName: "Fleet Services",
                identityPoolId: plistString("VSAIdentityPoolId") ?? "us-east-1:00000000-0000-0000-0000-000000000000"
            )
        ]
    }

    static var defaultPool: TenantPool {
        tenantPools[defaultTenantId]!
    }

    // MARK: - Demo personas

    /// Demo personas shown as quick-pick cards on the sign-in screen.
    /// Each persona maps to (tenantId, email, password) — tapping a card
    /// fills the form and triggers normal sign-in. All three personas
    /// live in the shared, per-deployment CMS UI Cognito user pool today
    /// so we don't need per-persona pool wiring; the persona pick just
    /// has to set `AppSession.activeTenantId` so the post-auth tenant
    /// config load (and the agent's segment-driven find_service_center
    /// filter) routes correctly.
    ///
    /// Real production deployments would split these into separate
    /// pools per tenant, but for the demo a single pool keeps the
    /// onboarding flow uniform.
    struct DemoPersona: Identifiable {
        let id: String              // stable key, used as tag in the picker
        let tenantId: String        // sets AppSession.activeTenantId
        let label: String           // e.g. "Fleet Driver"
        let subtitle: String        // e.g. "Fleet · Samantha Carter"
        let email: String
        let password: String
        let symbolName: String      // SF Symbol for the card icon
    }

    /// Demo password for quick-fill personas. Only available in DEBUG builds;
    /// production builds return an empty string so no credential is bundled
    /// into the release artifact. Sign in via the real auth flow in production.
    ///
    /// The value is sourced from `VSA_DEMO_PASSWORD` in the gitignored
    /// `Staging.local.xcconfig` — no demo credential is committed. Empty
    /// there (or unset) means the quick-pick card fills only the email
    /// and leaves password entry to the developer, which is the intended
    /// state for any published build. A prior hardcoded literal was
    /// rotated portfolio-wide 2026-08-05; the scanner config
    /// (`.publish-secrets-scan.yml § forbidden_strings`) carries the exact
    /// value as a reintroduction guard.
    static var demoPassword: String {
        #if DEBUG
        return plistString("VSADemoPassword") ?? ""
        #else
        return ""  // Production builds: no demo password. Sign in via real auth flow.
        #endif
    }

    // NOTE on `tenantId`: these MUST match a real row in the
    // `vsa-{stage}-tenant-config` DynamoDB table, because
    // `MainTabView.loadTenantConfig(tenantId:)` looks the row up by this exact
    // value and `AppSession.layoutSegment` is derived from the row's `segment`
    // attribute. When the lookup misses, `tenantConfig` stays nil and
    // `LayoutSegment.from(nil)` silently falls through to `.fleet` — which
    // mislabels tabs and applies the wrong theming with no error surfaced.
    //
    // The seeded tenant row for the shipping app is `meridian` (segment `oem`).
    // Older builds carried two additional tenants (a fleet-segment and a
    // rental-segment persona); those rows and their Cognito users were removed
    // 2026-08-21 when the persona picker collapsed to one card. The
    // fleet/rental picker cards had become dead references — their Cognito
    // accounts were deleted the same day.
    static let demoPersonas: [DemoPersona] = [
        DemoPersona(
            id: "oem",
            // `meridian` is the primary key of the row in
            // `vsa-{stage}-tenant-config` — lookup by this exact value drives
            // theming and tab labels. See the NOTE block above.
            tenantId: "meridian",
            label: "OEM Driver",
            subtitle: "OEM Owner · Marcus Reyes · Meridian Trailwind",
            // The persona's Cognito user is `meridian.driver@example.com`,
            // seeded against the deployed CMS UI pool (see the deployment
            // runbook for provisioning).
            //
            // Immutable custom claims — `custom:tenantId`, `custom:driverId`,
            // `custom:vehicleId` — cannot be renamed via
            // `AdminUpdateUserAttributes`; the account was recreated when the
            // tenant slug moved to `meridian`. `email` itself IS mutable, so
            // the sign-in identifier can be rotated on an existing user while
            // every immutable attribute stays exactly as created — no
            // delete-and-recreate is needed for an email-only change.
            email: "meridian.driver@example.com",
            password: Self.demoPassword,
            symbolName: "car.side"
        ),
    ]

    /// The persona the sign-in screen prefills.
    ///
    /// Optional rather than force-unwrapped: `demoPersonas` is data, and a build that
    /// trims it to empty should degrade to a blank form, not crash on launch.
    ///
    /// Carries `tenantId` as well as credentials, and that part is load-bearing —
    /// `VSAConfig.defaultTenantId` is "fleet", so a sign-in that sets only email and
    /// password would resolve the WRONG tenant config and silently theme the app as the
    /// fleet segment. The quick-pick used to set `session.activeTenantId` on tap; with
    /// the picker gone, whatever prefills the form has to do the same.
    static var primaryPersona: DemoPersona? { demoPersonas.first }

    /// Dev-only auto-login. Set a non-nil email + password here and DEBUG
    /// builds will sign in automatically on app launch, skipping the
    /// manual login screen. Useful when iterating on a specific driver
    /// scenario (e.g. VEH-0025 via stephanie.johnson). Leave nil when
    /// testing multi-driver flows so each launch gives you the empty
    /// login form.
    ///
    /// Release builds ignore these values (see the #else branch).
    /// If you set one, set both — the auto-sign-in only fires when the
    /// email + password fields both pre-populate.
    #if DEBUG
    // Multi-driver testing mode (2026-05-04+): auto-login disabled so we
    // can sign in as samantha.carter, jose.roberts, etc. Re-enable one
    // line below to get Stephanie's Equinox back with one tap.
    static let devEmail: String? = nil
    static let devPassword: String? = nil
    // Quick toggle for Stephanie Johnson → DRV-0055 / VEH-0025:
    // static let devEmail: String? = "stephanie.johnson@example.com"
    // static let devPassword: String? = demoPassword
    #else
    static let devEmail: String? = nil
    static let devPassword: String? = nil
    #endif
}
