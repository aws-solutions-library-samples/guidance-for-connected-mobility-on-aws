import Foundation
import UserNotifications
import Observation
import os

// MARK: - Protocol for testability
// UNUserNotificationCenter is a class, not a protocol.
// Wrapping it in a thin protocol lets unit tests inject a mock.
// Source: clients/ios/docs/tech.md § "UNUserNotificationCenter mockability for tests"
protocol NotificationCenterProtocol: AnyObject {
    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool
    func setNotificationCategories(_ categories: Set<UNNotificationCategory>)
}

extension UNUserNotificationCenter: NotificationCenterProtocol {}

// MARK: - Persistence keys
// tech.md §(b): iOS has no OS-level per-category toggle, so per-category
// enable/disable is client-owned state. We use UserDefaults (not Keychain)
// for these non-secret UI preferences — tech.md recommends UserDefaults
// for non-secret UI preferences, Keychain only for auth tokens.
private enum ConsentKeys {
    static let hasRequestedAuthorization = "avx.notifications.hasRequestedAuthorization"
    static let safetyEnabled             = "avx.notifications.safetyEnabled"
    static let coverageEnabled           = "avx.notifications.coverageEnabled"
    static let serviceEnabled            = "avx.notifications.serviceEnabled"
}

// MARK: - Notification category identifiers
// Must match the `category` key in remote-notification `aps` payloads.
// Source: tech.md §(b)
enum AVXNotificationCategory: String {
    case safety   = "AVX_SAFETY"
    case coverage = "AVX_COVERAGE"
    case service  = "AVX_SERVICE"
}

// MARK: - NotificationConsentService
/// Wraps UNUserNotificationCenter to:
///  1. Request `.alert, .sound, .badge` authorization on first active scenePhase.
///  2. Register the three AVX notification categories (AVX_SAFETY, AVX_COVERAGE, AVX_SERVICE).
///  3. Expose per-category enable/disable state that persists across launches via UserDefaults.
///
/// iOS does NOT provide per-category OS-level authorization; per-category on/off is
/// client-side state only (see tech.md §(b)).
@Observable
final class NotificationConsentService {
    // MARK: Shared instance
    static let shared = NotificationConsentService()

    // MARK: Per-category state (persisted via UserDefaults)
    var safetyEnabled: Bool {
        didSet { defaults.set(safetyEnabled, forKey: ConsentKeys.safetyEnabled) }
    }
    var coverageEnabled: Bool {
        didSet { defaults.set(coverageEnabled, forKey: ConsentKeys.coverageEnabled) }
    }
    var serviceEnabled: Bool {
        didSet { defaults.set(serviceEnabled, forKey: ConsentKeys.serviceEnabled) }
    }

    // MARK: Auth state (read-only, updated after requestAuthorization)
    private(set) var authorizationGranted: Bool = false

    // MARK: Private
    private let center: NotificationCenterProtocol
    private let defaults: UserDefaults
    private let log = Logger(subsystem: "com.aws.meridianmotors.companion", category: "AVXNotifications")

    // MARK: Init (injectable center and defaults for tests)
    init(center: NotificationCenterProtocol = UNUserNotificationCenter.current(),
         defaults: UserDefaults = .standard) {
        self.center = center
        self.defaults = defaults

        // Load persisted per-category preferences; default to `true` on first install.
        // Using `bool(forKey:)` returns `false` if the key is absent, so we invert the
        // logic: treat absence as "enabled" by writing `true` if not yet set.
        if defaults.object(forKey: ConsentKeys.safetyEnabled) == nil {
            defaults.set(true, forKey: ConsentKeys.safetyEnabled)
        }
        if defaults.object(forKey: ConsentKeys.coverageEnabled) == nil {
            defaults.set(true, forKey: ConsentKeys.coverageEnabled)
        }
        if defaults.object(forKey: ConsentKeys.serviceEnabled) == nil {
            defaults.set(true, forKey: ConsentKeys.serviceEnabled)
        }

        safetyEnabled   = defaults.bool(forKey: ConsentKeys.safetyEnabled)
        coverageEnabled = defaults.bool(forKey: ConsentKeys.coverageEnabled)
        serviceEnabled  = defaults.bool(forKey: ConsentKeys.serviceEnabled)
    }

    // MARK: - Category registration
    /// Registers the three AVX UNNotificationCategory objects with the notification center.
    /// Called once after authorization is granted (or on each launch to refresh).
    func registerCategories() {
        let safety = UNNotificationCategory(
            identifier: AVXNotificationCategory.safety.rawValue,
            actions: [],
            intentIdentifiers: [],
            options: []
        )
        let coverage = UNNotificationCategory(
            identifier: AVXNotificationCategory.coverage.rawValue,
            actions: [],
            intentIdentifiers: [],
            options: []
        )
        let service = UNNotificationCategory(
            identifier: AVXNotificationCategory.service.rawValue,
            actions: [],
            intentIdentifiers: [],
            options: []
        )
        center.setNotificationCategories([safety, coverage, service])
        log.info("🔔 AVX: notification categories registered (SAFETY, COVERAGE, SERVICE)")
    }

    // MARK: - Authorization request
    /// Requests notification authorization (.alert, .sound, .badge).
    /// Safe to call multiple times — subsequent calls use the cached OS decision
    /// without re-prompting the user (source: tech.md §(a)).
    /// On first call (when consent has never been requested), this triggers the OS prompt.
    @MainActor
    func requestAuthorizationIfNeeded() async {
        do {
            let granted = try await center.requestAuthorization(options: [.alert, .sound, .badge])
            authorizationGranted = granted
            if granted {
                registerCategories()
                log.info("🔔 AVX: notification authorization granted")
            } else {
                log.info("🔔 AVX: notification authorization denied by user")
            }
            self.defaults.set(true, forKey: ConsentKeys.hasRequestedAuthorization)
        } catch {
            log.error("🔔 AVX: requestAuthorization failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    // MARK: - Category consent gate (used by AppDelegate.willPresent)
    /// Returns `true` if the given category identifier is enabled.
    /// Used in `willPresent` to decide whether to show a notification banner.
    func isEnabled(categoryIdentifier: String) -> Bool {
        switch categoryIdentifier {
        case AVXNotificationCategory.safety.rawValue:   return safetyEnabled
        case AVXNotificationCategory.coverage.rawValue: return coverageEnabled
        case AVXNotificationCategory.service.rawValue:  return serviceEnabled
        default: return true // unknown categories pass through
        }
    }
}
