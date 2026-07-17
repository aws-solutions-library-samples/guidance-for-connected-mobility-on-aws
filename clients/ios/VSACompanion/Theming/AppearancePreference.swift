import SwiftUI

/// User-controllable appearance override.
///
/// Stored locally per-device via `@AppStorage("appearancePreference")`,
/// applied at the app's root with `.preferredColorScheme(_:)`. The
/// system option (default) lets iOS drive the appearance based on the
/// user's Settings → Display & Brightness choice; light/dark force the
/// app into that mode regardless of the system setting.
///
/// Why string-backed: `@AppStorage` only handles a handful of native
/// types out of the box. RawRepresentable<String> support means we
/// store the case name in UserDefaults and SwiftUI rehydrates the enum
/// on read.
enum AppearancePreference: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    /// Human-facing label for the picker.
    var label: String {
        switch self {
        case .system: return "System"
        case .light:  return "Light"
        case .dark:   return "Dark"
        }
    }

    /// SF Symbol that pairs with the label in the picker.
    var systemImage: String {
        switch self {
        case .system: return "iphone"
        case .light:  return "sun.max"
        case .dark:   return "moon"
        }
    }

    /// What to feed into `.preferredColorScheme(_:)`. `nil` means
    /// "defer to the system setting" — SwiftUI's documented behavior
    /// for that modifier.
    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light:  return .light
        case .dark:   return .dark
        }
    }

    /// UserDefaults key, exposed so view code that reads/writes via
    /// `@AppStorage` keeps the spelling in one place.
    static let storageKey = "appearancePreference"
}
