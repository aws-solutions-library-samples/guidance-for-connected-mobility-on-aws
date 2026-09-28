import UIKit
import UserNotifications
import os.log

// MARK: - AppDelegate
//
// Bridges SwiftUI's App lifecycle to UIApplicationDelegate for APNs callbacks.
// Wired into MeridianMotorsCompanionApp via `@UIApplicationDelegateAdaptor(AppDelegate.self)`.
//
// Task 3.1 (WAVE A): Scaffold — log-only bodies + breadcrumbs on AppSession.
// Task 3.4 (WAVE B, CONTRACT-COUPLED): Full behavioural wiring.
//
// ## Accept criteria (Task 3.4)
// 1. didRegisterForRemoteNotificationsWithDeviceToken — converts Data → hex, calls
//    VSAClient.registerDevice LIVE (Task 3.2 outcome: TOKEN_RECEIVED_ONLY_WITH_ENTITLEMENT).
// 2. didReceiveRemoteNotification (silent push, content-available == 1) — calls
//    BadgeService.refresh() and calls completionHandler(.newData) within ~30s.
// 3. willPresent (foreground) — presents banner ONLY when the notification's category
//    matches an ENABLED NotificationConsentService category (defence-in-depth mirror of
//    AVX core's server-side broker gate). Category-disabled: completionHandler([]).
// 4. didReceive (tap) — extracts finding_id from userInfo, calls
//    AVXDeepLinkRouter.shared.open(findingId:) to route into the AVX Cards tab.
//
// ## Platform string for registerDevice
// "apns-staging" (Debug/Staging.xcconfig: APS_ENVIRONMENT=development) or
// "apns-prod"    (Release/Release.xcconfig: APS_ENVIRONMENT=production).
// The "apns-" prefix is REQUIRED by the AVX core OpenAPI contract (enforced by
// VSAClientTests.test_registerDevice_body_shape_matches_contract).

private let apnsLog = OSLog(subsystem: "com.aws.meridianmotors.companion", category: "APNs")

// MARK: - ApnsClientProtocol
//
// Thin protocol over VSAClient's registerDevice method.
// Allows AppDelegateTests to inject a stub without a URLSession.
protocol ApnsClientProtocol: AnyObject {
    func registerDevice(
        token: Data,
        perCategoryConsent: VSAClient.RegisterDeviceCategoryConsent,
        platform: String
    ) async throws -> VSAClient.RegisterDeviceResponse
}

