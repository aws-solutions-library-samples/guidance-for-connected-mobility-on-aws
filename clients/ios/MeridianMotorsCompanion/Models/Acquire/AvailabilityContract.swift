import Foundation

// MARK: - AvailabilityContract (task 5.4)

/// Client-side contract modelling per-option availability and delivery window.
///
/// ## Cross-repo dependency
/// The backend availability/lead-time service is CVX-side and does NOT exist yet
/// (there is one incidental `availability` mention across the acquire Lambdas).
/// This file builds the client half against the contract only; swapping in the
/// real service is a data-source change, not a UI change — the client reads
/// `AvailabilityContract` regardless of where the data comes from.
///
/// Until the service exists, `loadFixture()` returns a clearly-labelled local
/// fixture so the option-gating UI is exercisable on the show floor.
///
/// ## Degradation contract
/// The client ALWAYS degrades to `allAvailable` on a bad or missing response.
/// Failing closed (all-disabled) would grey out the whole configurator on the
/// show floor — that is a worse failure than showing an option that turns out
/// to be slow to build. See task 5.4 constraints.
struct AvailabilityContract: Equatable {

    // MARK: - Delivery window

    enum DeliveryWindow: String, Codable, Equatable {
        /// Standard production slot — typically 6–12 weeks.
        case standard = "standard"
        /// Custom-order slot — typically 12–24 weeks.
        case custom   = "custom"
    }

    // MARK: - Per-option availability

    struct OptionAvailability: Equatable, Identifiable {
        var id: String { optionId }
        let optionId: String
        let available: Bool
        /// Human-readable reason when `available == false`.
        let unavailableReason: String?
        /// Whether this option becomes available when switching to `custom` window.
        let availableOnCustom: Bool
    }

    // MARK: - Contract fields

    let deliveryWindow: DeliveryWindow
    let colorOptions: [OptionAvailability]
    let interiorStyleOptions: [OptionAvailability]

    // MARK: - Convenience

    /// A contract where every option is available — used as the fallback
    /// when the backend does not respond or returns an error.
    static let allAvailable = AvailabilityContract(
        deliveryWindow: .standard,
        colorOptions: [],
        interiorStyleOptions: []
    )

    /// Returns availability for a color option id.
    /// Falls back to available when the id is not listed (open-world assumption).
    func availability(forColorId id: String) -> OptionAvailability {
        colorOptions.first(where: { $0.optionId == id })
            ?? OptionAvailability(optionId: id, available: true,
                                  unavailableReason: nil, availableOnCustom: false)
    }

    /// Returns availability for an interior-style id.
    func availability(forStyleId id: String) -> OptionAvailability {
        interiorStyleOptions.first(where: { $0.optionId == id })
            ?? OptionAvailability(optionId: id, available: true,
                                  unavailableReason: nil, availableOnCustom: false)
    }

    /// Returns an updated contract with the delivery window switched to `custom`.
    /// Options that were unavailable on `standard` but available on `custom`
    /// become available.
    func withCustomWindow() -> AvailabilityContract {
        func unlockIfCustom(_ opts: [OptionAvailability]) -> [OptionAvailability] {
            opts.map { opt in
                opt.availableOnCustom
                    ? OptionAvailability(optionId: opt.optionId, available: true,
                                        unavailableReason: nil, availableOnCustom: true)
                    : opt
            }
        }
        return AvailabilityContract(
            deliveryWindow: .custom,
            colorOptions: unlockIfCustom(colorOptions),
            interiorStyleOptions: unlockIfCustom(interiorStyleOptions)
        )
    }

    // MARK: - Local fixture (clearly labelled; swap for real service call)

    /// Returns a clearly-labelled local fixture behind the same contract shape.
    ///
    /// **This is NOT real availability data.** It exists so the option-gating
    /// UI is exercisable on the show floor before the CVX backend service is built.
    /// Replace this with a network call once the service exists — the call site
    /// in `ConfiguratorFlow.loadAvailability()` does not change.
    ///
    /// Returns `nil` only if the fixture JSON is malformed, in which case the
    /// caller degrades to `allAvailable`.
    static func loadFixture() -> AvailabilityContract? {
        // [FIXTURE — NOT REAL AVAILABILITY DATA]
        // Shows one unavailable color (standard window) that opens on custom,
        // and one unavailable interior style (unavailable on both windows) to
        // exercise the full disabled-with-reason path.
        return AvailabilityContract(
            deliveryWindow: .standard,
            colorOptions: [
                OptionAvailability(
                    optionId: "racing-red",
                    available: false,
                    unavailableReason: "Not in current production run",
                    availableOnCustom: true
                )
            ],
            interiorStyleOptions: [
                OptionAvailability(
                    optionId: InteriorStyle.touring.rawValue,
                    available: false,
                    unavailableReason: "Leather supplier lead time: 20 weeks",
                    availableOnCustom: false
                )
            ]
        )
    }
}
