import SwiftUI

/// Continuously-rotating view of the owned vehicle, rendered from a bundled frame
/// sequence — as either a full turntable or a partial arc.
///
/// ## Rotation is automatic; drag is optional
///
/// The view rotates on its own for as long as it is on screen. There is no
/// "Drag to rotate" hint, because the motion is itself the affordance — an
/// unattended kiosk visitor sees the vehicle turning without touching anything,
/// which is the point. Drag still works and takes precedence while active, then
/// automatic rotation resumes after a short hold.
///
/// A one-shot intro animation plus a drag hint was the earlier design (removed
/// 2026-08-18 on user direction). It optimised for teaching a gesture most
/// visitors will never use.
///
/// ## Two rotation modes, because the assets genuinely differ
///
/// Generated clips do not reliably produce a full revolution. Of the four Meridian
/// assets, one is a full 360 and three are partial arcs, so the view carries a
/// `RotationMode` and each registry entry declares which it is.
///
/// - `.fullTurntable` — frame N is adjacent to frame 1. Rotation runs one way
///   forever and drag **wraps**.
/// - `.partialArc` — the sequence does not close. Rotation **bounces** at both
///   ends and drag **clamps**.
///
/// Getting this wrong is visible, not theoretical: running a partial arc one-way
/// snaps across the width of the car every cycle, and bouncing a full turntable
/// throws away half the vehicle.
///
/// **The mode enum supersedes an earlier decision in this file's history.** The
/// first version deliberately refused a mode flag, reasoning that a wrapping
/// turntable and a clamped sweep differ in gesture handling, playback *and*
/// user-facing copy, and that one view carrying both tends to get the copy wrong.
/// That held while only a partial arc existed. A real full-360 asset arrived, and
/// the honest read is that the difference is a small, enumerable set — which an
/// enum expresses precisely. The copy risk is answered by deriving the
/// accessibility string **from the mode** rather than letting a caller pass it.
///
/// ## Measured arcs (2026-08-18)
///
/// - **Crestwind** — 1 ≈ +45°, 13 = 0° (front), 24 ≈ −50°. About **95°**. `.partialArc`.
/// - **Trailwind** — 1 = front-left, 7 = left profile, 13 = dead-on rear,
///   19 = right-rear, 32 closes toward 1. **Full revolution.** `.fullTurntable`.
/// - **Windrose** — 1 ≈ +40°, 17 ≈ +105° profile, 32 ≈ 180° rear. About **140°**,
///   reaches the rear but does not close. `.partialArc`.
/// - **Azimuth** — 1 ≈ +40°, 17 = 0° (front), 24 ≈ −45°. About **85°**. `.partialArc`.
///
/// ## Relationship to `Vehicle360View`
///
/// `Vehicle360View` is the **remote** turntable: it fetches a numbered sequence
/// from `VSA_VEHICLE_360_BASE_URL`, bundles nothing, and exists because real tenant
/// imagery cannot ship in committed source — real-OEM brand strings are
/// canary-forbidden. This view is its **bundled** counterpart, which is permissible
/// only because Meridian is a *fictional* demo brand, the same allowance the
/// `bundledHeroImage` sites already rely on.
///
/// The two are not merged because their failure modes differ: the remote one must
/// survive HTTP status codes, timeouts and CDNs that reject requests without a
/// browser-ish User-Agent, none of which applies to a bundle read.
struct VehicleSweepView: View {

    /// How the frame sequence behaves at its ends.
    enum RotationMode: Equatable {
        /// Sequence closes — frame N is adjacent to frame 1.
        case fullTurntable
        /// Sequence does not close; ends are hard stops.
        case partialArc

        var accessibilityDescription: String {
            switch self {
            case .fullTurntable:
                return "Rotating 360 degree view of your vehicle. "
                    + "Swipe horizontally to control it."
            case .partialArc:
                return "Rotating view of your vehicle. "
                    + "Swipe horizontally to control it."
            }
        }
    }

