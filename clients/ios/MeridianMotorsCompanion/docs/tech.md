# MeridianMotorsCompanion Technical Reference

Verified API patterns for Zone 2 iPad adaptive layout + XCTest target work.
All entries carry a source citation. Verified 2026-08-11.

---

## Snapshot testing — baseline convention

Baselines are PNG files committed alongside the test source file under a
`__Snapshots__` directory.  The path convention (swift-snapshot-testing 1.17.6):

```
MeridianMotorsCompanionTests/Snapshots/__Snapshots__/<SwiftFilename>/<testMethodName>.<name>.png
```

where `<SwiftFilename>` is the source file's name **without extension** (not the
class name), `<testMethodName>` is the `#function` of the calling test, and
`<name>` is the optional `named:` argument passed to `assertAdaptiveSnapshot`.

Example (harness smoke test):
```
MeridianMotorsCompanionTests/Snapshots/__Snapshots__/SnapshotHelpers/testHarnessSmoke.phonePortrait.png
```

The `__Snapshots__` directory is filesystem-only — it is NOT added to
`project.pbxproj`.  `xcodebuild test` locates baselines via `#file` at runtime.

### Record mode (opt-in only)

```bash
# Record new baselines. The compile-time flag is the delivery path that works
# with plain xcodebuild; keep $(inherited) so the target's own flags survive.
xcodebuild test \
  -scheme MeridianMotorsCompanion \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:MeridianMotorsCompanionTests/Snapshots \
  OTHER_SWIFT_FLAGS='$(inherited) -D RECORD_SNAPSHOTS'
```

Recording always **fails** the test — that is the library's design, not an error:
`Record mode is on. Automatically recorded snapshot: … Turn record mode off and
re-run … to assert against the newly-recorded snapshot`. Re-run without the flag
to confirm the baseline asserts.

> **Do not use an environment variable from the command line.** Verified
> 2026-08-17: neither `RECORD_SNAPSHOTS=1` in the parent shell nor
> `TEST_RUNNER_RECORD_SNAPSHOTS=1` reaches the simulator test process, so a
> record command written that way runs green and records nothing. The helper
> still honours `RECORD_SNAPSHOTS` in the process environment, but only for the
> Xcode path — a scheme or test plan with Test-action environment variables.
>
> Source: verified empirically against `xcodebuild` on Xcode 26.4; see
> `.kiro/specs/2026-08-04-zone2-ipad-adaptive-kiosk-and-xctest/decisions.md`
> § 2026-08-17.

**NEVER hardcode `record: true` in committed test source.**  A suite left in
record mode passes unconditionally and is worse than no suite.

### Assert (normal CI run)

```bash
xcodebuild test \
  -scheme MeridianMotorsCompanion \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:MeridianMotorsCompanionTests/Snapshots
```

A missing baseline FAILS, and fails **without writing anything to disk**.
`assertAdaptiveSnapshot` checks for the reference before handing off to the
library, because the library's own behaviour on a missing reference is to record
it *and* fail — which means run 1 fails and every run afterwards passes against a
baseline nobody reviewed. The failure mode is the second run, not the first.

Source: https://github.com/pointfreeco/swift-snapshot-testing/blob/1.17.6/Sources/SnapshotTesting/AssertSnapshot.swift#L20

---

## swift-snapshot-testing

