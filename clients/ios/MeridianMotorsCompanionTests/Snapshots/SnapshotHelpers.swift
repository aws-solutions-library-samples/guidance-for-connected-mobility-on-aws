import XCTest
import SwiftUI
import SnapshotTesting
@testable import MeridianMotorsCompanion

// MARK: - Snapshot recording control
//
// Record mode is OPT-IN per run, never globally enabled in source.
//
// To record baselines, set the RECORD_SNAPSHOTS environment variable before
// running the test suite:
//
//   xcodebuild test \
//     -scheme MeridianMotorsCompanion \
//     -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
//     -only-testing:MeridianMotorsCompanionTests/Snapshots \
//     OTHER_SWIFT_FLAGS="-D RECORD_SNAPSHOTS"
//
// Or in Xcode: Product > Scheme > Edit Scheme > Test > Arguments > Environment Variables
//   RECORD_SNAPSHOTS = 1
//
// NEVER commit a test file with record mode hard-coded to `true` — a suite
// left in record mode passes unconditionally and is worse than no suite.
//
// Baseline directory convention (swift-snapshot-testing 1.17.6):
//   __Snapshots__/<TestClassName>/<testMethodName>.<name>.png
//   — created alongside the test source file, i.e.:
//   MeridianMotorsCompanionTests/Snapshots/__Snapshots__/<TestClass>/<method>.<name>.png
//
// See: clients/ios/MeridianMotorsCompanion/docs/tech.md § "Snapshot testing — baseline convention"

// MARK: - Device size catalogue
//
// Maps `AdaptiveLayoutContext` cases to concrete device dimensions used by the
// snapshot harness.  Tests state intent (`.phonePortrait`) rather than raw
// numbers — if device pixels change, only this table needs updating.

/// Canonical screen sizes (logical points) for each `AdaptiveLayoutContext`.
///
/// iPhone 17 Pro is the single available simulator and the canonical show-floor
/// device; its dimensions drive the compact-width cases.
///
/// **iPad support was cancelled 2026-08-17 (user decision) — it is not deferred.**
/// The regular-width entries below survive for one reason only: `size(for:)` and
/// `traits(for:)` switch exhaustively over `AdaptiveLayoutContext`, which still
/// carries its iPad cases as production code. Deleting these would force a
/// `default:` clause and lose that exhaustiveness, which is a worse trade than
/// keeping three unused constants.
///
/// Do NOT author a snapshot case at regular width. There is no iPad in the
/// show-floor plan, no iPad simulator on the build host, and no human to approve
/// a regular-width baseline. See `decisions.md` § 2026-08-17.
public enum SnapshotDeviceSize {
    /// iPhone 17 Pro portrait  — 393 × 852 pt  (iOS 18 logical resolution)
    static let phonePortrait    = CGSize(width: 393, height: 852)
    /// iPhone 17 Pro landscape — 852 × 393 pt
    static let phoneLandscape   = CGSize(width: 852, height: 393)
    /// iPad 11" portrait       — 820 × 1180 pt
    static let iPadPortrait     = CGSize(width: 820, height: 1180)
    /// iPad 11" landscape      — 1180 × 820 pt
    static let iPadLandscape    = CGSize(width: 1180, height: 820)
    /// iPad Pro 12.9" landscape — 1366 × 1024 pt
    static let iPadLandscapeLarge = CGSize(width: 1366, height: 1024)

    /// Returns the canonical size for the given layout context.
    static func size(for context: AdaptiveLayoutContext) -> CGSize {
        switch context {
        case .phonePortrait:         return phonePortrait
        case .phoneLandscape:        return phoneLandscape
        case .iPadPortraitStandard:  return iPadPortrait
        case .iPadLandscapeStandard: return iPadLandscape
        case .iPadLandscapeLarge:    return iPadLandscapeLarge
        case .externalDisplay16by9:  return CGSize(width: 1920, height: 1080)
        }
    }