    /// Bundle subdirectory holding `1.jpg` … `<frameCount>.jpg`.
    let resourceSubdirectory: String
    let frameCount: Int
    let theme: TenantTheme
    let rotationMode: RotationMode
    /// Rendered height. Matches the static hero it replaces.
    var height: CGFloat = 190

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var frames: [UIImage] = []
    @State private var currentIndex: Int = 0
    @State private var isLoading = true
    @State private var hasAutoPlayed = false
    /// Frame index when the active drag began, so scrubbing is relative.
    /// `-1` means no drag in progress.
    @State private var dragStartIndex: Int = -1
    /// Background sampled from the asset itself. See `stageColor`.
    @State private var sampledStageColor: Color?
    /// True while a drag is in flight, so automatic rotation yields to the user.
    @State private var isDragging = false
    /// Automatic rotation stays paused until this instant. Set when a drag ends.
    @State private var resumeAutoAfter: Date = .distantPast
    /// Ping-pong direction for `.partialArc`: `+1` forward, `-1` back.
    @State private var sweepDirection: Int = 1
    /// Aspect (width / height) of the loaded frames, so the container can match.
    @State private var imageAspect: CGFloat?

    /// Aspect used before frames load, so the placeholder does not jump size.
    /// Close to the cropped assets' ~2:1 so the settle is imperceptible.
    private static let fallbackAspect: CGFloat = 2.0

    /// Horizontal points of drag per frame advance. Tuned so a comfortable
    /// thumb sweep covers the whole arc.
    private static let pointsPerFrame: CGFloat = 12

    /// Seconds between automatic frame advances.
    ///
    /// Deliberately much slower than the 55 ms the old one-shot intro used. That
    /// value was tuned for a single quick reveal; applied to *continuous* rotation
    /// it spins the Trailwind's 32 frames in 1.8 s, which reads as a blur rather
    /// than a vehicle. At 140 ms a full revolution takes ~4.5 s and an arc's
    /// round trip ~6.7 s, which is slow enough to look at.
    private static let autoFrameInterval: TimeInterval = 0.14

    /// How long automatic rotation waits after the user lets go.
    private static let resumeDelaySeconds: TimeInterval = 2.5

    var body: some View {
        ZStack {
            // Single container. The stage colour is sampled from the asset so the
            // image's own baked background continues seamlessly into it — see
            // `stageColor` — which is what stops this reading as a picture inside a
            // box inside a card.
            //
            // Deliberately NO inner padding: the earlier `.padding(6)` inset the
            // frame from its own stage, producing a visible third boundary on top
            // of `SectionCard`'s rounded rect, brand tint and stroke border. With
            // the frames now cropped to content, the image fills this container
            // edge to edge.
            Rectangle()
                .fill(stageColor)

            if let image = currentFrame {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .accessibilityLabel(rotationMode.accessibilityDescription)
            } else if isLoading {
                ProgressView().tint(theme.primary)
            } else {
                Image(systemName: "car.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Vehicle image unavailable")
            }
        }
        // Height follows the asset's aspect rather than being fixed.
        //
        // The frames are cropped to content, so their aspects now differ per model
        // (2.43:1 Azimuth … 1.78:1 Windrose). A single fixed height letterboxes the
        // wide ones — the Azimuth in a 190pt box wasted ~54pt vertically, which is
        // precisely the "small image in a big container" look this is meant to fix.
        // `height` is now a ceiling, not a target.
        .aspectRatio(imageAspect ?? Self.fallbackAspect, contentMode: .fit)
        .frame(maxHeight: height)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .contentShape(Rectangle())
        .gesture(
            DragGesture()
                .onChanged { value in
                    guard !frames.isEmpty else { return }
                    if dragStartIndex == -1 { dragStartIndex = currentIndex }
                    isDragging = true
                    let delta = Int(value.translation.width / Self.pointsPerFrame)
                    currentIndex = resolve(dragStartIndex - delta)
                }
                .onEnded { _ in
                    dragStartIndex = -1
                    isDragging = false
                    // Hold before the automatic rotation takes over again, so it
                    // does not immediately pull the vehicle away from the angle
                    // the user just chose.
                    resumeAutoAfter = Date().addingTimeInterval(Self.resumeDelaySeconds)
                }
        )
        .task { await loadFrames() }
    }

    private var currentFrame: UIImage? {
        guard !frames.isEmpty else { return nil }
        return frames[resolve(currentIndex)]
    }

