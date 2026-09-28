import SwiftUI

/// Mock finance qualification result used for in-session state.
/// Mirrors the `finance_qualify()` tool response shape from the backend.
struct FinanceQualification: Equatable {
    let tier: String            // e.g. "A", "B", "C"
    let rateRangeMin: Double
    let rateRangeMax: Double
    let disclosureText: String  // verbatim from backend — NOT paraphrased per spec constraint
    let depositAmount: Double
    let depositCurrency: String
    let transactionRef: String? // set after payment_initiate
}

/// Step 7 of ConfiguratorFlow — Finance & payment.
///
/// In the current demo window the `/acquire/*` endpoints do not exist
/// server-side (decisions.md 2026-07-28 "Demo scope"). This step uses a
/// deterministic local mock that mirrors the `finance_qualify()` tool shape.
/// When real endpoints are provisioned, the mock is replaced by an
/// `AcquireCatalogClient` call with identical UX behavior.
///
/// Per spec constraint: the `disclosureText` from the qualification
/// result is displayed VERBATIM — this view does not paraphrase it.
struct FinanceStep: View {
    let configuredPrice: Double
    let currencySymbol: String
    /// ISO 4217 code from tenant config. Kept separate from `currencySymbol`
    /// because the qualification names a currency rather than rendering it;
    /// this was hardcoded to a single market before.
    let currencyCode: String
    let financePartners: AcquireConfig.FinancePartners?
    let theme: TenantTheme
    let onQualified: (FinanceQualification) -> Void
    let onBack: () -> Void

    @State private var qualification: FinanceQualification? = nil
    @State private var isLoading: Bool = false
    @State private var error: String? = nil

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                backRow

                if let qual = qualification {
                    qualificationCard(qual)
                    paymentSection(qual)
                } else {
                    preQualSection
                }
            }
            .padding()
        }
        .onAppear {
            // Auto-start qualification simulation on step entry
            if qualification == nil && !isLoading {
                Task { await runQualification() }
            }
        }
    }

    // MARK: - Subviews

    @ViewBuilder
    private var backRow: some View {
        Button(action: onBack) {
            Label("Back to review", systemImage: "chevron.left")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var preQualSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            if isLoading {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Checking eligibility…")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.top, 20)
            } else if let err = error {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Eligibility check unavailable")
                        .font(.subheadline.bold())
                    Text(err)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Try again") {
                        Task { await runQualification() }
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
    }

    @ViewBuilder
    private func qualificationCard(_ qual: FinanceQualification) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Finance pre-qualification", systemImage: "creditcard.fill")
                .font(.caption.bold())
                .foregroundStyle(theme.primary)

            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Tier").font(.caption).foregroundStyle(.secondary)
                    Text(qual.tier).font(.title2.bold()).foregroundStyle(theme.primary)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("Rate range").font(.caption).foregroundStyle(.secondary)
                    Text(String(format: "%.1f%% – %.1f%%", qual.rateRangeMin, qual.rateRangeMax))
                        .font(.subheadline.bold())
                }
            }

            // Disclosure text displayed VERBATIM per spec constraint.
            // Swift view does NOT paraphrase.
            Text(qual.disclosureText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(theme.primary.opacity(0.20), lineWidth: 1)
                )
        )
    }

    @ViewBuilder
    private func paymentSection(_ qual: FinanceQualification) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Reservation deposit")
                .font(.headline)
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Refundable deposit")
                        .font(.subheadline)
                    Text("Cancellable until manufacturing stage begins.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("\(currencySymbol)\(Int(qual.depositAmount))")
                    .font(.title3.bold())
                    .foregroundStyle(theme.primary)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color(.tertiarySystemGroupedBackground))
            )

            if let partner = financePartners?.primary {
                Text("Finance partner: \(partner.displayName ?? "Primary lender")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Button {
                onQualified(qual)
            } label: {
                Text("Pay deposit & reserve")
                    .bold()
                    .frame(maxWidth: .infinity, minHeight: 32)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(theme.primary)
        }
    }

    // MARK: - Qualification logic (deterministic mock)

    /// Deterministic mock finance qualification.
    /// Tier is derived from `configuredPrice / 100_000` so same inputs → same tier.
    /// In production this would call `AcquireCatalogClient` which would invoke
    /// `finance_qualify()` on the backend.
    private func runQualification() async {
        isLoading = true
        error = nil
        defer { isLoading = false }

        // Simulate a short async round-trip (endpoint not yet provisioned).
        try? await Task.sleep(nanoseconds: 800_000_000)

        // Deterministic tier: price ≤ 100k → A, ≤ 300k → B, else C.
        let tier: String
        let rateMin: Double
        let rateMax: Double
        switch configuredPrice {
        case ..<100_000:
            tier = "A"; rateMin = 6.5;  rateMax = 8.5
        case ..<300_000:
            tier = "B"; rateMin = 9.0;  rateMax = 12.0
        default:
            tier = "C"; rateMin = 12.5; rateMax = 16.0
        }

        // Disclosure text is verbatim mock — matches the agent tool's output shape.
        let disclosure = "This is a pre-qualification estimate only and does not constitute a credit offer or approval guarantee. Actual rates depend on final credit review and lender terms. Rates shown (\(rateMin)%–\(rateMax)% APR) are indicative only. You have consented to a soft credit inquiry for this estimate."

        qualification = FinanceQualification(
            tier: tier,
            rateRangeMin: rateMin,
            rateRangeMax: rateMax,
            disclosureText: disclosure,
            depositAmount: 2_000,
            depositCurrency: currencyCode,
            transactionRef: nil
        )
    }
}