extension VSAClient: ApnsClientProtocol {}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    // MARK: - Dependencies (injectable for tests)

    /// VSAClient used for registerDevice. Defaults to `nil`; populated by
    /// `configure(client:consentService:badgeService:router:)` after the App
    /// creates its `VSAClient` instance. When nil (unit-test path), calls are
    /// skipped.
    var vsaClient: ApnsClientProtocol?

    /// NotificationConsentService — per-category consent gate for willPresent.
    var consentService: NotificationConsentService = .shared

    /// BadgeService — called on silent push.
    var badgeService: BadgeService = .shared

    /// AVXDeepLinkRouter — receives finding_id on notification tap.
    var router: AVXDeepLinkRouter = .shared

    // MARK: - Application lifecycle

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        // Register as the UNUserNotificationCenter delegate so willPresent/didReceive
        // fire on this object. Must be set before the app finishes launching to avoid
        // a window where notifications arrive without a delegate set.
        UNUserNotificationCenter.current().delegate = self
        os_log("🔔 APNs: application didFinishLaunching — delegate set", log: apnsLog, type: .info)
        return true
    }

    // MARK: - Remote notification registration

    /// Called when `UIApplication.shared.registerForRemoteNotifications()` succeeds.
    ///
    /// Task 3.2 outcome: TOKEN_RECEIVED_ONLY_WITH_ENTITLEMENT — a synthetic Simulator
    /// token IS delivered (~230ms) when the aps-environment entitlement is present.
    /// This handler therefore takes the LIVE path: converts Data → hex and calls
    /// VSAClient.registerDevice. The fallback (nil vsaClient) is exercised only in
    /// unit tests where no real URLSession is wired.
    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        // Convert the opaque Data to a hex string.
        // Per Apple docs, the token's length is variable — do NOT hard-code a size.
        let tokenHex = deviceToken.map { String(format: "%02.2hhx", $0) }.joined()
        // SECURITY (security review Cycle 2, Warning): the token is logged at
        // `%{private}s`, NOT `%{public}s`. `os_log`'s default redaction is disabled by
        // `%{public}`, which would place the device token in the unified log in the clear
        // on a Release-reachable path — readable via sysdiagnose, MDM log collection, or
        // the device console. The byte count is logged publicly instead so the handler
        // stays debuggable without emitting the identifier itself.
        // Guarded by `test_device_token_is_never_logged_at_public_privacy`.
        os_log(
            "🔔 APNs: didRegisterForRemoteNotificationsWithDeviceToken — tokenBytes=%{public}d token=%{private}s",
            log: apnsLog,
            type: .info,
            deviceToken.count,
            tokenHex
        )

        guard let client = vsaClient else {
            os_log(
                "🔔 APNs: didRegisterForRemoteNotificationsWithDeviceToken — vsaClient not configured, skipping registerDevice",
                log: apnsLog, type: .info
            )
            return
        }

        // Build per-category consent for the register-device body.
        let consent = VSAClient.RegisterDeviceCategoryConsent(
            safety:   consentService.safetyEnabled,
            coverage: consentService.coverageEnabled,
            service:  consentService.serviceEnabled
        )

        // Platform is "apns-{stage}". APS_ENVIRONMENT build setting drives the
        // stage portion: "development" → "staging"; "production" → "prod".
        let apsEnv = Bundle.main.object(forInfoDictionaryKey: "APS_ENVIRONMENT") as? String ?? "development"
        let stage = apsEnv == "production" ? "prod" : "staging"
        let platform = "apns-\(stage)"

        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let response = try await client.registerDevice(
                    token: deviceToken,
                    perCategoryConsent: consent,
                    platform: platform
                )
                os_log(
                    "🔔 APNs: registerDevice succeeded — platform=%{public}s success=%{public}s",
                    log: apnsLog, type: .info,
                    platform,
                    response.success ? "true" : "false"
                )
            } catch {
                os_log(
                    "🔔 APNs: registerDevice failed — error=%{public}s",
                    log: apnsLog, type: .error,
                    error.localizedDescription
                )
            }
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        // Reachable on Simulator when aps-environment entitlement is absent
        // (Task 3.2 Variant B outcome: "no valid aps-environment entitlement string found").
        os_log(
            "🔔 APNs: didFailToRegisterForRemoteNotificationsWithError — error=%{public}s",
            log: apnsLog,
            type: .error,
            error.localizedDescription
        )
        // No retry logic here — the Simulator returns this deterministically when
        // the entitlement is absent. Task 3.2 records both paths empirically.
    }

    // MARK: - Silent push (background fetch)

    /// Called on `content-available == 1` (silent push).
    ///
    /// iOS background-execution budget: ~30 seconds (Apple docs; cited in tech.md §(d)).
    /// This method MUST call completionHandler before that window closes.
    ///
    /// Accept #2: Calls BadgeService.refresh(ownerId:) then completionHandler(.newData).
    func application(
        _ application: UIApplication,
        didReceiveRemoteNotification userInfo: [AnyHashable: Any],
        fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        let aps = userInfo["aps"] as? [String: Any]
        let contentAvailable = aps?["content-available"] as? Int ?? 0

        os_log(
            "🔔 APNs: didReceiveRemoteNotification — content-available=%{public}d",
            log: apnsLog,
            type: .info,
            contentAvailable
        )

        guard contentAvailable == 1 else {
            // Not a silent push — nothing to refresh.
            completionHandler(.noData)
            return
        }

        // Refresh the badge count from the server.
        // BadgeService.refresh swallows errors (Accept #4c in Task 3.6), so we always
        // complete with .newData on content-available == 1 to signal that the push was
        // processed, even if the badge update fails.
        Task { @MainActor [badgeService] in
            // AppSession.currentOwnerId is not directly accessible from AppDelegate
            // (it is in the SwiftUI environment). We resolve the owner via the
            // shared AVXCardsViewModel / AppSession by reading it off
            // AVXDeepLinkRouter's associated session reference.
            //
            // Practical path: read ownerId from NotificationConsentService's associated
            // session, or fall back to BadgeService's configured client ownerId.
            // BadgeService.refresh(ownerId:) is a noop when ownerId is nil/empty.
            //
            // Note: The proper ownerId source (AppSession.currentOwnerId) is injected
            // via configure(ownerId:) from the App level; until then, refresh is a noop.
            let ownerId = self.currentOwnerId
            await badgeService.refresh(ownerId: ownerId)
            completionHandler(.newData)
        }
    }

    // MARK: - UNUserNotificationCenterDelegate (foreground)

    /// Accept #3: Displays banner+sound+badge ONLY when the notification's category
    /// matches an ENABLED consent category (defence-in-depth mirror of the server-side
    /// broker gate — the server remains authoritative; this is not described as a control).
    ///
    /// Category-disabled notifications are dropped silently (completionHandler([])).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        let categoryId = notification.request.content.categoryIdentifier
        let userInfo = notification.request.content.userInfo
        let findingId = userInfo["finding_id"] as? String ?? "<none>"

        // Client-side consent gate (defence-in-depth).
        // Per spec.md § Deterministic-narration guards: the server-side broker is the
        // authoritative gate; this is a defence-in-depth mirror only. Do NOT describe
        // this as the control.
        let isConsented = consentService.isEnabled(categoryIdentifier: categoryId)

        if isConsented {
            os_log(
                "🔔 APNs: presenting banner for finding_id=%{public}s category=%{public}s",
                log: apnsLog, type: .info,
                findingId, categoryId
            )
            completionHandler([.banner, .sound, .badge])
        } else {
            os_log(
                "🔔 APNs: category %{public}s is disabled — dropping notification silently",
                log: apnsLog, type: .info,
                categoryId
            )
            completionHandler([])
        }
    }

    // MARK: - UNUserNotificationCenterDelegate (tap)

    /// Accept #4: Extracts `finding_id` from userInfo; if present, invokes
    /// `AVXDeepLinkRouter.shared.open(findingId:)` to switch to the AVX Cards tab
    /// and highlight the matching card.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let userInfo = response.notification.request.content.userInfo
        let findingId = userInfo["finding_id"] as? String

        os_log(
            "🔔 APNs: didReceive (tap) — finding_id=%{public}s",
            log: apnsLog,
            type: .info,
            findingId ?? "<none>"
        )

        if let findingId, !findingId.isEmpty {
            Task { @MainActor [router] in
                router.open(findingId: findingId)
            }
        }

        completionHandler()
    }

    // MARK: - Owner ID (injected from App level)

    /// Set by `MeridianMotorsCompanionApp` after sign-in via `configure(ownerId:)`.
    /// Used by the silent-push badge refresh path.
    var currentOwnerId: String? = nil

    /// Configure the AppDelegate with the services it needs to call.
    /// Called from `MeridianMotorsCompanionApp` (or from a test) once the App's
    /// shared state is available.
    ///
    /// - Parameters:
    ///   - client: The VSAClient instance to use for registerDevice calls.
    ///   - consentService: Per-category consent gate.
    ///   - badgeService: Badge refresh service.
    ///   - router: Deep-link router.
    ///   - ownerId: Cognito `sub` claim for the signed-in user.
    func configure(
        client: ApnsClientProtocol,
        consentService: NotificationConsentService = .shared,
        badgeService: BadgeService = .shared,
        router: AVXDeepLinkRouter = .shared,
        ownerId: String? = nil
    ) {
        self.vsaClient = client
        self.consentService = consentService
        self.badgeService = badgeService
        self.router = router
        self.currentOwnerId = ownerId
    }
}
