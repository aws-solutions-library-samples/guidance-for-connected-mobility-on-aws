import SwiftUI

/// Resolves a TenantConfig's branding block into SwiftUI colors and fonts.
/// Falls back to system defaults if config is missing or parsing fails.
struct TenantTheme {
    let primary: Color
    let secondary: Color
    let font: Font.Design
    let displayName: String
    let greeting: String

    static let fallback = TenantTheme(
        primary: Color(red: 0.10, green: 0.24, blue: 0.66),   // fleet navy
        secondary: Color(red: 0.96, green: 0.83, blue: 0.37), // warm yellow
        font: .default,
        displayName: "VSA",
        greeting: "Live support for your fleet."
    )

    static func from(_ config: TenantConfig?) -> TenantTheme {
        guard let c = config else { return .fallback }
        return TenantTheme(
            primary: Color(hex: c.branding.primaryColor) ?? fallback.primary,
            secondary: Color(hex: c.branding.secondaryColor) ?? fallback.secondary,
            font: .default,
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