**Package**: `https://github.com/pointfreeco/swift-snapshot-testing`
**Version pin**: `1.17.6` (exact, no ranges).
- Released: 21 Oct 2024 — well past the 7-day quarantine requirement.
- Last in the 1.17.x line before the spec was authored; chosen over 1.17.5
  because it fixes `assertSnapshot` for Swift Testing tests (#916).
- Reason for 1.17.x vs newer (1.18+/1.19+): spec § Constraints pins 1.17.x.
  The 1.18.0 release added Android support and refactored `dump` strategy;
  behaviour-compatible but spec constraint takes precedence.

Source: https://github.com/pointfreeco/swift-snapshot-testing/releases/tag/1.17.6

### SPM import

```swift
// In Package.swift (or added programmatically to the Xcode project):
.package(
    url: "https://github.com/pointfreeco/swift-snapshot-testing",
    exact: "1.17.6"
)
```

Product name for import: `SnapshotTesting` (module `SnapshotTesting`).

```swift
import SnapshotTesting
```

### assertSnapshot signature

```swift
// Primary overload (as of 1.17.6):
public func assertSnapshot<Value, Format>(
    of value: Value,
    as snapshotting: Snapshotting<Value, Format>,
    named name: String? = nil,
    record recording: Bool = false,
    snapshotDirectory: String? = nil,
    timeout: TimeInterval = 5,
    file: StaticString = #file,
    testName: String = #function,
    line: UInt = #line
) where Value : AnyObject
```

Simpler alias for views:
```swift
assertSnapshot(of: view, as: .image(layout: .device(config: .iPhone13Pro)))
assertSnapshot(of: view, as: .image(traits: UITraitCollection(traitsFrom: [
    .init(horizontalSizeClass: .regular),
    .init(verticalSizeClass: .regular)
])))
```

Source: https://github.com/pointfreeco/swift-snapshot-testing/blob/1.17.6/Sources/SnapshotTesting/AssertSnapshot.swift

### .image strategies

```swift
// .image(layout:) — available layouts:
Snapshotting<UIViewController, UIImage>.image(layout: .device(config: .iPhone13Pro))
Snapshotting<UIView, UIImage>.image(precision: 0.99, size: CGSize(width: 390, height: 844))

// .image(traits:) — inject a trait collection:
let regularWidthTraits = UITraitCollection(traitsFrom: [
    UITraitCollection(horizontalSizeClass: .regular),
    UITraitCollection(verticalSizeClass: .regular),
    UITraitCollection(userInterfaceIdiom: .pad)
])
assertSnapshot(of: vc, as: .image(traits: regularWidthTraits))
```

Source: https://github.com/pointfreeco/swift-snapshot-testing/blob/1.17.6/Sources/SnapshotTesting/Snapshotting/UIView%2BSnapshotting.swift

### Record mode

```swift
// Env-var driven (preferred — never hardcode record=true in committed tests):
// Set RECORD_SNAPSHOTS=1 in the test scheme's environment variables,
// or pass on the xcodebuild command line:
//   xcodebuild test ... OTHER_SWIFT_FLAGS="-D RECORD_SNAPSHOTS"
// Or via isRecording global (deprecated in 1.19.0 but present in 1.17.6):
isRecording = true   // file-global in test file; revert before committing
```

### Baseline directory convention

Baselines stored alongside the test file:
`__Snapshots__/<TestClassName>/<testMethodName>.<name>.png`

The directory is auto-created relative to the test source file on first record.
Source: https://github.com/pointfreeco/swift-snapshot-testing/blob/1.17.6/Sources/SnapshotTesting/AssertSnapshot.swift#L20

### Size-class injection for SwiftUI snapshot testing

```swift
import SwiftUI
import SnapshotTesting
import UIKit

// Wrap the SwiftUI view in a UIHostingController, then inject traits:
let vc = UIHostingController(rootView: MyView().environment(\.adaptiveLayoutContext, .iPadLandscapeStandard))
vc.overrideUserInterfaceStyle = .light

let regularTraits = UITraitCollection(traitsFrom: [
    UITraitCollection(horizontalSizeClass: .regular),
    UITraitCollection(verticalSizeClass: .regular),
    UITraitCollection(userInterfaceIdiom: .pad),
    UITraitCollection(displayScale: 2.0)
])

assertSnapshot(of: vc, as: .image(on: .iPadPro12_9, traits: regularTraits))
// Or with explicit size:
assertSnapshot(of: vc, as: .image(traits: regularTraits), named: "iPadLandscape")
```

Source: https://github.com/pointfreeco/swift-snapshot-testing/blob/1.17.6/Sources/SnapshotTesting/Snapshotting/UIViewController%2BSnapshotting.swift

---

## xcodeproj Ruby gem

**Version**: 1.28.1 (installed via `gem install --user-install xcodeproj`)
**Verification**: `ruby -e "require 'xcodeproj'; puts Xcodeproj::VERSION"` → `1.28.1`

Source: https://github.com/CocoaPods/Xcodeproj

### Open a project

```ruby
require 'xcodeproj'
project = Xcodeproj::Project.open('MeridianMotorsCompanion.xcodeproj')
```

### new_target

```ruby
target = project.new_target(
  :unit_test_bundle,         # product type symbol
  'MeridianMotorsCompanionTests',        # name
  :ios,                       # platform
  '17.0',                    # deployment target
  nil,                       # product_group (nil = default Products group)
  :swift                     # language
)
```

Product type constants:
- `:unit_test_bundle` → `com.apple.product-type.bundle.unit-test`
- `:ui_test_bundle` → `com.apple.product-type.bundle.ui-testing`

Source: https://www.rubydoc.info/gems/xcodeproj/Xcodeproj/Project#new_target-instance_method

### Idempotency check

```ruby
existing = project.targets.find { |t| t.name == 'MeridianMotorsCompanionTests' }
if existing
  puts "MeridianMotorsCompanionTests target already exists — no changes."
  exit 0
end
```

### Add a group

```ruby
group = project.main_group.new_group('MeridianMotorsCompanionTests', 'MeridianMotorsCompanionTests')
```

### Add a file reference and build phase membership

```ruby
file_ref = group.new_file('MeridianMotorsCompanionTests/SmokeTests.swift')
target.source_build_phase.add_file_reference(file_ref)
```

### Add package dependency (SPM)

```ruby
# Add the remote package to the project
pkg = project.root_object.package_references.find { |p|
  p.repositoryURL == 'https://github.com/pointfreeco/swift-snapshot-testing'
} || begin
  p = project.new(Xcodeproj::Project::Object::XCRemoteSwiftPackageReference)
  p.repositoryURL = 'https://github.com/pointfreeco/swift-snapshot-testing'
  p.requirement = { 'kind' => 'exactVersion', 'version' => '1.17.6' }
  project.root_object.package_references << p
  p
end

# Add product dependency to the test target
dep = project.new(Xcodeproj::Project::Object::XCSwiftPackageProductDependency)
dep.package = pkg
dep.product_name = 'SnapshotTesting'
target.package_product_dependencies << dep
```

Source: https://www.rubydoc.info/gems/xcodeproj/Xcodeproj/Project/Object/XCRemoteSwiftPackageReference

### Update scheme Testables

```ruby
scheme_path = Xcodeproj::XCScheme.shared_data_dir('MeridianMotorsCompanion.xcodeproj') + 'MeridianMotorsCompanion.xcscheme'
scheme = Xcodeproj::XCScheme.new(scheme_path)

already_added = scheme.test_action.testables.any? { |t|
  t.buildable_references.any? { |r| r.blueprint_name == 'MeridianMotorsCompanionTests' }
}
unless already_added
  testable = Xcodeproj::XCScheme::TestAction::TestableReference.new(target)
  scheme.test_action.add_testable(testable)
  scheme.save!
end
```

Source: https://www.rubydoc.info/gems/xcodeproj/Xcodeproj/XCScheme/TestAction

### Save project

```ruby
project.save
```

---

## UIAccessibility.isGuidedAccessEnabled

**iOS minimum**: iOS 7+
**API**: `UIAccessibility.isGuidedAccessEnabled` (Bool, read-only)
**Notification**: `UIAccessibility.guidedAccessStatusDidChangeNotification`

```swift
// Read current state:
let isGA = UIAccessibility.isGuidedAccessEnabled

// Observe changes:
NotificationCenter.default.addObserver(
    self,
    selector: #selector(guidedAccessChanged),
    name: UIAccessibility.guidedAccessStatusDidChangeNotification,
    object: nil
)
```

Source: https://developer.apple.com/documentation/uikit/uiaccessibility/1615173-isguidedaccessenabled

---

## UIScreen.didConnectNotification / didDisconnectNotification

**API** (iOS 13+, deprecated in iOS 16 in favour of UIScene):
```swift
NotificationCenter.default.addObserver(
    self,
    selector: #selector(screenDidConnect(_:)),
    name: UIScreen.didConnectNotification,
    object: nil
)
NotificationCenter.default.addObserver(
    self,
    selector: #selector(screenDidDisconnect(_:)),
    name: UIScreen.didDisconnectNotification,
    object: nil
)

@objc func screenDidConnect(_ notification: Notification) {
    guard let screen = notification.object as? UIScreen else { return }
    // attach UIWindow to this screen
}
```

**Note**: `UIScreen.didConnectNotification` still fires on iOS 17+; the
deprecation note targets apps migrating to UISceneSession. For reliability
across iOS 17 (project min target) we use the UIKit imperative path.

Source: https://developer.apple.com/documentation/uikit/uiscreen/didconnectnotification

---

## UIWindow + UIHostingController for external display

```swift
// Attach a SwiftUI view to an external UIScreen (iOS 17 imperative path):
let window = UIWindow(frame: screen.bounds)
window.screen = screen
let hosting = UIHostingController(rootView: WallMirrorScene())
window.rootViewController = hosting
window.makeKeyAndVisible()
// Note: do NOT call makeKeyAndVisible() on the external window if you
// want the main window to remain the key window. Use:
window.isHidden = false
```

Source: https://developer.apple.com/documentation/uikit/uiwindow

---

## NavigationSplitView + horizontalSizeClass

**iOS**: 16+
**SwiftUI environment value**: `@Environment(\.horizontalSizeClass) var hSizeClass: UserInterfaceSizeClass?`

```swift
// Reading size classes:
@Environment(\.horizontalSizeClass) var horizontalSizeClass
@Environment(\.verticalSizeClass) var verticalSizeClass

// NavigationSplitView (two-column):
NavigationSplitView {
    sidebar
} detail: {
    content
}
// On compact width, NavigationSplitView collapses to a stack.
// On regular width, it shows sidebar + detail side by side.
```

**Caution**: `NavigationSplitView` suits browse-and-detail, not wizard flows.
For wizard flows (ConfiguratorFlow), use explicit HStack/VStack branching
on `AdaptiveLayoutContext`.

Source: https://developer.apple.com/documentation/swiftui/navigationsplitview

---

## UITraitCollection.current

```swift
// Reading current trait collection (UIKit):
let traits = UITraitCollection.current
let hSizeClass = traits.horizontalSizeClass  // .compact | .regular | .unspecified

// Overriding trait collection for a view controller:
// (used in XCTest to inject specific layouts for snapshot tests)
let testTraits = UITraitCollection(traitsFrom: [
    UITraitCollection(horizontalSizeClass: .regular),
    UITraitCollection(verticalSizeClass: .regular),
    UITraitCollection(userInterfaceIdiom: .pad)
])
vc.setOverrideTraitCollection(testTraits, forChild: childVC)
// Or for UIHostingController: pass traits to assertSnapshot directly
```

Source: https://developer.apple.com/documentation/uikit/uitraitcollection/current

---

## UISceneConfiguration + UIWindowScene (external display, imperative iOS 17 path)

```swift
// For external screens without UIScene multi-window setup:
// Use the simpler UIScreen-based window approach above.
// UIWindowScene approach (iOS 13+ declarative, requires Info.plist scene manifest):
//   - Requires UIApplicationSceneManifest in Info.plist
//   - MeridianMotorsCompanionApp.swift uses @main SwiftUI App which configures a single
//     WindowGroup; UIWindowScene is the backing scene.
// This app does NOT use multiple UISceneConfiguration entries.
// The external screen is handled by creating a UIWindow on the external UIScreen.
```

Source: https://developer.apple.com/documentation/uikit/uisceneconfiguration

---

## External display detection (non-deprecated, iOS 16+)

Used in `AdaptiveLayoutContext.swift` `DetectAdaptiveLayoutModifier.externalScreenIsConnected()`.
Replaces the iOS 16-deprecated `UIScreen.screens` API.

```swift
// Returns true when a non-main external screen is connected.
// UIApplication.shared.connectedScenes enumerates all UIWindowScenes.
// Any scene whose .screen differs from UIScreen.main is an external display.
@MainActor
private func externalScreenIsConnected() -> Bool {
    let mainScreen = UIScreen.main
    return UIApplication.shared.connectedScenes
        .compactMap { $0 as? UIWindowScene }
        .contains { $0.screen != mainScreen }
}
```

Notes:
- `UIApplication.shared` is `@MainActor`-isolated; annotate the caller or call from the main thread.
- `UIWindowScene.screen` is available iOS 13+; no availability guard needed at the 18.0 deployment target.
- For *observing* connect/disconnect events use `UIScene.willConnectNotification` / `UIScene.didDisconnectNotification` (non-deprecated equivalents of `UIScreen.didConnectNotification`).

Source: https://developer.apple.com/documentation/uikit/uiapplication/connectedscenes
Source: https://developer.apple.com/documentation/uikit/uiwindowscene/screen

---

## Guided Access — API surface (iOS 7+)

`UIAccessibility.isGuidedAccessEnabled` is a read-only `Bool` property on the
`UIAccessibility` type. `guidedAccessStatusDidChangeNotification` is posted on
the main thread when the state changes (e.g. operator engages or disengages
Guided Access via the triple-click gesture).

```swift
import UIKit

// Read current state synchronously (no async required):
let enabled: Bool = UIAccessibility.isGuidedAccessEnabled

// Observe changes (in KioskGuidedAccessMonitor):
NotificationCenter.default.addObserver(
    forName: UIAccessibility.guidedAccessStatusDidChangeNotification,
    object: nil,
    queue: .main
) { _ in
    let isNowEnabled = UIAccessibility.isGuidedAccessEnabled
    // update UI accordingly
}
```

**Note**: Guided Access *engagement* is an operator action (Settings → Accessibility →
Guided Access, then triple-click the side/home button at runtime). Autonomous Single App
Mode — which would let the app engage Guided Access programmatically — requires MDM
supervision and is out of scope for this build.

Source: https://developer.apple.com/documentation/uikit/uiaccessibility/1615173-isguidedaccessenabled
Source: https://developer.apple.com/documentation/uikit/uiaccessibility/1648223-guidedaccessstatusdidchangenotif

---

## External display detection — current status (stubbed)

`AdaptiveLayoutContext.externalDisplay16by9` is retained in the enum and
`externalScreenIsConnected()` is present in `DetectAdaptiveLayoutModifier`,
but it stubs to `false`. Group 6 (WallMirrorScene / ExternalDisplayCoordinator)
was dropped in the 2026-08-11 spec revision because the wall is an OS-level
mirror of the phone on a portrait panel — no first-party scene is needed.

**Why the stub exists rather than deleting the case**: the case is retained so
that reinstating Group 6 against a real device is a data-source change (implement
`externalScreenIsConnected()` against `UIApplication.shared.connectedScenes`)
rather than a design change. See `decisions.md` § 2026-08-11 and `spec.md` § D5
(marked SUPERSEDED).

**To reinstate** (when a real device is available):
```swift
// Replace the stub in DetectAdaptiveLayoutModifier:
@MainActor
private func externalScreenIsConnected() -> Bool {
    let mainScreen = UIScreen.main
    return UIApplication.shared.connectedScenes
        .compactMap { $0 as? UIWindowScene }
        .contains { $0.screen != mainScreen }
}
// Then wire UIScene.willConnectNotification / didDisconnectNotification
// to trigger a re-detection in .detectAdaptiveLayout().
```

Source: https://developer.apple.com/documentation/uikit/uiapplication/connectedscenes
Source: spec `decisions.md` § 2026-08-11 — "Wall is a mirror, not a redrawn scene"

---

## AvailabilityContract — client-side shape (task 5.4)

`AvailabilityContract.swift` in `Models/Acquire/` defines the client-side
contract for option gating by delivery window. The backend availability/lead-time
service does not yet exist on the CVX side; the client reads a clearly-labelled
local fixture (`AvailabilityContract.demo`) behind the same protocol so that
swapping in the real service is a data-source change, not a UI change.

```swift
/// Represents the availability and delivery-window constraint for a
/// single configuration option (color, interior style, IVE package).
struct OptionAvailability {
    let optionId: String
    let isAvailable: Bool
    let unavailableReason: String?    // shown in UI when isAvailable == false
    let deliveryWindow: DeliveryWindow
}

enum DeliveryWindow {
    case standard   // within the quoted lead time
    case custom     // longer; may enable additional options
}

/// Degrades to all-available on a bad or missing response.
/// Failing closed would grey out the whole configurator on the show floor.
extension AvailabilityContract {
    static let allAvailable = AvailabilityContract(options: [], defaultAvailable: true)
}
```

**Degradation contract**: on a bad or missing response, the client returns
`AvailabilityContract.allAvailable` — all options shown as selectable. Failing
closed (all-disabled) is a worse show-floor failure than showing an option that
is slow to build. This is test-enforced: one of the ≥5 tests in
`AvailabilityContractTests` asserts that an empty/malformed response degrades to
all-available rather than all-disabled.

**Backend counterpart** (CVX-owned, not yet built): See
`REINVENT-2026-ZONE2-WHAT-WE-DELIVER-REV3.md` § Cross-repo dependencies item 2.
The contract shape and HTTP endpoint the client expects are documented in the iOS
spec (`tasks.md` task 5.4). Swapping in the real service is a data-source change.

Source: `clients/ios/MeridianMotorsCompanion/Models/Acquire/AvailabilityContract.swift`
Source: `tasks.md` task 5.4 — option gating by delivery window

---

## Launch screen keys — UILaunchScreen vs UILaunchStoryboardName

**`UILaunchStoryboardName` overrides `UILaunchScreen`.** If both are present the
system ignores `UILaunchScreen` entirely. This is the opposite of what the
"UILaunchScreen replaces the older key" framing suggests, so it is worth stating
plainly: adding a launch storyboard means *removing* `UILaunchScreen`, not
layering the storyboard on top of it.

This project previously shipped `<key>UILaunchScreen</key><dict/>` (an empty
dict — system background, no image). Adding `LaunchScreen.storyboard` required
deleting that key; leaving it would have worked but implied a fallback that does
nothing.

Note `GENERATE_INFOPLIST_FILE = NO` here, so `MeridianMotorsCompanion/Info.plist` is
authoritative — there is no `INFOPLIST_KEY_UILaunch*` build setting to reconcile.

Source: <https://useyourloaf.com/blog/dropping-launch-storyboards/> ("remember to
delete the `UILaunchStoryboardName` key … or the system will ignore the
`UILaunchScreen` setting"). Verified 2026-08-20 against the built bundle:
`plutil -extract UILaunchStoryboardName raw -o - MeridianMotorsCompanion.app/Info.plist`
returns `LaunchScreen`, and `LaunchScreen.storyboardc` is present.

Launch storyboards resolve asset-catalog images by name, so `MeridianLogo` in
`Assets.xcassets` serves both the storyboard and the SwiftUI fallback path.
Verified with `xcrun assetutil --info MeridianMotorsCompanion.app/Assets.car`.

---

## AVPlayerLayer in a UIViewRepresentable (play-once splash)

Used by `Views/Splash/SplashVideoPlayer.swift`. SwiftUI's `VideoPlayer` is not
usable for a splash: it surfaces playback controls and does not expose
`videoGravity`.

```swift
final class PlayerHostView: UIView {
    let playerLayer = AVPlayerLayer()
    override init(frame: CGRect) {
        super.init(frame: frame)
        playerLayer.videoGravity = .resizeAspect
        layer.addSublayer(playerLayer)
    }
    override func layoutSubviews() {          // REQUIRED
        super.layoutSubviews()
        playerLayer.frame = bounds
    }
}
```

`layoutSubviews` is load-bearing: an `AVPlayerLayer` added as a sublayer keeps
its initial `.zero` bounds and renders nothing without it. Nothing errors.

### Completion + failure signals

Three distinct signals, and they are not interchangeable:

| Signal | API | Fires when |
|---|---|---|
| Reached end | `Notification.Name.AVPlayerItemDidPlayToEndTime` | normal completion |
| Failed mid-playback | `.AVPlayerItemFailedToPlayToEndTime` | stalled/aborted after start |
| Failed before playback | KVO on `AVPlayerItem.status == .failed` | undecodable item |

Scope the notification observers with `object: item` — unscoped, they fire for
*any* player item in the process. The KVO path is the only signal for an item
that fails before playback begins; no notification is posted for that case.

None of the three is guaranteed, so a play-once splash also needs a timeout, and
all four routes must collapse to one callback (`SplashSettleGuard`) or the
completion handler runs more than once.

### Concurrency

`NotificationCenter.addObserver(forName:object:queue:using:)` takes a `@Sendable`
block, so capturing a non-Sendable coordinator warns under Swift 5 language mode:

```
warning: capture of 'self' with non-Sendable type 'Coordinator?' in a '@Sendable' closure
```

Fix: annotate the coordinator `@MainActor`. A global-actor-isolated class is
**implicitly Sendable**, which clears the capture warning; the block body then
uses `MainActor.assumeIsolated { … }`, sound because `queue: .main` guarantees
the main thread. Verified 0 errors / 0 warnings via
`swiftc -typecheck -swift-version 5 -sdk $(xcrun --show-sdk-path --sdk iphonesimulator) -target arm64-apple-ios18.0-simulator`.

Consequence: a `@MainActor` class cannot call an isolated `teardown()` from
`deinit` (deinit is nonisolated). Teardown therefore lives in
`static func dismantleUIView(_:coordinator:)`, which SwiftUI calls when the
representable leaves the hierarchy.

### Reduce Motion

Use `@Environment(\.accessibilityReduceMotion)` rather than
`UIAccessibility.isReduceMotionEnabled` — it is reactive, and it matches
`VehicleSweepView` / `SupplyChainPlanningView`. It is a **read-only** key path:
`.environment(\.accessibilityReduceMotion, true)` does not compile
(`cannot convert value of type 'KeyPath<…>' to expected argument type 'WritableKeyPath<…>'`),
so it cannot be injected in a `#Preview`. Test the decision logic through an
injectable resolver instead — see `resolveSplashPlan(reduceMotion:locateVideo:)`.

---

## App icon: not addressable via UIImage(named:)

`UIImage(named: "AppIcon")` returns **nil**, so it cannot be used to assert the
icon shipped. The app icon is not an ordinary catalog image: the build rasterises
copies to the bundle root and records the catalog name in `Info.plist`.

Verified 2026-08-20 against the built bundle:

```
MeridianMotorsCompanion.app/AppIcon60x60@2x.png
MeridianMotorsCompanion.app/AppIcon76x76@2x~ipad.png
CFBundleIcons → CFBundlePrimaryIcon → CFBundleIconName = "AppIcon"
Assets.car (xcrun assetutil --info) → Name "AppIcon", 1024×1024
```

So the testable assertions are `CFBundleIcons.CFBundlePrimaryIcon.CFBundleIconName`
plus `Bundle.main.path(forResource: "AppIcon60x60@2x", ofType: "png")`. The catalog
name comes from `ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon`.

### CFBundleDisplayName vs CFBundleName vs PRODUCT_NAME

Three different things, easy to conflate:

| Key | Controls |
|---|---|
| `PRODUCT_NAME` (build setting) | the `.app` bundle **filename** |
| `CFBundleName` | fallback label, 16-char limit |
| `CFBundleDisplayName` | the **home screen / Settings / Spotlight** label |

Setting `CFBundleDisplayName` does NOT rename the built product, so scripts that
glob `MeridianMotorsCompanion*.app` keep working. Home-screen labels truncate at roughly 12
characters.

---

## Compositing white-on-black art onto a coloured background

Cropping a region out of white-on-black artwork and drawing it with
`CGContext.draw(_:in:)` brings the crop's **black canvas** with it, which appears
as a black rectangle over any non-black background. Observed while generating the
stacked app icon.

Use luminance as a paint mask instead — for white-on-black art, luminance *is*
coverage, and antialiased edges are preserved:

```swift
// Build a DeviceGray copy…
let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                    bytesPerRow: w, space: CGColorSpaceCreateDeviceGray(),
                    bitmapInfo: CGImageAlphaInfo.none.rawValue)!
ctx.draw(crop, in: CGRect(x: 0, y: 0, width: w, height: h))
let mask = ctx.makeImage()!

// …then paint through it.
target.saveGState()
target.clip(to: destRect, mask: mask)          // bright areas of mask = painted
target.setFillColor(.init(red: 1, green: 1, blue: 1, alpha: 1))
target.fill(destRect)
target.restoreGState()
```

Both `NSBitmapImageRep.colorAt(x:y:)` and `CGImage.cropping(to:)` use a
**top-left** origin, so pixel detection and cropping share a coordinate space and
need no flip. `CGContext` *drawing* is bottom-left, but `draw(in:)` un-flips the
source, so mask construction and mask application flip identically and cancel.

Gotchas hit while writing the generator script:
- `NSImage.lockFocus()` / `draw(in:)` crash off the main thread — use `CGContext`.
- `String(format: "%s", swiftString)` crashes; `%@` or interpolation is required.
- `AVAssetImageGenerator.image(at:)` is `async` in current SDKs — needs `await`.

---

## Acquire flow — verified API shapes (spec `2026-08-21-cvx-configurator-full-picker-flow`)

> **Pre-delta snapshot — 2026-08-21.** Every entry below documents the source tree
> as it stands BEFORE the delta in spec `2026-08-21-cvx-configurator-full-picker-flow`
> is applied. Later tasks in that spec are written against these shapes. Do not
> update this section mid-spec; a post-delta update belongs in a separate entry
> once Groups 2–7 are merged.

---

### `CatalogAccessory` — current shape

Source: `Api/AcquireCatalogClient.swift:151`

```swift
struct CatalogAccessory: Codable, Identifiable, Equatable {
    let accessoryId: String
    let displayName: String
    let description: String?
    let price: Double?
    let categoryId: String?         // category-scoped or nil for universal
    let imageKey: String?
    let sortOrder: Int?

    var id: String { accessoryId }
}
```

Seven fields total; **no `leadDays` field exists today**. The spec's Group 2
(Task 2.1) adds `let leadDays: Int?` after `sortOrder`. Any task asserting on
`CatalogAccessory` before Group 2 ships must not reference `leadDays`.

`CatalogAccessory` is embedded in `CatalogResponse` (`AcquireCatalogClient.swift:163`)
as `let accessories: [CatalogAccessory]`. The wire shape is `GET /acquire/catalog/{tenantId}`.

`ReservationRequest` (`AcquireCatalogClient.swift:222`) carries
`let selectedAccessoryIds: [String]` (non-optional, can be empty) as the
order-submission counterpart.

---

### `SupplyChainPlan.plan(...)` — current signature

Source: `Models/Acquire/SupplyChainPlan.swift:195`

```swift
static func plan(
    modelId: String,
    variantId: String,
    colorId: String,
    interiorStyle: InteriorStyle,
    deliveryWindow: AvailabilityContract.DeliveryWindow,
    now: Date = Date()
) -> SupplyChainPlan
```

**Five named parameters** (plus the injectable clock `now`). The spec's Group 2
(Task 2.2) adds two more, both defaulted:
```swift
selectedAccessoryIds: [String] = [],
accessoryLeadDays: [String: Int] = [:]
```
All pre-delta call sites (including `LightConfigurationTests`) compile unchanged
because the new parameters are defaulted.

#### Seed inputs (current)

Source: `Models/Acquire/SupplyChainPlan.swift:204`

```swift
let seed = deterministicSeed(
    for: [modelId, variantId, colorId, interiorStyle.rawValue, deliveryWindow.rawValue]
)
```

Five components joined with `|` then run through FNV-1a. Post-delta a sixth
component (`selectedAccessoryIds.sorted().joined(separator: ",")`) is appended.

#### Manufacturing-days derivation (current)

Source: `Models/Acquire/SupplyChainPlan.swift:215–224`

```swift
var mfgDays: Int
switch deliveryWindow {
case .standard: mfgDays = 28 + Int((seed / 17) % 15)   // 28–42
case .custom:   mfgDays = 56 + Int((seed / 19) % 29)   // 56–84
}
if matchedStock { mfgDays = max(7, mfgDays / 3) }
mfgDays += longLead * 7
```

No accessory contribution today. Post-delta an `accessoryLeadTotal` term is added
**after** `mfgDays += longLead * 7` — outside the `matchedStock` branch
deliberately, so accessory install time adds real days even on a matched-stock
build.

#### Attribution note (test-asserted verbatim)

Source: `Models/Acquire/SupplyChainPlan.swift:168`

```swift
static let attributionNote =
    "Planning pattern powered by Amazon Connect Decisions. "
    + "Dates and facility selection are demo data for this experience."
```

This string is test-asserted and must not change. The spec's § Constraints
states: "`SupplyChainPlan.attributionNote` remains test-asserted verbatim".

---

### `enum Step` — current cases

Source: `Views/Acquire/ConfiguratorFlow.swift:71–88`

Ten cases (post Zone-2 spec 2026-08-04; `.pickAccessories`, `.review`, and
`.pickFinance` were removed in that spec):

```swift
enum Step {
    case pickCategory                                                      // :72
    case pickModel(CatalogCategory)                                       // :73
    case pickVariant(CatalogCategory, CatalogModel)                       // :74
    case showEditionSummary(IvePackage, CatalogModel, CatalogVariant)     // :76
    case pickColor(IvePackage, CatalogModel, CatalogVariant)              // :77
    case pickInteriorStyle(IvePackage, CatalogModel, CatalogVariant,      // :78
                           CatalogColor)
    case supplyChainPlanning(IvePackage, CatalogModel, CatalogVariant,    // :80–81
                             CatalogColor, InteriorStyle)
    case deliveryProposal(IvePackage, CatalogModel, CatalogVariant,       // :83–84
                          CatalogColor, InteriorStyle, SupplyChainPlan)
    case confirm(IvePackage, CatalogModel, CatalogVariant,                // :85
                 CatalogColor, InteriorStyle)
    case success(ReservationResponse)                                      // :86
}
```

`LightConfigurationTests.swift:156` (pre-delta) asserts the machine has **no**
`pickAccessories`, `review`, or `finance` cases. The spec's Group 4 (Task 4.1)
reinserts `.pickAccessories` and updates that test in Group 6.

`Step.isAutoAdvancing` returns `true` only for `.supplyChainPlanning`
(source: `ConfiguratorFlow.swift:155–157`). Unchanged by this spec.

`navTitle` cases (source: `ConfiguratorFlow.swift:559–568`) cover all ten current
cases; the spec's Group 4 (Task 4.1) adds `case .pickAccessories: return "Add accessories"`.

#### `ForwardSelection` (test seam)

Source: `Views/Acquire/ConfiguratorFlow.swift:97–118`

```swift
struct ForwardSelection {
    let color: CatalogColor
    let interiorStyle: InteriorStyle
    let reservationResponse: ReservationResponse
    var supplyChainPlan: SupplyChainPlan = SupplyChainPlan.plan(
        modelId: "unknown", variantId: "unknown", colorId: "unknown",
        interiorStyle: .classic, deliveryWindow: .standard
    )
}
```

The spec's Group 4 (Task 4.1) adds `var selectedAccessoryIds: Set<String> = []`
to this struct so pre-delta tests keep compiling.

---

### `AvailabilityContract.loadFixture()` — precedent for `CatalogFallback`

Source: `Models/Acquire/AvailabilityContract.swift:106`

```swift
static func loadFixture() -> AvailabilityContract? {
    // [FIXTURE — NOT REAL AVAILABILITY DATA]
    // Shows one unavailable color (standard window) that opens on custom,
    // and one unavailable interior style (unavailable on both windows) to
    // exercise the full disabled-with-reason path.
    return AvailabilityContract(
        deliveryWindow: .standard,
        colorOptions: [
            OptionAvailability(
                optionId: "racing-red",
                available: false,
                unavailableReason: "Not in current production run",
                availableOnCustom: true
            )
        ],
        interiorStyleOptions: [
            OptionAvailability(
                optionId: InteriorStyle.touring.rawValue,
                available: false,
                unavailableReason: "Leather supplier lead time: 20 weeks",
                availableOnCustom: false
            )
        ]
    )
}
```

#### Why this shape is the precedent for `CatalogFallback`

`CatalogFallback.loadFixture()` (new in spec Group 3, Task 3.1) follows this
exact pattern for four specific reasons:

1. **Labelled fixture comment inside the function body.** The inline
   `// [FIXTURE — NOT REAL AVAILABILITY DATA]` comment (line 107) marks the
   content as a non-live fixture without touching the function's doc-comment,
   so the doc-comment can be rendered by Xcode documentation without embedding
   the label. `CatalogFallback` copies this: `// [FIXTURE — NOT REAL CATALOG DATA]`
   inside the function, with the label also replicated in the type-level doc-comment
   as `/// [FIXTURE — NOT REAL CATALOG DATA]` per the spec's Group 3 requirement
   (Task 3.1 Accept: `grep -cE '\[FIXTURE .- NOT REAL CATALOG DATA\]' ...` ≥ 1).

2. **`-> OptionalType?` return that callers degrade on nil.** `loadFixture()`
   returns `AvailabilityContract?` and its caller (`ConfiguratorFlow.loadAvailability`)
   does `availability = AvailabilityContract.loadFixture() ?? .allAvailable`. The
   optional return means a malformed fixture body degrades gracefully rather than
   crashing. `CatalogFallback.loadFixture() -> CatalogResponse` is non-optional
   because `CatalogResponse` has no all-available sentinel — the fallback IS the
   sentinel — but the call sites must still guard against a future nil return.

3. **No external dependencies, no async.** The fixture is a synchronous
   value-constructor; it calls nothing, reads nothing, and is safe to call from
   any context. `CatalogFallback` follows suit: no `async`, no `throws`, no
   network, no disk I/O.

4. **Same-directory placement and same-contract shape.** Both fixtures live in
   `Models/Acquire/`, return the same DTO type their live counterpart uses, and
   contain no content from outside the fictional Meridian lineup. This keeps the
   rule for how to add a new fixture discoverable: one file per fixture type, in
   `Models/Acquire/`, labelled, same DTO as the network path.

#### `loadCatalog()` — current failure modes (pre-delta)

Source: `Views/Acquire/ConfiguratorFlow.swift:574`

```swift
private func loadCatalog() async {
    guard case .signedIn(let token, _) = session.authState else { return }  // :575
    // ... network call ...
    } catch let err as AcquireError where err.isEndpointUnavailable {
        catalogError = err          // :588 — sets error state; UI stays at empty catalog
        NSLog("ConfiguratorFlow: catalog endpoint unavailable. %@", err.localizedDescription)
    }
}
```

Two pre-delta failure modes the spec's Group 3 (Task 3.2) patches to use the
fallback instead:

- **Not signed in** (line 575): early `return` leaves `categories`, `models`,
  `variants`, `colors` all `[]`. The picker renders the "Catalog loading…"
  placeholder indefinitely.
- **Endpoint unavailable** (line 588): sets `catalogError` but does not populate
  catalog state. Same placeholder result.

Post-delta both branches call `CatalogFallback.loadFixture()` and populate
catalog state, so the picker always renders.

#### Warm-entry `.task` block (pre-delta)

Source: `Views/Acquire/ConfiguratorFlow.swift:250–270`

```swift
.task {
    // Warm entry: skip catalog load and open at edition summary.
    if let oh = offerHandoff {
        let edition = oh.edition ?? IvePackage.defaultFor(modelId: oh.modelName)
        let placeholderModel = CatalogModel(
            modelId: "offer-model",      // :255 — placeholder; pickers skipped
            ...
        )
        let placeholderVariant = CatalogVariant(
            variantId: "offer-variant",  // :261
            modelId: "offer-model",
            ...
        )
        step = .showEditionSummary(edition, placeholderModel, placeholderVariant)
    }
    await loadCatalog()
    await loadAvailability()
}
```

The `"offer-model"` and `"offer-variant"` hardcoded placeholders (lines 255, 261)
exist because warm entry skips the pickers entirely. The spec's Group 5 (Task 5.2)
deletes them — warm entry will load the real catalog, resolve the offer's
`modelName` against it, and open at `.pickCategory` with preselect fields
populated. The verify command `grep -c '"offer-model"' ... returns 0` confirms
the deletion.


---

## Acquire flow — post-delta state (spec `2026-08-21-cvx-configurator-full-picker-flow`)

> **Post-delta snapshot — 2026-08-21 Groups 2–7 merged.** This section documents the source tree
> AFTER the delta is applied. Updates here replace the pre-delta entries above; this is the current
> reference state.

### `enum Step` — post-delta (11 cases, accessories restored)

Source: `Views/Acquire/ConfiguratorFlow.swift:71–88` (updated 2026-08-21)

The `.pickAccessories` step is restored between `.pickInteriorStyle` and `.supplyChainPlanning`.
The `.supplyChainPlanning` and `.confirm` cases gain a trailing `Set<String>` associated value
for `selectedAccessoryIds`.

```swift
enum Step {
    case pickCategory
    case pickModel(CatalogCategory)
    case pickVariant(CatalogCategory, CatalogModel)
    case showEditionSummary(IvePackage, CatalogModel, CatalogVariant)
    case pickColor(IvePackage, CatalogModel, CatalogVariant)
    case pickInteriorStyle(IvePackage, CatalogModel, CatalogVariant, CatalogColor)
    case pickAccessories(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, [CatalogAccessory])
    case supplyChainPlanning(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, Set<String>)
    case deliveryProposal(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, SupplyChainPlan)
    case confirm(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, Set<String>)
    case success(ReservationResponse)
}
```

**Eleven cases total.** The step sequence is:
```
pickCategory → pickModel → pickVariant → showEditionSummary → pickColor
→ pickInteriorStyle → pickAccessories → supplyChainPlanning (auto-advance)
→ deliveryProposal → confirm → success
```

### `CatalogFallback` — client-side catalog degradation (spec Group 3)

Source: `Models/Acquire/CatalogFallback.swift` (new in this spec)

Graceful degradation pattern mirroring `AvailabilityContract.loadFixture()`.
Engaged on three failure modes:

1. **Unsigned** (`guard case .signedIn(...)` fails in `loadCatalog()`)
2. **Endpoint unavailable** (AcquireError.isEndpointUnavailable)
3. **No fetch attempted** (cold entry, no network call made)

```swift
static func loadFixture() -> CatalogResponse {
    // [FIXTURE — NOT REAL CATALOG DATA]
    // Returns a well-formed catalog with 3 categories (including family SUV),
    // 3 models, 3 variants, 3 colors, and 3 accessories.
    // Used when endpoint is unavailable or user is unsigned.
    return CatalogResponse(
        tenantId: "fallback-meridian",
        catalogVersion: "fallback-2026-08-21",
        categories: [...],
        models: [...],
        variants: [...],
        colors: [...],
        accessories: [
            CatalogAccessory(accessoryId: "family-cargo-rack", displayName: "Cargo Rack",
                           price: 799, leadDays: 3, categoryId: nil),
            CatalogAccessory(accessoryId: "family-third-row-seat", displayName: "Third Row Seat",
                           price: 1899, leadDays: 14, categoryId: nil),
            CatalogAccessory(accessoryId: "family-tow-package", displayName: "Tow Package",
                           price: 1299, leadDays: 7, categoryId: nil)
        ]
    )
}
```

**Call site logic** (per Task 3.2):

```swift
private func loadCatalog() async {
    // Fallback on unsigned
    guard case .signedIn(let token, _) = session.authState else {
        let fallback = CatalogFallback.loadFixture()
        categories = fallback.categories
        models = fallback.models
        variants = fallback.variants
        colors = fallback.colors
        accessories = fallback.accessories
        NSLog("ConfiguratorFlow: catalog fallback engaged (unsigned)")
        return
    }
    
    // Fallback on endpoint unavailable
    do {
        let response = try await client.getCatalog(...)
        categories = response.categories
        // ... populate other arrays ...
    } catch let err as AcquireError where err.isEndpointUnavailable {
        let fallback = CatalogFallback.loadFixture()
        categories = fallback.categories
        // ... populate other arrays from fallback ...
        catalogError = err  // observability: still record the error state
        NSLog("ConfiguratorFlow: catalog fallback engaged (endpoint-unavailable)")
    }
}
```

The fallback ensures the picker always renders, even on the show floor with no network.

---

## Acquire flow — upgrade-flow continuity delta (spec `2026-08-21-cvx-upgrade-flow-continuity`)

> **Current state — 2026-08-21.** This section documents the FINAL state of the source tree after
> spec `2026-08-21-cvx-upgrade-flow-continuity` is merged. The step machine, chrome modes,
> handover integration, and supply-chain final leg documented here are the current baseline.

### `enum Step` — final 12 cases with handover and Door A/B

Source: `Views/Acquire/ConfiguratorFlow.swift:105–130`

The step machine now has **12 cases** including the new `.pickHandover` step between accessories and
supply-chain planning. The `.supplyChainPlanning` and `.confirm` cases each carry a trailing
`HandoverMethod` in addition to `Set<String>` for `selectedAccessoryIds`.

```swift
enum Step {
    case pickCategory
    case pickModel(CatalogCategory)
    case pickVariant(CatalogCategory, CatalogModel)
    case showEditionSummary(IvePackage, CatalogModel, CatalogVariant)
    case pickColor(IvePackage, CatalogModel, CatalogVariant)
    case pickInteriorStyle(IvePackage, CatalogModel, CatalogVariant, CatalogColor)
    case pickAccessories(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, [CatalogAccessory])
    case pickHandover(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, Set<String>)
    case supplyChainPlanning(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, Set<String>, HandoverMethod)
    case deliveryProposal(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, SupplyChainPlan)
    case confirm(IvePackage, CatalogModel, CatalogVariant, CatalogColor, InteriorStyle, Set<String>, HandoverMethod)
    case success(ReservationResponse)
}
```

**Step sequence**:
```
pickCategory → pickModel → pickVariant → showEditionSummary → pickColor
→ pickInteriorStyle → pickAccessories → pickHandover → supplyChainPlanning (auto-advance)
→ deliveryProposal → confirm → success
```

### Two entry doors into the step machine

| Door | Entry point | Pre-selected data | Opening step | Use case |
|---|---|---|---|---|
| **A (Cold)** | `handoff: nil` | nothing | `.pickCategory` | Buy tab, Home configure action, cold entry |
| **B (Warm)** | `offerHandoff: ConfiguratorOfferHandoff` | model (resolved from offer) | `.showEditionSummary` over real catalog | Upgrade flow with accepted offer |

Door B resolves the offer's `modelName` against the loaded catalog via `resolveEstablishedConfiguration()`.
If the model exists in the catalog but has no variants, or if the name matches nothing, Door B falls back
to `.pickCategory` exactly as Door A, making the fallback a first-class graceful path — not an error.

Door B adds a "Change vehicle" affordance to `EditionSummaryStep`, rendering as a tertiary button that
navigates back to `.pickCategory` so the pickers remain one tap away and never in the way.

### `ConfiguratorFlow.Chrome` — conditional navigation chrome

Source: `Views/Acquire/ConfiguratorFlow.swift:82–96`

```swift
enum Chrome {
    case standalone   // owns NavigationStack, title, close button
    case hosted       // content only; host supplies chrome
}

var chrome: Chrome = .standalone
```

**`.standalone` (default)**:
- Creates a `NavigationStack`, renders `navigationTitle(navTitle)`, and adds a close toolbar button.
- Used by all three existing call sites (`MainTabView`, `HomeTabView`, `BuyLandingView`) without any change.
- `onNavTitleChange` is not used; the configurator's internal title is displayed as-is.

**`.hosted`**:
- Renders only the step content, with no `NavigationStack`, no title, no toolbar.
- Host (e.g. `UpgradeFlow`) supplies the chrome — the navigation bar, title, and dismiss button.
- `onNavTitleChange` callback fires on every step transition so the host can update its title in sync
  with the inner step without owning the step machine.
- Example: `UpgradeFlow.stepContent` renders `ConfiguratorFlow(chrome: .hosted, …)` inside its own presentation.

### `HandoverMethod` — pickup vs. home delivery

Source: `Models/Acquire/SupplyChainPlan.swift` (plus the `.pickHandover` step)

```swift
enum HandoverMethod: Equatable {
    /// Collect at the driver's preferred dealer. The vehicle already ships there.
    case dealerPickup(centerId: String, name: String)
    /// Final-mile delivery to the driver's address.
    case homeDelivery
}
```

The choice drives three supply-chain legs:

| Leg | Driver | Result |
|---|---|---|
| `manufacturingDays` | model + variant + color + interior | same for both handover methods |
| `transitDays` | above + accessories | same for both — plant → dealer is incurred either way |
| `finalLegDays` | handover method | **0 for pickup**, 2–4 deterministic for delivery |

**Not included**: `centerId` does NOT enter the seed. Switching dealers must not move any of the first two legs
(manufacturing and transit), which is why the choice is made AFTER seeing the initial estimate on
`supplyChainPlanning`. See `decisions.md` § "The spec's seed rule for handover was wrong".

### Supply-chain planning — six stages after handover choice

Source: `SupplyChainPlanningView.swift` (step stage enum)

The planning view shows six stages:
1. Facility (factory selection)
2. Manufacturing / Build
3. Transit (factory → dealer)
4. Handover (dealer pickup or final-leg delivery)
5. Routing the final delivery leg (if delivery chosen)
6. —

Each stage has a `symbolName` (SF Symbol) and a `dwellSeconds` for animation timing.

### Delivery proposal — three-row breakdown

Source: `DeliveryProposalView.swift:87–90`

The proposal now shows four rows:

| Row | Shows |
|---|---|
| Manufacturing / Build | `manufacturingDays` |
| Transit | `transitDays` |
| Handover | dealer name (pickup) or "home delivery" label |
| Total | `totalDays` (sum of all three legs) |

The `totalDays` field now equals `manufacturingDays + transitDays + finalLegDays`, matching the proposal
date arithmetic. (This is tested: `testTotalDaysMatchesTheProposedDateOffset` asserts the day offset
from now to `proposedDeliveryDate` equals `totalDays` for both handover methods.)

### ReservationRequest — optional handover fields

Source: `Api/AcquireCatalogClient.swift:238–240`

```swift
struct ReservationRequest: Codable {
    // ... existing fields ...
    let handoverMethod: String?       // "dealerPickup" or "homeDelivery"
    let handoverCenterId: String?     // centerId when pickup method
}
```

Both fields are optional for backward compatibility. When submitting an order:
- Pickup: `handoverMethod = "dealerPickup"`, `handoverCenterId = <dealer centerId>`
- Delivery: `handoverMethod = "homeDelivery"`, `handoverCenterId = nil`

### Progress bar spanning both machines

When `ConfiguratorFlow` is hosted inside `UpgradeFlow`:

- `ConfiguratorFlow.Step` exposes `var progressStep: Int` (1-based) for every case, with `.supplyChainPlanning`
  and `.deliveryProposal` sharing the same step (the planning auto-advances, so the bar must not tick without
  the visitor acting). Similarly, `.confirm` and `.success` share a step.
- `ConfiguratorFlow` has `static let progressStepCount: Int` (derived, not hardcoded) and fires
  `onProgressChange: ((Int, Int) -> Void)?` with `(progressStep, progressStepCount)` on every step change.
- `UpgradeFlow` observes this callback and renders `3 + ConfiguratorFlow.progressStepCount` capsules, so the bar
  spans the upgrade's three pre-configure steps (offer, trade-in, financing) plus the configurator's steps.

This is a one-way callback — the host observes, it does not drive the step machine.