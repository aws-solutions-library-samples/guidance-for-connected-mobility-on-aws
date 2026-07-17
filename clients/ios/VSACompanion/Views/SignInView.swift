import SwiftUI
import UIKit

/// Sign-in screen. Rewritten 2026-05-07 with a richer layout, Face ID /
/// Touch ID flow, a forgot-password sheet, and the standard footer links
/// (Help · Contact · Legal · Privacy · ©). The old minimal layout is
/// preserved only in spirit — core flow is still email + password +
/// Sign In — but the surrounding chrome now looks like a real app.
///
/// DEBUG-only: double-tapping the hero car icon fills in a random demo
/// driver's email + demo password. Useful for demoing without
/// typing; release builds ignore the gesture entirely.
struct SignInView: View {
    @Environment(AppSession.self) private var session

    @State private var email: String = VSAConfig.devEmail ?? ""
    @State private var password: String = VSAConfig.devPassword ?? ""
    @State private var isWorking: Bool = false
    @State private var biometricWorking: Bool = false
    @State private var rememberMe: Bool = true

    // Sheet/alert presentation.
    @State private var isForgotPasswordPresented: Bool = false
    @State private var footerSheet: FooterLink? = nil
    @State private var demoToast: String? = nil

    /// Whether the device has Face ID / Touch ID enrolled and we've got
    /// creds stashed to unlock. Computed once at `.onAppear` so we don't
    /// re-probe LAContext on every body re-evaluation.
    @State private var biometricState: BiometricState = .unknown

    private enum BiometricState {
        case unknown                           // hasn't been probed yet
        case unavailable                       // no biometric + no stored creds
        case available(BiometricAuth.Biometry) // ready to offer Face/Touch ID
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                hero
                    .padding(.top, 48)
                    .padding(.bottom, 28)

                personaQuickPick
                    .padding(.horizontal, 20)
                    .padding(.bottom, 14)

                formCard
                    .padding(.horizontal, 20)

                if let msg = demoToast {
                    demoToastBanner(message: msg)
                        .padding(.top, 14)
                        .padding(.horizontal, 20)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }

                if case .failed(let msg) = session.authState {
                    errorBanner(message: msg)
                        .padding(.top, 14)
                        .padding(.horizontal, 20)
                        .transition(.opacity)
                }

                Spacer(minLength: 40)

