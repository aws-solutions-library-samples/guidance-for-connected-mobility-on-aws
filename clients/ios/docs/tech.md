# iOS Client — SDK & Framework Reference

**Verified against**: iOS SDK 18.0 (deployment target `IPHONEOS_DEPLOYMENT_TARGET = 18.0`),
Xcode 26.4.1, Swift 6.  
All entries below cite live Apple Developer Documentation URLs. Reviewer spot-check policy:
verify at least three URLs resolve with HTTP 200 before closing Task 1.1.

---

## (a) `UNUserNotificationCenter.requestAuthorization(options:)`

**Framework**: `UserNotifications`  
**Source**: https://developer.apple.com/documentation/usernotifications/unusernotificationcenter/requestauthorization(options:completionhandler:)

### Signatures

```swift
// Callback-based (iOS 10+)
func requestAuthorization(
    options: UNAuthorizationOptions = [],
    completionHandler: @escaping @Sendable (Bool, (any Error)?) -> Void
)

// Async/throws (iOS 15+, preferred for Swift concurrency)
func requestAuthorization(
    options: UNAuthorizationOptions = []
) async throws -> Bool
```

### Options set (`UNAuthorizationOptions`)

Source: https://developer.apple.com/documentation/usernotifications/unauthorizationoptions

| Option | Purpose |
|--------|---------|
| `.alert` | Display alert banners |
| `.sound` | Play notification sounds |
| `.badge` | Update the app icon badge number |
| `.carPlay` | Show notifications in CarPlay |
| `.criticalAlert` | Play sounds for critical alerts (requires entitlement) |
| `.providesAppNotificationSettings` | Show an in-app settings button in notification UI |
| `.provisional` | Post non-interrupting notifications without prompting |

**AVX iOS usage**: request `[.alert, .sound, .badge]` on first launch.

### Usage pattern

```swift
let center = UNUserNotificationCenter.current()
do {
    let granted = try await center.requestAuthorization(options: [.badge, .sound, .alert])
    if granted {
        // Request device token
        await UIApplication.shared.registerForRemoteNotifications()
    }
} catch {
    // log error; do not crash
}
```

> **Note**: Call this before scheduling local notifications and before calling
> `UIApplication.shared.registerForRemoteNotifications()`. The first call prompts
> the user; subsequent calls use the cached authorization state without prompting.

---

## (b) `UNNotificationCategory` — per-category consent pattern

**Framework**: `UserNotifications`  
**Source**: https://developer.apple.com/documentation/usernotifications/unnotificationcategory

### Class declaration

```swift
class UNNotificationCategory: NSObject
```

### Role in AVX iOS

Categories are the mechanism for per-topic opt-in/out. Each `UNNotificationCategory` has
an `identifier` string that must match the `category` key in the remote-notification `aps`
dictionary. Three categories for AVX iOS:

| Identifier constant | Human label | Corresponds to AVX severity group |
|--------------------|-------------|----------------------------------|
| `"AVX_SAFETY"` | Safety alerts | P0 / P1 stop-driving findings |
| `"AVX_COVERAGE"` | Coverage alerts | P2 findings |
| `"AVX_SERVICE"` | Service reminders | P3 findings |

### Registration pattern

```swift
func registerNotificationCategories() {
    let safetyCategory = UNNotificationCategory(
        identifier: "AVX_SAFETY",
        actions: [],
        intentIdentifiers: [],
        options: []
    )
    let coverageCategory = UNNotificationCategory(
        identifier: "AVX_COVERAGE",
        actions: [],
        intentIdentifiers: [],
        options: []
    )
    let serviceCategory = UNNotificationCategory(
        identifier: "AVX_SERVICE",
        actions: [],
        intentIdentifiers: [],
        options: []
    )
    UNUserNotificationCenter.current().setNotificationCategories([
        safetyCategory, coverageCategory, serviceCategory
    ])
}
```

### Per-category enable/disable

iOS does **not** provide per-category authorization at the OS level — the OS grants or
denies all notifications for an app. Per-category enable/disable in `NotificationConsentService`
is therefore **client-side state** (persisted in Keychain or `UserDefaults`). The
`willPresent` delegate method checks the notification's `categoryIdentifier` against the
local preference and silently drops notifications for disabled categories
(see § (d) below for the delegate method).

> **Source citation**: The `UNNotificationCategory` docs confirm category identifiers
> link remote payload `category` keys to registered app categories and may attach action
> buttons. Per-category OS-level toggling is not supported; the App Notification Settings
> option (`.providesAppNotificationSettings`) redirects users to an in-app UI, which is the
> correct pattern for this feature.

---

