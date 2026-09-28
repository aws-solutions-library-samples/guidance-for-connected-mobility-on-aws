import Foundation

/// Watches the telemetry stream and fires `/triage` when thresholds cross.
/// Debounced so the 2s mock loop doesn't spam the API.
///
/// The classifier is authoritative — this actor only decides *when to ask*,
/// not what the classification is. Thresholds here are intentionally slightly
/// looser than the classifier's (classify at 115°C, ask at 112°C) so we don't
/// miss edge cases.
actor TriageCoordinator {
    private let client: VSAClient
    private let onResult: @Sendable (TriageResponse) async -> Void
    /// Supplier of the current VIN. Called on every triage fire so a
    /// mid-session driver/vehicle change (rare but possible) is honored.
    /// The supplier is @Sendable because this actor can be called from
    /// any task; AppSession.effectiveVin is a main-actor read but
    /// Swift's concurrency model lets a sync closure marshal it.
    private let vinSupplier: @Sendable () -> String

    private var lastFiredAt: Date?
    private var lastSignature: String?
    private let debounceSeconds: TimeInterval = 30
    private var inFlight = false

    init(
        client: VSAClient,
        vinSupplier: @escaping @Sendable () -> String,
        onResult: @escaping @Sendable (TriageResponse) async -> Void
    ) {
        self.client = client
        self.vinSupplier = vinSupplier
        self.onResult = onResult
    }

    /// Evaluate a telemetry frame. If a threshold is crossed and we haven't
    /// fired recently with the same signature, call `/triage`.
    func consider(frame: TelemetryFrame) async {
        guard let signature = thresholdSignature(for: frame) else { return }
        if signature == lastSignature,
           let last = lastFiredAt,
           Date().timeIntervalSince(last) < debounceSeconds {
            return
        }
        guard !inFlight else { return }
        inFlight = true
        defer { inFlight = false }

        do {
            let resp = try await client.postTriage(TriageRequest(
                vin: vinSupplier(),
                sessionId: nil,
                tenantId: VSAConfig.defaultTenantId,
                extra: ["fleet_sla": "tier-1", "trigger": signature]
            ))
            lastFiredAt = Date()
            lastSignature = signature
            await onResult(resp)
        } catch {
            // Leave lastFiredAt nil so we retry on next frame.
            print("Auto-triage failed (\(signature)): \(error.localizedDescription)")
        }
    }

    /// Reset debounce — useful when presenter manually flips scenarios.
    func reset() {
        lastFiredAt = nil
        lastSignature = nil
    }

    // MARK: - Thresholds

    /// Returns a short signature describing why we'd want to triage, or nil if
    /// nothing interesting is happening. Matching signatures within the debounce
    /// window are suppressed.
    private func thresholdSignature(for frame: TelemetryFrame) -> String? {
        if frame.absStatusOk == false {
            return "abs-fault"
        }
        if let c = frame.coolantTempC, c >= 112 {
            return "coolant-high"
        }
        let tires = [frame.tireFlPsi, frame.tireFrPsi, frame.tireRlPsi, frame.tireRrPsi].compactMap { $0 }
        if tires.contains(where: { $0 < 32 }) {
            return "tire-low"
        }
        return nil
    }
}
