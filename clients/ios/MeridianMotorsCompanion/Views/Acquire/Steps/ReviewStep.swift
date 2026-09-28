import SwiftUI

/// Step 6 of ConfiguratorFlow — configuration summary + agent-narrated review.
///
/// Displays a structured summary of the chosen configuration and streams a
/// persona-tuned narration from the AgentCore text runtime via the same
/// POST /assistant/chat endpoint used by CMS web UI and DiscoverFlowModel.
/// No second code path is introduced — request shape and auth mirror
/// DiscoverFlowModel.callAssistantChat exactly.
///
/// ## Narration strategy
/// - Signed-in: LTM context passed via personaId "consumer" (same as DiscoverFlow).
/// - Anonymous: prompt sent without personaContext payload.
/// - Network failure: error handled gracefully — the static fallback copy
///   is shown and the "Continue to Finance" button is never blocked.
///
/// ## Scope constraint (spec: concurrent iPad spec owns summarySection / configRow)
/// This file touches ONLY the narration pane. Do NOT restructure summarySection,
/// configRow, or add pickInteriorStyle / pickIvePackage — those belong to
/// spec 2026-08-04-zone2-ipad-adaptive-kiosk-and-xctest Task 5.6.
struct ReviewStep: View {
    let model: CatalogModel
    let variant: CatalogVariant
    let color: CatalogColor
    let selectedAccessories: [CatalogAccessory]
    let currencySymbol: String
    let vehicleCategoryLabel: String
    let theme: TenantTheme
    let onContinue: () -> Void
    let onBack: () -> Void

    // MARK: - Session for narration calls

    @Environment(AppSession.self) private var session

    // MARK: - Narration state

