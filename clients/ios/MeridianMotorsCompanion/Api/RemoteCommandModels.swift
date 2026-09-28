import Foundation

// MARK: - Wire models

/// One actuator as advertised by `GET /api/commands/catalog`.
///
/// The catalog is a rich contract — it carries the human label, the value type and
/// its range, the state field to read back, and a per-command timeout — so the
/// client does not hardcode any of that. What it does NOT carry is whether the
/// command can actually actuate; see `RemoteCommandAllowlist`.
struct CommandCatalogEntry: Decodable, Identifiable, Equatable {
    var id: String { commandName }
    let commandName: String
    let label: String
    let category: String
    /// `"boolean"`, `"integer"`, `"float"`, `"string"`.
    let valueType: String?
    /// Bounds arrive as strings in the wire payload (DynamoDB numeric attributes
    /// round-tripped through the Lambda's Decimal encoder), so they are decoded as
    /// strings and converted at the point of use rather than trusted as numbers.
    let min: String?
    let max: String?
    let unit: String?
    let options: [String]?
    let responseTimeout: String?
    /// Live-state field this command mutates, e.g. `all_doors_locked`. The read-back
    /// key for stateful commands.
    let signalField: String?

    var isBoolean: Bool { (valueType ?? "").lowercased() == "boolean" }
}

/// `GET /api/commands/catalog` response.
struct CommandCatalogResponse: Decodable {
    let actuators: [String: [CommandCatalogEntry]]
    let totalCount: Int?
}

/// `POST /api/commands/{vehicleId}` request.
struct SendCommandRequest: Encodable {
    let commandName: String
    /// Deliberately a `String`, not `Bool`/`Int`.
    ///
    /// The backend stores `str(value)` and two producer paths already disagree on
    /// the shape of the same boolean — the simulator emits `1` while the
    /// FleetWise decoder manifest types every CAN signal `FLOAT64` and yields
    /// `1.0`. See `issues/2026-08-04-actuator-value-shape-divergence`. Sending a
    /// Swift `Bool` would add a third shape (`true`) to a defect that already has
    /// two, so the caller states the wire shape explicitly.
    let value: String
    let label: String?
    let category: String?
}

/// `POST /api/commands/{vehicleId}` response.
///
/// `status` is **`SENT`**, which means published to MQTT — not actuated, and not
/// acknowledged. A separate response handler transitions the row afterwards, so UI
/// must not report success on receipt of this.
struct SendCommandResponse: Decodable {
    let success: Bool
    let commandId: String
    let status: String
    let topic: String?
}

/// One row of `GET /api/commands/{vehicleId}`.
struct CommandHistoryEntry: Decodable, Identifiable, Equatable {
    var id: String { commandId }
    let commandId: String
    let commandName: String
    let status: String
    let issuedAt: String?
    let value: String?
    let label: String?

    /// Whether the vehicle has confirmed execution, as opposed to the command
    /// merely having been published.
    var isConfirmed: Bool { status.uppercased() == "SUCCEEDED" }
    var isFailed: Bool {
        ["FAILED", "TIMEOUT", "REJECTED"].contains(status.uppercased())
    }
    /// Published but unconfirmed. Presented as in-flight, never as done.
    var isPending: Bool { !isConfirmed && !isFailed }
}

struct CommandHistoryResponse: Decodable {
    let commands: [CommandHistoryEntry]
    let count: Int?
}

// MARK: - Allowlist

/// The commands this app is willing to offer, and why the catalog is not enough.
///
/// `GET /api/commands/catalog` advertises **48** actuators. The simulator that has
/// to move the signal implements **13** (`_ACTUATOR_MAP` + `_TRANSIENT_ACTUATORS`
/// in `services/simulation/realtime_telemetry_simulator.py`), and the two lists
/// disagree in both directions — 37 advertised commands have no implementation, and
/// two implemented ones (`honk_horn`, `trunk_lock`) are not advertised at all.
///
/// CORRECTION 2026-08-21. An earlier version of this comment also claimed the two sides
/// "disagree on state field names", citing `all_doors_locked` versus `doors_locked`. That
/// was WRONG and is retracted: those are different layers, not a mismatch.
/// `_ACTUATOR_MAP` maps a command to the simulator's internal `VehicleState` ATTRIBUTE
/// (`doors_locked`); the simulator then serialises that to the wire field
/// `allDoorsLocked`, which the catalog aliases to canonical `all_doors_locked` via
/// `deployment/scripts/patch_catalog_actuator_aliases.py`. The chain is intact end to end,
/// and the five "mismatches" were an artefact of comparing an internal attribute name
/// against a catalog field name.
///
/// So a catalog-driven sheet would render 48 controls of which 11 work. This
/// allowlist is the intersection, verified by reading both sources on 2026-08-19.
/// It is a deliberate duplication of server knowledge, and the honest trade: the
/// alternative is shipping 37 controls that accept a tap, return HTTP 200, and do
/// nothing — a failure the user cannot distinguish from a broken car.
///
/// Related tracked defects, same family:
/// - `issues/2026-08-04-actuator-value-shape-divergence` (P2, open)
/// - `issues/2026-08-03-sim-actuator-field-mapping`
///
/// **Maintenance**: when the simulator gains an actuator, add it here. When the
/// divergence is fixed at source, delete this type and drive the sheet from the
/// catalog — that is the desired end state, not this list.
enum RemoteCommandAllowlist {

    /// Commands that mutate persistent state, so success can be confirmed by
    /// reading `signalField` back from live state.
    static let stateful: Set<String> = [
        "lock_all_doors",
        "lock_door_frontleft",
        "lock_door_frontright",
        "lock_door_rearleft",
        "lock_door_rearright",
        "open_charge_door",
        "remote_start",
        "start_preconditioning"
    ]

    /// Momentary commands with no persistent state. The simulator ACKs them
    /// `SUCCEEDED` without mutating anything, so their only honest feedback is the
    /// command's own status — there is nothing to read back, and waiting for a
    /// state change would hang forever.
    static let transient: Set<String> = [
        "find_my_vehicle",
        "flash_hazards",
        "panic_mode"
    ]

    static var all: Set<String> { stateful.union(transient) }

    static func isAllowed(_ commandName: String) -> Bool {
        all.contains(commandName)
    }

    /// Whether feedback for this command must come from its ACK rather than a
    /// state read-back.
    static func isTransient(_ commandName: String) -> Bool {
        transient.contains(commandName)
    }

    /// The subset a companion-app owner would actually reach for, in display order.
    ///
    /// The four per-door commands are allowlisted (they do actuate) but omitted
    /// here: an owner locks the car, not the rear-left door, and four near-identical
    /// controls would crowd out the ones that matter. They remain available to any
    /// caller that wants them.
    static let featured: [String] = [
        "lock_all_doors",
        "remote_start",
        "start_preconditioning",
        "flash_hazards",
        "find_my_vehicle",
        "open_charge_door"
    ]
}