## (c) `application(_:didRegisterForRemoteNotificationsWithDeviceToken:)` — device-token shape

**Framework**: `UIKit`  
**Source**: https://developer.apple.com/documentation/uikit/uiapplicationdelegate/application(_:didregisterforremotenotificationswithdevicetoken:)

### Signature

```swift
optional func application(
    _ application: UIApplication,
    didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
)
```

### Parameters

- `application`: The singleton app object.
- `deviceToken`: A globally unique `Data` value identifying this device to APNs.
  APNs device tokens are of **variable length** — do NOT hard-code their size.

### Data → hex-string conversion

The standard conversion pattern for forwarding the token to a server:

```swift
func application(
    _ application: UIApplication,
    didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
) {
    let tokenHex = deviceToken.map { String(format: "%02.2hhx", $0) }.joined()
    // Forward tokenHex to VSAClient.registerDevice(token:perCategoryConsent:platform:)
    os_log("🔔 APNs: registered token=%{public}s", log: .default, type: .info, tokenHex)
}
```

> **Important from docs**: Never cache the device token locally. APNs issues a new token
> after device restore, reinstall, or OS reinstall. Always request a fresh token at launch
> and forward it to the server.

### Companion failure handler

```swift
optional func application(
    _ application: UIApplication,
    didFailToRegisterForRemoteNotificationsWithError error: Error
)
```

Implement both — APNs registration may fail if the device is offline or if provisioning
entitlements are missing.

---

## (d) `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)` — background execution window

**Framework**: `UIKit`  
**Source**: https://developer.apple.com/documentation/uikit/uiapplicationdelegate/application(_:didreceiveremotenotification:fetchcompletionhandler:)

### Signature

```swift
optional func application(
    _ application: UIApplication,
    didReceiveRemoteNotification userInfo: [AnyHashable: Any],
    fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
)
```

### **Background-execution budget: 30 seconds (wall-clock)**

From the official documentation (same URL as above):

> *"Your app has up to **30 seconds** of wall-clock time to process the notification and
> call the specified completion handler block. In practice, you should call the handler block
> as soon as you are done processing the notification."*

- **Call `completionHandler` as quickly as possible** — the system tracks elapsed time,
  power usage, and data costs. Apps that use significant power may not be woken early for
  future notifications.
- Pass `.newData`, `.noData`, or `.failed` according to what the fetch produced.
- **Downstream tasks 3.4 and 3.6 assert against this budget.** The `BadgeService.refresh()`
  call from the silent-push handler must complete — or be cancelled by cooperative
  cancellation — well within this window.

### Prerequisites

This method is called (in foreground **and** background) only when:
1. `UIBackgroundModes` in `Info.plist` contains `remote-notification` (see § (i)).
2. The user has not force-quit the app. (Force-quit disables background launch until the
   user relaunches manually.)

### Silent-push (`content-available: 1`) pattern

```swift
func application(
    _ application: UIApplication,
    didReceiveRemoteNotification userInfo: [AnyHashable: Any],
    fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
) {
    guard let aps = userInfo["aps"] as? [String: Any],
          aps["content-available"] as? Int == 1 else {
        completionHandler(.noData)
        return
    }
    Task {
        do {
            await BadgeService.shared.refresh()
            completionHandler(.newData)
        }
        // Task cooperative cancellation keeps this within the ~30s window
    }
}
```

---

## (e) `@UIApplicationDelegateAdaptor` — SwiftUI bridge to `UIApplicationDelegate`

**Framework**: `SwiftUI`  
**Source**: https://developer.apple.com/documentation/swiftui/uiapplicationdelegateadaptor

### Declaration

```swift
@MainActor @preconcurrency @propertyWrapper
struct UIApplicationDelegateAdaptor<DelegateType>
    where DelegateType: NSObject, DelegateType: UIApplicationDelegate
```

### Usage

```swift
// MeridianMotorsCompanionApp.swift
@main
struct MeridianMotorsCompanionApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
```

```swift
// AppDelegate.swift (new file)
class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }
    // ... other delegate methods
}
```

### Constraints

- Declare the adaptor **only once** inside the `App` struct. Multiple declarations produce
  a runtime error.
- If `AppDelegate` conforms to `ObservableObject`, SwiftUI places it in the `Environment`
  automatically (accessible via `@EnvironmentObject`).
- Apple recommends handling lifecycle events via `ScenePhase` where possible; use the
  delegate adaptor specifically for APNs registration callbacks that have no SwiftUI
  equivalent.

---

## (f) Badge number API — iOS 17 deprecation and replacement

### Deprecated API: `UIApplication.shared.applicationIconBadgeNumber`

**Source**: https://developer.apple.com/documentation/uikit/uiapplication/applicationiconbadgenumber  

