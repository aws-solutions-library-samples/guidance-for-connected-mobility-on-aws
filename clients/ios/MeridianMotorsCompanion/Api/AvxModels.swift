import Foundation

// MARK: - AVX contract models
//
// Derived field-by-field from the byte-identical vendored schemas at
// `MeridianMotorsCompanionTests/Fixtures/avx/{finding,action}.schema.json`.
//
// These types are **owned by AVX core** (CVX repo `agents/tier2/schemas/`) and are
// inherited here by reference. Do NOT add a field, rename one, or widen an enum
// without a corresponding change in AVX core — `FixtureHashesTests` pins the schema
// bytes and will fail on drift. If iOS needs a shape AVX core did not ship, that is
// an `[!]` and a PO conversation, per `spec.md` § Constraints.
//
// Naming: Swift properties are camelCase with an explicit `CodingKeys` mapping to the
// contract's snake_case. `CodingKeys` is written out rather than relying on
// `.convertFromSnakeCase` deliberately — the mapping is part of the contract surface,
// and a key-strategy change elsewhere in the app must not be able to silently
// re-map these.

// MARK: - Lossless arbitrary JSON

/// Represents the contract's genuinely untyped fields without losing information.
///
/// Two fields are schema-typed as free-form: `evidence[].value` (no `type` at all)
/// and `Action.parameters` (`type: object`, no declared properties). Modelling either
/// as `[String: String]` would decode today's fixtures and silently corrupt anything
/// nested or numeric; modelling them as `[String: Any]` is not `Codable`.
///
/// `integer` is a separate case from `number` on purpose: JSON `1124` must re-encode
/// as `1124`, not `1124.0`. Collapsing both into `Double` passes a decode test and
/// then fails a byte- or semantic-equality round-trip, which is exactly what Task
/// 1.4's round-trip assertion is for.
public enum AvxJSONValue: Codable, Equatable, Sendable {
    case string(String)
    case integer(Int)
    case number(Double)
    case bool(Bool)
    case object([String: AvxJSONValue])
    case array([AvxJSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
            return
        }
        // Bool before Int: `JSONDecoder` will happily decode `true` as `1` on some
        // platforms, which would round-trip a boolean out as a number.
        if let v = try? c.decode(Bool.self) {
            self = .bool(v)
        } else if let v = try? c.decode(Int.self) {
            self = .integer(v)
        } else if let v = try? c.decode(Double.self) {
            self = .number(v)
        } else if let v = try? c.decode(String.self) {
            self = .string(v)
        } else if let v = try? c.decode([AvxJSONValue].self) {
            self = .array(v)
        } else if let v = try? c.decode([String: AvxJSONValue].self) {
            self = .object(v)
        } else {
            throw DecodingError.dataCorruptedError(
                in: c, debugDescription: "Unrepresentable JSON value in AVX payload"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let v): try c.encode(v)
        case .integer(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        }
    }
}

// MARK: - Enums (all pinned by FixtureHashesTests)

/// `Action.status` — eight values, per AVX core addenda D4 (`dismissed`) and D5
/// (`declined`, `failed`).
///
/// Decoding is **strict**: an unrecognised status fails rather than falling back to a
/// default. That is deliberate. The UI contract is that all eight render distinctly
/// (`dismissed` is not `cancelled`; `failed` is not `declined`), so a silent fallback
/// would reintroduce precisely the collapse the addenda exist to prevent — and it
/// would do so invisibly, on a card the owner is reading to decide what to do. A loud
/// decode failure is recoverable; a mislabelled terminal state is not.
public enum AvxActionStatus: String, Codable, CaseIterable, Sendable {
    case pendingExecution = "pending_execution"
    case executed
    case inProgress = "in_progress"
    case completed
    case cancelled
    case declined
    case dismissed
    case failed
}

public enum AvxTargetSystem: String, Codable, CaseIterable, Sendable {
    case dms
    case vehicleCommand = "vehicle-command"
    case retail
    case none
    case chat
    /// Inbound direction: a dealer-originated proposal surfaced to the owner.
    /// Rendered by `AVXIosOwnerActionCard`, not offered in the owner's approve picker.
    case iosOwner = "ios-owner"
}

public enum AvxActionKind: String, Codable, CaseIterable, Sendable {
    case bookCombinedVisit = "book_combined_visit"
    case scheduleInspection = "schedule_inspection"
    case updateChargingSchedule = "update_charging_schedule"
    case fileWarrantyPre = "file_warranty_pre"
    case surfaceRecall = "surface_recall"
    case acknowledgeOnly = "acknowledge_only"
}

/// Finding severity. `p0` is the safety-critical band whose `headline` and `detail`
/// MUST render verbatim — see `spec.md` § Deterministic-narration guards.
public enum AvxSeverity: String, Codable, CaseIterable, Sendable {
    case p0
    case attention
    case soon
    case routine
    case informational

