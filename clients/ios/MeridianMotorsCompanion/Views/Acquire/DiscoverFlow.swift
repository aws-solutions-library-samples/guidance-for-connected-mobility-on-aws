import SwiftUI

// MARK: - XSS-safe markdown rendering helper

/// Attribute-stripping sanitizeHtml pattern.
///
/// Mirrors the allowlist from `2026-06-19-cvx-web-ui-surfacing` spec § Constraints:
/// permits only `<b>`, `<i>`, `<em>`, `<strong>`, `<br>`, and `<p>`.
/// All other HTML tags are stripped to prevent XSS.
/// Strip unsafe HTML from agent-produced markdown before rendering.
///
/// Two passes, deliberately. Pass 1 removes any tag outside the safe inline
/// allowlist; pass 2 strips attributes from the allowed tags themselves, so
/// `<b onerror="…">` becomes `<b>` rather than surviving intact.
///
/// Pass 2 was added 2026-08-05 to align this with
/// `ReviewStep.sanitizeNarration`, which already had it. The single-pass
/// version here removed disallowed tags but left attributes on the allowed
/// ones — an asymmetry flagged as a hardening Suggestion by security-review
/// cycle 3 of spec `2026-08-04-cvx-oem-discover-order-meridian`.
///
/// Not currently exploitable: both call sites render through SwiftUI `Text`,
/// which does not interpret HTML or execute script. The fix matters because
/// that inertness is a property of the *renderer*, not of this function — if
/// either surface later moves to `AttributedString`, markdown-with-HTML, or a
/// web view, the weaker sanitizer becomes the exploitable one. Two sanitizers
/// guarding the same class of input should not differ in strength.
/// Flatten GitHub-style markdown tables into lines the chat bubble can render.
///
/// The bubble parses markdown with `.inlineOnlyPreservingWhitespace`, which
/// handles `**bold**` and `_italic_` but has no table support — so a table
/// arrives as literal `|---|---|` pipe soup. The agent reaches for tables
/// unprompted when it has three dealers with slots and ratings, which is exactly
/// the test-drive answer.
///
/// Flattening rather than deleting: the cells are the answer. A table like
///
///     | Dealer | Next Slots | Rating |
///     |---|---|---|
///     | **Meridian of Nashville** | Wed 1 PM | 4.7 |
///
/// becomes
///
///     **Meridian of Nashville** — Next Slots: Wed 1 PM · Rating: 4.7
///
/// The first column leads (it is the thing being listed) and the remaining cells
/// become `Label: value`, so no information is lost and it reads in a narrow
/// column. Done client-side on purpose: a prompt instruction is advice the model
/// may drop on any given turn, whereas this holds for whatever arrives.
func flattenMarkdownTables(_ raw: String) -> String {
    guard raw.contains("|") else { return raw }

    func cells(_ line: String) -> [String] {
        var t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") { t.removeLast() }
        return t.components(separatedBy: "|").map {
            $0.trimmingCharacters(in: .whitespaces)
        }
    }

    // A separator row is all dashes/colons/spaces between the pipes.
    func isSeparator(_ line: String) -> Bool {
        let c = cells(line)
        guard !c.isEmpty, line.contains("-") else { return false }
        return c.allSatisfy { cell in
            !cell.isEmpty && cell.allSatisfy { $0 == "-" || $0 == ":" || $0 == " " }
        }
    }

    let lines = raw.components(separatedBy: .newlines)
    var out: [String] = []
    var i = 0
    while i < lines.count {
        let line = lines[i]
        // Header + separator + >=0 body rows is the only shape treated as a table.
        // Requiring the separator means a sentence containing a stray "|" is
        // left completely alone.
        if line.contains("|"), i + 1 < lines.count, isSeparator(lines[i + 1]) {
            let headers = cells(line)
            i += 2
            while i < lines.count, lines[i].contains("|"), !isSeparator(lines[i]) {
                let row = cells(lines[i])
                let lead = row.first.map { $0 } ?? ""
                var parts: [String] = []
                for (idx, cell) in row.enumerated() where idx > 0 {
                    let value = cell.trimmingCharacters(in: .whitespaces)
                    guard !value.isEmpty, value != "-" else { continue }
                    let label = idx < headers.count
                        ? headers[idx].trimmingCharacters(in: .whitespaces)
                        : ""
                    parts.append(label.isEmpty ? value : "\(label): \(value)")
                }
                if lead.isEmpty && parts.isEmpty {
                    // Nothing to render — drop the empty row rather than
                    // emitting a bare separator.
                } else if parts.isEmpty {
                    out.append(lead)
                } else if lead.isEmpty {
                    out.append(parts.joined(separator: " · "))
                } else {
                    out.append("\(lead) — \(parts.joined(separator: " · "))")
                }
                i += 1
            }
            continue
        }
        out.append(line)
        i += 1
    }
    return out.joined(separator: "\n")
}

