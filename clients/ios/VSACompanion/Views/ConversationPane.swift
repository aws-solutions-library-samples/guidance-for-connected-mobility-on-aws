import SwiftUI

struct ConversationPane: View {
    let theme: TenantTheme
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Conversation").font(.headline)
            Text(theme.greeting)
                .foregroundStyle(.secondary)
                .padding(.top, 4)
            Spacer()
            HStack {
                Text("Voice arrives in Phase 3 (Nova 2 Sonic)")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                Spacer()
                Image(systemName: "mic.slash")
                    .foregroundStyle(.tertiary)
            }
        }
        .padding()
    }
}