```swift
// DEPRECATED as of iOS 17.0
var applicationIconBadgeNumber: Int { get set }
```

**Deprecation note (iOS 17.0+)**:
`applicationIconBadgeNumber` was deprecated in **iOS 17.0**.
Use `UNUserNotificationCenter.setBadgeCount(_:withCompletionHandler:)` instead.

Source confirming the deprecation message verbatim from Xcode:
> `applicationIconBadgeNumber was deprecated in iOS 17.0: Use -[UNUserNotificationCenter setBadgeCount:withCompletionHandler:] instead.`

See also: Apple Developer Forum confirming the deprecation:
https://developer.apple.com/forums/thread/740096

### Replacement API: `UNUserNotificationCenter.setBadgeCount(_:withCompletionHandler:)`

**Source**: https://developer.apple.com/documentation/usernotifications/unusernotificationcenter/setbadgecount(_:withcompletionhandler:)

**Available**: iOS 16.0+ (the replacement was introduced before the old API was deprecated in 17.0)

```swift
// Callback form
func setBadgeCount(
    _ newBadgeCount: Int,
    withCompletionHandler completionHandler: (@Sendable ((any Error)?) -> Void)? = nil
)

// Async/throws form (preferred)
func setBadgeCount(_ newBadgeCount: Int) async throws
```

### Usage in `BadgeService`

```swift
// Correct pattern for iOS 18.0 deployment target
let center = UNUserNotificationCenter.current()
do {
    try await center.setBadgeCount(summary.openCount)
} catch {
    os_log("🔔 APNs: badge update failed: %{public}s", error.localizedDescription)
}
```

### Behavioral difference vs. deprecated API

- `setBadgeCount` updates the badge number **without** removing notifications from
  Notification Center (the old `applicationIconBadgeNumber = 0` cleared both badge
  and all notifications).
- `setBadgeCount` requires `UNAuthorizationOptions.badge` to have been granted.
  Setting it when `.badge` authorization is denied silently no-ops.

> **Project requirement**: Since `IPHONEOS_DEPLOYMENT_TARGET = 18.0`, the deployment
> target is above iOS 17.0. Use `setBadgeCount` exclusively; do not reference the
> deprecated property at all.

---

## (g) `xcrun simctl push` — payload schema

**Source (authoritative)**: WWDC 2020 "Become a Simulator Expert", session 10647  
URL: https://developer.apple.com/videos/play/wwdc2020/10647/  
Timestamps: 12:47 (payload structure), 13:15 (command with explicit bundle ID), 13:43 (bundle ID in payload)

**Source (payload keys)**: https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification

### `Simulator Target Bundle` requirement

When using drag-and-drop onto the Simulator window, the JSON file **must** contain the
`"Simulator Target Bundle"` key with the app's bundle identifier. When using the CLI
command with an explicit bundle ID argument, the key may be omitted from the file (the
CLI argument overrides the file value).

### Alert push payload (alert + badge + sound)

```json
{
  "Simulator Target Bundle": "com.aws.meridianmotors.companion",
  "aps": {
    "alert": {
      "title": "AVX Finding",
      "body": "Battery isolation test marginal — review recommended."
    },
    "badge": 3,
    "sound": "default",
    "category": "AVX_SAFETY"
  },
  "finding_id": "FIND-test-0001"
}
```

### Silent push payload (`content-available: 1`)

```json
{
  "Simulator Target Bundle": "com.aws.meridianmotors.companion",
  "aps": {
    "content-available": 1
  }
}
```

> Per Apple docs on `content-available`: must not include `alert`, `badge`, or `sound`
> keys alongside it when sending a background-only notification.

### CLI commands

```bash
# With bundle ID in payload (drag-and-drop or CLI)
xcrun simctl push booted /tmp/avx-push-test.apns

# With bundle ID on command line (overrides payload value)
xcrun simctl push booted com.aws.meridianmotors.companion /tmp/avx-push-test.apns
```

Returns `Notification sent` on success (exit 0). No output on failure; check exit status.

> **Note from Xcode 12 release notes** (Simulator Known Issues):
> "When simulating a push notification in Simulator with the `content-available` key set,
> the system calls `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)`
> instead of `application(_:didFinishLaunchingWithOptions:)`." (Bug 60426170, 60974170 —
> documented in Xcode 12 release notes, subsequently fixed. Behavior should be correct on
> current Xcode 26.4.1 / iOS 26.4 Simulator.)

---

## (h) `.entitlements` file — `aps-environment` values per build config

**Source**: https://developer.apple.com/documentation/bundleresources/entitlements/aps-environment

### Entitlement key

