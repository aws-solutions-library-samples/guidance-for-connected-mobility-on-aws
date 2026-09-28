import Foundation

/// Which upgrade offers the user has permanently dismissed, and the one place that
/// knows how that set is stored.
///
/// Extracted 2026-08-19 when the offer moved into an auto-presented sheet. Two
/// callers now need this state and they must not disagree:
///
/// - `UpgradeOfferBanner` writes it, and reads it via `@AppStorage` so the view
///   re-renders the moment a dismissal happens.
/// - `HomeTabView` reads it *before presenting the sheet*. Without that gate the
///   sheet presents over a banner that has dismissed itself, i.e. an empty sheet
///   arriving unbidden — worse than the problem it was solving.
///
/// The storage key and the separator live here rather than being written twice.
/// A duplicated `"dismissedUpgradeOfferIds"` string that drifts by one character
/// fails silently and looks exactly like "dismissal doesn't work", which is the bug
/// this file's history already contains once.
enum UpgradeOfferDismissals {

    /// Shared with `@AppStorage` in the banner. Changing this orphans existing
    /// dismissals — every previously-dismissed offer reappears once.
    static let storageKey = "dismissedUpgradeOfferIds"

    /// ASCII unit separator. Not a comma: offer ids fall back to `modelName`, which
    /// contains spaces and punctuation and could contain a comma.
    static let separator: Character = "\u{1F}"

    static func ids(in raw: String) -> Set<String> {
        Set(raw.split(separator: separator).map(String.init))
    }

    static func encode(_ ids: Set<String>) -> String {
        ids.sorted().joined(separator: String(separator))
    }

    /// Current dismissal set, read straight from `UserDefaults`.
    ///
    /// For non-view callers. Views should prefer `@AppStorage` so they re-render on
    /// change; this returns a snapshot and does not observe.
    static var current: Set<String> {
        ids(in: UserDefaults.standard.string(forKey: storageKey) ?? "")
    }

    static func isDismissed(_ offerId: String) -> Bool {
        current.contains(offerId)
    }

    /// Records a permanent dismissal. The ONE writer.
    ///
    /// Writes through `UserDefaults` rather than a view's `@AppStorage` because two
    /// views now need to cause a dismissal and only one of them should own the
    /// encoding. `@AppStorage` observes this key, so the banner still re-renders on
    /// the write even though the write did not come from it.
    ///
    /// Added 2026-08-21: "Not interested in upgrading" has to outlive the view that
    /// offers it. The button used to write the banner's own `@AppStorage`, which
    /// emptied the banner *while the sheet stayed open* — the sheet's content
    /// vanished and the drawer remained. Persisting now happens after the drawer has
    /// finished sliding away, which means the caller doing the persisting is
    /// `HomeTabView`, not the banner.
    static func persist(dismissing offerId: String) {
        var ids = current
        ids.insert(offerId)
        UserDefaults.standard.set(encode(ids), forKey: storageKey)
    }

    /// Whether any offer in `offers` is still undismissed — the question the sheet
    /// gate actually needs to answer.
    ///
    /// Takes the whole list rather than one id because the banner selects its own
    /// recommendation via `OfferRecommendationBasis.bestMatch`, and the gate must not
    /// duplicate that selection logic; a gate that picked a different offer than the
    /// banner renders would present a sheet about something the user cannot see.
    static func hasUndismissedOffer(
        among offers: [AcquireConfig.UpgradeOffer],
        ownedModelName: String?
    ) -> Bool {
        guard let top = OfferRecommendationBasis.bestMatch(
            among: offers, ownedModelName: ownedModelName
        ) else { return false }
        return !isDismissed(top.offer.id)
    }
}