                footer
                    .padding(.top, 32)
                    .padding(.bottom, 20)
            }
            .frame(maxWidth: .infinity)
        }
        .background(
            LinearGradient(
                colors: [
                    Color(.systemBackground),
                    Color(red: 0.95, green: 0.96, blue: 0.99),
                ],
                startPoint: .top, endPoint: .bottom
            )
            .ignoresSafeArea()
        )
        .animation(.easeInOut(duration: 0.2), value: demoToast)
        .animation(.easeInOut(duration: 0.2), value: session.authState)
        .onAppear(perform: probeBiometric)
        .sheet(isPresented: $isForgotPasswordPresented) {
            ForgotPasswordSheet(initialEmail: email)
        }
        .sheet(item: $footerSheet) { link in
            FooterSheet(link: link)
        }
        #if DEBUG
        .onAppear(perform: maybeAutoSignIn)
        #endif
    }

    // MARK: - Hero section

    /// Branding block — icon, app name, tagline. Tappable twice in DEBUG
    /// to fill in a random demo driver.
    private var hero: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(red: 0.10, green: 0.24, blue: 0.66),
                                Color(red: 0.18, green: 0.44, blue: 0.82),
                            ],
                            startPoint: .topLeading, endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 96, height: 96)
                    .shadow(color: Color(red: 0.10, green: 0.24, blue: 0.66).opacity(0.35),
                            radius: 12, x: 0, y: 6)

                Image(systemName: "car.fill")
                    .resizable().scaledToFit()
                    .frame(width: 44, height: 44)
                    .foregroundStyle(.white)
            }
            // DEBUG-only double-tap → randomize. iOS sends single-tap
            // events too if we don't suppress them, but the VStack itself
            // isn't a hit target normally, so we attach the gesture to
            // the ZStack only.
            #if DEBUG
            .contentShape(Circle())
            .onTapGesture(count: 2, perform: randomizeDemoDriver)
            .accessibilityHint("Double-tap to fill in a random demo driver")
            #endif

            Text("Connected Mobility on AWS")
                .font(.title2).bold()
                .foregroundStyle(Color(.label))

            Text("Sign in to your fleet account")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Form card

    /// Demo persona quick-pick row above the email/password fields.
    /// Tapping a card fills the form, sets `AppSession.activeTenantId`,
    /// and triggers normal sign-in. Three cards = three personas
    /// (Fleet / OEM / Rental). Persona definitions live in VSAConfig
    /// so adding a fourth is just an entry there.
    ///
    /// The cards lay out horizontally so all three are visible without
    /// scrolling on standard iPhone widths. On narrower devices (older
    /// SE) they remain readable at the cost of some text truncation —
    /// label only, the subtitle line truncates first.
    @ViewBuilder
    private var personaQuickPick: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Sign in as")
                .font(.caption).bold()
                .foregroundStyle(.secondary)
                .padding(.horizontal, 4)
            HStack(spacing: 10) {
                ForEach(VSAConfig.demoPersonas) { persona in
                    personaCard(persona)
                }
            }
        }
    }

    @ViewBuilder
    private func personaCard(_ persona: VSAConfig.DemoPersona) -> some View {
        Button {
            selectPersona(persona)
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                Image(systemName: persona.symbolName)
                    .font(.title3)
                    .foregroundStyle(personaTint(persona))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(persona.label)
                    .font(.subheadline).bold()
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Text(persona.subtitle)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(.tertiarySystemBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .strokeBorder(personaTint(persona).opacity(0.3), lineWidth: 1)
                    )
            )
        }
        .buttonStyle(.plain)
        .disabled(isWorking || biometricWorking)
    }

    /// Per-persona accent color so each card reads as its tenant brand
    /// even before sign-in completes (when the real `tenantConfig.branding`
    /// loads). Falls back to the form's primary navy if a persona id
    /// isn't recognized.
    private func personaTint(_ persona: VSAConfig.DemoPersona) -> Color {
        switch persona.tenantId {
        case "fleet":      return Color(red: 0.10, green: 0.24, blue: 0.66)  // navy
        case "oem":        return Color(red: 0.00, green: 0.20, blue: 0.47)  // OEM blue
        case "enterprise": return Color(red: 0.00, green: 0.44, blue: 0.24)  // Enterprise green
        default:           return Color(red: 0.10, green: 0.24, blue: 0.66)
        }
    }

    /// Persona pick → fill form → set active tenant → sign in.
    /// activeTenantId is set BEFORE signIn() so that, after auth, the
    /// post-sign-in MainTabView's loadTenantConfig fetches the right
    /// tenant config (which carries segment + branding for the layout
    /// selector). Without that ordering the user would briefly see
    /// the previous tenant's theme before the new config landed.
    private func selectPersona(_ persona: VSAConfig.DemoPersona) {
        email = persona.email
        password = persona.password
        rememberMe = false  // demo personas are short-lived; don't write Keychain
        session.activeTenantId = persona.tenantId
        Task { await signIn() }
    }

    private var formCard: some View {
        VStack(spacing: 14) {
            labeledField(
                title: "Email",
                systemImage: "envelope",
                content: {
                    TextField("you@example.com", text: $email)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                }
            )

            labeledField(
                title: "Password",
                systemImage: "lock",
                content: {
                    SecureField("••••••••", text: $password)
                        .textContentType(.password)
                }
            )

            HStack(spacing: 8) {
                Toggle(isOn: $rememberMe) {
                    Text("Remember me").font(.footnote)
                }
                .toggleStyle(.switch)
                .controlSize(.mini)

                Spacer()

                Button("Forgot password?") {
                    isForgotPasswordPresented = true
                }
                .font(.footnote)
                .foregroundStyle(Color(red: 0.10, green: 0.24, blue: 0.66))
            }
            .padding(.top, 4)

            signInButton
                .padding(.top, 6)

            if case .available(let kind) = biometricState {
                biometricDivider
                biometricButton(kind: kind)
            }
        }
        .padding(20)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(.secondarySystemBackground))
                .shadow(color: .black.opacity(0.06), radius: 12, x: 0, y: 4)
        )
    }

    /// Rounded-border input with a section label + SF Symbol. Matches the
    /// "labeled field" pattern used by most polished iOS sign-in screens
    /// (Apple ID setup, banking apps, etc.) — denser than .roundedBorder
    /// alone but still idiomatic.
    @ViewBuilder
    private func labeledField<Content: View>(
        title: String,
        systemImage: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption).bold()
                .foregroundStyle(.secondary)
            HStack(spacing: 10) {
                Image(systemName: systemImage)
                    .foregroundStyle(.secondary)
                    .frame(width: 18)
                content()
                    .font(.body)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 12)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color(.tertiarySystemBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(Color(.separator), lineWidth: 0.5)
                    )
            )
        }
    }

    private var signInButton: some View {
        Button {
            Task { await signIn() }
        } label: {
            HStack {
                if isWorking {
                    ProgressView().tint(.white)
                } else {
                    Text("Sign In").bold()
                }
            }
            .frame(maxWidth: .infinity, minHeight: 28)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(Color(red: 0.10, green: 0.24, blue: 0.66))
        .disabled(email.isEmpty || password.isEmpty || isWorking || biometricWorking)
    }

    private var biometricDivider: some View {
        HStack(spacing: 10) {
            Rectangle().fill(Color(.separator)).frame(height: 0.5)
            Text("OR").font(.caption2).foregroundStyle(.tertiary)
            Rectangle().fill(Color(.separator)).frame(height: 0.5)
        }
        .padding(.vertical, 6)
    }

    private func biometricButton(kind: BiometricAuth.Biometry) -> some View {
        Button {
            Task { await signInWithBiometric(kind: kind) }
        } label: {
            HStack(spacing: 10) {
                if biometricWorking {
                    ProgressView().tint(Color(red: 0.10, green: 0.24, blue: 0.66))
                } else {
                    Image(systemName: kind.systemImage)
                        .font(.title3)
                }
                Text("Sign in with \(kind.displayName)").bold()
            }
            .frame(maxWidth: .infinity, minHeight: 28)
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
        .tint(Color(red: 0.10, green: 0.24, blue: 0.66))
        .disabled(isWorking || biometricWorking)
    }

    // MARK: - Banners

    private func errorBanner(message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(message)
                .font(.footnote)
                .foregroundStyle(.red)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.red.opacity(0.08))
        )
    }

    /// Green success-ish toast for demo-mode randomization. Fades in
    /// when a random driver is filled so the user sees what happened;
    /// auto-dismisses after ~2 seconds.
    private func demoToastBanner(message: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkles")
                .foregroundStyle(.purple)
            Text(message)
                .font(.footnote)
                .foregroundStyle(.primary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.purple.opacity(0.10))
        )
    }

    // MARK: - Footer (Help · Contact · Legal · Privacy · ©)

    private var footer: some View {
        VStack(spacing: 10) {
            HStack(spacing: 16) {
                ForEach(FooterLink.allCases) { link in
                    Button {
                        footerSheet = link
                    } label: {
                        Text(link.title)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    if link != FooterLink.allCases.last {
                        Text("·").font(.footnote).foregroundStyle(.tertiary)
                    }
                }
            }
            Text("© \(currentYear) Connected Mobility on AWS · v\(appVersion)")
                .font(.caption2).foregroundStyle(.tertiary)
        }
    }

    private var currentYear: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy"
        return f.string(from: Date())
    }

    private var appVersion: String {
        (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "0.0"
    }

    // MARK: - Actions

    private func probeBiometric() {
        let kind = BiometricAuth.available()
        if kind != .none && RememberedCredentials.hasAny {
            biometricState = .available(kind)
        } else {
            biometricState = .unavailable
        }
    }

    @MainActor
    private func signIn() async {
        isWorking = true
        defer { isWorking = false }
        session.authState = .signingIn
        do {
            let auth = AuthService()
            let token = try await auth.signIn(email: email, password: password)

            // Persist creds for biometric unlock next time, iff the user
            // wants us to. Writing happens on success only so we don't
            // remember bad creds.
            if rememberMe {
                RememberedCredentials.save(email: email, password: password)
            }

            // Resolve the driver + vehicle BEFORE flipping authState to
            // .signedIn. MainTabView observes authState and mounts as
            // soon as it flips; its own Task then calls
            // loadCurrentDriver + warmVoiceSession in parallel with the
            // ones below. Prior to this ordering fix (2026-05-11) the
            // MainTabView warm ran against the fallback demo vehicle
            // (VEH-0025) because currentVehicle hadn't been populated
            // yet, which then tripped the identity-drift guard and
            // forced a full session teardown-and-rebuild on every
            // fresh sign-in — visible in the iOS log as the noisy
            // "[VoiceSession] identity drift" line before every voice
            // session. Resolving the driver first means MainTabView
            // sees the real vehicle from its first render.
            let client = VSAClient(idTokenProvider: { token })
            await session.loadCurrentDriver(client: client)

            session.authState = .signedIn(idToken: token, email: email)
            session.connectWebSocket(token: token)
            Task { await session.warmVoiceSession() }
        } catch {
            print("[VSA Auth] Sign-in failed: \(error)")
            session.authState = .failed(error.localizedDescription)
        }
    }

    @MainActor
    private func signInWithBiometric(kind: BiometricAuth.Biometry) async {
        biometricWorking = true
        defer { biometricWorking = false }

        let outcome = await BiometricAuth.authenticate(
            reason: "Sign in to your fleet account"
        )
        switch outcome {
        case .success:
            guard let creds = RememberedCredentials.load() else {
                // Shouldn't happen — hasAny gated the UI — but if the
                // keychain entry disappeared between render and tap,
                // degrade to the manual form.
                biometricState = .unavailable
                return
            }
            email = creds.email
            password = creds.password
            await signIn()
        case .cancelled:
            // User bailed — no message, no action.
            break
        case .failed(let msg):
            session.authState = .failed(msg)
        }
    }

    /// DEBUG-only: fill in a random demo driver's email with the demo password.
    /// Shows a short toast so the user can see which driver got picked
    /// (useful when double-tapping repeatedly to try different drivers).
    #if DEBUG
    private func randomizeDemoDriver() {
        guard let driver = DemoDrivers.all.randomElement() else { return }
        email = driver.email
        password = VSAConfig.demoPassword
        rememberMe = false  // don't persist demo creds to keychain
        demoToast = "Filled: \(driver.displayName) (\(driver.driverId))"
        // Haptic so the gesture has physical feedback — aligns with the
        // "demo magic" feel.
        let feedback = UIImpactFeedbackGenerator(style: .medium)
        feedback.impactOccurred()

        // Auto-dismiss the toast after a couple of seconds.
        Task {
            try? await Task.sleep(nanoseconds: 2_200_000_000)
            await MainActor.run { demoToast = nil }
        }
    }

    private func maybeAutoSignIn() {
        // Dev convenience: if devEmail/devPassword are set and we're
        // signed out, kick off sign-in automatically. Avoids tapping
        // through on every simulator run.
        if case .signedOut = session.authState,
           VSAConfig.devEmail != nil,
           VSAConfig.devPassword != nil,
           !email.isEmpty,
           !password.isEmpty {
            Task { await signIn() }
        }
    }
    #endif
}