private func sanitizeMarkdown(_ raw: String) -> String {
    // Pass 0 — flatten tables the renderer cannot draw. Before tag stripping so
    // the table shape is still intact when it is matched.
    let raw = flattenMarkdownTables(raw)

    // Pass 1 — strip every tag except the safe inline set.
    // The `(?:\s|>|/)` lookahead boundary also covers self-closing shapes
    // like `<b/>`, which an `(?:\s|/?>)`-style boundary let through.
    let tagPattern = "<(?!/?(?:b|i|em|strong|br|p)(?:\\s|>|/))[^>]+>"
    guard let tagRegex = try? NSRegularExpression(pattern: tagPattern, options: [.caseInsensitive]) else {
        // Fail closed: if the pattern will not compile, drop every angle-bracket
        // construct rather than returning the raw string unfiltered.
        return raw.replacingOccurrences(of: "<[^>]*>", with: "", options: .regularExpression)
    }
    var result = tagRegex.stringByReplacingMatches(
        in: raw,
        options: [],
        range: NSRange(raw.startIndex..., in: raw),
        withTemplate: ""
    )

    // Pass 2 — strip attributes from the allowed tags, e.g. `<p style="…">` → `<p>`.
    let attrPattern = "<(b|i|em|strong|br|p)\\s[^>]*>"
    if let attrRegex = try? NSRegularExpression(pattern: attrPattern, options: [.caseInsensitive]) {
        result = attrRegex.stringByReplacingMatches(
            in: result,
            options: [],
            range: NSRange(result.startIndex..., in: result),
            withTemplate: "<$1>"
        )
    }

    return result
}

// MARK: - DiscoverFlow

/// Voice-first conversational catalog exploration view.
///
/// Entry point for the Buy tab. On convergence ("Let's build this"),
/// fires `onConverge(handoff:)` to hand off to `ConfiguratorFlow`.
///
/// Persona hydration:
///   - anonymous (default): opens cold, no LTM read
///   - signed-in: `AppSession.authState` is `.signedIn` → LTM hydration
///     fires on `.onAppear` via the persona hydration branch
///   - Path B mock: `session.pathBActive == true` → warm-start with seeded
///     persona snapshot from `session.personaHint`
///
/// Callback signatures:
///   - `onConverge(_ handoff: DiscoverHandoff)` — user tapped "Let's build this";
///     present ConfiguratorFlow with the handoff.
///   - `onLeadCaptured(_ leadId: String)` — lead_capture completed without
///     navigation; show toast.
///   - `onDismiss()` — user tapped the close button.
struct DiscoverFlow: View {
    @Environment(AppSession.self) private var session

    /// Tenant-aware visual theme.
    let theme: TenantTheme

