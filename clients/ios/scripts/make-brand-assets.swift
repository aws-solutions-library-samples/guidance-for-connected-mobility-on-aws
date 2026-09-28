#!/usr/bin/env swift
//
// make-brand-assets.swift — derives the runtime brand assets from the Meridian
// wordmark masters.
//
// Usage:  swift scripts/make-brand-assets.swift          (run from clients/ios)
//
// Inputs:
//   art-sources/meridian/wordmark-logo.png      1024², single-line "MERIDIAN | MOTORS"
//   art-sources/meridian/wordmark-stacked.png   1024², two-line lockup
//
// Outputs:
//   MeridianMotorsCompanion/Assets.xcassets/MeridianLogo.imageset/meridian_logo.png
//       Content-cropped SINGLE-LINE wordmark, for LaunchScreen.storyboard and
//       SplashView's static fallback. Single-line on purpose: the animated
//       reveal is single-line, so a two-line static image would visibly jump at
//       the handoff between the launch screen, the clip, and the fallback.
//
//       The crop itself is a correctness fix, not an optimisation — the master
//       is a 1024² canvas whose wordmark occupies a 657×39 band (~4% of the
//       pixels), so fitting the master into a 200pt box rendered the type about
//       7pt tall.
//
//   MeridianMotorsCompanion/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png
//       Two-line lockup on the app's accent-navy background. Uses the STACKED
//       master as drawn. An earlier version synthesised this by cropping the two
//       words out of the single-line master and re-stacking them at a shared
//       scale — which produced larger type but flattened the designed size
//       hierarchy (MOTORS is intentionally ~half MERIDIAN's cap height). Using
//       the real render preserves the design; see README for the tradeoff.
//
//   art-sources/meridian/appicon-monogram-1024.png
//       Alternative monogram icon (the "M" alone). Not registered. Far more
//       legible at small sizes but visibly soft, being a ~11× upscale of a 36px
//       crop; it would want a vector redraw before shipping.
//
// Letterforms are always CROPPED from a master, never re-typeset: these are
// AI-generated renders with no accompanying font, so re-typesetting would only
// approximate the brand's letterforms.

import AppKit
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// MARK: - Paths

let iosDir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
let lineMaster = iosDir.appendingPathComponent("art-sources/meridian/wordmark-logo.png")
let stackedMaster = iosDir.appendingPathComponent("art-sources/meridian/wordmark-stacked.png")
let logoOut = iosDir.appendingPathComponent(
    "MeridianMotorsCompanion/Assets.xcassets/MeridianLogo.imageset/meridian_logo.png")
let iconOut = iosDir.appendingPathComponent(
    "MeridianMotorsCompanion/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png")
let monoOut = iosDir.appendingPathComponent(
    "art-sources/meridian/appicon-monogram-1024.png")

for m in [lineMaster, stackedMaster] where !FileManager.default.fileExists(atPath: m.path) {
    FileHandle.standardError.write(
        "✗ master not found: \(m.path)\n  run from clients/ios\n".data(using: .utf8)!)
    exit(1)
}

// MARK: - Brand constants

/// App accent (`Assets.xcassets/AccentColor.colorset`, #1A3CA8) — also the
/// TenantTheme fallback "fleet navy". Reused so the icon ties to the app's
/// existing palette rather than introducing a new colour.
let accent = (r: 0x1A / 255.0, g: 0x3C / 255.0, b: 0xA8 / 255.0)
let white = CGColor(red: 1, green: 1, blue: 1, alpha: 1)

/// Luminance floor for "this pixel is wordmark".
///
/// 0.30 is chosen, not arbitrary. The single-line master carries a faint
/// generator artifact in its bottom-right corner, visible only below ~0.15:
/// measured bboxes are 831×518 at threshold 0.05 (artifact included) versus
/// 657×39 at 0.30 (wordmark only), so the crop drops it as a side effect.
/// The stacked master is clean at every threshold (475×70 at 0.05), but the same
/// floor is applied so one code path serves both.
let lumaFloor = 0.30