// MARK: - Footer links

/// Reachable from the bottom of SignInView. Order matters — it's the
/// display order. Stubbed content; real legal copy plugs into the
/// `markdown` field when we have it.
private enum FooterLink: String, CaseIterable, Identifiable {
    case help, contact, legal, privacy

    var id: String { rawValue }
    var title: String {
        switch self {
        case .help:    return "Help"
        case .contact: return "Contact Us"
        case .legal:   return "Legal"
        case .privacy: return "Privacy"
        }
    }

    var systemImage: String {
        switch self {
        case .help:    return "questionmark.circle"
        case .contact: return "envelope"
        case .legal:   return "doc.text"
        case .privacy: return "hand.raised"
        }
    }

    /// Placeholder copy. Replace with real content plumbed in from the
    /// tenant config or a bundled Markdown file when available.
    var body: String {
        switch self {
        case .help:
            return """
            Need a hand? Most issues are solved by signing out and back in, or \
            by re-opening the app. If you're still stuck, reach out via the \
            Contact Us screen.

            For voice-session issues, check that Face ID / microphone permissions \
            are granted in Settings → Connected Mobility on AWS.
            """
        case .contact:
            return """
            Fleet support: fleet-support@example.com
            Technical issues: vsa-support@example.com
            Phone: +1 (555) 010-VSA1

            Hours: Monday–Friday, 7am–7pm local time.
            """
        case .legal:
            return """
            © \(Calendar.current.component(.year, from: Date())) Connected Mobility on AWS.

            This app is provided "as is" for demonstration purposes. By using it, \
            you agree to the terms governing your fleet's agreement with AWS and \
            the tenant that deployed this app. No warranty, express or implied, \
            is made regarding availability, accuracy, or fitness for a particular \
            purpose.
            """
        case .privacy:
            return """
            Connected Mobility on AWS collects:
            • Your voice recordings during active triage sessions
            • Vehicle telemetry (speed, location, diagnostic codes) from your \
              assigned vehicle
            • Safety events (harsh braking, phone usage, etc.)
            • Sign-in metadata (timestamps, device identifier)

            This data is stored in your fleet's tenant database and used to \
            provide triage, safety, and maintenance recommendations. It is \
            never sold. It is shared with your fleet operator and AWS-service \
            vendors that help operate this app.

            To request deletion, contact your fleet administrator.
            """
        }
    }
}

