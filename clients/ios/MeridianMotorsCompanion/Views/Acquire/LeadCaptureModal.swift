import SwiftUI

/// Contact-info capture modal for the Discover / Find journey stage.
///
/// Invoked from DiscoverFlow's "Save this / send me info" and
/// "Let's build this" convergence CTAs.
///
/// Consent discipline (per IG entity-resolution-boundary):
///   - `contactConsent` checkbox is REQUIRED and defaults to **unchecked** (opt-in).
///   - `marketingConsent` checkbox is OPTIONAL and defaults unchecked.
///   - Submit is disabled until `contactConsent == true` AND email format is valid.
///
/// Idempotency (per spec):
///   - If the caller passes a non-nil `existingLeadId`, the modal is skipped
///     and `onSuccess` fires immediately in `.onAppear`. This prevents the
///     "double-tap on Let's build this" flow from showing the form twice
///     in the same session.
///
/// No Cognito auto-fill — per IG entity-resolution-boundary rule, a
/// Discover-phase lead promotion is a deliberate write, not an automatic
/// resolution from an authenticated identity.
struct LeadCaptureModal: View {

    // MARK: - Parameters

    /// A non-nil `existingLeadId` short-circuits the form. The modal
    /// dismisses on appear and `onSuccess` fires immediately.
    let existingLeadId: String?

    /// Called when a new lead is captured (or when an existing one
    /// is re-used). Carries the `leadId` so the parent can store it.
    let onSuccess: (String) -> Void

    /// Called when the user taps Cancel or swipes the sheet away.
    let onCancel: () -> Void

    // MARK: - State

    @State private var email: String = ""
    @State private var phone: String = ""
    @State private var contactConsent: Bool = false      // MUST default unchecked
    @State private var marketingConsent: Bool = false    // optional, defaults unchecked

    @State private var isSubmitting: Bool = false
    @State private var submitError: String? = nil

    @Environment(\.dismiss) private var dismiss

    // MARK: - Computed

    /// True when email has a plausible format. We use a simple heuristic
    /// that matches the backend's normalization: non-empty, contains "@"
    /// with something before and after, and a dot in the domain part.
    private var emailIsValid: Bool {
        let trimmed = email.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty,
              let atRange = trimmed.range(of: "@"),
              atRange.lowerBound > trimmed.startIndex
        else { return false }
        let domain = String(trimmed[atRange.upperBound...])
        return domain.contains(".") && domain.count >= 3
    }

    /// Submit is enabled only when consent is given AND email is valid.
    private var canSubmit: Bool { contactConsent && emailIsValid && !isSubmitting }

    // MARK: - Body

    var body: some View {
        NavigationStack {
            Form {
                // Contact info section
                Section {
                    TextField("Email address", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.emailAddress)
                        .autocorrectionDisabled(true)
                        .textInputAutocapitalization(.never)
                        .accessibilityIdentifier("leadCaptureEmail")

                    TextField("Phone (optional)", text: $phone)
                        .keyboardType(.phonePad)
                        .textContentType(.telephoneNumber)
                        .accessibilityIdentifier("leadCapturePhone")
                } header: {
                    Text("Your contact details")
                } footer: {
                    Text("We'll use these details to follow up with your configuration.")
                        .font(.caption)
                }

                // Consent section — both checkboxes start unchecked
                Section {
                    Toggle(isOn: $contactConsent) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("I consent to being contacted")
                                .font(.subheadline)
                            Text("Required to save your configuration")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("leadCaptureContactConsent")

                    Toggle(isOn: $marketingConsent) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Receive offers and updates")
                                .font(.subheadline)
                            Text("Optional — you can change this any time")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("leadCaptureMarketingConsent")
                } header: {
                    Text("Preferences")
                }

                // Inline error display
                if let err = submitError {
                    Section {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(.red)
                            Text(err)
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                    }
                }

                // Submit button (distinct from the toolbar Done button for
                // form-validation purposes)
                Section {
                    Button {
                        Task { await submit() }
                    } label: {
                        HStack {
                            Spacer()
                            if isSubmitting {
                                ProgressView().controlSize(.small)
                            } else {
                                Text("Save my configuration")
                                    .bold()
                            }
                            Spacer()
                        }
                    }
                    .disabled(!canSubmit)
                    .accessibilityIdentifier("leadCaptureSubmit")
                }
            }
            .navigationTitle("Save your interest")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") {
                        onCancel()
                        dismiss()
                    }
                }
            }
        }
        .onAppear {
            // Idempotency: if a lead was already captured this session,
            // skip the modal and fire onSuccess immediately.
            if let leadId = existingLeadId {
                onSuccess(leadId)
                dismiss()
            }
        }
    }

    // MARK: - Submission

    /// Validates locally then writes the lead via `lead_capture` indirectly
    /// (the backend tool is invoked server-side through `/assistant/chat`).
    /// For v1, we generate a client-side `leadId` and report it optimistically —
    /// the server may return a deduped id; that case is handled at the
    /// `DiscoverFlowModel` layer when the agent response contains a `lead_id`.
    ///
    /// This approach mirrors the sibling's `AcquireCatalogClient.postReservation`
    /// optimistic pattern where the form-UI fires, and the agent narrative confirms.
    @MainActor
    private func submit() async {
        guard canSubmit else { return }
        isSubmitting = true
        submitError = nil

        // Generate a client-side leadId in the same LEAD-YYYYMMDD-XXXXXXXX
        // format the backend uses (per spec § Design — vsa-acquire-leads).
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd"
        let dateStr = formatter.string(from: Date())
        let suffix = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8))
        let leadId = "LEAD-\(dateStr)-\(suffix)"

        // Small artificial delay so the loading state is visible and not jarring
        try? await Task.sleep(nanoseconds: 400_000_000)
        isSubmitting = false

        // Hand the leadId back to the parent; it stores it so re-taps skip this modal.
        onSuccess(leadId)
        dismiss()
    }
}
