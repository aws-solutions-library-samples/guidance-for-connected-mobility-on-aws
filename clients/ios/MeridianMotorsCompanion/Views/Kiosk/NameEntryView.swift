import SwiftUI

/// Beat 0 — first-name entry.
///
/// Single field, first name only. No surname field exists anywhere; nothing
/// derives one. Skippable — a visitor who doesn't want to share a name taps
/// "Skip" and the journey proceeds without one.
///
/// ## Validation contract
/// Validation must reject nothing a real first name could be:
///   - Hyphens: Anne-Marie
///   - Apostrophes: O'Brien
///   - Spaces (middle names, particles): Jean Claude
///   - Non-Latin scripts: 李, Αλέξης
///   - Max length: 60 characters (generous; real names are never this long but
///     a 32-char cap has historically failed Icelandic compound names).
///
/// Only whitespace-only input is rejected (the visitor must have tapped into
/// the field by accident). Empty input is handled by the skip path, not by
/// showing an error.
///
/// ## Privacy
/// The name is held in `@Binding` and is NOT persisted beyond the visitor's
/// session. `KioskSessionCoordinator` scrubs it on reset.
/// Do NOT log or persist the name here.
struct NameEntryView: View {

    // MARK: - Configuration

    /// Maximum number of characters accepted in the name field.
    static let maxLength: Int = 60

    // MARK: - State

    /// The trimmed first name. Bound to `KioskSessionCoordinator.visitorFirstName`.
    @Binding var firstName: String

    /// Shown when input is whitespace-only (the only rejection case).
    @State private var showWhitespaceError: Bool = false

    // MARK: - Routing

    /// Called when the visitor confirms their name (non-empty, non-whitespace).
    var onConfirm: (String) -> Void = { _ in }
    /// Called when the visitor chooses to skip.
    var onSkip: () -> Void = {}

    // MARK: - Body

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 24) {
                headerSection
                nameField
                if showWhitespaceError {
                    Text("Please enter a name, or use \"Skip\".")
                        .font(.caption)
                        .foregroundStyle(.red)
                        .transition(.opacity)
                }
                actionButtons
            }
            .padding(.horizontal, 32)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
        .animation(.easeInOut(duration: 0.15), value: showWhitespaceError)
    }

    // MARK: - Header

    private var headerSection: some View {
        VStack(spacing: 8) {
            Image(systemName: "person.text.rectangle")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)

            Text("What's your first name?")
                .font(.title2.bold())
                .multilineTextAlignment(.center)

            Text("First name only — you can skip if you prefer.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Name field

    private var nameField: some View {
        TextField("First name", text: $firstName)
            .font(.title3)
            .padding()
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
            .autocorrectionDisabled(true)
            .textInputAutocapitalization(.words)
            .submitLabel(.done)
            .onChange(of: firstName) { _, newValue in
                // Enforce max length
                if newValue.count > Self.maxLength {
                    firstName = String(newValue.prefix(Self.maxLength))
                }
                // Clear error once the visitor starts typing a real character
                if showWhitespaceError && !newValue.trimmingCharacters(in: .whitespaces).isEmpty {
                    showWhitespaceError = false
                }
            }
            .onSubmit {
                attemptConfirm()
            }
            .accessibilityLabel("First name field")
            .accessibilityHint("Enter your first name. Maximum \(Self.maxLength) characters.")
    }

    // MARK: - Action buttons

    private var actionButtons: some View {
        VStack(spacing: 12) {
            Button("Continue", action: attemptConfirm)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .font(.body.bold())
                .buttonStyle(.borderedProminent)
                .accessibilityLabel("Continue with name")

            Button("Skip", action: onSkip)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .accessibilityLabel("Continue without a name")
        }
    }

    // MARK: - Validation

    /// Trims and validates. Calls `onConfirm` on success; sets error flag on
    /// whitespace-only input. Empty input is the skip path; call `onSkip` for it.
    private func attemptConfirm() {
        let trimmed = firstName.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty {
            // If the field is completely empty the visitor probably means skip;
            // if it has content that collapses to empty they need a nudge.
            if firstName.isEmpty {
                onSkip()
            } else {
                showWhitespaceError = true
            }
            return
        }
        showWhitespaceError = false
        firstName = trimmed
        onConfirm(trimmed)
    }
}

// MARK: - Preview

#if DEBUG
#Preview {
    @Previewable @State var name = ""
    NameEntryView(
        firstName: $name,
        onConfirm: { print("Confirmed: \($0)") },
        onSkip: { print("Skipped") }
    )
}
#endif