private struct FooterSheet: View {
    let link: FooterLink
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 12) {
                        Image(systemName: link.systemImage)
                            .font(.largeTitle)
                            .foregroundStyle(Color(red: 0.10, green: 0.24, blue: 0.66))
                        Text(link.title).font(.largeTitle).bold()
                    }
                    Text(link.body)
                        .font(.body)
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(link.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

// MARK: - Forgot password

/// Forgot-password flow. This is a UI stub — we collect an email address
/// and pretend to send instructions. Real Cognito ForgotPassword requires
/// SES/SNS integration which the demo pool doesn't have configured today.
/// The copy makes the stub honest: "If an account exists, you'll receive
/// an email". When SES is wired, the body of `submit()` becomes a real
/// Cognito call and the stub comment goes away.
private struct ForgotPasswordSheet: View {
    let initialEmail: String
    @Environment(\.dismiss) private var dismiss

    @State private var email: String = ""
    @State private var submitted: Bool = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                if submitted {
                    VStack(alignment: .leading, spacing: 10) {
                        Image(systemName: "envelope.badge")
                            .font(.largeTitle)
                            .foregroundStyle(Color(red: 0.10, green: 0.24, blue: 0.66))
                        Text("Check your email").font(.title2).bold()
                        Text("If an account with **\(email)** exists, we've sent instructions to reset your password. It may take a few minutes to arrive.")
                            .font(.body).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.top)
                } else {
                    Text("Reset your password").font(.title2).bold()
                    Text("Enter your account email and we'll send instructions to reset your password.")
                        .font(.body).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)

                    TextField("Email", text: $email)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                        .padding(12)
                        .background(
                            RoundedRectangle(cornerRadius: 10)
                                .fill(Color(.tertiarySystemBackground))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 10)
                                        .strokeBorder(Color(.separator), lineWidth: 0.5)
                                )
                        )

                    Button {
                        submit()
                    } label: {
                        Text("Send Reset Instructions").bold()
                            .frame(maxWidth: .infinity, minHeight: 28)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .tint(Color(red: 0.10, green: 0.24, blue: 0.66))
                    .disabled(email.isEmpty)
                }

                Spacer()
            }
            .padding()
            .navigationTitle("Forgot Password")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(submitted ? "Done" : "Cancel") { dismiss() }
                }
            }
            .onAppear {
                if email.isEmpty { email = initialEmail }
            }
        }
    }

    /// Stub: in a real deployment this would call
    /// AWSCognitoIdentityProviderService.ForgotPassword and then open
    /// a confirm-code sheet. For now we flip a local flag so the view
    /// shows the "check your email" confirmation copy.
    private func submit() {
        submitted = true
    }
}

