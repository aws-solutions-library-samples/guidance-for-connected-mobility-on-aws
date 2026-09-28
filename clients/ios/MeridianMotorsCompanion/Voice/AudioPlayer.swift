import AVFoundation
import Foundation

/// Plays base64-encoded PCM audio chunks received from the voice backend.
///
/// Format: 24kHz mono 16-bit PCM (Nova Sonic's native output format —
/// see bidi_app.OUTPUT_SAMPLE_RATE). If a future backend outputs at a
/// different rate, the `outputSampleRate` init param lets us swap it.
///
/// Backend-agnostic. Any component that can produce base64 PCM chunks
/// can drive this — Nova Sonic directly, Polly via a Lambda, etc.
///
/// Buffers are scheduled on an AVAudioPlayerNode so playback is
/// gapless as long as chunks arrive faster than they're consumed.
/// If the queue goes empty mid-utterance there will be a short stall;
/// that's acceptable for the demo.
///
/// `flush()` clears the scheduled queue so a barge-in can silence the
/// assistant immediately — critical for Nova Sonic's interruption
/// feature to feel natural.
///
/// Phase 3 step 4 scaffolding. Not yet wired into AssistantTabView.
actor AudioPlayer {
    enum PlayerError: Error {
        case engineFailed(Error)
        case invalidChunk
        /// The output hardware reported a format the engine cannot connect to (0 Hz or
        /// no channels). Voice output is unavailable; the session can still run as text.
        case outputUnavailable(sampleRate: Double, channelCount: AVAudioChannelCount)
    }

    /// The audio-session and hardware calls that `start()` depends on.
    ///
    /// Injected so tests can simulate a device whose output hardware reads 0 Hz without
    /// touching the real shared session. Production code uses `.live`.
    struct Environment: Sendable {
        /// Sets the category and mode, then activates the shared session.
        var activateSession: @Sendable () throws -> Void
        /// Deactivates the shared session. Used after a failed start and by `stop()`.
        var deactivateSession: @Sendable () -> Void
        /// Reads the output hardware format from the engine's output node.
        var readOutputFormat: @Sendable (AVAudioEngine) -> AVAudioFormat

        static let live = Environment(
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
                try session.setActive(true, options: [])

                // Don't force speaker override — let iOS route to earbuds when
                // connected. .defaultToSpeaker handles the no-earbuds fallback.

                // Log the resolved output route so we can confirm the simulator
                // chose an audible destination (e.g. "Speaker") and not something
                // phantom like "BuiltInReceiver" that won't make sound on Mac host.
                let route = session.currentRoute.outputs.map { "\($0.portType.rawValue)/\($0.portName)" }.joined(separator: ",")
                NSLog("🔊 AudioPlayer: session active. category=%@ mode=%@ route=%@ sessionVol=%.2f hwRate=%.0f",
                      session.category.rawValue, session.mode.rawValue, route, session.outputVolume, session.sampleRate)
            },
            deactivateSession: {
                try? AVAudioSession.sharedInstance().setActive(
                    false, options: [.notifyOthersOnDeactivation]
                )
            },
            readOutputFormat: { engine in
                engine.outputNode.outputFormat(forBus: 0)
            }
        )
    }

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format: AVAudioFormat
    private let environment: Environment
    private var started = false
    /// True once we've actually called `node.play()`. We defer that call
    /// until the engine has had an I/O cycle to avoid an iOS 26 simulator
    /// assertion ("player did not see an IO cycle") — `node.play()` must
    /// not be invoked until AVAudioEngine's internal audio unit has
    /// ticked at least once. In practice this means we wait until we're
    /// about to schedule the first real buffer in `play(base64Chunk:)`.
    private var nodePlayStarted = false
    /// Running count of scheduled buffers. Used only for rate-limited
    /// diagnostic logging.
    private var playsScheduled: Int = 0

    init(outputSampleRate: Double = 24_000, environment: Environment = .live) {
        // Nova Sonic sends 24 kHz mono 16-bit PCM, but the player node is
        // connected with a FLOAT32 format and each chunk is converted in
        // `makeBuffer(fromPCM16:format:)`.
        //
        // Why not Int16 on the connection: on iPhone hardware (Device Farm,
        // iPhone 16 / iOS 18.6.2) `engine.connect(node, to: mainMixerNode,
        // format: <Int16>)` fails in SetFormat with -10868
        // (kAudioUnitErr_FormatNotSupported) and AVFAudio raises an
        // Objective-C exception that no Swift `catch` can stop: the app
        // aborted right after sign-in. The iOS simulator accepts Int16, which
        // is why this was never seen in development. See
        // issues/2026-09-25-ios-voice-audio-engine-crash-on-zero-hz-input/.
        //
        // The standard format is deinterleaved Float32. Mono stays explicit:
        // passing nil lets the mixer negotiate its own channel count, which
        // then trips "_outputFormat.channelCount == buffer.format.channelCount"
        // when mono buffers are scheduled.
        guard let f = AVAudioFormat(standardFormatWithSampleRate: outputSampleRate,
                                    channels: 1) else {
            fatalError("AudioPlayer: unable to construct output format at \(outputSampleRate)Hz")
        }
        self.format = f
        self.environment = environment
        // Attach only. Do NOT connect to `mainMixerNode` here: the graph is
        // built in `start()`, after the session is configured and the output
        // hardware format has been checked, so any failure can surface as a
        // Swift error there instead of in an initializer nobody can catch.
        engine.attach(node)
    }

    /// The format the player node is connected with. Tests pin this to Float32:
    /// the simulator accepts Int16 here, so no simulator run can catch a
    /// regression back to it.
    var connectionFormat: AVAudioFormat { format }

    /// Converts one Nova Sonic chunk (16-bit signed little-endian mono PCM) into a
    /// Float32 buffer in `format`, scaling to [-1, 1). Returns nil for an empty
    /// chunk. A trailing odd byte is ignored.
    static func makeBuffer(fromPCM16 data: Data, format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let frameCount = AVAudioFrameCount(data.count / MemoryLayout<Int16>.size)
        guard frameCount > 0,
              format.commonFormat == .pcmFormatFloat32,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount),
              let dst = buffer.floatChannelData?[0] else {
            return nil
        }
        buffer.frameLength = frameCount
        data.withUnsafeBytes { raw in
            for i in 0..<Int(frameCount) {
                let sample = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: i * 2, as: Int16.self))
                dst[i] = Float(sample) / 32_768
            }
        }
        return buffer
    }

    /// True once the player node is wired into the engine. Read from the engine
    /// itself, not from a flag, so tests can prove the graph is never built from
    /// an unusable format.
    var isGraphConnected: Bool {
        !engine.outputConnectionPoints(for: node, outputBus: 0).isEmpty
    }

    /// True once `start()` has completed and the engine is running.
    var isStarted: Bool { started }

    /// Whether the player node is currently playing. For tests.
    var isPlayerNodePlaying: Bool { node.isPlaying }

    /// Prepare the engine. Call once before the first `play()`.
    ///
    /// Order matters: configure and activate the session, then read and check
    /// the output hardware format, and only then build the graph. If the
    /// format is unusable (0 Hz or no channels) this throws
    /// `PlayerError.outputUnavailable` and leaves the graph unbuilt, so the
    /// caller can continue in text-only mode instead of crashing.
    ///
    /// Important: the engine is started here but the player node is NOT.
    /// Calling `node.play()` before the engine's audio unit has completed
    /// its first I/O cycle trips an "AVAudioPlayerNodeImpl::Start: player
    /// did not see an IO cycle" runtime assertion on the iOS 26 simulator
    /// (and can trip on older iOS versions under rare race conditions).
    /// The node is started lazily on the first `play(base64Chunk:)` call,
    /// by which time the I/O graph has had time to settle.
    func start() async throws {
        guard !started else { return }

        try environment.activateSession()

        let hardware = environment.readOutputFormat(engine)
        guard AudioFormatValidity.isUsable(hardware) else {
            NSLog("🔊 AudioPlayer: output hardware format unusable (%.0f Hz, %d ch); not building the audio graph",
                  hardware.sampleRate, Int(hardware.channelCount))
            environment.deactivateSession()
            throw PlayerError.outputUnavailable(sampleRate: hardware.sampleRate,
                                                channelCount: hardware.channelCount)
        }

        if !isGraphConnected {
            // Float32 mono, see `init` for why not Int16. The mixer converts
            // sample rate and channel count to its own output format.
            NSLog("🔊 AudioPlayer: connecting player hw=%.0f Hz/%d ch player=%.0f Hz/%d ch fmt=%d",
                  hardware.sampleRate, Int(hardware.channelCount),
                  format.sampleRate, Int(format.channelCount), Int(format.commonFormat.rawValue))
            engine.connect(node, to: engine.mainMixerNode, format: format)
            NSLog("🔊 AudioPlayer: graph connected")
        }

        do {
            engine.prepare()
            try engine.start()
            started = true
            // Give the audio graph a render cycle before we touch the
            // player node. 60ms is enough in practice on both simulator
            // and device; if the first chunk arrives before this settles,
            // `play(base64Chunk:)`'s own guard will still defer node.play
            // until after the scheduleBuffer call.
            try? await Task.sleep(nanoseconds: 60_000_000)
            NSLog("🔊 AudioPlayer: started engine.isRunning=%@ (node.play deferred to first buffer) mixerVol=%.2f",
                  "\(engine.isRunning)", engine.mainMixerNode.outputVolume)
        } catch {
            throw PlayerError.engineFailed(error)
        }
    }

    /// Enqueue a base64-encoded PCM chunk. Safe to call before start() —
    /// the chunk will be dropped with no error (acceptable because the
    /// same start()-first pattern applies on the capture side).
    ///
    /// Async because on iOS 26 simulator we sometimes need to wait for
    /// the engine to finish restarting (when AudioCapture has just
    /// stopped) before calling `node.play()`, or we'll trip the "player
    /// did not see an IO cycle" assertion.
    func play(base64Chunk: String) async {
        guard started else { return }
        guard let data = Data(base64Encoded: base64Chunk), !data.isEmpty else { return }

        // iOS permits only one AVAudioEngine at a time to own the I/O
        // context. When AudioCapture.start() runs, its engine takes over
        // and ours quietly stops — buffers scheduled while we're stopped
        // are silently discarded with no error. Re-starting our engine
        // here on demand restores playback the moment capture releases
        // the I/O (or lets us share it, since CoreAudio mixes multiple
        // engines as long as at least one is running at any given time).
        var restarted = false
        if !engine.isRunning {
            do {
                engine.prepare()
                try engine.start()
                restarted = true
                // Mark node as stopped — we'll re-start it *after*
                // scheduling the next buffer + the I/O settles.
                nodePlayStarted = false
                NSLog("🔊 AudioPlayer: re-started engine (was stopped) isRunning=%@",
                      "\(engine.isRunning)")
            } catch {
                NSLog("🔊 AudioPlayer: re-start failed: %@", "\(error)")
                return
            }
        }

        guard let buffer = Self.makeBuffer(fromPCM16: data, format: format) else {
            return
        }
        let frameCount = buffer.frameLength

        // Rate-limited diagnostic so we can tell from the console whether
        // the engine/node are still live at scheduling time and whether the
        // buffer contains non-silence. Fires on the first call and every
        // 20th call thereafter.
        playsScheduled += 1
        if playsScheduled == 1 || playsScheduled % 20 == 0 {
            // Peak-sample check so we can distinguish "real audio arrived"
            // from "zero-filled buffers arrived." Silence peaks near 0;
            // speech peaks in the thousands.
            var peak: Int16 = 0
            data.withUnsafeBytes { raw in
                guard let p = raw.baseAddress?.assumingMemoryBound(to: Int16.self) else { return }
                for i in 0..<Int(frameCount) {
                    let v = abs(Int32(p[i]))
                    if Int32(peak) < v { peak = Int16(min(v, 32767)) }
                }
            }
            // On the very first chunk of a turn, also dump the session
            // route so we can correlate audible / inaudible runs with
            // the actual output destination. Simulator routing is the
            // prime suspect when engine+node look healthy but there's
            // no sound.
            if playsScheduled == 1 {
                let session = AVAudioSession.sharedInstance()
                let route = session.currentRoute.outputs
                    .map { "\($0.portType.rawValue)/\($0.portName)" }
                    .joined(separator: ",")
                NSLog("🔊 AudioPlayer: first chunk. route=%@ category=%@ mode=%@",
                      route, session.category.rawValue, session.mode.rawValue)
            }
            NSLog("🔊 AudioPlayer: play #%d frames=%d peak=%d engine.isRunning=%@ node.isPlaying=%@",
                  playsScheduled, Int(frameCount), peak,
                  "\(engine.isRunning)", "\(node.isPlaying)")
        }

        // Schedule the buffer BEFORE starting the node. On iOS 26
        // simulator, calling `node.play()` on a node that hasn't
        // seen an I/O cycle yet triggers a runtime assertion; having
        // a buffer in the queue when we finally do call play() keeps
        // the node in a well-defined state.
        node.scheduleBuffer(buffer, completionCallbackType: .dataConsumed) { _ in }

        if !nodePlayStarted || !node.isPlaying {
            // If we just restarted the engine, give it a render cycle
            // before calling node.play(). 40ms is empirically enough on
            // simulator and inaudible on device (first chunk is still
            // queued and will start playing the instant the node does).
            if restarted {
                try? await Task.sleep(nanoseconds: 40_000_000)
            }
            node.play()
            nodePlayStarted = true
        }
    }

    /// Clear the playback queue. Use for barge-in / interruption to stop
    /// the assistant mid-utterance when the user starts speaking again.
    func flush() {
        guard started else { return }
        // Only touch the node if it was actually started — calling
        // node.stop() on a node that has never played is a no-op but
        // node.play() right after can still trip the iOS 26 simulator
        // assertion. Gate on `nodePlayStarted` to be safe.
        guard nodePlayStarted else { return }
        node.stop()
        node.play()
    }

    /// Stop playback and tear down the engine + shared audio session.
    /// Idempotent.
    ///
    /// The shared AVAudioSession is deactivated here rather than in
    /// AudioCapture.stop() because the session is jointly owned with
    /// the capture actor: the player outlives the capture (capture only
    /// runs while the user is talking, player runs for the whole voice
    /// session). Deactivating it from capture.stop() silences in-flight
    /// assistant audio the instant the user stops speaking.
    func stop() {
        guard started else { return }
        if nodePlayStarted {
            node.stop()
            nodePlayStarted = false
        }
        engine.stop()
        started = false
        environment.deactivateSession()
    }
}
