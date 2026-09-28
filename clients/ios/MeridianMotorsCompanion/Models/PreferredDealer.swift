import Foundation

/// The dealer this driver has chosen as theirs, and the one place that knows how that
/// choice is stored.
///
/// WHY THIS EXISTS
/// ---------------
/// Before this, a service centre was picked fresh on every booking, ranked by distance,
/// and then forgotten. That is a marketplace model. It is defensible for the fleet and
/// rental segments — a fleet wants whichever bay is free, a renter has no relationship at
/// all — but it is wrong for a manufacturer's own owner app. A real owner has *their*
/// dealer: where the car came from, who did the last service, whose advisor they know by
/// name. Asking them to re-pick from a distance-sorted list every time actively works
/// against that, because nearest and preferred are not the same place and distance-first
/// will happily route someone past their own dealer.
///
/// So the preference is a stored fact about the driver, not a step in a flow.
///
/// WHAT IS STORED, AND WHY IT IS DENORMALISED
/// ------------------------------------------
/// The whole `ServiceCenter` snapshot is kept, not just `centerId`. The standing card on
/// the Service tab has to render on a cold launch, offline, before any network call — an id
/// alone would force a `/find-service-center` round trip (which needs a capability and
/// coordinates it does not have yet) just to draw a name. Storing the snapshot means the
/// card is instant and the tab has no new dependency.
///
/// The cost is staleness: if the dealer's phone number or address changes, the cached copy
/// is wrong until the driver next sees them in a booking list. That is an acceptable trade
/// for a name, an address and a phone number, and `refresh(from:)` exists to reconcile it
/// whenever a live list does arrive. It would NOT be acceptable for slot availability,
/// which is why `nextAvailableSlots` is deliberately dropped on the way in — a cached
/// "Tuesday at 10" would be a lie within a day, and a confidently wrong appointment time is
/// worse than no time at all.
///
/// KEYED PER DRIVER
/// ----------------
/// One device may be used by more than one persona during a demo, and a preference leaking
/// across drivers would show one person another person's dealer. `driverId` from
/// `vehicleContext.driver` is the key. Absent a driver id there is no preference — see
/// `key(for:)` — because a nil-keyed global slot is exactly how such a leak happens.
enum PreferredDealer {

    /// Prefix for the per-driver key. Changing it orphans every stored preference, so every
    /// driver silently reverts to having no dealer.
    static let storageKeyPrefix = "preferredDealer."

    /// Storage key for a driver, or nil when there is no driver to key on.
    ///
    /// Returning nil rather than falling back to a shared key is deliberate: a shared slot
    /// would be written by whichever persona booked last and read by all of them.
    static func key(for driverId: String?) -> String? {
        guard let driverId, !driverId.isEmpty else { return nil }
        return storageKeyPrefix + driverId
    }

    // MARK: - Stored shape

    /// Denormalised snapshot, sufficient to render the standing card with no network.
    ///
    /// Deliberately NOT a stored `ServiceCenter`: that type is a decode target for a server
    /// response and will grow fields tied to a particular query (distance from where the
    /// driver happened to be, slots on a particular day). Persisting it wholesale would
    /// bake a moment-in-time answer into a durable preference. This is the subset that is
    /// true about the dealer regardless of when or where you asked.
    struct Stored: Codable, Equatable {
        let centerId: String
        let name: String
        /// "dealer" | "independent" | "fleet-service" | … — kept so the card can badge
        /// itself the same way the booking list does.
        let type: String
        let address: String
        let phone: String?
        /// When the driver chose this dealer. Shown as "your dealer since March" and used to
        /// distinguish a deliberate choice from a stale default.
        let chosenAt: Date

        init(centerId: String, name: String, type: String, address: String, phone: String?, chosenAt: Date = Date()) {
            self.centerId = centerId
            self.name = name
            self.type = type
            self.address = address
            self.phone = phone
            self.chosenAt = chosenAt
        }