    /// True when this finding's narration is safety-critical and must not be
    /// truncated, ellipsised, or softened by the client.
    public var requiresVerbatimRendering: Bool { self == .p0 }
}

public enum AvxConfidence: String, Codable, CaseIterable, Sendable {
    case low, medium, high
}

public enum AvxAgentId: String, Codable, CaseIterable, Sendable {
    case agent2VehicleHealth = "agent-2-vehicle-health"
    case agent1LiteEnergy = "agent-1-lite-energy"
    case agent3OwnershipCost = "agent-3-ownership-cost"
    case agent4LiteConcierge = "agent-4-lite-concierge"
}

public enum AvxFindingKind: String, Codable, CaseIterable, Sendable {
    case healthTires = "health.tires"
    case healthBrakes = "health.brakes"
    case healthBattery = "health.battery"
    /// Active fault codes outside tires, brakes and battery (spec
    /// `2026-09-25-avx-own-vehicle-findings`). Added at CVX commit 998d1aa.
    case healthDiagnostics = "health.diagnostics"
    case energyCharging = "energy.charging"
    case energyCost = "energy.cost"
    case coverageWarranty = "coverage.warranty"
    case coverageRecall = "coverage.recall"
    case loyaltyPoints = "loyalty.points"
    case loyaltyExpiry = "loyalty.expiry"
    case conciergeAppointment = "concierge.appointment"
    case conciergeService = "concierge.service"
    /// Fallback for a `finding_kind` value the client does not yet recognise.
    ///
    /// Decodes to `.unknown` rather than failing the whole Findings list, so a
    /// server-side schema addendum can introduce a new kind without crashing older
    /// clients. The card renders a neutral generic representation (no category-
    /// specific icon, no crash). **Known-value encoding is unchanged**: `.unknown`
    /// never appears as an output from this client because we never write Finding
    /// payloads; if it somehow did, it would encode as the raw string `"unknown"`,
    /// which is not a valid AVX core value — that behaviour is intentional (the
    /// client is the reader, not the writer).
    case unknown

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let raw = try container.decode(String.self)
        self = AvxFindingKind(rawValue: raw) ?? .unknown
    }
}

/// Evidence provenance. Rendered with a visual distinction per parent PRD § M14 —
/// `mocked` and `absent` must not present as `live`.
public enum AvxProvenance: String, Codable, CaseIterable, Sendable {
    case live, mocked, absent
}

public enum AvxFailureKind: String, Codable, CaseIterable, Sendable {
    case targetRefused = "target_refused"
    case executorExhaustedRetries = "executor_exhausted_retries"
    case executorTerminalError = "executor_terminal_error"
}

// MARK: - Shared sub-objects

/// `$defs/evidence_entry`. Shared by both Finding and Action.
public struct AvxEvidence: Codable, Equatable, Sendable {
    public let kind: String
    public let source: String
    public let sourceRef: String
    public let value: AvxJSONValue
    public let computedAt: String
    public let provenance: AvxProvenance
    public let vin: String?

    enum CodingKeys: String, CodingKey {
        case kind
        case source
        case sourceRef = "source_ref"
        case value
        case computedAt = "computed_at"
        case provenance
        case vin
    }
}

/// `$defs/scope` on Finding. Every field is optional in the schema; the schema's own
/// `allOf` constrains which combinations are legal, which `Codable` cannot express —
/// `finding_scope_two_fields_INVALID.json` is the vendored counter-example.
public struct AvxScope: Codable, Equatable, Sendable {
    public let vin: String?
    public let ownerId: String?
    public let fleetId: String?
    public let dealershipId: String?
    public let districtId: String?

    enum CodingKeys: String, CodingKey {
        case vin
        case ownerId = "owner_id"
        case fleetId = "fleet_id"
        case dealershipId = "dealership_id"
        case districtId = "district_id"
    }
}

/// `$defs/finding_audit_entry` — tracks re-computation, so it carries
/// `from_expires_at` / `to_expires_at` rather than a status transition.
public struct AvxFindingAuditEntry: Codable, Equatable, Sendable {
    public let at: String
    public let agentVersion: String
    public let inputsHash: String
    public let fromExpiresAt: String
    public let toExpiresAt: String
    public let note: String?