    /// Streamed / fetched narration text. Starts empty; populated on .task.
    @State private var narrationText: String = ""
    /// True while the narration HTTP call is in flight.
    @State private var isLoadingNarration: Bool = false
    /// Non-nil when the narration call failed; shown as secondary copy so
    /// the Continue button stays enabled.
    @State private var narrationError: String? = nil

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                backRow
                summarySection
                Divider()
                agentNarrationPane
                Divider()
                totalPriceSection
                continueButton
            }
            .padding()
        }
        .task {
            await loadNarration()
        }
    }

    // MARK: - Computed

    private var configuredPrice: Double {
        let base = model.basePrice ?? 0
        let variantAdder = variant.priceAdder ?? 0
        let colorAdder = color.priceAdder ?? 0
        let accessoryTotal = selectedAccessories.compactMap { $0.price }.reduce(0, +)
        return base + variantAdder + colorAdder + accessoryTotal
    }

    // MARK: - Subviews

    @ViewBuilder
    private var backRow: some View {
        Button(action: onBack) {
            Label("Back to accessories", systemImage: "chevron.left")
                .font(.subheadline)
                .foregroundStyle(theme.primary)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var summarySection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Your \(vehicleCategoryLabel) configuration")
                .font(.title3.bold())
            configRow("Model", model.displayName)
            configRow("Trim", variant.displayName)
            configRow("Color", color.displayName)
            if !selectedAccessories.isEmpty {
                configRow("Accessories", selectedAccessories.map { $0.displayName }.joined(separator: ", "))
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    @ViewBuilder
    private func configRow(_ key: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(key)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 90, alignment: .leading)
            Text(value)
                .font(.subheadline)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
    }

    // MARK: - Agent narration pane (replaces static placeholder)

    @ViewBuilder
    private var agentNarrationPane: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                    .foregroundStyle(theme.primary)
                    .font(.caption.bold())
                Text("Assistant summary")
                    .font(.caption.bold())
                    .foregroundStyle(theme.primary)
                Spacer(minLength: 0)
                if isLoadingNarration {
                    ProgressView()
                        .controlSize(.mini)
                        .tint(theme.primary)
                }
            }

            if isLoadingNarration && narrationText.isEmpty {
                // Skeleton / loading state
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(0..<3, id: \.self) { _ in
                        RoundedRectangle(cornerRadius: 4)
                            .fill(theme.primary.opacity(0.10))
                            .frame(maxWidth: .infinity)
                            .frame(height: 12)
                    }
                    RoundedRectangle(cornerRadius: 4)
                        .fill(theme.primary.opacity(0.06))
                        .frame(maxWidth: 200)
                        .frame(height: 12)
                }
            } else if !narrationText.isEmpty {
                // Markdown-safe streamed narration.
                // `sanitizeHtml` attribute-stripping allowlist pattern from DiscoverFlowModel:
                // keep <b>/<i>/<em>/<strong>/<br>/<p>; strip everything else.
                // SwiftUI's `Text(LocalizedStringKey)` renders **bold** and _italic_
                // inline; we split on newlines so visual structure is preserved.
                let sanitized = Self.sanitizeNarration(narrationText)
                let lines = sanitized.split(
                    separator: "\n",
                    omittingEmptySubsequences: false
                )
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        if line.isEmpty {
                            Color.clear.frame(height: 4)
                        } else {
                            Text(LocalizedStringKey(String(line)))
                                .font(.subheadline)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            } else if let errMsg = narrationError {
                // Failed narration — show fallback copy so the step stays usable.
                Text("Tap the mic button to ask about your configuration.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                // Only log in debug; don't expose raw error strings to the UI.
                let _ = NSLog("ReviewStep: narration failed — %@", errMsg)
            } else {
                // Not signed in / anonymous branch
                Text("Sign in to receive a personalised summary, or tap the mic to ask about your configuration.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(theme.primary.opacity(0.06))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(theme.primary.opacity(0.20), lineWidth: 1)
                )
        )
    }

    @ViewBuilder
    private var totalPriceSection: some View {
        HStack {
            Text("Configured price")
                .font(.headline)
            Spacer()
            Text("\(currencySymbol)\(Int(configuredPrice))")
                .font(.title3.bold())
                .foregroundStyle(theme.primary)
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.secondarySystemGroupedBackground))
        )
    }

    @ViewBuilder
    private var continueButton: some View {
        Button(action: onContinue) {
            Text("Continue to Finance")
                .bold()
                .frame(maxWidth: .infinity, minHeight: 32)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(theme.primary)
        // Continue is NEVER blocked by narration state — a failed narration
        // must not prevent the user from proceeding (spec acceptance criterion).
    }

    // MARK: - Narration fetch

    /// Fetches a persona-tuned narration by POSTing to the same
    /// POST /assistant/chat endpoint used by CMS web UI and DiscoverFlowModel.
    /// No second code path — same URL, same request shape, same auth.
    private func loadNarration() async {
        // Anonymous branch: skip the call, show static fallback.
        guard case .signedIn(let token, _) = session.authState else { return }

        isLoadingNarration = true
        defer { isLoadingNarration = false }

        do {
            let text = try await callAssistantChat(token: token)
            await MainActor.run {
                narrationText = text
                narrationError = nil
            }
        } catch {
            // Network failure must not block Continue — just show fallback.
            await MainActor.run {
                narrationError = error.localizedDescription
                narrationText = ""
            }
        }
    }

    /// Build a prompt that asks the agent to narrate the current configuration
    /// in a persona-tuned way. Includes all selection details so the agent has
    /// the full context without needing a prior conversation history.
    private func buildNarrationPrompt() -> String {
        var accessories = selectedAccessories.map { $0.displayName }
        let accLine = accessories.isEmpty
            ? "No accessories selected."
            : "Accessories: \(accessories.joined(separator: ", "))."

        return """
        The customer has just finished configuring their vehicle. Provide a \
        brief, warm, persona-tuned summary of their configuration in 2–3 sentences. \
        Highlight what makes this combination a good choice. Do not ask any \
        questions — this is a summary, not a conversation turn.

        Configuration details:
        - \(vehicleCategoryLabel): \(model.displayName)
        - Trim: \(variant.displayName)
        - Color: \(color.displayName)
        - \(accLine)
        - Configured price: \(currencySymbol)\(Int(configuredPrice))
        """
    }

    /// Calls POST /assistant/chat with the same request shape as DiscoverFlowModel.
    /// Uses a one-off session ID (review step is not part of the Discover session).
    private func callAssistantChat(token: String) async throws -> String {
        let url = VSAConfig.restApiUrl
            .appendingPathComponent("assistant")
            .appendingPathComponent("chat")

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 30

        // Session ID format mirrors DiscoverFlowModel: "rev_" + UUID (41 chars ≥ 33 minimum).
        let reviewSessionId = "rev_\(UUID().uuidString)"

        let body = AssistantChatRequest(
            prompt: buildNarrationPrompt(),
            // No visitor utterance exists on this path — the narration prompt is
            // entirely app-authored, and the configuration selections are app
            // state rather than anything the customer typed. So there is no
            // untrusted span to hand the guardrail, and this takes the
            // fallback: the runtime guards the whole prompt.
            //
            // That prompt is currently REFUSED by the kiosk guardrail for the
            // same reason the Discover handoff was — it opens with instructions
            // ("Provide a brief... Do not ask any questions"), which the
            // PROMPT_ATTACK content filter reads as an injection attempt.
            // Verified against the deployed staging guardrail 2026-08-18. The
            // failure is silent here: `loadNarration` catches it and renders the
            // fallback, so the step still works without its narration.
            //
            // Not fixable by populating this field, because there is nothing
            // honest to put in it. The fix is to stop sending instructions in
            // the user-prompt slot at all — tracked as the durable fix in
            // `cvx/issues/2026-08-18-kiosk-guardrail-blocks-own-prompt-scaffolding/`.
            userText: nil,
            runtimeSessionId: reviewSessionId,
            tenantId: session.activeTenantId,
            personaId: "consumer",
            tenantSegment: session.tenantConfig?.segment,
            vehicleId: session.effectiveVehicleId,
            tenantDisplayName: session.tenantConfig?.displayName,
            distanceUnit: session.tenantConfig?.distanceUnit,
            personaContext: buildPersonaPayload()
        )

        request.httpBody = try JSONEncoder().encode(body)

        let (data, resp) = try await URLSession.shared.data(for: request)
        guard let http = resp as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            let bodyText = String(data: data, encoding: .utf8) ?? ""
            throw URLError(.badServerResponse, userInfo: [
                NSLocalizedDescriptionKey: "HTTP \(http.statusCode): \(bodyText.prefix(120))"
            ])
        }

        // Decode via AssistantChatResponse (same type DiscoverFlowModel uses).
        if let decoded = try? JSONDecoder().decode(AssistantChatResponse.self, from: data) {
            return decoded.message
        }
        return String(data: data, encoding: .utf8) ?? ""
    }

    /// Persona payload for LTM context. Nil when no persona snapshot is present
    /// (anonymous session or pre-Discover cold entry).
    private func buildPersonaPayload() -> AssistantChatRequest.PersonaContextPayload? {
        // ReviewStep has no DiscoverFlowModel, so we use basic session info only.
        // A signed-in user gets their Cognito identity forwarded; LTM is server-side.
        guard case .signedIn = session.authState else { return nil }
        let identity = Self.ownIdentity(session: session)
        return AssistantChatRequest.PersonaContextPayload(
            actorId: identity.actorId,
            engagementSegment: session.layoutSegment.rawValue,
            currentVehicleHint: identity.vehicleHint,
            statedInterest: "vehicle configuration review",
            sourcePath: "pathA",
            pathBActive: session._pathBActive
        )
    }

    // MARK: - HTML sanitisation (attribute-stripping allowlist)

    /// Strip unsafe HTML, keeping only inline formatting tags.
    /// Mirrors the `sanitizeHtml` allowlist used by DiscoverFlowModel and
    /// the CMS web UI ChatAgent (spec: "reuse the sanitizeHtml attribute-stripping
    /// allowlist pattern that DiscoverFlowModel already implements").
    ///
    /// Allowed: <b>, <i>, <em>, <strong>, <br>, <p> (and their closing forms).
    /// All other tags are stripped. Attributes are stripped from allowed tags.
    private static func sanitizeNarration(_ text: String) -> String {
        // Strip all tags except the safe inline set (without attributes).
        let tagPattern = "<(?!/?(?:b|i|em|strong|br|p)(?:\\s|>|/))[^>]+>"
        guard let regex = try? NSRegularExpression(pattern: tagPattern, options: [.caseInsensitive]) else {
            return text
        }
        let range = NSRange(text.startIndex..., in: text)
        var result = regex.stringByReplacingMatches(in: text, options: [], range: range, withTemplate: "")

        // Strip attributes from the allowed tags themselves, e.g. <b class="x"> → <b>.
        let attrPattern = "<(b|i|em|strong|br|p)\\s[^>]*>"
        if let attrRegex = try? NSRegularExpression(pattern: attrPattern, options: [.caseInsensitive]) {
            let r2 = NSRange(result.startIndex..., in: result)
            result = attrRegex.stringByReplacingMatches(in: result, options: [], range: r2, withTemplate: "<$1>")
        }

        // Convert <br> and <p> to newlines for SwiftUI line-split rendering.
        result = result
            .replacingOccurrences(of: "<br>", with: "\n", options: .caseInsensitive)
            .replacingOccurrences(of: "<br/>", with: "\n", options: .caseInsensitive)
            .replacingOccurrences(of: "</p>", with: "\n", options: .caseInsensitive)
            .replacingOccurrences(of: "<p>", with: "", options: .caseInsensitive)

        return result.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

extension ReviewStep {
    /// The signed-in user's own driver id and VIN for the persona payload, each nil
    /// when unknown. Not `effectiveDriverId` / `effectiveVin`: those fall back to the
    /// demo DRV-0055 / VEH-0025 pair, which would hand a shopper with no driver record
    /// another person's identity (CMS issue
    /// 2026-09-27-ios-alerts-tab-stuck-without-vehicle-context).
    static func ownIdentity(session: AppSession) -> (actorId: String?, vehicleHint: String?) {
        let actorId = session.currentDriver?.driverId
        let vin = session.currentVehicle?.vin
        return ((actorId ?? "").isEmpty ? nil : actorId, (vin ?? "").isEmpty ? nil : vin)
    }
}