// MARK: - Demo drivers (DEBUG randomizer)

/// Known-good demo drivers for the double-tap randomizer.
///
/// **Current behaviour (2026-05-07):** the double-tap always fills
/// Samantha Carter. Her vehicle (VEH-0047) has 4 active CRITICAL DTCs
/// (P0217, C1234, P0A80, +) so any vehicle-symptom utterance in the
/// voice session auto-classifies to **P0** and triggers the Connect
/// handover — which is what we want to demo. Other seeded drivers
/// don't currently have P0-worthy DTCs, so picking them meant the
/// voice assistant would say "all clear" and there'd be nothing to
/// escalate, which made demo-testing frustrating (reported by PM
/// 2026-05-07: "I keep checking other drivers but nobody else has a
/// P0").
///
/// **To re-enable the wider pool**, change `all` to return
/// `allKnown` instead of `p0Ready`. Or, better, expand `p0Ready` as
/// more drivers get critical DTCs seeded against their vehicles.
///
/// All drivers listed here exist in Cognito and use the canonical
/// demo password (reset via `admin-set-user-password` on
/// 2026-05-07). If the pool is re-seeded, run that reset script
/// again or the login will 401.
///
/// Samantha Carter is always first in `allKnown` — flagship demo.
enum DemoDrivers {
    struct Entry: Equatable {
        let driverId: String
        let firstName: String
        let lastName: String
        let email: String