    enum CodingKeys: String, CodingKey {
        case at
        case agentVersion = "agent_version"
        case inputsHash = "inputs_hash"
        case fromExpiresAt = "from_expires_at"
        case toExpiresAt = "to_expires_at"
        case note
    }
}

/// `$defs/audit_entry` on Action — a status transition, optionally with a failure
/// classification and retry count (addendum D5).
public struct AvxActionAuditEntry: Codable, Equatable, Sendable {
    public let at: String
    public let by: String
    public let fromStatus: AvxActionStatus
    public let toStatus: AvxActionStatus
    public let note: String?
    public let failureKind: AvxFailureKind?
    public let retryCount: Int?

    enum CodingKeys: String, CodingKey {
        case at
        case by
        case fromStatus = "from_status"
        case toStatus = "to_status"
        case note
        case failureKind = "failure_kind"
        case retryCount = "retry_count"
    }
}

// MARK: - Finding

/// A Tier 2 agent's finding. Carries its own evidence and staleness, per the Tier 2
/// artifact contract in `~/.kiro/steering/agentic-tiers.md`.
///
/// The client is inert with respect to this content: it renders what the API returned
/// and never regenerates, summarises, or re-words it.
public struct AvxFinding: Codable, Equatable, Sendable, Identifiable {
    public let findingId: String
    public let findingKey: String
    public let agentId: AvxAgentId
    public let agentVersion: String
    public let scope: AvxScope
    public let severity: AvxSeverity
    public let computedAt: String
    public let expiresAt: String
    public let confidence: AvxConfidence
    public let findingKind: AvxFindingKind
    public let headline: String
    public let detail: String
    public let evidence: [AvxEvidence]
    public let inputsHash: String
    public let tokensUsed: Int
    public let costUsd: Double
    /// Present when the finding carries mandated disclosure text. Rendered verbatim.
    public let disclosureTxt: String?
    public let auditTrail: [AvxFindingAuditEntry]

    /// `Identifiable` conformance for SwiftUI lists. Maps to the contract's
    /// `finding_id`; it is NOT a rename of that field.
    public var id: String { findingId }

    enum CodingKeys: String, CodingKey {
        case findingId = "finding_id"
        case findingKey = "finding_key"
        case agentId = "agent_id"
        case agentVersion = "agent_version"
        case scope
        case severity
        case computedAt = "computed_at"
        case expiresAt = "expires_at"
        case confidence
        case findingKind = "finding_kind"
        case headline
        case detail
        case evidence
        case inputsHash = "inputs_hash"
        case tokensUsed = "tokens_used"
        case costUsd = "cost_usd"
        case disclosureTxt = "disclosure_txt"
        case auditTrail = "audit_trail"
    }
}

// MARK: - Action

/// An owner- or dealer-originated action derived from a Finding.
public struct AvxAction: Codable, Equatable, Sendable, Identifiable {
    public let actionId: String
    public let findingId: String
    public let createdBy: String
    public let createdAt: String
    public let updatedAt: String
    public let targetSystem: AvxTargetSystem
    /// e.g. a DMS repair-order reference. Absent until the executor returns one.
    public let targetRef: String?
    public let status: AvxActionStatus
    public let actionKind: AvxActionKind
    public let parameters: AvxJSONValue
    public let evidence: [AvxEvidence]
    public let auditTrail: [AvxActionAuditEntry]

    public var id: String { actionId }

    enum CodingKeys: String, CodingKey {
        case actionId = "action_id"
        case findingId = "finding_id"
        case createdBy = "created_by"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case targetSystem = "target_system"
        case targetRef = "target_ref"
        case status
        case actionKind = "action_kind"
        case parameters
        case evidence
        case auditTrail = "audit_trail"
    }
}

// MARK: - AvxAction local-mutation helpers

extension AvxAction {
    /// Returns a copy of this action with a new `status`.
    ///
    /// Used by `AVXCardsViewModel` for optimistic local state transitions
    /// (dismiss → `.dismissed`) before the server response arrives.
    func withStatus(_ newStatus: AvxActionStatus) -> AvxAction {
        AvxAction(
            actionId:     actionId,
            findingId:    findingId,
            createdBy:    createdBy,
            createdAt:    createdAt,
            updatedAt:    ISO8601DateFormatter().string(from: Date()),
            targetSystem: targetSystem,
            targetRef:    targetRef,
            status:       newStatus,
            actionKind:   actionKind,
            parameters:   parameters,
            evidence:     evidence,
            auditTrail:   auditTrail
        )
    }
}
