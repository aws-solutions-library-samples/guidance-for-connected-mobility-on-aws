import Foundation
import LocalAuthentication

/// Thin wrapper around LocalAuthentication that powers the "Sign in with
/// Face ID" affordance on SignInView. Works in tandem with KeychainStore:
/// the user's email + password are persisted in the keychain after the
/// first successful sign-in (opt-in via the "Enable Face ID" prompt); onx
/// subsequent launches, LAContext gates access to those persisted creds.
///
/// Design notes
/// ------------
/// - We evaluate with `.deviceOwnerAuthenticationWithBiometrics` so the
///   fallback is "passcode", not an arbitrary password prompt. On demo
///   devices that's what the user expects.
/// - Error surfacing is intentionally soft — if biometric auth fails or
///   isn't available, SignInView falls back to the email/password fields
///   (they're always rendered). No red error text unless the user
///   explicitly tried biometric and the attempt failed for a non-cancel
///   reason.
/// - The credential store uses a separate keychain service string from
///   the token store (`KeychainStore(service: "...tokens")`) so clearing
///   tokens on sign-out doesn't also wipe the remembered creds.
enum BiometricAuth {

    /// The biometric flavour available on the current device.
    enum Biometry {
        case faceID
        case touchID
        case opticID
        case none

        var displayName: String {
            switch self {
            case .faceID:   return "Face ID"
            case .touchID:  return "Touch ID"
            case .opticID:  return "Optic ID"
            case .none:     return "Biometrics"
            }
        }

        /// SF Symbol for buttons/banners. `none` falls back to a generic
        /// lock so callers don't crash if they ignore the availability
        /// check — the button just looks generic instead of missing.
        var systemImage: String {
            switch self {
            case .faceID:   return "faceid"
            case .touchID:  return "touchid"
            case .opticID:  return "opticid"
            case .none:     return "lock"
            }
        }
    }

    /// Probe what biometric modality is enrolled on the device. Returns
    /// `.none` when biometrics aren't enrolled, unavailable, or blocked
    /// by policy (e.g. simulator without Face ID simulation).
    static func available() -> Biometry {
        let ctx = LAContext()
        var error: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) else {
            return .none
        }
        switch ctx.biometryType {
        case .faceID:   return .faceID
        case .touchID:  return .touchID
        case .opticID:  return .opticID
        @unknown default: return .none
        }
    }

    /// Outcome of a biometric challenge. `.cancelled` is callsite-ignored
    /// (user dismissed the prompt); `.failed(reason)` should be shown.
    enum Result {
        case success
        case cancelled
        case failed(String)
    }

    /// Prompt the user to confirm identity with Face ID / Touch ID.
    /// The reason string shows as the secondary label under the biometric
    /// prompt; callers should phrase it as a user-facing goal, not a
    /// technical assertion.
    static func authenticate(reason: String) async -> Result {
        let ctx = LAContext()
        ctx.localizedFallbackTitle = "Use Passcode"
        var error: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) else {
            return .failed(error?.localizedDescription ?? "Biometrics unavailable")
        }
        do {
            try await ctx.evaluatePolicy(
                .deviceOwnerAuthenticationWithBiometrics,
                localizedReason: reason
            )
            return .success
        } catch let laError as LAError {
            switch laError.code {
            case .userCancel, .systemCancel, .appCancel:
                return .cancelled
            default:
                return .failed(laError.localizedDescription)
            }
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}

/// Persists the most-recently-used email + password in the keychain so
/// Face ID can unlock them on subsequent launches. Separate service
/// string from the token store — clearing tokens on sign-out should not
/// forget "who last used this app", which is the remembered-cred
/// contract drivers expect from the biometric toggle.
///
/// Scope is deliberately narrow: one (email, password) pair per device.
/// We don't support multi-account remember-me.
struct RememberedCredentials {
    private static let store = KeychainStore(service: "com.aws.vsa.companion.remember")
    private static let kEmail = "email"
    private static let kPassword = "password"

    /// Save credentials after a successful sign-in. Wipes anything
    /// previously remembered so account switches don't leave stale
    /// creds behind.
    static func save(email: String, password: String) {
        store.write(key: kEmail, value: email)
        store.write(key: kPassword, value: password)
    }

    /// Load the remembered credentials. Returns nil when either half is
    /// missing — we never want to auto-fill a partial pair.
    static func load() -> (email: String, password: String)? {
        guard let e = store.read(key: kEmail),
              let p = store.read(key: kPassword),
              !e.isEmpty, !p.isEmpty else { return nil }
        return (e, p)
    }

    /// True when both halves are in the keychain. Cheap enough to call
    /// from SwiftUI init / onAppear — SecItemCopyMatching is a single
    /// syscall.
    static var hasAny: Bool {
        load() != nil
    }

    /// Clear both halves. Called when the user toggles biometric off
    /// or when a sign-in attempt with remembered creds fails (stale
    /// password, account deleted, etc.) so we don't keep retrying.
    static func clear() {
        store.delete(key: kEmail)
        store.delete(key: kPassword)
    }
}