        var displayName: String { "\(firstName) \(lastName)" }
    }

    /// The driver(s) currently known to have a P0-triggering DTC on
    /// their assigned vehicle. `randomElement()` over a single-item
    /// array is equivalent to hard-coding; leaving it as a list makes
    /// it trivial to append more drivers when their DTCs land.
    static let p0Ready: [Entry] = [
        Entry(driverId: "DRV-0054", firstName: "Samantha", lastName: "Carter",   email: "samantha.carter@example.com"),
    ]

    /// The full set of demo drivers known to exist in Cognito. Kept
    /// around (but not currently drawn from) so we can widen the pool
    /// quickly when additional drivers get P0 DTCs, or for non-voice
    /// demos where a random sign-in is useful.
    static let allKnown: [Entry] = [
        Entry(driverId: "DRV-0054", firstName: "Samantha", lastName: "Carter",   email: "samantha.carter@example.com"),
        Entry(driverId: "DRV-0062", firstName: "Michael",  lastName: "Wright",   email: "michael.wright@example.com"),
        Entry(driverId: "DRV-0058", firstName: "Heather",  lastName: "Nelson",   email: "heather.nelson@example.com"),
        Entry(driverId: "DRV-0028", firstName: "Jennifer", lastName: "Hill",     email: "jennifer.hill@example.com"),
        Entry(driverId: "DRV-0023", firstName: "Kevin",    lastName: "Wright",   email: "kevin.wright@example.com"),
        Entry(driverId: "DRV-0074", firstName: "Daniel",   lastName: "Lewis",    email: "daniel.lewis@example.com"),
        Entry(driverId: "DRV-0061", firstName: "Angela",   lastName: "Adams",    email: "angela.adams@example.com"),
        Entry(driverId: "DRV-0063", firstName: "Vanessa",  lastName: "Moore",    email: "vanessa.moore@example.com"),
        Entry(driverId: "DRV-0019", firstName: "Aaron",    lastName: "Lewis",    email: "aaron.lewis@example.com"),
        Entry(driverId: "DRV-0059", firstName: "Dustin",   lastName: "Jones",    email: "dustin.jones@example.com"),
        Entry(driverId: "DRV-0057", firstName: "Christina",lastName: "Carter",   email: "christina.carter@example.com"),
        Entry(driverId: "DRV-0072", firstName: "Linda",    lastName: "Brown",    email: "linda.brown@example.com"),
        Entry(driverId: "DRV-0007", firstName: "Tiffany",  lastName: "Lee",      email: "tiffany.lee@example.com"),
        Entry(driverId: "DRV-0006", firstName: "Amber",    lastName: "Davis",    email: "amber.davis@example.com"),
        Entry(driverId: "DRV-0053", firstName: "Ashley",   lastName: "Brown",    email: "ashley.brown@example.com"),
        Entry(driverId: "DRV-0046", firstName: "Carlos",   lastName: "Perez",    email: "carlos.perez@example.com"),
    ]

    /// Source used by the double-tap randomizer. Aliased so the view
    /// call-site (`DemoDrivers.all.randomElement()`) stays unchanged
    /// regardless of which pool we're drawing from.
    static var all: [Entry] { p0Ready }
}