        /// Snapshot a live `ServiceCenter`. Drops `distanceMiles`, `rating`,
        /// `averageWaitDays` and `nextAvailableSlots` on purpose — all four are answers to
        /// "right now, from here", and none of them survives being cached.
        init(from center: ServiceCenter, chosenAt: Date = Date()) {
            self.init(
                centerId: center.centerId,
                name: center.name,
                type: center.type,
                address: center.address,
                phone: center.phone,
                chosenAt: chosenAt
            )
        }
    }

    // MARK: - Encoding

    /// JSON, so the shape can gain fields without a bespoke parser. A decode failure is
    /// treated as "no preference" rather than crashing: a corrupt value should cost the
    /// driver one re-pick, not the launch.
    static func encode(_ stored: Stored) -> String? {
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        guard let data = try? enc.encode(stored) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func decode(_ raw: String) -> Stored? {
        guard !raw.isEmpty, let data = raw.data(using: .utf8) else { return nil }
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        return try? dec.decode(Stored.self, from: data)
    }

    // MARK: - Read / write

    /// Current preference for a driver, read straight from `UserDefaults`.
    ///
    /// For non-view callers. Views should prefer `@AppStorage` on `key(for:)` so they
    /// re-render when the choice changes — the same division `UpgradeOfferDismissals` draws.
    static func current(for driverId: String?, defaults: UserDefaults = .standard) -> Stored? {
        guard let key = key(for: driverId), let raw = defaults.string(forKey: key) else { return nil }
        return decode(raw)
    }

    @discardableResult
    static func set(_ center: ServiceCenter, for driverId: String?, defaults: UserDefaults = .standard) -> Stored? {
        guard let key = key(for: driverId) else { return nil }
        let stored = Stored(from: center)
        guard let raw = encode(stored) else { return nil }
        defaults.set(raw, forKey: key)
        return stored
    }

    static func clear(for driverId: String?, defaults: UserDefaults = .standard) {
        guard let key = key(for: driverId) else { return }
        defaults.removeObject(forKey: key)
    }

    /// True when this centre is the driver's chosen dealer. Used to badge and pin it in a
    /// booking list.
    static func isPreferred(_ center: ServiceCenter, for driverId: String?, defaults: UserDefaults = .standard) -> Bool {
        current(for: driverId, defaults: defaults)?.centerId == center.centerId
    }

    /// Reconcile the cached snapshot against a freshly-fetched list, preserving `chosenAt`.
    ///
    /// This is how staleness gets corrected without asking the driver to re-choose: any time
    /// a live list happens to contain their dealer, the durable fields are refreshed. It is
    /// a no-op when the dealer is absent from the list — absence means "not returned for
    /// this capability near this location", which is emphatically not "no longer my dealer".
    @discardableResult
    static func refresh(from centers: [ServiceCenter], for driverId: String?, defaults: UserDefaults = .standard) -> Stored? {
        guard let key = key(for: driverId), let existing = current(for: driverId, defaults: defaults) else { return nil }
        guard let live = centers.first(where: { $0.centerId == existing.centerId }) else { return existing }
        let merged = Stored(from: live, chosenAt: existing.chosenAt)
        guard merged != existing, let raw = encode(merged) else { return existing }
        defaults.set(raw, forKey: key)
        return merged
    }

    /// Pin the driver's dealer to the front, leaving everything else in server order.
    ///
    /// A preference is a default, not a cage: the alternatives stay in the list, in the
    /// distance ranking the backend chose. This only changes what is *first*.
    static func pinPreferredFirst(_ centers: [ServiceCenter], for driverId: String?, defaults: UserDefaults = .standard) -> [ServiceCenter] {
        guard let preferred = current(for: driverId, defaults: defaults) else { return centers }
        guard let idx = centers.firstIndex(where: { $0.centerId == preferred.centerId }) else { return centers }
        var out = centers
        out.insert(out.remove(at: idx), at: 0)
        return out
    }
}
