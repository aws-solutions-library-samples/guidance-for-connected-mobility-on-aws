import AVFoundation
import XCTest
@testable import MeridianMotorsCompanion

/// Tests for the audio-engine start-up guards.
///
/// The bug being pinned: on Device Farm iPhones the app aborted about 1 s after sign-in.
/// `engine.connect(playerNode, to: mainMixerNode, format:)` with an Int16 format failed in
/// `SetFormat` with -10868 and AVFAudio raised an Objective-C exception that Swift `catch`
/// cannot stop. The simulator accepts Int16. See CMS issue
/// `issues/2026-09-25-ios-voice-audio-engine-crash-on-zero-hz-input/`.
///
/// The load-bearing tests are `test_connectionFormat_isFloat32Mono24kHz` (the device
/// failure), `test_init_doesNotBuildTheGraph` (no graph work where no `catch` can run) and
/// the `..._throwsOutputUnavailable...` / `..._throwsInputUnavailable...` tests (a 0 Hz
/// format must become a Swift error, never reach the engine).

// MARK: - Helpers

/// Thread-safe record of which environment hooks ran, in order.
private final class CallLog: @unchecked Sendable {
    private let lock = NSLock()
    private var entries: [String] = []

    func record(_ entry: String) {
        lock.lock(); entries.append(entry); lock.unlock()
    }

    var all: [String] {
        lock.lock(); defer { lock.unlock() }
        return entries
    }
}

private struct ActivationFailed: Error {}

private func pcmFormat(_ sampleRate: Double, _ channels: AVAudioChannelCount) -> AVAudioFormat {
    AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: channels)!
}

// MARK: - AudioFormatValidity

final class AudioFormatValidityTests: XCTestCase {

    func test_usualHardwareFormats_areUsable() {
        XCTAssertTrue(AudioFormatValidity.isUsable(sampleRate: 48_000, channelCount: 2))
        XCTAssertTrue(AudioFormatValidity.isUsable(sampleRate: 44_100, channelCount: 1))
        XCTAssertTrue(AudioFormatValidity.isUsable(sampleRate: 24_000, channelCount: 1))
        XCTAssertTrue(AudioFormatValidity.isUsable(sampleRate: 16_000, channelCount: 1))
    }

    /// The format the Device Farm iPhones reported.
    func test_zeroHz_isRejected() {
        XCTAssertFalse(AudioFormatValidity.isUsable(sampleRate: 0, channelCount: 2))
    }

    func test_zeroChannels_isRejected() {
        XCTAssertFalse(AudioFormatValidity.isUsable(sampleRate: 48_000, channelCount: 0))
    }

    func test_negativeSampleRate_isRejected() {
        XCTAssertFalse(AudioFormatValidity.isUsable(sampleRate: -48_000, channelCount: 2))
    }

    /// NaN already fails `> 0`; infinity does not, which is why `isFinite` is there.
    func test_nonFiniteSampleRates_areRejected() {
        XCTAssertFalse(AudioFormatValidity.isUsable(sampleRate: .nan, channelCount: 2))
        XCTAssertFalse(AudioFormatValidity.isUsable(sampleRate: .infinity, channelCount: 2))
    }

    func test_formatOverload_readsTheFormatsOwnValues() {
        XCTAssertFalse(AudioFormatValidity.isUsable(pcmFormat(0, 2)))
        XCTAssertTrue(AudioFormatValidity.isUsable(pcmFormat(48_000, 2)))
    }
}

// MARK: - AudioPlayer

final class AudioPlayerStartupTests: XCTestCase {

    private func environment(log: CallLog,
                             output: AVAudioFormat,
                             activationFails: Bool = false) -> AudioPlayer.Environment {
        AudioPlayer.Environment(
            activateSession: {
                log.record("activate")
                if activationFails { throw ActivationFailed() }
            },
            deactivateSession: { log.record("deactivate") },
            readOutputFormat: { _ in
                log.record("readFormat")
                return output
            }
        )
    }

    /// THE REGRESSION TEST. The crash was in `init`, before any `do/catch` could run:
    /// constructing the player must not connect anything to the engine.
    func test_init_doesNotBuildTheGraph() async {
        let log = CallLog()
        let player = AudioPlayer(environment: environment(log: log, output: pcmFormat(0, 2)))

        let connected = await player.isGraphConnected
        XCTAssertFalse(connected, "init must not connect the player node")
        XCTAssertEqual(log.all, [], "init must not touch the session or read hardware formats")
    }