    /// Called when the user is ready to configure — fires with the STAR handoff.
    var onConverge: (DiscoverHandoff) -> Void = { _ in }
    /// Called when lead capture succeeds without navigation.
    var onLeadCaptured: (String) -> Void = { _ in }
    /// Called when the user closes the flow.
    var onDismiss: () -> Void = {}
    /// Called when user taps the voice button in-flow.
    /// Passes (discoverSessionId, focusedModelId?) so the parent can open
    /// AssistantTabView with Discover context (Group 4 Task 1).
    var onOpenAssistant: ((String, String?) -> Void)? = nil
    /// Opening prompt supplied directly by the presenter, taking precedence over
    /// `AppSession.pendingDiscoverPrompt`.
    ///
    /// Exists because routing an opener through the shared session field is
    /// order-dependent and lost prompts. `BuyLandingView` wrote the field in this
    /// view's `.onAppear` while `autoOpenConversation()` reads it from this view's
    /// `.task` — and `.task` can run first, so the field was still nil and the flow
    /// fell through to its generic "I'm thinking about upgrading…" opener. Observed
    /// 2026-08-19: tapping "Book a test drive" opened a conversation about upgrading.
    ///
    /// A parameter has no ordering to get wrong. The session field is retained for
    /// the cross-tab path (`UpgradeFlow` writes it, then asks its caller to switch
    /// tabs), where there is no direct init to pass through.
    var initialPrompt: String? = nil

    // MARK: - State

    @StateObject private var model: DiscoverFlowModel

    /// Controls the lead-capture modal for "Save this / send me info".
    @State private var showsLeadCaptureModal: Bool = false
    /// Controls convergence into ConfiguratorFlow (triggered by "Let's build this").
    @State private var pendingHandoff: DiscoverHandoff? = nil
    /// Toast message shown after lead capture or Path B warm-start.
    @State private var toastMessage: String? = nil

    /// Guards `autoOpenConversation()` so the opening agent turn fires at most
    /// once per presentation. `.task` can re-run (e.g. the view is re-created
    /// when the Buy tab is re-selected), and a second auto-open would inject a
    /// duplicate customer line into an in-progress conversation.
    @State private var hasAutoOpened: Bool = false
    /// Dismisses the toast automatically after a delay.
    @State private var toastTask: Task<Void, Never>? = nil

    // MARK: - Init

    init(theme: TenantTheme,
         session: AppSession,
         onConverge: @escaping (DiscoverHandoff) -> Void = { _ in },
         onLeadCaptured: @escaping (String) -> Void = { _ in },
         onOpenAssistant: ((String, String?) -> Void)? = nil,
         onDismiss: @escaping () -> Void = {},
         initialPrompt: String? = nil) {
        self.theme = theme
        self.onConverge = onConverge
        self.onLeadCaptured = onLeadCaptured
        self.onOpenAssistant = onOpenAssistant
        self.onDismiss = onDismiss
        self.initialPrompt = initialPrompt

        // Build the model — cannot use @Environment(AppSession.self) in init,
        // so we read what we need from the passed-in session.
        let acquireConfig = session.tenantConfig?.acquire
        let tenantId = session.activeTenantId
        let idTokenProvider: () -> String? = {
            if case .signedIn(let token, _) = session.authState { return token }
            return nil
        }
        _model = StateObject(wrappedValue: DiscoverFlowModel(
            tenantId: tenantId,
            tenantSegment: session.tenantConfig?.segment,
            tenantDisplayName: session.tenantConfig?.displayName,
            distanceUnit: session.tenantConfig?.distanceUnit,
            acquireConfig: acquireConfig,
            idTokenProvider: idTokenProvider
        ))
    }

    // MARK: - Body

