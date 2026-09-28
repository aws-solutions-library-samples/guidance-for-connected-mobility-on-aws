import SwiftUI

/// Displays the IVE Edition that arrived with the accepted offer.
///
/// The Edition is SHOWN, not chosen — the visitor accepted the offer and
/// the Edition is part of what they accepted. The role of this step is to
/// tell them what the Edition means in terms of bundled features.
///
/// Task 5.2 / Zone 2 Revision 3. Tap 1 of 4 from offer-accepted to order-placed.
struct EditionSummaryStep: View {
    let edition: IvePackage
    let model: CatalogModel
    let theme: TenantTheme
    let onContinue: () -> Void
    /// Nil on the warm (offer-accepted) entry path — there is nowhere to go back to.
    let onBack: (() -> Void)?
    /// Optional escape into the vehicle pickers (Task 2.3 / Group 2).
    ///
    /// When non-nil, a subordinate "Change vehicle" text button is rendered below
    /// the primary continue action.  The visitor can restart at the category
    /// picker to choose a different model — the pickers are one tap away, satisfying
    /// *"pickers must be present"* without competing with the primary continue path.
    ///
    /// Nil on Door A (cold / Discover entry) — the button is absent, so Door A's
    /// behaviour is untouched.
    var onChangeVehicle: (() -> Void)? = nil

    /// Bundled asset name for a static photo of the vehicle being configured.
    ///
    /// Resolved by the caller, not here, because the caller knows the offer's marketing
    /// name ("Meridian Trailwind 2026") which carries the model YEAR — and the Trailwind
    /// has two generations in the bundle. A catalog `displayName` alone
    /// ("Trailwind Adventurer") has no year, so choosing the asset here would risk
    /// illustrating a new-vehicle configuration with the visitor's outgoing car.
    ///
    /// Static and bundled deliberately: `CatalogModel.imageKey` resolves against
    /// `AcquireConfig.assetBaseUrl`, which is `""` on the live tenant row, so the remote
    /// path yields nothing. Nil renders no image rather than a placeholder frame.
    var vehicleImageName: String? = nil

    @Environment(\.adaptiveLayoutContext) private var layoutContext

    /// Static photo of the vehicle being configured. Absent when the caller could not
    /// resolve an asset, rather than substituting a glyph — on a screen headed "Your
    /// Edition", a generic symbol reads as a missing image, and showing the WRONG
    /// vehicle would be worse than showing none.
    @ViewBuilder
    private var vehiclePhoto: some View {
        if let vehicleImageName {
            Image(vehicleImageName)
                .resizable()
                .scaledToFit()
                .frame(maxWidth: .infinity)
                .frame(maxHeight: layoutContext.isCompact ? 170 : 240)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .accessibilityLabel("\(model.displayName)")
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: layoutContext.isCompact ? 14 : 20) {
                if let onBack {
                    Button(action: onBack) {
                        Label("Back", systemImage: "chevron.left")
                            .font(.subheadline)
                            .foregroundStyle(theme.primary)
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                vehiclePhoto
                editionCard
                featureList

                Text("This Edition is part of your accepted offer and is "
                     + "included in your order.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Button(action: onContinue) {
                    Text("Continue — choose your colour")
                        .bold()
                        .frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(theme.primary)

                // Subordinate escape: lets the visitor restart at the category
                // picker to change the vehicle.  Rendered only when wired by the
                // host (Door B); absent on Door A so that entry is unaffected.
                if let onChangeVehicle {
                    Button(action: onChangeVehicle) {
                        Text("Change vehicle")
                            .font(.subheadline)
                            .foregroundStyle(theme.primary.opacity(0.8))
                            .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(layoutContext.isCompact ? 14 : 20)
        }
    }

    // MARK: - Edition card

    private var editionCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Image(systemName: "star.circle.fill")
                    .font(.title2)
                    .foregroundStyle(theme.primary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(edition.displayName + " Edition")
                        .font(.title3.bold())
                    Text(model.displayName)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            Divider()
            Text(edition.featureSummary)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(theme.primary.opacity(0.25), lineWidth: 1)
                )
        )
    }

    // MARK: - Feature list

    private var featureList: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("What's included").font(.headline)
            ForEach(featuresForEdition(edition), id: \.self) { feature in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(theme.primary)
                        .font(.subheadline)
                    Text(feature)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// Edition feature highlights.
    ///
    /// Rewritten 2026-08-21. These were motorcycle features — "Comfort saddle",
    /// "Child pillion footrest", "Heated grips", "Dual-sport windshield",
    /// "Lightweight riding mode" — surviving from before the demo became an automotive
    /// one. A saddle and a pillion footrest do not exist on an SUV, so the configurator's
    /// first screen was describing a different vehicle class than the one being configured.
    ///
    /// Kept generic and descriptive rather than inventing trademarked feature names, and
    /// EV-aware where it matters (one-pedal driving, preconditioning) because the Meridian
    /// lineup is electric — the Trailwind's own spec sheet is in kWh/100km.
    private func featuresForEdition(_ edition: IvePackage) -> [String] {
        switch edition {
        case .family:
            return ["Second-row captain's chairs",
                    "Tri-zone climate control",
                    "Integrated child-seat anchors",
                    "Hands-free power tailgate"]
        case .executive:
            return ["Premium audio system",
                    "Heated and ventilated front seats",
                    "Acoustic laminated glass",
                    "Connected dashboard"]
        case .adventure:
            return ["Adaptive off-road suspension",
                    "Underbody skid plates",
                    "All-terrain tires",
                    "Roof rails and crossbars"]
        case .entryUrban:
            return ["Compact urban footprint",
                    "One-pedal city driving",
                    "Scheduled charge preconditioning",
                    "USB-C fast charging"]
        }
    }
}