    func test_start_withZeroHzOutput_throwsOutputUnavailable_andLeavesGraphUnbuilt() async {
        let log = CallLog()
        let player = AudioPlayer(environment: environment(log: log, output: pcmFormat(0, 2)))

        do {
            try await player.start()
            XCTFail("start() must throw for a 0 Hz output format")
        } catch AudioPlayer.PlayerError.outputUnavailable(let sampleRate, let channelCount) {
            XCTAssertEqual(sampleRate, 0)
            XCTAssertEqual(channelCount, 2)
        } catch {
            XCTFail("expected outputUnavailable, got \(error)")
        }

        let connected = await player.isGraphConnected
        let started = await player.isStarted
        XCTAssertFalse(connected, "the graph must not be built from a 0 Hz format")
        XCTAssertFalse(started)
    }

    /// Activating the session first is what gives the hardware a real format; a failed
    /// start must hand the session back.
    func test_start_activatesSessionBeforeReadingFormat_andDeactivatesOnFailure() async {
        let log = CallLog()
        let player = AudioPlayer(environment: environment(log: log, output: pcmFormat(0, 2)))

        try? await player.start()

        XCTAssertEqual(log.all, ["activate", "readFormat", "deactivate"])
    }

    func test_start_whenActivationFails_neverReadsFormatOrBuildsGraph() async {
        let log = CallLog()
        let player = AudioPlayer(environment: environment(log: log,
                                                          output: pcmFormat(48_000, 2),
                                                          activationFails: true))

        do {
            try await player.start()
            XCTFail("start() must rethrow the activation error")
        } catch is ActivationFailed {
            // expected
        } catch {
            XCTFail("expected ActivationFailed, got \(error)")
        }

        let connected = await player.isGraphConnected
        XCTAssertEqual(log.all, ["activate"])
        XCTAssertFalse(connected)
    }

    /// Positive control: with a usable format the graph IS built, so the
    /// `isGraphConnected == false` assertions above are not vacuous.
    func test_start_withUsableOutput_buildsTheGraph() async {
        let log = CallLog()
        let player = AudioPlayer(environment: environment(log: log, output: pcmFormat(48_000, 2)))

        // engine.start() may fail on a simulator without host audio; the graph is
        // built before that, which is what this test checks.
        try? await player.start()

        let connected = await player.isGraphConnected
        XCTAssertTrue(connected, "a usable format must lead to a connected graph")
        await player.stop()
    }

    /// THE DEVICE REGRESSION TEST. On iPhone hardware, connecting the player node to the
    /// main mixer with an Int16 format fails in SetFormat (-10868) and aborts the app.
    /// The simulator accepts Int16, so this format assertion is the only guard a
    /// simulator run can provide.
    func test_connectionFormat_isFloat32Mono24kHz() async {
        let player = AudioPlayer(environment: environment(log: CallLog(), output: pcmFormat(0, 2)))

        let format = await player.connectionFormat
        XCTAssertEqual(format.commonFormat, .pcmFormatFloat32)
        XCTAssertEqual(format.channelCount, 1)
        XCTAssertEqual(format.sampleRate, 24_000)
    }

    func test_makeBuffer_convertsLittleEndianPCM16ToScaledFloat() throws {
        let samples: [Int16] = [0, 16_384, -32_768, 32_767, -1, 256]
        let data = samples.withUnsafeBufferPointer { ptr in
            Data(buffer: UnsafeBufferPointer(start: ptr.baseAddress, count: ptr.count))
        }
        let format = pcmFormat(24_000, 1)

        let buffer = try XCTUnwrap(AudioPlayer.makeBuffer(fromPCM16: data, format: format))

        XCTAssertEqual(buffer.frameLength, 6)
        let out = try XCTUnwrap(buffer.floatChannelData?[0])
        let expected: [Float] = [0, 0.5, -1, 32_767.0 / 32_768, -1.0 / 32_768, 256.0 / 32_768]
        for (i, value) in expected.enumerated() {
            XCTAssertEqual(out[i], value, accuracy: 1e-7, "sample \(i)")
        }
    }