    /// Resolves an index according to the rotation mode.
    ///
    /// `.fullTurntable` wraps, so dragging past either end keeps spinning.
    /// `.partialArc` clamps: the arc is open — index 0 is one three-quarter view
    /// and the last index is the other — so wrapping would jump ~95° across the
    /// front of the car, which reads as the image glitching rather than as
    /// continuous rotation.
    private func resolve(_ i: Int) -> Int {
        guard !frames.isEmpty else { return 0 }
        let n = frames.count
        switch rotationMode {
        case .fullTurntable:
            return ((i % n) + n) % n
        case .partialArc:
            return min(max(i, 0), n - 1)
        }
    }

    // MARK: - Stage background

    /// Background the vehicle sits on.
    ///
    /// ## Why this is sampled from the asset, not a semantic system colour
    ///
    /// These renders carry their own studio background baked in — measured
    /// 2026-08-18: Trailwind and Windrose are essentially pure white
    /// (`#FDFFFE`–`#FFFFFF`), Crestwind is a light grey gradient
    /// (`#E4E4E6`–`#EFF0F4`). Whatever sits behind the image is visible around it,
    /// because the frame is `scaledToFit` inside a taller container.
    ///
    /// The obvious approach — leave the container on
    /// `Color(.tertiarySystemGroupedBackground)` — fails, and so does the
    /// mirror-image idea of baking a matching hex into the render. Both fail for
    /// the same reason: `tertiarySystemGroupedBackground` is a **dynamic** colour,
    /// near-white in light mode and dark grey in dark mode, and this app lets the
    /// user pick light/dark/system (`AppearancePreference`, applied at the root via
    /// `.preferredColorScheme`). A single baked hex can match exactly one
    /// appearance and clashes in the other — and the clash is the conspicuous
    /// direction: a white square glowing on a dark card.
    ///
    /// So the container matches the **asset** instead of the theme. The vehicle
    /// reads as a product shot on a lit stage in both appearances, which is how
    /// vehicle configurators generally present a car, and it needs no coordination
    /// between the image-generation prompt and this code.
    ///
    /// Sampling rather than hardcoding means a re-rendered asset with a different
    /// background self-corrects, and the Crestwind's grey is absorbed without
    /// re-rendering it. A gradient background cannot be matched exactly by one
    /// flat colour, but the Crestwind's corners span only ~11/255, which is below
    /// the threshold where an abutting edge is visible.
    private var stageColor: Color {
        sampledStageColor ?? Color(white: 0.98)
    }

    /// Samples a frame's corner to use as the stage colour.
    ///
    /// Inset from the true corner because JPEG ringing at the extreme edge can
    /// skew a single-pixel read. Returns `nil` on any failure so the caller falls
    /// back to a fixed near-white rather than rendering a black stage — the
    /// failure mode of an unchecked `CGImage` read.
    private static func sampleCornerColor(of image: UIImage) -> Color? {
        guard let cg = image.cgImage else { return nil }
        let inset = 8
        guard cg.width > inset * 2, cg.height > inset * 2 else { return nil }

        var pixel = [UInt8](repeating: 0, count: 4)
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(
                data: &pixel, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
                space: space,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return nil }

        // Draw the image translated so the sampled pixel lands in the 1x1 context.
        ctx.draw(cg, in: CGRect(x: -CGFloat(inset), y: -CGFloat(inset),
                                width: CGFloat(cg.width), height: CGFloat(cg.height)))

        return Color(.sRGB,
                     red: Double(pixel[0]) / 255.0,
                     green: Double(pixel[1]) / 255.0,
                     blue: Double(pixel[2]) / 255.0,
                     opacity: 1.0)
    }