    /// Returns the `UITraitCollection` that matches the given layout context.
    static func traits(for context: AdaptiveLayoutContext) -> UITraitCollection {
        switch context {
        case .phonePortrait:
            return UITraitCollection(traitsFrom: [
                UITraitCollection(horizontalSizeClass: .compact),
                UITraitCollection(verticalSizeClass: .regular),
                UITraitCollection(userInterfaceIdiom: .phone),
                UITraitCollection(displayScale: 3.0),
            ])
        case .phoneLandscape:
            return UITraitCollection(traitsFrom: [
                UITraitCollection(horizontalSizeClass: .compact),
                UITraitCollection(verticalSizeClass: .compact),
                UITraitCollection(userInterfaceIdiom: .phone),
                UITraitCollection(displayScale: 3.0),
            ])
        case .iPadPortraitStandard:
            return UITraitCollection(traitsFrom: [
                UITraitCollection(horizontalSizeClass: .regular),
                UITraitCollection(verticalSizeClass: .regular),
                UITraitCollection(userInterfaceIdiom: .pad),
                UITraitCollection(displayScale: 2.0),
            ])
        case .iPadLandscapeStandard:
            return UITraitCollection(traitsFrom: [
                UITraitCollection(horizontalSizeClass: .regular),
                UITraitCollection(verticalSizeClass: .regular),
                UITraitCollection(userInterfaceIdiom: .pad),
                UITraitCollection(displayScale: 2.0),
            ])
        case .iPadLandscapeLarge:
            return UITraitCollection(traitsFrom: [
                UITraitCollection(horizontalSizeClass: .regular),
                UITraitCollection(verticalSizeClass: .regular),
                UITraitCollection(userInterfaceIdiom: .pad),
                UITraitCollection(displayScale: 2.0),
            ])
        case .externalDisplay16by9:
            return UITraitCollection(traitsFrom: [
                UITraitCollection(horizontalSizeClass: .regular),
                UITraitCollection(verticalSizeClass: .regular),
                UITraitCollection(userInterfaceIdiom: .pad),
                UITraitCollection(displayScale: 2.0),
            ])
        }
    }
}

// MARK: - Baseline path resolution

/// Mirrors `swift-snapshot-testing`'s own path convention so the harness can tell
/// whether a baseline exists *before* the library gets a chance to write one.
///
/// Convention (verified against the committed smoke baseline):
///   `<dir of test file>/__Snapshots__/<test file basename>/<testName>.<name>.png`
///
/// Returns `nil` when `name` is absent — the library falls back to a positional
/// counter there, and guessing it wrong would produce a false failure. Every call
/// site in this suite passes `named:`, so the check is live in practice.
///
/// Pinned to `swift-snapshot-testing` 1.17.6. If a future upgrade changes the
/// layout convention, the harness smoke test fails first and points here.
private func expectedBaselineURL(file: StaticString, testName: String, name: String?) -> URL? {
    guard let name else { return nil }

    let fileURL = URL(fileURLWithPath: "\(file)")
    let suiteDirectory = fileURL.deletingPathExtension().lastPathComponent

    return fileURL
        .deletingLastPathComponent()
        .appendingPathComponent("__Snapshots__")
        .appendingPathComponent(suiteDirectory)
        .appendingPathComponent("\(sanitizePathComponent(testName)).\(sanitizePathComponent(name))")
        .appendingPathExtension("png")
}

/// Reproduces `swift-snapshot-testing`'s internal `sanitizePathComponent`, which
/// is not public. Collapses non-word runs to `-` and trims leading/trailing `-`,
/// so `#function`'s `"testHarnessSmoke()"` resolves to `"testHarnessSmoke"`.
private func sanitizePathComponent(_ string: String) -> String {
    string
        .replacingOccurrences(of: "\\W+", with: "-", options: .regularExpression)
        .replacingOccurrences(of: "^-|-$", with: "", options: .regularExpression)
}

// MARK: - Core snapshot helper