    func test_makeBuffer_emptyChunk_returnsNil() {
        XCTAssertNil(AudioPlayer.makeBuffer(fromPCM16: Data(), format: pcmFormat(24_000, 1)))
        XCTAssertNil(AudioPlayer.makeBuffer(fromPCM16: Data([0x01]), format: pcmFormat(24_000, 1)))
    }

    func test_makeBuffer_refusesANonFloatFormat() {
        let int16 = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24_000,
                                  channels: 1, interleaved: false)!
        XCTAssertNil(AudioPlayer.makeBuffer(fromPCM16: Data([0, 1, 2, 3]), format: int16))
    }

    /// Drives one real chunk through the real engine: makeBuffer -> scheduleBuffer ->
    /// node.play. A buffer whose format disagrees with the connection format raises an
    /// uncatchable exception in scheduleBuffer, which would kill the app on the first
    /// spoken reply. Device Farm has no audio path, so this is the only automated check
    /// that the playback half of the Float32 fix works.
    func test_play_schedulesARealChunkThroughTheRealEngine() async throws {
        let player = AudioPlayer()
        try? await player.start()
        let started = await player.isStarted
        guard started else { throw XCTSkip("the simulator's audio engine did not start") }

        // 100 ms of a 440 Hz tone: 24 kHz mono 16-bit little-endian, as Nova Sonic sends.
        let samples: [Int16] = (0..<2_400).map { i in
            Int16(8_000 * sin(2 * Double.pi * 440 * Double(i) / 24_000))
        }
        let chunk = samples.withUnsafeBufferPointer { Data(buffer: $0) }.base64EncodedString()

        await player.play(base64Chunk: chunk)

        let playing = await player.isPlayerNodePlaying
        XCTAssertTrue(playing, "a valid chunk must leave the player node playing")
        await player.stop()
    }
}

// MARK: - AudioCapture

final class AudioCaptureStartupTests: XCTestCase {

    private func environment(log: CallLog,
                             permission: Bool = true,
                             inputAvailable: Bool = true,
                             input: AVAudioFormat) -> AudioCapture.Environment {
        AudioCapture.Environment(
            requestPermission: {
                log.record("permission")
                return permission
            },
            activateSession: { log.record("activate") },
            isInputAvailable: {
                log.record("inputAvailable")
                return inputAvailable
            },
            readInputFormat: { _ in
                log.record("readFormat")
                return input
            }
        )
    }

    func test_start_withZeroHzInput_throwsInputUnavailable() async {
        let log = CallLog()
        let capture = AudioCapture(environment: environment(log: log, input: pcmFormat(0, 1)))

        do {
            _ = try await capture.start()
            XCTFail("start() must throw for a 0 Hz input format")
        } catch AudioCapture.CaptureError.inputUnavailable {
            // expected
        } catch {
            XCTFail("expected inputUnavailable, got \(error)")
        }

        let running = await capture.isRunning
        XCTAssertFalse(running)
        XCTAssertEqual(log.all, ["permission", "activate", "inputAvailable", "readFormat"])
    }

    /// With no input route, `engine.inputNode` must not be touched at all.
    func test_start_withNoInputRoute_throwsInputUnavailable_withoutReadingFormat() async {
        let log = CallLog()
        let capture = AudioCapture(environment: environment(log: log,
                                                            inputAvailable: false,
                                                            input: pcmFormat(0, 1)))

        do {
            _ = try await capture.start()
            XCTFail("start() must throw when no input is available")
        } catch AudioCapture.CaptureError.inputUnavailable {
            // expected
        } catch {
            XCTFail("expected inputUnavailable, got \(error)")
        }

        let running = await capture.isRunning
        XCTAssertFalse(running)
        XCTAssertFalse(log.all.contains("readFormat"), "inputNode must not be read with no input route")
    }

    func test_start_whenPermissionDenied_throwsBeforeTouchingTheSession() async {
        let log = CallLog()
        let capture = AudioCapture(environment: environment(log: log,
                                                            permission: false,
                                                            input: pcmFormat(48_000, 1)))

        do {
            _ = try await capture.start()
            XCTFail("start() must throw when permission is denied")
        } catch AudioCapture.CaptureError.permissionDenied {
            // expected
        } catch {
            XCTFail("expected permissionDenied, got \(error)")
        }

        XCTAssertEqual(log.all, ["permission"])
    }
}