```
com.apple.developer.aps-environment
```

### Values

| Value | When to use |
|-------|-------------|
| `development` | Debug builds using development provisioning. Also called the "sandbox" environment. |
| `production` | Release / App Store builds using distribution provisioning. |

### xcconfig-driven pattern

Drive the value from a build setting so Debug vs. Release builds automatically use the
correct environment:

**`Staging.xcconfig`** (existing file, add):
```
APS_ENVIRONMENT = development
```

**`Release.xcconfig`** (existing file, add):
```
APS_ENVIRONMENT = production
```

**`MeridianMotorsCompanion.entitlements`** (new file):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>aps-environment</key>
    <string>$(APS_ENVIRONMENT)</string>
</dict>
</plist>
```

Then reference the file via `project.pbxproj`'s `CODE_SIGN_ENTITLEMENTS` build setting
for both Debug and Release configurations.

### Simulator APNs behavior note (empirically uncertain — see Task 3.2)

The `aps-environment` entitlement controls real APNs routing. For Simulator testing via
`xcrun simctl push`, the entitlement file is not functionally required for payload delivery
(simctl bypasses the real APNs path). However, whether `registerForRemoteNotifications()`
returns a Simulator-generated device token on current Xcode/iOS has changed across
versions. Task 3.2 runs the empirical experiment and records the outcome.

> **Xcode 14+ note**: Starting with Xcode 14, the iOS Simulator can communicate with APNs
> sandbox to register for notifications. Source: Apple Developer Forum thread 796868
> (https://developer.apple.com/forums/thread/796868). Task 3.2 verifies actual behavior
> on Xcode 26.4.1 / iOS 26.4 Simulator.

---

## (i) `UIBackgroundModes` — `remote-notification` in `Info.plist`

**Source**: https://developer.apple.com/documentation/bundleresources/information-property-list/uibackgroundmodes

**Supporting source** (background notification enable requirement):
https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app

### Required key

```xml
<!-- Info.plist addition -->
<key>UIBackgroundModes</key>
<array>
    <string>remote-notification</string>
</array>
```

### Purpose

Without `UIBackgroundModes: [remote-notification]`:
- The app will **not** be launched in the background when a silent push (`content-available: 1`)
  arrives.
- `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)` is only called
  when the app is in the **foreground**.

With the key present:
- The system wakes the app from suspended state on silent push delivery.
- The app gets up to 30 seconds of background execution time (see § (d)).

### How to add in Xcode

In the target's **Signing & Capabilities** tab, add the **Background Modes** capability
and check **Remote notifications**. Xcode writes the plist key automatically.
Alternatively, edit `Info.plist` directly and add the array item above.

### AVX iOS requirement

The `BadgeService.refresh()` call on silent push (Task 3.6) and the device-wakeup path
for badge updates both depend on this key being present.

---

## Additional notes for downstream tasks

### `UNUserNotificationCenterDelegate` — `willPresent` (foreground) + `didReceive` (tap)

Per-category consent gate in `willPresent`:

```swift
func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    willPresent notification: UNNotification,
    withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
) {
    let categoryId = notification.request.content.categoryIdentifier
    let consentService = NotificationConsentService.shared
    let allowed: Bool
    switch categoryId {
    case "AVX_SAFETY":   allowed = consentService.safetyEnabled
    case "AVX_COVERAGE": allowed = consentService.coverageEnabled
    case "AVX_SERVICE":  allowed = consentService.serviceEnabled
    default:             allowed = true
    }
    completionHandler(allowed ? [.banner, .sound, .badge] : [])
}
```

Tap handler in `didReceive`:

```swift
func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    didReceive response: UNNotificationResponse,
    withCompletionHandler completionHandler: @escaping () -> Void
) {
    let userInfo = response.notification.request.content.userInfo
    if let findingId = userInfo["finding_id"] as? String {
        AVXDeepLinkRouter.shared.open(findingId: findingId)
    }
    completionHandler()
}
```

### `UNUserNotificationCenter` mockability for tests

`UNUserNotificationCenter` is a class, not a protocol. For unit testing
`NotificationConsentService`, create a thin protocol wrapper:

```swift
protocol NotificationCenter {
    func requestAuthorization(
        options: UNAuthorizationOptions
    ) async throws -> Bool
    func setBadgeCount(_ count: Int) async throws
}

extension UNUserNotificationCenter: NotificationCenter {}
```

Inject the protocol in the service initializer; tests can supply a mock.

---

*Document created 2026-09-18 for spec `2026-09-18-avx-ios-cards` Task 1.1.*  
*Verified against Xcode 26.4.1, iOS 26.4 runtime, iOS 18.0 deployment target.*
