import SwiftUI

/// Drag-to-rotate 360° view of the owned vehicle.
///
/// Renders a numbered frame sequence — `<base>/1.png` … `<base>/<N>.png` —
/// as a turntable. Horizontal drag scrubs frames; on first appear it plays one
/// slow revolution so the interaction is discoverable without a hint label.
///
/// ## Why the frames are remote and unconfigured by default
///
/// The base URL is supplied at build time via `VSAConfig.vehicle360BaseUrl`,
/// which reads `VSA_VEHICLE_360_BASE_URL` from the gitignored
/// `Staging.local.xcconfig`. A real value points at a tenant's own product
/// imagery, and tenant brand strings are canary-forbidden in committed source
/// (see `.publish-secrets-scan.yml`). No image is bundled and no URL is
/// committed. When the setting is absent this view renders nothing and the
/// caller falls back to a themed glyph, so an unconfigured build is not a
/// broken build.
///
/// ## Decoding note
///
/// The frames are commonly served as WebP even when the path ends `.png`, and
/// with `Content-Type: application/octet-stream`. `UIImage(data:)` sniffs the
/// container via ImageIO, so both decode correctly; do not add an
/// extension-based format check.
struct Vehicle360View: View {
    let baseUrl: String
    let frameCount: Int
    let theme: TenantTheme

    /// Decoded frames, index 0 == frame 1. Empty until the prefetch resolves.
    @State private var frames: [UIImage] = []
    @State private var currentIndex: Int = 0
    @State private var isLoading: Bool = true
    @State private var loadFailed: Bool = false
    @State private var hasAutoSpun: Bool = false
    /// Frame index when the active drag began, so scrubbing is relative.
    @State private var dragStartIndex: Int = 0

    /// Horizontal points of drag per frame advance. Tuned so a comfortable
    /// thumb sweep covers most of a revolution.
    private static let pointsPerFrame: CGFloat = 14

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(.tertiarySystemGroupedBackground))

            if let image = currentFrame {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .padding(6)
                    .accessibilityLabel("360 degree view of your vehicle. "
                                        + "Swipe horizontally to rotate.")
            } else if isLoading {
                ProgressView().tint(theme.primary)
            } else {
                // Loaded nothing and not still loading -> network or config
                // problem. Stay quiet visually; the caller's fallback glyph
                // is not shown because this view already occupies the slot.
                Image(systemName: "cube.transparent")
                    .font(.system(size: 34))
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Vehicle image unavailable")
            }

            if !frames.isEmpty {
                VStack {
                    Spacer()
                    HStack(spacing: 6) {
                        Image(systemName: "hand.draw")
                        Text("Drag to rotate")
                    }
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.bottom, 6)
                }
            }
        }
        .frame(height: 190)
        .contentShape(Rectangle())
        .gesture(
            DragGesture()
                .onChanged { value in
                    guard !frames.isEmpty else { return }
                    if dragStartIndex == -1 { dragStartIndex = currentIndex }
                    let delta = Int(value.translation.width / Self.pointsPerFrame)
                    currentIndex = wrap(dragStartIndex - delta)
                }
                .onEnded { _ in dragStartIndex = -1 }
        )
        .task { await loadFrames() }
    }

    private var currentFrame: UIImage? {
        guard !frames.isEmpty else { return nil }
        return frames[wrap(currentIndex)]
    }

    /// Frame indices are circular — dragging past either end keeps spinning.
    private func wrap(_ i: Int) -> Int {
        guard !frames.isEmpty else { return 0 }
        let n = frames.count
        return ((i % n) + n) % n
    }

    /// Prefetch every frame before showing any of them.
    ///
    /// Deliberately not `AsyncImage` per frame: scrubbing through lazily
    /// loaded frames flickers and stutters, which reads as broken in a live
    /// demo. Sixteen frames at roughly 60 KB is about 1 MB — acceptable to
    /// hold for a screen the user opened on purpose.
    private func loadFrames() async {
        guard frames.isEmpty, frameCount > 0,
              let base = URL(string: baseUrl) else {
            isLoading = false
            loadFailed = frameCount > 0
            return
        }

        var loaded: [UIImage] = []
        loaded.reserveCapacity(frameCount)

        for i in 1...frameCount {
            let url = base.appendingPathComponent("\(i).png")
            do {
                var request = URLRequest(url: url)
                request.timeoutInterval = 12
                // Some CDNs reject requests without a browser-ish UA.
                request.setValue("Mozilla/5.0", forHTTPHeaderField: "User-Agent")
                let (data, response) = try await URLSession.shared.data(for: request)
                if let http = response as? HTTPURLResponse,
                   !(200..<300).contains(http.statusCode) {
                    continue
                }
                if let img = UIImage(data: data) { loaded.append(img) }
            } catch {
                // One bad frame should not kill the turntable — a 15-frame
                // spin is indistinguishable from 16 at this size.
                continue
            }
        }

        await MainActor.run {
            frames = loaded
            isLoading = false
            loadFailed = loaded.isEmpty
            dragStartIndex = -1
            if !loaded.isEmpty && !hasAutoSpun {
                hasAutoSpun = true
                Task { await autoSpin() }
            }
        }
    }

    /// One slow revolution on first appear, so the affordance is obvious.
    private func autoSpin() async {
        let n = frames.count
        guard n > 1 else { return }
        for step in 1...n {
            try? await Task.sleep(nanoseconds: 55_000_000)
            await MainActor.run { currentIndex = wrap(step) }
        }
    }
}