/// Asserts a pixel-identical snapshot of a SwiftUI view rendered at the
/// canonical size for the given `AdaptiveLayoutContext`.
///
/// The view is wrapped in a `UIHostingController`, the layout context is
/// injected into the SwiftUI environment, and the hosting controller's
/// `UITraitCollection` is overridden to match — so the view believes it is
/// running on the target device.
///
/// Record mode is controlled by the `RECORD_SNAPSHOTS` environment variable;
/// see the file-level comment above.
///
/// - Parameters:
///   - view: The SwiftUI view to snapshot.
///   - context: The `AdaptiveLayoutContext` whose canonical device size and
///     traits to use.
///   - name: Optional suffix appended to the baseline filename, useful when
///     snapshotting the same view type in multiple states.
///   - file: Source file of the calling test (used to locate the `__Snapshots__`
///     directory alongside it). Defaults to `#file`.
///   - testName: Name of the calling test function (used as the baseline
///     filename prefix). Defaults to `#function`.
///   - line: Source line (used to pin XCT failure to the call site). Defaults
///     to `#line`.
public func assertAdaptiveSnapshot<V: View>(
    of view: V,
    as context: AdaptiveLayoutContext,
    named name: String? = nil,
    file: StaticString = #file,
    testName: String = #function,
    line: UInt = #line
) {
    let size = SnapshotDeviceSize.size(for: context)
    let traits = SnapshotDeviceSize.traits(for: context)

    // Inject the AdaptiveLayoutContext into the SwiftUI environment so views
    // that read `\.adaptiveLayoutContext` see the intended context.
    let wrapped = view
        .environment(\.adaptiveLayoutContext, context)
        .frame(width: size.width, height: size.height)

    let vc = UIHostingController(rootView: wrapped)
    vc.view.frame = CGRect(origin: .zero, size: size)

    // Opt-in record mode. Two delivery paths, because they serve different callers:
    //
    //   1. `-D RECORD_SNAPSHOTS` via OTHER_SWIFT_FLAGS — the command-line path, and the
    //      only one that works with plain `xcodebuild` and no test plan.
    //   2. RECORD_SNAPSHOTS in the process environment — the Xcode path, for a scheme or
    //      test plan that sets Test-action environment variables.
    //
    // Verified 2026-08-17 that the environment variable alone is NOT deliverable from the
    // command line: neither `OTHER_SWIFT_FLAGS="-D RECORD_SNAPSHOTS"` (compile-time, does
    // not reach ProcessInfo) nor `TEST_RUNNER_RECORD_SNAPSHOTS=1` engaged record mode when
    // this read was environment-only. The documented record command was therefore inert —
    // it would have silently failed the user's baseline-recording session. Hence path 1.
    //
    // Neither path hardcodes `record: true` in committed source, which is what the
    // file-level prohibition is actually about: a suite left in record mode passes
    // unconditionally and is worse than no suite.
    #if RECORD_SNAPSHOTS
    let shouldRecord = true
    #else
    let shouldRecord = ProcessInfo.processInfo.environment["RECORD_SNAPSHOTS"] != nil
    #endif

    // Missing-baseline override.
    //
    // `assertSnapshot` fails when a reference is absent, but it ALSO writes the
    // reference it just rendered. That side effect is the problem: the first run
    // fails, and every run after it passes against a baseline no human approved.
    // On 2026-08-17 a cancelled agent run produced exactly that — 8 journey-surface
    // baselines written agent-side, which had to be deleted. Task 4.3 forbids it
    // because an agent cannot judge whether a layout *looks* right.
    //
    // So: outside record mode, an absent baseline fails here and returns before
    // anything touches disk. Recording is a deliberate, human-driven act.
    if !shouldRecord, let expected = expectedBaselineURL(file: file, testName: testName, name: name) {
        if !FileManager.default.fileExists(atPath: expected.path) {
            XCTFail(
                """
                No committed baseline for this snapshot, and record mode is off.

                Expected: \(expected.path)

                Baselines are recorded by a human who has reviewed them, never by an
                agent. To record: RECORD_SNAPSHOTS=1 with the documented xcodebuild
                command, review the generated PNGs, then re-run without the variable
                to confirm byte-identical passes.
                """,
                file: file,
                line: line
            )
            return
        }
    }

    assertSnapshot(
        of: vc,
        as: .image(on: .init(size: size), traits: traits),
        named: name,
        record: shouldRecord,
        file: file,
        testName: testName,
        line: line
    )
}

// MARK: - Harness smoke test

/// Harness smoke test — proves the snapshot loop closes end-to-end.
///
/// Named `Snapshots` so the xcodebuild filter
/// `-only-testing:MeridianMotorsCompanionTests/Snapshots` resolves to this class.
///
/// Uses a deterministic, dependency-free `Color.blue` view — not a real
/// journey surface.  The intent is to confirm:
///
///   1. `SnapshotTesting` compiles and links correctly.
///   2. `assertAdaptiveSnapshot` renders without crashing.
///   3. A missing baseline FAILS (rather than silently passing), so 4.2's
///      cases with absent baselines will correctly fail.
///
/// The baseline for this test is committed to source control alongside this
/// file at:
///   MeridianMotorsCompanionTests/Snapshots/__Snapshots__/SnapshotHelpers/testHarnessSmoke.phonePortrait.png
///
/// To re-record: set `RECORD_SNAPSHOTS=1` and re-run.
final class Snapshots: XCTestCase {

    /// Trivial smoke: renders a solid blue rectangle at `.phonePortrait`
    /// dimensions and asserts pixel-identity with the committed baseline.
    func testHarnessSmoke() {
        let view = Color.blue
            .frame(width: SnapshotDeviceSize.phonePortrait.width,
                   height: SnapshotDeviceSize.phonePortrait.height)

        assertAdaptiveSnapshot(of: view, as: .phonePortrait, named: "phonePortrait")
    }
}
