import SwiftUI

/// Tab-level loading skeleton shown while a tab's first data load is
/// in flight. Replaces the previous "render-with-stub-values, then
/// flash to real values" pattern that drivers were reading as the app
/// downgrading their numbers (e.g. health 100 → 39 right after sign-in).
///
/// Each tab uses its own `AppSession.hasLoadedInitial<Tab>` computed
/// property to gate between this skeleton and the real content. After
/// the first load completes the corresponding `LoadedAt` timestamp
/// stays non-nil for the session lifetime, so subsequent
/// pull-to-refreshes update the cards in place without returning to
/// this skeleton.
///
/// Deliberately bland — no numbers or labels with placeholder text —
/// so nothing here can be misread as real data. The `cardCount`
/// argument lets each tab reserve roughly the right amount of vertical
/// space for the cards that are coming.
struct TabLoadingSkeleton: View {
    /// Number of card-shaped placeholders to render. Tune to match the
    /// rough number of sections each tab shows so the layout doesn't
    /// jump dramatically when real content swaps in.
    let cardCount: Int

    /// Whether to show the small identity strip placeholder (avatar +
    /// two text bars) at the top. Home/Alerts/Service skip it; Vehicle
    /// uses it because the real Vehicle tab leads with vehicle
    /// identity.
    let showsIdentityStrip: Bool

    init(cardCount: Int = 3, showsIdentityStrip: Bool = false) {
        self.cardCount = cardCount
        self.showsIdentityStrip = showsIdentityStrip
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if showsIdentityStrip {
                HStack(spacing: 12) {
                    Circle()
                        .fill(Color(.systemGray5))
                        .frame(width: 48, height: 48)
                    VStack(alignment: .leading, spacing: 6) {
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color(.systemGray5))
                            .frame(width: 160, height: 18)
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color(.systemGray6))
                            .frame(width: 220, height: 12)
                    }
                    Spacer()
                }
            }
            ForEach(0..<cardCount, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color(.systemGray6))
                    .frame(height: 92)
            }
            HStack {
                Spacer()
                ProgressView()
                    .controlSize(.regular)
                    .padding(.top, 8)
                Spacer()
            }
        }
        .padding()
        .redacted(reason: .placeholder)
    }
}