    /// Load every frame from the bundle before showing any.
    ///
    /// Bundled reads are fast enough that this is imperceptible, and loading all
    /// of them up front keeps scrubbing free of the decode stutter that made
    /// per-frame lazy loading unusable for the remote turntable.
    private func loadFrames() async {
        guard frames.isEmpty, frameCount > 0 else {
            isLoading = false
            return
        }

        var loaded: [UIImage] = []
        loaded.reserveCapacity(frameCount)

        for i in 1...frameCount {
            guard let url = Bundle.main.url(forResource: "\(i)",
                                            withExtension: "jpg",
                                            subdirectory: resourceSubdirectory),
                  let data = try? Data(contentsOf: url),
                  let img = UIImage(data: data) else {
                // A missing middle frame shortens the arc but does not break it.
                // A missing FIRST frame usually means the resource folder did not
                // ship, which the empty-frames fallback surfaces.
                continue
            }
            loaded.append(img)
        }

        await MainActor.run {
            frames = loaded
            isLoading = false
            dragStartIndex = -1
            // Match the stage to the asset's own background — see `stageColor`.
            if let first = loaded.first {
                sampledStageColor = Self.sampleCornerColor(of: first)
                if first.size.height > 0 {
                    imageAspect = first.size.width / first.size.height
                }
            }
            // Start at frame 1. For the arc that is the three-quarter end, which
            // is the most flattering static view; for a turntable it is wherever
            // the render began, which is conventionally a three-quarter too.
            currentIndex = 0
            if !loaded.isEmpty && !hasAutoPlayed && !reduceMotion {
                hasAutoPlayed = true
                Task { await autoRotateLoop() }
            }
        }
    }

    /// Rotates continuously for as long as the view is on screen.
    ///
    /// Replaces the earlier one-shot intro animation. The intro existed to make a
    /// drag affordance discoverable; with the affordance hint removed, the motion
    /// itself is the presentation — the vehicle simply turns, and no visitor action
    /// is required to see it. That suits an unattended kiosk, where most visitors
    /// will never touch the image.
    ///
    /// Drag still works and takes precedence: `isDragging` suspends this loop, and
    /// `resumeAutoAfter` holds it off for a couple of seconds once the user lets go
    /// so it does not immediately drag the vehicle off the angle they picked.
    ///
    /// Cancellation is automatic — `.task` tears this down when the view goes away,
    /// which matters on a kiosk where the journey subtree is destroyed on reset.
    /// A loop surviving that would keep waking the CPU every 140 ms forever.
    ///
    /// Skipped entirely under Reduce Motion, where the view settles on frame 1.
    private func autoRotateLoop() async {
        guard frames.count > 1 else { return }

        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: UInt64(Self.autoFrameInterval * 1_000_000_000))
            if Task.isCancelled { return }

            // Yield to the user, and stay yielded briefly after they finish.
            if isDragging || Date() < resumeAutoAfter { continue }

            await MainActor.run { advanceOneAutoStep() }
        }
    }

    /// Advances one frame in the direction the mode implies.
    ///
    /// `.fullTurntable` runs one way forever — the sequence closes, so it never
    /// needs to turn around. `.partialArc` bounces at both ends, which is the only
    /// way an open arc can rotate indefinitely without a ~95–180° snap back to the
    /// start.
    private func advanceOneAutoStep() {
        let n = frames.count
        guard n > 1 else { return }

        switch rotationMode {
        case .fullTurntable:
            currentIndex = resolve(currentIndex + 1)

        case .partialArc:
            var next = currentIndex + sweepDirection
            if next >= n {
                // Step back inside and reverse. Using n-2 rather than n-1 avoids
                // rendering the end frame twice in a row, which reads as a stall.
                sweepDirection = -1
                next = max(0, n - 2)
            } else if next < 0 {
                sweepDirection = 1
                next = min(n - 1, 1)
            }
            currentIndex = resolve(next)
        }
    }
}

// MARK: - Bundled sweep registry

extension VehicleSweepView {

    /// Describes a bundled frame sequence.
    struct BundledSweep: Equatable {
        /// Bundle subdirectory containing `1.jpg` … `frameCount.jpg`.
        let subdirectory: String
        let frameCount: Int
        /// Whether this sequence closes. Declared per asset because generated
        /// clips do not reliably produce a full revolution — see the type
        /// docstring for the measured arcs.
        let rotationMode: RotationMode
    }

