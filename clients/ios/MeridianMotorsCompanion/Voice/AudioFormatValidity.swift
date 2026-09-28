import AVFoundation
import Foundation

/// Decides whether an audio format is safe to hand to AVAudioEngine.
///
/// AVAudioEngine reports a bad format in `connect(_:to:format:)`, `installTap` and
/// graph setup by raising an Objective-C exception. Swift `do/catch` cannot catch it,
/// so the app aborts. A 0 Hz or 0-channel format is a known trigger for `installTap`
/// ("IsFormatSampleRateAndChannelCountValid"). See CMS issue
/// `issues/2026-09-25-ios-voice-audio-engine-crash-on-zero-hz-input/`, where an Int16
/// player connection aborted the app on iPhone hardware.
///
/// Check a hardware format with this before building an audio graph from it, and throw
/// a Swift error when it fails so callers can fall back to text-only.
enum AudioFormatValidity {
    /// True when the sample rate is a finite number above zero and there is at least
    /// one channel. `isFinite` matters: NaN fails `> 0` on its own, but infinity does not.
    static func isUsable(sampleRate: Double, channelCount: AVAudioChannelCount) -> Bool {
        sampleRate.isFinite && sampleRate > 0 && channelCount > 0
    }

    static func isUsable(_ format: AVAudioFormat) -> Bool {
        isUsable(sampleRate: format.sampleRate, channelCount: format.channelCount)
    }
}
