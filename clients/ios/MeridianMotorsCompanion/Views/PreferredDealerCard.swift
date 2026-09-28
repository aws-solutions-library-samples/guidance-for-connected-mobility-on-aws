import SwiftUI

/// The standing "your dealer" card at the top of the Service tab.
///
/// This is the surface that turns a dealer from a list you filter into a place you have. It
/// renders from the cached `PreferredDealer.Stored` snapshot, so it draws on a cold launch
/// with no network call — see that type's doc comment for why the snapshot is denormalised.
///
/// Two states, and the empty one earns its place:
///   - chosen   → name, badge, address, and the two things an owner actually wants to do
///                with a dealer: call them, or book with them.
///   - not set  → a single quiet prompt. NOT a modal, NOT a nag. An owner who ignores it
///                loses nothing; booking still works and still offers to remember the
///                choice afterwards.
///
/// The card is deliberately NOT tappable as a whole. Its two actions do different things —
/// one leaves the app to place a call — and a whole-card tap gesture with two child buttons
/// makes which one fired ambiguous.
struct PreferredDealerCard: View {
    let theme: TenantTheme
    /// Cached snapshot, or nil when the driver has not chosen a dealer.
    let dealer: PreferredDealer.Stored?
    /// Opens the booking flow. Same entry point as the tab's primary CTA.
    let onBook: () -> Void
    /// Presents the picker so an existing choice can be changed.
    let onChange: () -> Void

    var body: some View {
        if let dealer {
            chosenCard(dealer)
        } else {
            setUpPrompt
        }
    }

    // MARK: - Chosen

    private func chosenCard(_ dealer: PreferredDealer.Stored) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "building.2.fill")
                    .font(.title3)
                    .foregroundStyle(theme.primary)
                VStack(alignment: .leading, spacing: 3) {
                    Text("YOUR DEALER")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .tracking(0.6)
                    Text(dealer.name)
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text(dealer.address)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 10) {
                // Calling is the reason a phone number is on the card at all, so it is a
                // real button rather than selectable text. Absent a number the button is
                // omitted entirely — a dead "Call" is worse than no Call.
                if let phone = dealer.phone, !phone.isEmpty, let url = telURL(phone) {
                    Link(destination: url) {
                        actionLabel("Call", systemImage: "phone.fill", filled: false)
                    }
                }
                Button(action: onBook) {
                    actionLabel("Book service", systemImage: "calendar.badge.plus", filled: true)
                }
                .buttonStyle(.plain)
                Spacer(minLength: 0)
                Button("Change", action: onChange)
                    .font(.subheadline)
                    .foregroundStyle(theme.primary)
            }
        }
        .padding()
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(.secondarySystemGroupedBackground)))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(theme.primary.opacity(0.25), lineWidth: 1)
        )
        .padding(.horizontal)
    }

    // MARK: - Not yet chosen

    private var setUpPrompt: some View {
        Button(action: onChange) {
            HStack(spacing: 10) {
                Image(systemName: "building.2")
                    .font(.title3)
                    .foregroundStyle(theme.primary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Choose your dealer")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.primary)
                    Text("Book faster next time, and keep your service in one place")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            .padding()
            .background(RoundedRectangle(cornerRadius: 14).fill(Color(.secondarySystemGroupedBackground)))
            .padding(.horizontal)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Bits

    private func actionLabel(_ title: String, systemImage: String, filled: Bool) -> some View {
        HStack(spacing: 5) {
            Image(systemName: systemImage).font(.caption)
            Text(title).font(.subheadline.weight(.medium))
        }
        .padding(.vertical, 7)
        .padding(.horizontal, 12)
        .foregroundStyle(filled ? .white : theme.primary)
        .background(
            RoundedRectangle(cornerRadius: 9)
                .fill(filled ? AnyShapeStyle(theme.primary) : AnyShapeStyle(theme.primary.opacity(0.12)))
        )
    }

    /// Strip everything that is not a digit or a leading `+` before building a `tel:` URL.
    /// Seeded numbers arrive as "(312) 555-0143", which does not survive as a URL host.
    private func telURL(_ phone: String) -> URL? {
        var digits = phone.filter { $0.isNumber || $0 == "+" }
        if let plus = digits.firstIndex(of: "+"), plus != digits.startIndex {
            digits.removeAll { $0 == "+" }
        }
        guard !digits.isEmpty else { return nil }
        return URL(string: "tel:\(digits)")
    }
}
