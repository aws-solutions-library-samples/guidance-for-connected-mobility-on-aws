import SwiftUI

struct Header: View {
    let theme: TenantTheme
    var onLogoTap: () -> Void

    var body: some View {
        HStack {
            Button(action: onLogoTap) {
                HStack(spacing: 8) {
                    Image(systemName: "car.circle.fill")
                        .foregroundStyle(theme.primary)
                        .font(.title2)
                    Text(theme.displayName).bold()
                }
            }
            .buttonStyle(.plain)
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text("VEH-0025").font(.caption).bold()
                Text("Driver: T. Hassan").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .background(theme.primary.opacity(0.08))
    }
}