    /// Bundled sweep for a specific make **and model**, or `nil` when none ships.
    ///
    /// Keyed on model, not make. The Meridian lineup spans four distinct body
    /// styles — Crestwind (large three-row SUV), Trailwind (mid SUV), Azimuth
    /// (sedan) and Windrose (compact/urban) — so a make-only lookup would show one
    /// body style for all four. The mapping mirrors
    /// `IvePackage.defaultFor(modelId:)` in `Models.swift`, which resolves the same
    /// four model slugs; keep the two in step.
    ///
    /// Defined **once** here rather than duplicated per tab. `bundledHeroImage(for:)`
    /// is currently copied verbatim into both `HomeTabView` and `VehicleTabView`, so
    /// those can drift — and a model that gets a sweep on one tab and a static image
    /// on the other is the kind of inconsistency nobody notices until it is on a
    /// screen behind a presenter.
    ///
    /// ## Returning `nil` is correct for a model with no art
    ///
    /// A model absent from this switch falls through to the caller's static-image
    /// and then glyph fallback. That is deliberate: rendering the Crestwind's
    /// three-row body for a Windrose compact would show the **wrong vehicle**, and
    /// this codebase already treats that as worse than showing none — see
    /// `UpgradeFlow.heroImage` ("showing the WRONG vehicle is far worse than
    /// showing none"). Add a model here only once its frames actually ship.
    ///
    /// Only **fictional** demo brands may appear here. Real-OEM brand strings are
    /// canary-forbidden in committed source and their imagery must arrive at runtime
    /// via `VSA_VEHICLE_360_BASE_URL`. Meridian is fictional.
    ///
    /// `frameCount` must match what `scripts/extract-360-frames.swift` produced; a
    /// value higher than the shipped file count silently shortens the arc rather
    /// than failing, because a missing frame is skipped on load.
    /// Asset-name suffix for a vehicle's generation, or `""` for the current one.
    ///
    /// Only the Trailwind ships two generations. Every other model returns `""` and
    /// therefore resolves to its single current-generation asset — **that fallback is
    /// the point**: a 2019 Crestwind should show the Crestwind we have, not a glyph.
    /// Returning nil for "generation not bundled" would trade a slightly-wrong year
    /// for no vehicle at all, which is the worse outcome.
    ///
    /// The 2024 cutoff is where the Trailwind's design language changes: the 2023 car
    /// has separate headlamps, chrome lower trim and smaller wheels, the 2026 has a
    /// full-width light bar and black cladding. A car from either side of that line is
    /// recognisably one or the other.
    private static func generationSuffix(model: String, year: Int?) -> String {
        guard model.contains("trailwind"), let year, year <= 2024 else { return "" }
        return "2023"
    }

    /// Extracts a 4-digit model year from free text, e.g. "Meridian Trailwind 2026".
    ///
    /// Offers carry the year inside the marketing name rather than as a field. Bounded
    /// to a plausible range so a price or a trim code cannot be mistaken for a year.
    private static func inferredYear(in text: String) -> Int? {
        let digits = text.split(whereSeparator: { !$0.isNumber })
        for token in digits where token.count == 4 {
            if let y = Int(token), (1990...2100).contains(y) { return y }
        }
        return nil
    }

    static func bundledSweep(make: String?, model: String?, year: Int? = nil) -> BundledSweep? {
        guard let make, make == "Meridian" else { return nil }
        let m = (model ?? "").lowercased()
        let gen = generationSuffix(model: m, year: year)

        // Second-generation Trailwind assets, when the vehicle predates the redesign.
        if !gen.isEmpty, m.contains("trailwind") {
            // ~90° arc, verified 2026-08-19: frame 1 ≈ +40°, 17 = dead-on front,
            // 32 ≈ −50°. Same pivot-through-front shape as the Crestwind and Azimuth,
            // so it gets their 24-frame density.
            return BundledSweep(subdirectory: "MeridianTrailwind2023Sweep",
                                frameCount: 24,
                                rotationMode: .partialArc)
        }

        if m.contains("crestwind") {
            // ~95° arc — no profile, rear, or far side. Clamps and ping-pongs.
            return BundledSweep(subdirectory: "MeridianCrestwindSweep",
                                frameCount: 24,
                                rotationMode: .partialArc)
        }
        if m.contains("trailwind") {
            // Full revolution, verified 2026-08-18: frame 7 is left profile,
            // 13 is dead-on rear, 19 is right-rear, and 32 closes back toward
            // frame 1. 32 frames = 11.2° per step.
            return BundledSweep(subdirectory: "MeridianTrailwindSweep",
                                frameCount: 32,
                                rotationMode: .fullTurntable)
        }
        if m.contains("windrose") {
            // ~140° arc, verified 2026-08-18: frame 1 ≈ +40° front three-quarter,
            // 17 ≈ +105° profile, 32 ≈ 180° dead-on rear. Larger than the
            // Crestwind's arc and it reaches the rear, but it does NOT close —
            // frame 32 back to frame 1 would jump ~180°. Ping-pongs, which suits
            // this asset well: the sweep reveals front, flank and rear.
            return BundledSweep(subdirectory: "MeridianWindroseSweep",
                                frameCount: 32,
                                rotationMode: .partialArc)
        }
        if m.contains("azimuth") {
            // ~85° arc, verified 2026-08-18: frame 1 ≈ +40°, 17 = dead-on front,
            // 24 ≈ −45°. Pivots through the front; no profile or rear. Same shape
            // as the Crestwind, so it gets the same 24-frame density (~3.5°/frame)
            // rather than the 32 used for wider sequences.
            return BundledSweep(subdirectory: "MeridianAzimuthSweep",
                                frameCount: 24,
                                rotationMode: .partialArc)
        }

        // Every model in the Meridian lineup now has art. A model added to the
        // lineup without art should return nil here rather than borrow another
        // body style — see `UpgradeFlow.heroImage` ("showing the WRONG vehicle is
        // far worse than showing none").
        return nil
    }