    var body: some View {
        NavigationStack {
            ZStack(alignment: .top) {
                Color(.systemGroupedBackground)
                    .ignoresSafeArea()

                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 16) {
                            // 2 — Voice affordance
                            voiceAffordanceSection

                            // 3 — Primed prompt chips (hidden once conversation starts)
                            if model.conversation.isEmpty {
                                primedPromptsSection
                            }

                            // 4 — Agent narration pane
                            advisorStatusBanner

                            if !model.conversation.isEmpty {
                                conversationSection(proxy: proxy)
                            }

                            // 5 — Evolving catalog card grid
                            if !model.cards.isEmpty {
                                catalogGridSection
                            }

                            // Offer banners
                            if !model.offers.isEmpty {
                                offersSection
                            }

                            // 6 — Convergence CTAs
                            if model.showsConvergenceCTAs {
                                convergenceCTAsSection
                            }

                            // Error display
                            if let err = model.lastError {
                                Text(err)
                                    .font(.caption)
                                    .foregroundStyle(.red)
                                    .padding(.horizontal)
                            }

                            // Bottom spacer for the text input bar
                            Color.clear.frame(height: 72)
                        }
                        .padding(.top, 8)
                        .onChange(of: model.conversation.count) { _ in
                            scrollToBottom(proxy: proxy)
                        }
                    }
                }
                .safeAreaInset(edge: .bottom) {
                    textInputBar
                        .padding(.horizontal)
                        .padding(.vertical, 8)
                        .background(.ultraThinMaterial)
                }
            }
            .navigationTitle(model.pathBActive ? returningOwnerTitle : "")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .sheet(isPresented: $showsLeadCaptureModal) {
                leadCaptureModalView
            }
        }
        .overlay(alignment: .bottom) {
            if let msg = toastMessage {
                toastView(msg)
                    .padding(.bottom, 96)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .animation(.easeInOut(duration: 0.3), value: toastMessage)
            }
        }
        .task { await hydratePersona() }
    }

    // MARK: - 1 Header (embedded in toolbar)

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            VStack(alignment: .leading, spacing: 2) {
                Text(model.greetingText)
                    .font(.headline)
                    .lineLimit(2)
                if model.pathBActive {
                    Label("Returning owner", systemImage: "person.crop.circle.badge.checkmark")
                        .font(.caption)
                        .foregroundStyle(theme.primary)
                }
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 14, weight: .semibold))
            }
            .accessibilityLabel("Close")
        }
    }

    private var returningOwnerTitle: String { "Welcome Back" }

    // MARK: - 2 Voice affordance

    private var voiceAffordanceSection: some View {
        VStack(spacing: 10) {
            Button(action: openVoiceAssistant) {
                HStack(spacing: 10) {
                    ZStack {
                        Circle()
                            .fill(theme.primary)
                            .frame(width: 44, height: 44)
                        Image(systemName: "mic.fill")
                            .font(.system(size: 18))
                            .foregroundStyle(.white)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Tap and speak")
                            .font(.headline)
                            .foregroundStyle(Color(.label))
                        Text("Voice-first — ask anything about our catalog")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(14)
                .background(Color(.secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .shadow(color: .black.opacity(0.06), radius: 3, x: 0, y: 2)
            }
            .buttonStyle(.plain)
            .padding(.horizontal)
            .accessibilityLabel("Open voice assistant for catalog exploration")
        }
    }

    private func openVoiceAssistant() {
        // Pass discoverSessionId + focusedModelId so AssistantTabView
        // continues the in-flight Discover conversation rather than
        // starting a fresh Service-triage turn (Group 4 Task 1).
        if let cb = onOpenAssistant {
            cb(model.discoverSessionId, model.focusedModelId)
        } else {
            // Fallback: dismiss DiscoverFlow and let the global FAB handle it
            onDismiss()
        }
    }

    // MARK: - 3 Primed prompt chips

    private var primedPromptsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Try asking:")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .padding(.horizontal)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(model.primedPrompts, id: \.self) { prompt in
                        Button(action: {
                            Task { await model.sendPrompt(prompt) }
                        }) {
                            Text(prompt)
                                .font(.subheadline)
                                .foregroundStyle(theme.primary)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 8)
                                .background(theme.primary.opacity(0.1))
                                .clipShape(Capsule())
                                .overlay(Capsule().strokeBorder(theme.primary.opacity(0.3), lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Ask: \(prompt)")
                    }
                }
                .padding(.horizontal)
            }
        }
    }

    // MARK: - 4 Agent narration pane

    private func conversationSection(proxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(model.conversation) { turn in
                if turn.role == .agent {
                    agentNarrationBubble(turn: turn)
                        .id(turn.id)
                } else if turn.role == .advisor {
                    advisorBubble(turn: turn)
                        .id(turn.id)
                } else {
                    userBubble(turn: turn)
                        .id(turn.id)
                }
            }
            if model.isSending {
                typingIndicator
            }
        }
        .padding(.horizontal)
    }

    /// A message from a HUMAN advisor over Amazon Connect.
    ///
    /// Visually distinct from the AI bubble on purpose: a person-icon and a
    /// named "Advisor" label, so there is never ambiguity about whether the
    /// customer is reading something a human said or something the model
    /// generated. That distinction matters more here than visual consistency.
    private func advisorBubble(turn: DiscoverTurn) -> some View {
        HStack(alignment: .top, spacing: 10) {
            ZStack {
                Circle().fill(Color(.systemGreen).opacity(0.18))
                    .frame(width: 32, height: 32)
                Image(systemName: "person.fill.checkmark")
                    .foregroundStyle(Color(.systemGreen))
                    .font(.system(size: 13))
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(advisorLabel)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Color(.systemGreen))
                Text(AttributedString(fromMarkdown: sanitizeMarkdown(turn.text)))
                    .font(.body)
                    .foregroundStyle(Color(.label))
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            Spacer(minLength: 0)
        }
    }

    private var advisorLabel: String {
        if case .connected(let name) = model.advisorState, let n = name, !n.isEmpty {
            return "\(n) · Advisor"
        }
        return "Advisor"
    }

    /// Banner showing the state of a live human handoff.
    @ViewBuilder
    private var advisorStatusBanner: some View {
        switch model.advisorState {
        case .inactive:
            EmptyView()
        case .connecting:
            advisorBanner(icon: "person.badge.clock", tint: Color(.systemOrange),
                          text: "Connecting you to an advisor…", showSpinner: true)
        case .connected(let name):
            advisorBanner(icon: "person.fill.checkmark", tint: Color(.systemGreen),
                          text: name.map { "You're chatting with \($0)" }
                                 ?? "You're connected to an advisor",
                          showSpinner: false)
        case .ended(let reason):
            advisorBanner(icon: "person.fill.xmark", tint: .secondary,
                          text: "Advisor chat ended (\(reason))", showSpinner: false)
        case .failed(let message):
            advisorBanner(icon: "exclamationmark.triangle.fill",
                          tint: Color(.systemRed),
                          text: "Couldn't reach an advisor. \(message)",
                          showSpinner: false)
        }
    }

    private func advisorBanner(icon: String, tint: Color,
                               text: String, showSpinner: Bool) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon).foregroundStyle(tint).font(.caption)
            Text(text).font(.caption).foregroundStyle(Color(.label))
            Spacer(minLength: 0)
            if showSpinner { ProgressView().controlSize(.mini) }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
        .padding(.horizontal)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(text)
    }

    private func agentNarrationBubble(turn: DiscoverTurn) -> some View {
        HStack(alignment: .top, spacing: 10) {
            ZStack {
                Circle().fill(theme.primary.opacity(0.15)).frame(width: 32, height: 32)
                Image(systemName: "sparkles").foregroundStyle(theme.primary).font(.system(size: 14))
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(AttributedString(fromMarkdown: sanitizeMarkdown(turn.text)))
                    .font(.body)
                    .foregroundStyle(Color(.label))
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            .padding(12)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            Spacer(minLength: 40)
        }
        .accessibilityLabel("Agent: \(turn.text)")
    }

    private func userBubble(turn: DiscoverTurn) -> some View {
        HStack {
            Spacer(minLength: 40)
            Text(turn.text)
                .font(.body)
                .foregroundStyle(.white)
                .padding(12)
                .background(theme.primary)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .accessibilityLabel("You: \(turn.text)")
    }

    private var typingIndicator: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle().fill(theme.primary.opacity(0.15)).frame(width: 32, height: 32)
                Image(systemName: "ellipsis").foregroundStyle(theme.primary).font(.system(size: 14))
            }
            Text("Checking catalog…")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }

    // MARK: - 5 Evolving catalog card grid

    private var catalogGridSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(catalogSectionHeader)
                .font(.headline)
                .padding(.horizontal)

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                ForEach(visibleCards) { card in
                    DiscoverCard(
                        card: card,
                        theme: theme,
                        onSelect: { model.toggleCardSelection(card.id) },
                        onCompare: selectedCount >= 2 ? { Task { await model.compareSelected() } } : nil
                    )
                }
            }
            .padding(.horizontal)

            if selectedCount >= 2 {
                Button(action: { Task { await model.compareSelected() } }) {
                    Label("Compare selected (\(selectedCount))", systemImage: "arrow.left.arrow.right")
                        .font(.subheadline)
                        .fontWeight(.medium)
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(theme.primary)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .padding(.horizontal)
                .accessibilityLabel("Compare \(selectedCount) selected items")
            }
        }
    }

    /// Cards are visible only after the agent turn that introduced them has
    /// been appended to the conversation (agent-first rule).
    private var visibleCards: [CatalogCard] {
        // Build the set of all card IDs introduced by any completed agent turn
        let introducedIds = Set(model.conversation
            .filter { $0.role == .agent }
            .flatMap { $0.introducedCardIds })
        // Return cards that have been introduced, or all cards if no
        // introduction tracking is available (agent didn't emit JSON blocks)
        if introducedIds.isEmpty {
            // If we have cards but no JSON-block tracking, show all cards
            // (the agent narrated them inline without structured JSON).
            return model.cards
        }
        return model.cards.filter { introducedIds.contains($0.id) }
    }

    private var selectedCount: Int {
        model.cards.filter { $0.isSelected }.count
    }

    private var catalogSectionHeader: String {
        let label = session.tenantConfig?.acquire?.vehicleCategoryLabel ?? "Options"
        return "\(label)s for you"
    }

    // MARK: - Offers section

    private var offersSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Current offers")
                .font(.headline)
                .padding(.horizontal)

            VStack(spacing: 8) {
                ForEach(model.offers) { offer in
                    OfferBanner(offer: offer, theme: theme)
                        .padding(.horizontal)
                }
            }
        }
    }

    // MARK: - 6 Convergence CTAs

    private var convergenceCTAsSection: some View {
        VStack(spacing: 10) {
            Divider().padding(.horizontal)

            // "Let's build this" — primary CTA
            Button(action: handleLetsBuildThis) {
                Label("Let's build this", systemImage: "wrench.and.screwdriver.fill")
                    .font(.headline)
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(theme.primary)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .padding(.horizontal)
            .accessibilityLabel("Configure and order the selected vehicle")

            HStack(spacing: 10) {
                // "Book a test drive" — secondary CTA
                Button(action: handleTestRide) {
                    Label("Test drive", systemImage: "figure.wave")
                        .font(.subheadline)
                        .fontWeight(.medium)
                        .foregroundStyle(theme.primary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(theme.primary.opacity(0.1))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(theme.primary.opacity(0.4), lineWidth: 1))
                }
                .accessibilityLabel("Book a test drive")

                // "Save this / send me info" — tertiary CTA
                Button(action: { showsLeadCaptureModal = true }) {
                    Label("Save / Info", systemImage: "envelope")
                        .font(.subheadline)
                        .fontWeight(.medium)
                        .foregroundStyle(theme.primary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(theme.primary.opacity(0.1))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(theme.primary.opacity(0.4), lineWidth: 1))
                }
                .accessibilityLabel("Save this selection or request information")
            }
            .padding(.horizontal)
        }
    }

    // MARK: - Convergence actions

    private func handleLetsBuildThis() {
        if model.capturedLeadId != nil {
            // Lead already captured — go straight to configurator
            onConverge(model.buildHandoff())
        } else {
            // Need to capture lead first — show modal, then converge
            showsLeadCaptureModal = true
            // The modal's onLeadCaptured callback will fire onConverge
        }
    }

    private func handleTestRide() {
        // Reuse Service-stage booking path via assistant primed prompt
        Task {
            await model.sendPrompt("I'd like to book a test drive for this model.")
        }
    }

    // MARK: - Text input bar

    private var textInputBar: some View {
        HStack(spacing: 8) {
            TextField("Ask about any model…", text: $model.typedInput, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...4)
                .submitLabel(.send)
                .onSubmit { sendTyped() }
                .accessibilityLabel("Type your question about vehicles")

            Button(action: sendTyped) {
                Image(systemName: model.isSending ? "arrow.clockwise" : "arrow.up.circle.fill")
                    .font(.system(size: 28))
                    .foregroundStyle(
                        model.typedInput.trimmingCharacters(in: .whitespaces).isEmpty || model.isSending
                        ? Color.secondary
                        : theme.primary
                    )
            }
            .disabled(model.typedInput.trimmingCharacters(in: .whitespaces).isEmpty || model.isSending)
            .accessibilityLabel("Send message")
        }
    }

    private func sendTyped() {
        let text = model.typedInput.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty, !model.isSending else { return }
        Task { await model.sendPrompt(text) }
    }

    // MARK: - Lead capture modal

    private var leadCaptureModalView: some View {
        // Uses the standalone LeadCaptureModal (Views/Acquire/LeadCaptureModal.swift).
        // Idempotency: pass existingLeadId when already captured this session — the
        // modal detects this on appear and fires onSuccess immediately without showing
        // the form (spec: "skip the modal and go straight to onConverge").
        LeadCaptureModal(
            existingLeadId: model.capturedLeadId,
            onSuccess: { leadId in
                showsLeadCaptureModal = false
                model.onLeadCaptured(leadId)
                showToast("Your details have been saved.")
                onLeadCaptured(leadId)
                // If the user was mid-converge (tapped "Let's build this"), fire
                // onConverge now that the lead is captured.
                if pendingHandoff != nil {
                    onConverge(model.buildHandoff())
                    pendingHandoff = nil
                }
            },
            onCancel: {
                showsLeadCaptureModal = false
                pendingHandoff = nil
            }
        )
    }

    // MARK: - Toast

    private func showToast(_ message: String) {
        toastMessage = message
        toastTask?.cancel()
        toastTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            toastMessage = nil
        }
    }

    @ViewBuilder
    private func toastView(_ message: String) -> some View {
        Text(message)
            .font(.subheadline)
            .foregroundStyle(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color(.systemGray))
            .clipShape(Capsule())
            .shadow(radius: 4)
    }

    // MARK: - Persona hydration

    /// Entry-point hydration. Three branches per spec § Design — Persona hydration.
    private func hydratePersona() async {
        NSLog("🛒 DISCOVER: hydratePersona entry — pathB=%@ authSignedIn=%@ sessionId=%@ turns=%d",
              session._pathBActive ? "yes" : "no",
              { if case .signedIn = session.authState { return "yes" } else { return "no" } }(),
              model.discoverSessionId,
              model.conversation.count)

        // Hand the connected-vehicle record to the model so the agent is told
        // what we already know (odometer, trip count, average trip distance)
        // instead of asking the customer for it.
        model.currentVehicle = session.currentVehicle
        NSLog("🛒 DISCOVER: vehicle context — %@ odo=%@ trips=%@",
              session.currentVehicle?.displayTitle ?? "none",
              session.currentVehicle?.odometer.map(String.init) ?? "-",
              session.currentVehicle?.totalTrips.map(String.init) ?? "-")

        // Identity is part of "what we already know". The agent was observed
        // asking a signed-in customer "what's your name so I can book it" while
        // the app was holding it in `session.currentDriver` — the same defect as
        // asking for the odometer, and more jarring because it is more obviously
        // known. `customerContextBlock()` states it and forbids re-asking.
        //
        // Nil for anonymous / kiosk visitors, where asking IS correct. The kiosk
        // captures a name at Beat 0 onto `KioskSession.visitorFirstName`, which
        // is a different source and reaches the configurator by its own path
        // (`ConfiguratorOfferHandoff.firstName`) — not wired here on purpose.
        model.customerFirstName = session.currentDriver?.firstName
        model.customerLastName = session.currentDriver?.lastName
        // Presence only — the name itself is never logged.
        NSLog("🛒 DISCOVER: customer identity — known=%@ driverId=%@",
              (session.currentDriver?.firstName?.isEmpty == false) ? "yes" : "no",
              session.currentDriver?.driverId ?? "none")

        if session._pathBActive {
            // Path B mock: forced warm-start with seeded persona snapshot
            model.pathBActive = true
            model.personaSnapshot = session._personaHint
            showToast("Path B warm start — returning owner session opened")
        } else if case .signedIn = session.authState {
            // Path A signed-in: LTM hydration (real Cognito user)
            // For v1 we propagate driver identity as a persona snapshot.
            // Full AgentCoreMemorySessionManager hydration is a follow-up spec.
            if let driver = session.currentDriver {
                model.personaSnapshot = PersonaSnapshot(
                    actorId: driver.driverId,
                    engagementSegment: nil,
                    currentVehicleHint: session.currentVehicle?.displayTitle,
                    statedInterest: nil
                )
            }
        }
        // Path A anonymous: no hydration, no persona snapshot

        await autoOpenConversation()
    }

    /// Fire the first agent turn automatically so the screen lands on content
    /// instead of an empty chat waiting to be typed into.
    ///
    /// Rationale: DiscoverFlow opened to a bare composer, which made the
    /// primary surface look inert and put the burden of starting on the user.
    /// When we know their vehicle and usage we can do better than a blank
    /// prompt — we can open with a recommendation grounded in how they actually
    /// ride.
    ///
    /// The opening prompt is phrased as the CUSTOMER's opening line, because
    /// the agent's persona prompt is what decides how to answer it. When
    /// vehicle facts are present, `vehicleContextBlock()` is prepended by
    /// `promptWithHistory` and the agent leads with a usage-grounded step-up;
    /// when they are absent, the same line reads as an ordinary cold-start
    /// request and the agent asks its qualifying questions instead.
    ///
    /// Guarded so it only ever fires once per session, and never on top of an
    /// existing conversation (Path B or a re-entered flow).
    private func autoOpenConversation() async {
        guard model.conversation.isEmpty, !model.isSending, !hasAutoOpened else { return }
        hasAutoOpened = true

        // Directly-supplied prompt wins. This is the deterministic path — no shared
        // field, no ordering assumption — and it is what a tile tap should use.
        if let direct = initialPrompt?.trimmingCharacters(in: .whitespacesAndNewlines),
           !direct.isEmpty {
            NSLog("🛒 DISCOVER: auto-open from initialPrompt (len=%d)", direct.count)
            // Clear any stale session handoff so it cannot be consumed by a later
            // re-entry and replay the wrong opener.
            if session.pendingDiscoverPrompt != nil {
                session.pendingDiscoverPrompt = nil
            }
            model.beginSend(direct)
            return
        }

        // A handoff from UpgradeFlow supplies its own opener carrying the
        // selections the customer already made, so the agent continues the
        // conversation instead of restarting it.
        if let handoff = session.pendingDiscoverPrompt, !handoff.isEmpty {
            NSLog("🛒 DISCOVER: auto-open from handoff (len=%d)", handoff.count)
            // Hand the send to the model BEFORE clearing the session field.
            // Clearing it mutates the observable session, which rebuilds any
            // ancestor reading it and re-creates this view; a send owned by this
            // view's `.task` would be cancelled mid-flight.
            model.beginSend(handoff)
            session.pendingDiscoverPrompt = nil
            return
        }

        // Generic opener — reached ONLY when no prompt was supplied by either route.
        // If a caller intended a specific opener and this fires instead, the prompt
        // was lost in transit rather than absent; that was the 2026-08-19 test-drive
        // defect. The log line below distinguishes the two cases.
        let opener = model.currentVehicle == nil
            ? "I'm thinking about a new vehicle. What do you have?"
            : "I'm thinking about upgrading. Based on how I actually drive, what would you recommend?"

        NSLog("🛒 DISCOVER: auto-open GENERIC opener — no prompt supplied (hasVehicle=%@)",
              model.currentVehicle == nil ? "no" : "yes")
        model.beginSend(opener)
    }

    // MARK: - Scroll helper

    private func scrollToBottom(proxy: ScrollViewProxy) {
        guard let last = model.conversation.last else { return }
        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
    }
}

// MARK: - AttributedString markdown helper

private extension AttributedString {
    /// Create an AttributedString from a markdown string, falling back to
    /// plain text if markdown parsing fails.
    init(fromMarkdown markdown: String) {
        if let attributed = try? AttributedString(markdown: markdown,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
            self = attributed
        } else {
            self = AttributedString(markdown)
        }
    }
}


