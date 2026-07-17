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
    }

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format: AVAudioFormat
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

    init(outputSampleRate: Double = 24_000) {
        // Nova Sonic default is 24kHz mono. Mono formats should be
        // non-interleaved (there's nothing to interleave with one channel);
        // some iOS builds treat mono+interleaved=true as malformed for the
        // mixer connection and produce no audible output with no error.
        guard let f = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                    sampleRate: outputSampleRate,
                                    channels: 1,
                                    interleaved: false) else {
            fatalError("AudioPlayer: unable to construct output format at \(outputSampleRate)Hz")
        }
        self.format = f
        engine.attach(node)
        // Connect the player node to the mixer using our mono Int16 format.
        // The mixer handles conversion to its own (Float32, probably stereo)
        // internal format. Passing nil here lets the mixer negotiate its
        // own channel count, which then triggers a runtime assertion when
        // we schedule mono buffers ("_outputFormat.channelCount == buffer.
        // format.channelCount"). Explicit mono on the connection avoids it.
        engine.connect(node, to: engine.mainMixerNode, format: format)
    }

    /// Prepare the engine. Call once before the first `play()`.
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
        NSLog("🔊 AudioPlayer: session active. category=%@ mode=%@ route=%@ sessionVol=%.2f",
              session.category.rawValue, session.mode.rawValue, route, session.outputVolume)

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

        let frameCount = AVAudioFrameCount(data.count / MemoryLayout<Int16>.size)
        guard frameCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            return
        }

        buffer.frameLength = frameCount
        data.withUnsafeBytes { raw in
            guard let src = raw.baseAddress else { return }
            let dst = buffer.int16ChannelData!.pointee
            dst.update(from: src.assumingMemoryBound(to: Int16.self), count: Int(frameCount))
        }

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
        try? AVAudioSession.sharedInstance().setActive(
            false, options: [.notifyOthersOnDeactivation]
        )
    }
}