let bleed = 3   // guards antialiased glyph edges from being clipped

// MARK: - Measurement

struct Master {
    let cg: CGImage
    let rep: NSBitmapImageRep
    let w: Int
    let h: Int

    init(_ url: URL) {
        let data = try! Data(contentsOf: url)
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cg = CGImageSourceCreateImageAtIndex(src, 0, nil),
              let rep = NSBitmapImageRep(data: data)
        else { fatalError("could not decode \(url.lastPathComponent)") }
        self.cg = cg; self.rep = rep; self.w = cg.width; self.h = cg.height
    }

    /// Top-left-origin luminance probe. `NSBitmapImageRep.colorAt(x:y:)` and
    /// `CGImage.cropping(to:)` share a top-left origin, so detection and
    /// cropping need no flip between them.
    func luma(_ x: Int, _ y: Int) -> Double {
        guard let c = rep.colorAt(x: x, y: y) else { return 0 }
        return 0.2126 * Double(c.redComponent)
            + 0.7152 * Double(c.greenComponent)
            + 0.0722 * Double(c.blueComponent)
    }

    /// Bounding box of ink above `lumaFloor`.
    func inkBounds() -> (minX: Int, maxX: Int, minY: Int, maxY: Int) {
        var minX = w, maxX = -1, minY = h, maxY = -1
        for y in 0..<h {
            for x in 0..<w where luma(x, y) > lumaFloor {
                minX = min(minX, x); maxX = max(maxX, x)
                minY = min(minY, y); maxY = max(maxY, y)
            }
        }
        guard maxX >= 0 else { fatalError("no ink above \(lumaFloor)") }
        return (minX, maxX, minY, maxY)
    }

    /// Contiguous rows containing ink, as (startY, endY). One entry per text line.
    func rowBands() -> [(Int, Int)] {
        var bands: [(Int, Int)] = []
        var start = -1
        for y in 0..<h {
            var hasInk = false
            for x in 0..<w where luma(x, y) > lumaFloor { hasInk = true; break }
            if hasInk { if start < 0 { start = y } }
            else if start >= 0 { bands.append((start, y - 1)); start = -1 }
        }
        if start >= 0 { bands.append((start, h - 1)) }
        return bands
    }

    /// Interior column gaps at least `minWidth` wide, within an x/y window.
    func columnGaps(minWidth: Int, x0: Int, x1: Int, y0: Int, y1: Int) -> [(Int, Int)] {
        var hasInk = [Bool](repeating: false, count: w)
        for x in x0...x1 {
            for y in y0...y1 where luma(x, y) > lumaFloor { hasInk[x] = true; break }
        }
        var gaps: [(Int, Int)] = []
        var start = -1
        for x in x0...x1 {
            if !hasInk[x] { if start < 0 { start = x } }
            else if start >= 0 { gaps.append((start, x - 1)); start = -1 }
        }
        return gaps.filter { $0.1 - $0.0 + 1 >= minWidth }
    }

    func crop(x0: Int, x1: Int, y0: Int, y1: Int) -> CGImage {
        let px0 = max(0, x0 - bleed), px1 = min(w - 1, x1 + bleed)
        let py0 = max(0, y0 - bleed), py1 = min(h - 1, y1 + bleed)
        let r = CGRect(x: px0, y: py0, width: px1 - px0 + 1, height: py1 - py0 + 1)
        guard let c = cg.cropping(to: r) else { fatalError("crop failed") }
        return c
    }
}

// MARK: - Drawing helpers

