import SwiftUI

/// Resolves a TenantConfig's branding block into SwiftUI colors and fonts.
/// Falls back to system defaults if config is missing or parsing fails.
struct TenantTheme {
    let primary: Color
    let secondary: Color
    /// Body-text design. Left `.default` deliberately: San Francisco is optimised for
    /// UI legibility at small sizes and for Dynamic Type, and swapping body copy to a
    /// geometric face costs readability for no brand gain — the brand reads in the
    /// display face and the palette, not in a settings label.
    let font: Font.Design
    /// Family used for brand/display type: large numerals, hero titles, the wordmark.
    ///
    /// `nil` means "use the system face", which is what every non-Meridian tenant gets.
    /// Chosen to match the Meridian Motors wordmark, which is a light-weight geometric
    /// sans in wide caps. `AvenirNext-UltraLight` is the closest face that SHIPS WITH
    /// iOS — verified by enumerating `UIFont.familyNames` on the simulator — so there is
    /// no bundled binary and no licence to track. Futura was the first instinct and is
    /// the nearer shape, but iOS carries only `Futura-Medium`; at the wordmark's weight
    /// that reads far too heavy. Helvetica Neue has the light weights but is a
    /// neo-grotesque, which fights the logo's circular O and D.
    let displayFontName: String?
    let displayName: String
    let greeting: String

    /// Display face at `size`, falling back to the system font when unset or when the
    /// named family is unavailable on the running OS.
    ///
    /// `Font.custom(_:size:)` silently substitutes a system face for an unknown name, so
    /// a missing family degrades rather than crashing — but it also means a typo is
    /// invisible. `TenantThemeTests` asserts the name resolves.
    func displayFont(size: CGFloat) -> Font {
        guard let displayFontName else { return .system(size: size, design: font) }
        return .custom(displayFontName, size: size)
    }

    /// Used whenever no tenant config is available — which is EVERY pre-auth surface, plus
    /// any error path that renders during load.
    ///
    /// Was a generic "fleet navy" palette with displayName "VSA", so the sign-in screen and
    /// the splash could not be branded no matter what the tenant config said. Now inherits
    /// `AppBrand`, because Meridian is the app: the sensible default identity is the
    /// product's own, not a placeholder.
    static let fallback = TenantTheme(
        primary: AppBrand.primary,
        secondary: Color(red: 0.62, green: 0.66, blue: 0.72),  // neutral silver
        font: .default,
        displayFontName: AppBrand.displayFontName,
        displayName: AppBrand.displayName,
        greeting: "Drive confidently — your dealer is one tap away."
    )

    /// Light geometric sans matching the Meridian Motors wordmark. Ships with iOS.
    static let meridianDisplayFont = "AvenirNext-UltraLight"

    static func from(_ config: TenantConfig?) -> TenantTheme {
        guard let c = config else { return .fallback }
        return TenantTheme(
            primary: Color(hex: c.branding.primaryColor) ?? fallback.primary,
            secondary: Color(hex: c.branding.secondaryColor) ?? fallback.secondary,
            font: .default,
            // Meridian is the app (2026-08-21), so the display face is applied for its
            // tenant only. Keyed on displayName rather than tenantId because the id is
            // still literally `ford` — an internal key whose rename is tracked
            // separately — and keying on it here would bake that inconsistency into
            // typography as well.
            displayFontName: c.displayName.hasPrefix("Meridian") ? Self.meridianDisplayFont : nil,
            displayName: c.displayName,
            greeting: c.branding.greeting.app
        )
    }
}

extension Color {
    init?(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(
            red: Double((v >> 16) & 0xff) / 255,
            green: Double((v >> 8) & 0xff) / 255,
            blue: Double(v & 0xff) / 255
        )
    }
}

/// Build-time brand identity for the app itself.
///
/// Meridian is the app, not one tenant among several (product decision 2026-08-21), so the
/// identity is a compile-time constant rather than something resolved from data.
///
/// ## Why this exists alongside `TenantTheme`
///
/// `TenantTheme` resolves colours from `TenantConfig.branding`, which arrives **after
/// authentication**. Every surface that renders before a tenant is known — the sign-in
/// screen, the splash, any error path that fires during load — therefore got
/// `TenantTheme.fallback`, and that fallback was a generic "fleet navy" palette with the
/// display name `"VSA"`. So the first screen a user ever saw could not be branded at all,
/// no matter what the tenant config said.
///
/// `AppBrand` is the answer to that: an identity that needs no network call. `TenantTheme`
/// still exists and still resolves per-tenant colours for authenticated surfaces — but its
/// fallback is now Meridian rather than a generic default, so pre-auth and error paths
/// inherit the real brand.
///
/// ## Changing the brand
///
/// To ship this app as a different marque, change the values here. Nothing else in the app
/// should hardcode a brand colour, name, or asset name — and `AppBrandTests` asserts the
/// palette matches `AccentColor` so the two cannot drift, which they had.
enum AppBrand {

    /// Company name, as it appears in user-facing copy.
    ///
    /// The short form "Meridian" is used for `CFBundleDisplayName` (the home-screen label,
    /// where width is tight); this is the full form for prose and headings.
    static let displayName = "Meridian Motors"

    /// Primary brand colour, `#003478`.
    ///
    /// Must equal the `AccentColor` asset. These are separate mechanisms — SwiftUI reads
    /// this, and UIKit-backed system controls read the asset — and they had silently
    /// disagreed (`#1A3CA8` vs `#003478`), so the app showed two blues depending on
    /// whether a control was ours or Apple's.
    static let primary = Color(red: 0x00 / 255, green: 0x34 / 255, blue: 0x78 / 255)

    /// Wordmark asset. Near-white ink (#E0E0E0) on transparent, so it REQUIRES a dark
    /// backing — it is invisible on white or light grey. There is no dark-ink variant, and
    /// no appearance variants in the imageset, which is why every surface showing it also
    /// supplies a dark background.
    static let logoAssetName = "MeridianLogo"

    /// Display face for large numerals and brand type. Ships with iOS; see
    /// `TenantTheme.displayFontName` for why this face and not Futura.
    static let displayFontName = "AvenirNext-UltraLight"

    /// The brand's dark backdrop, sampled from the app icon rather than invented.
    ///
    /// Measured down the icon's centre column: `#000101` at the top through `#0B1D42` at
    /// the bottom. Reusing it means the launch screen, the splash and the sign-in screen
    /// all sit on the same surface the user just tapped on the home screen, instead of
    /// three approximations of navy.
    static let backgroundTop = Color(red: 0x00 / 255, green: 0x01 / 255, blue: 0x01 / 255)
    static let backgroundBottom = Color(red: 0x0B / 255, green: 0x1D / 255, blue: 0x42 / 255)

    /// Full-bleed brand backdrop. Use on pre-auth surfaces, where the wordmark needs dark
    /// ink behind it and no tenant colour is available yet.
    static var backgroundGradient: LinearGradient {
        LinearGradient(
            colors: [backgroundTop, backgroundBottom],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    /// Text colours for use directly on `backgroundGradient`, which is dark in both
    /// appearances. Fixed, not semantic: the sign-in screen's `.preferredColorScheme(.dark)`
    /// loses to the root's `.preferredColorScheme(appearance.colorScheme)`, so in light mode
    /// `.primary`/`.secondary`/`Color(.label)` resolve to dark ink on this dark surface.
    static let onBackdropPrimary = Color.white
    static let onBackdropSecondary = Color.white.opacity(0.75)
    static let onBackdropTertiary = Color.white.opacity(0.55)
}
