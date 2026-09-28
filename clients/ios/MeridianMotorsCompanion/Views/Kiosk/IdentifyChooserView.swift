import SwiftUI

/// Beat 0 — the station's resting state.
///
/// Shown at app launch in kiosk mode and after every inter-visitor reset.
/// Two affordances let a visitor identify themselves before the journey starts:
///   1. **Scan badge** — present but DISABLED (badge scanning deferred; see
///      `decisions.md` 2026-08-11 "Name capture is scan-OR-type from the start").
///      The affordance must be visible so the screen shape accommodates scanning
///      when it arrives, without rework at that point.
///   2. **Enter name** — the v1 path; routes to `NameEntryView`.
///
/// A "Continue without name" link lets a visitor skip identification entirely.
///
/// ## DO NOT add camera permission or badge-parsing code here.
/// Adding `NSCameraUsageDescription` to Info.plist would show an iOS permission
/// prompt to every visitor before anything else — poor first impression and
/// the permission would go unused. See spec task 7.1 constraints.
struct IdentifyChooserView: View {

    // MARK: - Routing

    /// Called when the visitor taps "Enter name".
    var onEnterName: () -> Void = {}
    /// Called when the visitor skips identification.
    var onSkip: () -> Void = {}

    // MARK: - Body

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 28) {
                headerSection
                choiceButtons
                skipLink
            }
            .padding(.horizontal, 32)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Identify yourself to start your journey")
    }

    // MARK: - Header

    private var headerSection: some View {
        VStack(spacing: 8) {
            Image(systemName: "person.crop.circle")
                .font(.system(size: 56))
                .foregroundStyle(.secondary)

            Text("How would you like to start?")
                .font(.title2.bold())
                .multilineTextAlignment(.center)

            Text("Your first name personalises the journey.\nYou can also skip.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Choice buttons

    private var choiceButtons: some View {
        VStack(spacing: 16) {
            // Scan badge — DISABLED. Badge scanning is deferred.
            // Present so the screen shape accommodates it when it arrives.
            // Do NOT add camera permission or scanning code here.
            Button {
                // no-op: disabled
            } label: {
                Label("Scan badge", systemImage: "creditcard.viewfinder")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .font(.body.bold())
            }
            .buttonStyle(.bordered)
            .disabled(true)
            .accessibilityLabel("Scan badge — coming soon")
            .accessibilityHint("Badge scanning is not yet available")
            .overlay(
                Text("Coming soon")
                    .font(.caption2.bold())
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.trailing, 12),
                alignment: .trailing
            )

            // Enter name — the v1 path.
            Button(action: onEnterName) {
                Label("Enter my name", systemImage: "keyboard")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .font(.body.bold())
            }
            .buttonStyle(.borderedProminent)
            .accessibilityLabel("Enter my name")
        }
    }

    // MARK: - Skip link

    private var skipLink: some View {
        Button("Continue without name", action: onSkip)
            .font(.subheadline)
            .foregroundStyle(.secondary)
            .accessibilityLabel("Continue without entering a name")
    }
}

// MARK: - Preview

#if DEBUG
#Preview {
    IdentifyChooserView(
        onEnterName: { print("Enter name tapped") },
        onSkip: { print("Skip tapped") }
    )
}
#endif
