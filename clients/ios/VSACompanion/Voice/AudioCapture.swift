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
    }

    /// Target format: 16kHz, mono, 16-bit PCM. Nova Sonic accepts this
    /// directly; other backends can resample server-side if they want.
    static let sampleRate: Double = 16_000
    static let channelCount: UInt32 = 1
    static let chunkDurationMs: Int = 200

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var targetFormat: AVAudioFormat?
    private var continuation: AsyncStream<String>.Continuation?
    private var running = false
    /// When true, captured audio is discarded (not yielded to the stream).
    var muted = false

    func mute() { muted = true }
    func unmute() { muted = false }

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
    /// Must be called from the main actor's context at least once to
    /// trigger the iOS permission prompt the first time.
    func start() async throws -> AsyncStream<String> {
        if running { throw CaptureError.alreadyRunning }

        try await requestPermission()

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

        let inputFormat = engine.inputNode.inputFormat(forBus: 0)
        guard let target = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                         sampleRate: AudioCapture.sampleRate,
                                         channels: AudioCapture.channelCount,
                                         interleaved: true) else {
            throw CaptureError.engineFailed(NSError(domain: "AudioCapture",
                                                    code: -1,
                                                    userInfo: [NSLocalizedDescriptionKey: "invalid target format"]))
        }
        self.targetFormat = target
        self.converter = AVAudioConverter(from: inputFormat, to: target)

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
        // iOS 17+ uses AVAudioApplication; older uses AVAudioSession.
        if #available(iOS 17.0, *) {
            let granted = await AVAudioApplication.requestRecordPermission()
            if !granted { throw CaptureError.permissionDenied }
        } else {
            let granted = await withCheckedContinuation { cont in
                AVAudioSession.sharedInstance().requestRecordPermission { cont.resume(returning: $0) }
            }
            if !granted { throw CaptureError.permissionDenied }
        }
    }
}