/// Builds a DeviceGray copy of `img` for use as a CoreGraphics paint mask.
///
/// The masters are white type on black, so luminance *is* coverage: glyph ≈ 1,
/// background ≈ 0, antialiased edges in between. Compositing through this mask
/// rather than drawing the crop directly is what stops each crop's black canvas
/// from appearing as a black rectangle over the icon background.
func luminanceMask(_ img: CGImage) -> CGImage {
    guard let ctx = CGContext(
        data: nil, width: img.width, height: img.height, bitsPerComponent: 8,
        bytesPerRow: img.width, space: CGColorSpaceCreateDeviceGray(),
        bitmapInfo: CGImageAlphaInfo.none.rawValue)
    else { fatalError("mask context failed") }
    ctx.interpolationQuality = .high
    ctx.draw(img, in: CGRect(x: 0, y: 0, width: img.width, height: img.height))
    guard let mask = ctx.makeImage() else { fatalError("mask image failed") }
    return mask
}

func paint(_ ctx: CGContext, mask: CGImage, in rect: CGRect, color: CGColor) {
    ctx.saveGState()
    ctx.clip(to: rect, mask: mask)
    ctx.setFillColor(color)
    ctx.fill(rect)
    ctx.restoreGState()
}

func writePNG(_ image: CGImage, to url: URL) {
    try? FileManager.default.createDirectory(
        at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    guard let dest = CGImageDestinationCreateWithURL(
        url as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { fatalError("destination failed for \(url.lastPathComponent)") }
    CGImageDestinationAddImage(dest, image, nil)
    guard CGImageDestinationFinalize(dest) else { fatalError("write failed") }
}

/// 1024² icon canvas. Bottom-left origin (CoreGraphics drawing convention);
/// `draw(in:)` un-flips the source, so mask construction and application flip
/// identically and cancel.
func makeIconCanvas() -> CGContext {
    let side = 1024
    guard let ctx = CGContext(
        data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpace(name: CGColorSpace.sRGB)!,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { fatalError("context failed") }
    let colors = [
        CGColor(red: 0, green: 0, blue: 0, alpha: 1),
        CGColor(red: 0.043, green: 0.082, blue: 0.200, alpha: 1)
    ] as CFArray
    if let space = CGColorSpace(name: CGColorSpace.sRGB),
       let grad = CGGradient(colorsSpace: space, colors: colors, locations: [0, 1]) {
        ctx.drawLinearGradient(
            grad, start: CGPoint(x: 0, y: CGFloat(side)), end: CGPoint(x: 0, y: 0), options: [])
    }
    return ctx
}

// MARK: - 1. Single-line wordmark → launch screen + splash fallback

let line = Master(lineMaster)
let lineBounds = line.inkBounds()
print("single-line master: \(line.w)×\(line.h)  ink x \(lineBounds.minX)…\(lineBounds.maxX) "
    + "y \(lineBounds.minY)…\(lineBounds.maxY)")

let lineCrop = line.crop(
    x0: lineBounds.minX, x1: lineBounds.maxX, y0: lineBounds.minY, y1: lineBounds.maxY)

do {
    let w = lineCrop.width, h = lineCrop.height
    guard let ctx = CGContext(
        data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpace(name: CGColorSpace.sRGB)!,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { fatalError("wordmark context failed") }
    paint(ctx, mask: luminanceMask(lineCrop),
          in: CGRect(x: 0, y: 0, width: w, height: h), color: white)
    guard let img = ctx.makeImage() else { fatalError("wordmark image failed") }
    writePNG(img, to: logoOut)
    print("✓ wordmark: \(w)×\(h) → \(logoOut.lastPathComponent) "
        + "(aspect \(String(format: "%.2f", Double(w) / Double(h))):1, transparent bg)")
}

// MARK: - 2. Stacked lockup → app icon

let stacked = Master(stackedMaster)
let stackedBounds = stacked.inkBounds()
let bands = stacked.rowBands()
print("stacked master: \(stacked.w)×\(stacked.h)  ink x \(stackedBounds.minX)…\(stackedBounds.maxX) "
    + "y \(stackedBounds.minY)…\(stackedBounds.maxY)  bands=\(bands.count)")

// Two bands is the whole reason this master exists. One band means the
// single-line render was passed in by mistake; three or more means an unexpected
// layout whose scaling this code has not been reasoned about.
guard bands.count == 2 else {
    fatalError("expected 2 text lines in the stacked master, found \(bands.count): \(bands)")
}
for (i, b) in bands.enumerated() {
    var mnX = stacked.w, mxX = -1
    for y in b.0...b.1 {
        for x in 0..<stacked.w where stacked.luma(x, y) > lumaFloor {
            mnX = min(mnX, x); mxX = max(mxX, x)
        }
    }
    print("   line \(i + 1): y \(b.0)…\(b.1) (cap \(b.1 - b.0 + 1)px)  x \(mnX)…\(mxX) "
        + "(w \(mxX - mnX + 1))")
}
let capLine1 = bands[0].1 - bands[0].0 + 1

let stackedCrop = stacked.crop(
    x0: stackedBounds.minX, x1: stackedBounds.maxX,
    y0: stackedBounds.minY, y1: stackedBounds.maxY)

do {
    let ctx = makeIconCanvas()
    let side = 1024.0
    // 0.86 rather than the 0.72 the synthetic version used. This lockup is
    // ~7:1 rather than ~8:1 per line and carries wide letterspacing, so it needs
    // an aggressive scale to reach a usable cap height; 7% side margins still
    // clear iOS's icon corner mask comfortably.
    let contentW = side * 0.86
    let scale = contentW / Double(stackedCrop.width)
    let drawH = Double(stackedCrop.height) * scale

    // Drawn as one unit, preserving the master's own line spacing and the
    // intentional size difference between the two words. No separator rule is
    // added: the line break replaces the single-line master's "|", and imposing
    // a rule here would be editing the supplied design.
    paint(ctx, mask: luminanceMask(stackedCrop),
          in: CGRect(x: (side - contentW) / 2, y: (side - drawH) / 2,
                     width: contentW, height: drawH),
          color: white)

    guard let img = ctx.makeImage() else { fatalError("icon image failed") }
    writePNG(img, to: iconOut)
    let capPx = Double(capLine1) * scale
    print("✓ app icon (stacked master): \(img.width)×\(img.height) → \(iconOut.lastPathComponent)")
    print("   line-1 cap height \(Int(capPx))px of 1024 → "
        + "~\(String(format: "%.1f", capPx / side * 60))pt on a 60pt home-screen icon")
}

// MARK: - 3. Monogram alternative

do {
    // The "M" is line 1 up to its first letterspacing gap. Taken from the
    // single-line master, whose glyphs are proportionally taller than the
    // stacked render's, so the upscale starts from more pixels.
    let gaps = line.columnGaps(
        minWidth: 8, x0: lineBounds.minX, x1: lineBounds.maxX,
        y0: lineBounds.minY, y1: lineBounds.maxY)
    guard let firstGap = gaps.first else { fatalError("no letterspacing gap found") }
    let mono = line.crop(
        x0: lineBounds.minX, x1: firstGap.0 - 1,
        y0: lineBounds.minY, y1: lineBounds.maxY)

    let ctx = makeIconCanvas()
    let side = 1024.0
    let targetH = side * 0.41
    let scale = targetH / Double(mono.height)
    let w = Double(mono.width) * scale
    paint(ctx, mask: luminanceMask(mono),
          in: CGRect(x: (side - w) / 2, y: (side - targetH) / 2, width: w, height: targetH),
          color: white)
    guard let img = ctx.makeImage() else { fatalError("monogram image failed") }
    writePNG(img, to: monoOut)
    let capPx = Double(lineBounds.maxY - lineBounds.minY + 1) * scale
    print("✓ app icon (monogram, alternative): \(img.width)×\(img.height) → "
        + "\(monoOut.lastPathComponent)")
    print("   cap height \(Int(capPx))px of 1024 → "
        + "~\(String(format: "%.1f", capPx / side * 60))pt on a 60pt home-screen icon")
}
