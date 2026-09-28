import AVFoundation
import Foundation

/// Captures microphone audio and publishes base64-encoded PCM chunks.
///
/// Format: 16kHz mono, 16-bit signed PCM, little-endian. Matches the
/// `audio.chunk` wire-protocol message defined in bidi_app.py and also
/// the sample rate Nova Sonic expects on input.
///
/// Chunk size: ~200ms (6400 raw bytes, ~8600 base64 bytes). This is well
/// under AgentCore's 32KB WebSocket frame limit and matches what the
/// server-side smoke test paces audio at. Pacing to wall-clock matters
/// because Nova Sonic's server-side VAD uses silence duration to detect
/// end-of-turn — sending the whole clip in one burst confuses it.
///
/// Backend-agnostic: the consumer of this stream decides what to do
/// with the base64 chunks. Today that consumer is VSABidiClient, which
/// wraps them in `{"type":"audio.chunk", "data":"...", "sampleRate":16000}`
/// WebSocket messages. A future Pattern A consumer would feed them
/// through a Lambda boundary instead — same chunk format.
///
/// Phase 3 step 4 scaffolding. Not yet wired into AssistantTabView.
actor AudioCapture {
    /// Audio-engine-level errors surfaced to the caller. All are terminal
    /// for the current capture session; the caller should call `start()`
    /// again after resolving (typically by re-requesting mic permission).
    enum CaptureError: Error {
        case permissionDenied
        case engineFailed(Error)
        case notRunning
        case alreadyRunning
        /// No usable microphone: the session reports no input, or the input
        /// hardware format is 0 Hz or has no channels. Typing still works.
        case inputUnavailable
    }

    /// The permission, audio-session and hardware calls that `start()` depends on.
    ///
    /// Injected so tests can simulate a device with no usable microphone without
    /// touching the real session or showing a permission prompt. Production code
    /// uses `.live`.
    struct Environment: Sendable {
        /// Asks for (or returns the cached) record permission.
        var requestPermission: @Sendable () async -> Bool
        /// Sets the category, mode and preferred sample rate, then activates the
        /// shared session.
        var activateSession: @Sendable () throws -> Void
        /// Whether the session currently has an audio input route.
        var isInputAvailable: @Sendable () -> Bool
        /// Reads the input hardware format from the engine's input node.
        var readInputFormat: @Sendable (AVAudioEngine) -> AVAudioFormat

        static let live = Environment(
            requestPermission: {
                // iOS 17+ uses AVAudioApplication; older uses AVAudioSession.
                if #available(iOS 17.0, *) {
                    return await AVAudioApplication.requestRecordPermission()
                } else {
                    return await withCheckedContinuation { cont in
                        AVAudioSession.sharedInstance().requestRecordPermission { cont.resume(returning: $0) }
                    }
                }
            },
            activateSession: {
                let session = AVAudioSession.sharedInstance()
                #if targetEnvironment(simulator)
                let mode: AVAudioSession.Mode = .default
                #else
                let mode: AVAudioSession.Mode = .voiceChat
                #endif
                try session.setCategory(.playAndRecord,
                                        mode: mode,
                                        options: [.defaultToSpeaker, .allowBluetooth, .allowBluetoothA2DP])
                try session.setPreferredSampleRate(AudioCapture.sampleRate)
                try session.setActive(true, options: [])
                // Don't override output port — let iOS route to earbuds/headphones
                // when connected. The .defaultToSpeaker option handles the no-earbuds case.
            },
            isInputAvailable: {
                AVAudioSession.sharedInstance().isInputAvailable
            },
            readInputFormat: { engine in
                engine.inputNode.inputFormat(forBus: 0)
            }
        )
    }

    /// Target format: 16kHz, mono, 16-bit PCM. Nova Sonic accepts this
    /// directly; other backends can resample server-side if they want.
    static let sampleRate: Double = 16_000
    static let channelCount: UInt32 = 1
    static let chunkDurationMs: Int = 200

    private let engine = AVAudioEngine()
    private let environment: Environment
    private var converter: AVAudioConverter?
    private var targetFormat: AVAudioFormat?
    private var continuation: AsyncStream<String>.Continuation?
    private var running = false
    /// When true, captured audio is discarded (not yielded to the stream).
    /// Capture-level mute. NOT the echo gate.
    ///
    /// As of 2026-08-21 nothing calls this: mic suppression is decided in ONE place,
    /// `VoiceSessionViewModel`'s capture loop, via the derived `MicSuppression`. This
    /// used to be a second, parallel gate that the view model kept in sync by hand —
    /// and because it latched shut when an assistant transcript arrived with no
    /// audio, it was half of a deadlock (see `MicSuppression`).
    ///
    /// Kept rather than deleted because muting at the source is genuinely cheaper
    /// than converting and base64-encoding frames that will be dropped downstream.
    /// If it is ever wired up again, drive it FROM the same `MicSuppression` value —
    /// do not reintroduce an independently-maintained flag.
    var muted = false

    func mute() { muted = true }
    func unmute() { muted = false }

    init(environment: Environment = .live) {
        self.environment = environment
    }

    /// True while capture is running (engine started, tap installed).
    var isRunning: Bool { running }

    /// Request mic permission without starting capture. Use this for a
    /// fail-fast check before opening an expensive WebSocket when the user
    /// is going to deny anyway. Idempotent; subsequent `start()` calls
    /// will see the cached permission grant.
    func requestPermissionOnly() async throws {
        try await requestPermission()
    }

    /// Start capturing. Returns an AsyncStream that yields base64-encoded
    /// PCM chunks of roughly `chunkDurationMs` each. The stream finishes
    /// when `stop()` is called or capture terminates.
    ///
    /// Throws `CaptureError.inputUnavailable` when there is no usable
    /// microphone (no input route, or a 0 Hz / 0-channel input format)
    /// instead of handing that format to AVAudioEngine, which would raise an
    /// uncatchable Objective-C exception and abort the app.
    ///
    /// Must be called from the main actor's context at least once to
    /// trigger the iOS permission prompt the first time.
    func start() async throws -> AsyncStream<String> {
        if running { throw CaptureError.alreadyRunning }

        try await requestPermission()

        try environment.activateSession()

        // Check for an input route before touching `engine.inputNode`: the
        // engine builds the input I/O unit on first access, and with no input
        // hardware its format is unusable.
        guard environment.isInputAvailable() else {
            NSLog("🎤 CAPTURE: no audio input available; not starting capture")
            throw CaptureError.inputUnavailable
        }

        let inputFormat = environment.readInputFormat(engine)
        guard AudioFormatValidity.isUsable(inputFormat) else {
            NSLog("🎤 CAPTURE: input hardware format unusable (%.0f Hz, %d ch); not starting capture",
                  inputFormat.sampleRate, Int(inputFormat.channelCount))
            throw CaptureError.inputUnavailable
        }

        guard let target = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                         sampleRate: AudioCapture.sampleRate,
                                         channels: AudioCapture.channelCount,
                                         interleaved: true) else {
            throw CaptureError.engineFailed(NSError(domain: "AudioCapture",
                                                    code: -1,
                                                    userInfo: [NSLocalizedDescriptionKey: "invalid target format"]))
        }
        // A nil converter used to be stored as-is, and `handleBuffer` then
        // dropped every buffer: capture "ran" and sent nothing. Fail instead.
        guard let converter = AVAudioConverter(from: inputFormat, to: target) else {
            throw CaptureError.engineFailed(NSError(domain: "AudioCapture",
                                                    code: -2,
                                                    userInfo: [NSLocalizedDescriptionKey: "cannot convert the microphone format"]))
        }
        self.targetFormat = target
        self.converter = converter

        let (stream, cont) = AsyncStream<String>.makeStream(bufferingPolicy: .unbounded)
        self.continuation = cont

        // Tap buffer size: request enough samples at the input format's
        // rate that the converted-to-16kHz output is ~200ms. The engine
        // rarely respects exactly — AVAudioEngine rounds — but it's
        // close enough for our VAD purposes.
        let bufferSize = AVAudioFrameCount(inputFormat.sampleRate * Double(AudioCapture.chunkDurationMs) / 1000.0)

        engine.inputNode.installTap(onBus: 0,
                                    bufferSize: bufferSize,
                                    format: inputFormat) { [weak self] buffer, _ in
            guard let self = self else { return }
            Task { await self.handleBuffer(buffer) }
        }

        do {
            try engine.start()
            running = true
        } catch {
            engine.inputNode.removeTap(onBus: 0)
            cont.finish()
            self.continuation = nil
            throw CaptureError.engineFailed(error)
        }

        return stream
    }

    /// Stop capture. Idempotent.
    ///
    /// Does NOT deactivate the shared AVAudioSession — that session is
    /// owned jointly with AudioPlayer, and deactivating it here would
    /// silence the assistant's playback the moment the user stops
    /// speaking (symptom: 460+ audio chunks scheduled, zero audible
    /// output). Session teardown is the VoiceSessionViewModel's job
    /// when the whole voice session ends.
    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        continuation?.finish()
        continuation = nil
        converter = nil
        targetFormat = nil
        running = false
    }

    // MARK: - Internal

    private func handleBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let converter = converter, let target = targetFormat else { return }

        // Target frame capacity: N input frames -> approximately
        // N * (16000 / inputSampleRate) output frames.
        let ratio = target.sampleRate / buffer.format.sampleRate
        let outCapacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32  // 32 slack for rounding

        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: target,
                                               frameCapacity: outCapacity) else {
            return
        }

        var consumed = false
        var error: NSError?
        let status = converter.convert(to: outBuffer, error: &error) { _, inputStatus in
            if consumed {
                inputStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            inputStatus.pointee = .haveData
            return buffer
        }

        guard status != .error, error == nil, outBuffer.frameLength > 0 else {
            // Drop corrupt buffers silently; Nova Sonic's VAD is tolerant.
            return
        }

        // Int16 interleaved -> raw bytes -> base64.
        guard let channelData = outBuffer.int16ChannelData else { return }
        let frameCount = Int(outBuffer.frameLength)
        let byteCount = frameCount * MemoryLayout<Int16>.size  // mono interleaved

        // Compute a cheap peak-amplitude number so we can tell silent
        // capture apart from real speech in the logs. Int16 ranges
        // -32768..32767; silence is peak ~0-10 (noise floor), human
        // speech at a normal distance peaks in the thousands.
        let samples = channelData.pointee
        var peak: Int16 = 0
        for i in 0..<frameCount {
            let v = abs(Int32(samples[i]))
            if Int32(peak) < v { peak = Int16(min(v, 32767)) }
        }
        Self.reportAmplitude(peak: peak, frames: frameCount)

        let data = Data(bytes: channelData.pointee, count: byteCount)
        let b64 = data.base64EncodedString()

        if !muted {
            continuation?.yield(b64)
        }
    }

    // MARK: - Amplitude diagnostic
    //
    // Stateless rolling reporter — avoids dragging extra state into the
    // actor just for a log. Each tap-buffer callback contributes one
    // observation; we print a summary every ~2 seconds.

    private static let amplitudeReportInterval: TimeInterval = 2
    private static var amplitudeReportLock = NSLock()
    private static var amplitudePeaks: [Int16] = []
    private static var amplitudeLastReport = Date()

    private static func reportAmplitude(peak: Int16, frames: Int) {
        amplitudeReportLock.lock()
        defer { amplitudeReportLock.unlock() }
        amplitudePeaks.append(peak)
        let now = Date()
        if now.timeIntervalSince(amplitudeLastReport) > amplitudeReportInterval {
            let peaks = amplitudePeaks
            amplitudePeaks.removeAll(keepingCapacity: true)
            amplitudeLastReport = now
            let maxPeak = peaks.max() ?? 0
            let mean = peaks.isEmpty ? 0 : peaks.map { Int($0) }.reduce(0, +) / peaks.count
            let quality: String
            switch maxPeak {
            case 0..<50: quality = "silence"
            case 50..<500: quality = "background"
            case 500..<3000: quality = "quiet speech"
            default: quality = "speech"
            }
            print("🎤 MIC: \(peaks.count) chunks last \(Int(Self.amplitudeReportInterval))s — peak=\(maxPeak), mean=\(mean) (\(quality))")
        }
    }

    private func requestPermission() async throws {
        let granted = await environment.requestPermission()
        if !granted { throw CaptureError.permissionDenied }
    }
}