    /// Name of the bundled static image for a make/model, or `nil` when none ships.
    ///
    /// Fallback for when no sweep exists. Same model-keyed reasoning and the same
    /// wrong-vehicle constraint as `bundledSweep(make:model:)`.
    static func bundledStaticImageName(make: String?, model: String?,
                                       year: Int? = nil) -> String? {
        guard let make, make == "Meridian" else { return nil }
        guard let base = imageName(forModelToken: model) else { return nil }
        let gen = generationSuffix(model: (model ?? "").lowercased(), year: year)
        return base + gen
    }

    /// Bundled static image for an **upgrade offer's** marketing model name.
    ///
    /// Offers carry a single free-text string like `"Meridian Crestwind Signature"`
    /// rather than a make/model pair, so they cannot use the overload above.
    ///
    /// Exists because `AcquireConfig.UpgradeOffer.imageUrl` is **deliberately nil**
    /// for every seeded offer (see `deployment/scripts/seed_tenant_acquire_offers.py`,
    /// which notes a broken remote image is worse than none). With it nil, every
    /// offer surface fell back to a glyph and the customer never saw the vehicle
    /// being sold to them. Bundled art fills that gap without introducing a network
    /// dependency.
    ///
    /// **The brand token is required, not optional.** A bare `"crestwind"` match on
    /// any tenant's offer string would render Meridian art for a different brand's
    /// vehicle — the wrong-vehicle failure `UpgradeFlow.heroImage` already guards
    /// against. `UpgradeOffer` carries no `make`, so requiring `"meridian"` in the
    /// string is the available guard.
    ///
    /// Precedence at every call site is: `offer.imageUrl` → this → glyph. The
    /// tenant-supplied URL is offer-specific and wins; this is the fallback.
    static func bundledStaticImageName(forOfferModelName name: String?) -> String? {
        guard let name, name.lowercased().contains("meridian") else { return nil }
        guard let base = imageName(forModelToken: name) else { return nil }
        // The year lives inside the offer's marketing name ("Meridian Trailwind
        // 2026"). Absent a year, an offer is for a NEW vehicle, so current-generation
        // is the right default — an offer must never illustrate itself with the
        // outgoing car.
        let gen = generationSuffix(model: name.lowercased(),
                                   year: inferredYear(in: name))
        return base + gen
    }

    /// Maps any text containing a Meridian model token to its bundled image name.
    ///
    /// Shared by both overloads so the model→asset mapping exists once. Matching is
    /// case-insensitive and substring-based, which tolerates `"Crestwind"`,
    /// `"crestwind-lx"` and `"Meridian Crestwind Signature"` alike — mirroring
    /// `IvePackage.defaultFor(modelId:)`.
    private static func imageName(forModelToken text: String?) -> String? {
        let m = (text ?? "").lowercased()
        if m.contains("crestwind") { return "MeridianCrestwind" }
        if m.contains("trailwind") { return "MeridianTrailwind" }
        if m.contains("windrose")  { return "MeridianWindrose" }
        if m.contains("azimuth")   { return "MeridianAzimuth" }
        return nil
    }
}
