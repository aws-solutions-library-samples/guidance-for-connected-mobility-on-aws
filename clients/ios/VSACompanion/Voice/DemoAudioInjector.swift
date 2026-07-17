import Foundation

/// Streams a bundled WAV clip into the voice session as if the driver
/// had spoken it. The point is to let the iOS Simulator (which can't
/// reliably capture mic) run the full demo flow on a Zoom call: tap a
/// scripted phrase → audio bytes go down the same WebSocket path mic
/// data normally would → backend's ASR transcribes → Nova replies via
/// TTS as usual.
///
/// Format expected: 16-bit signed little-endian PCM at 16 kHz, mono.
/// `say -o foo.wav --data-format=LEI16@16000 "..."` produces exactly
/// this; any 16 kHz/mono PCM WAV will work.
///
/// Pacing: chunks are sent at ~200 ms intervals to mimic real-time
/// capture. Sending the whole clip instantly works for Nova Sonic's
/// ASR but produces noticeably bursty transcript timing in the UI;
/// real-time pacing keeps the demo visually identical to a live mic
/// session.
actor DemoAudioInjector {
    /// Chunk duration in milliseconds — matches AudioCapture's tap so
    /// payload sizes line up with what the server sees from a live mic.
    private static let chunkDurationMs: Int = 200
    private static let sampleRate: Int = 16_000
    /// Bytes per chunk = sampleRate * (chunkMs/1000) * 2 (Int16) * 1 (mono).
    private static let bytesPerChunk: Int =
        sampleRate * chunkDurationMs / 1000 * 2

    /// How many silent chunks to append after the speech audio.
    /// 10 chunks × 200 ms = 2000 ms of silence. Bumped from 4 to 10
    /// on 2026-05-22 after empirical bisection showed Nova Sonic v2's
    /// bidi end-of-turn detection consistently fails below ~1600ms of
    /// trailing silence. The original "comfortably above 400-600ms"
    /// estimate was wrong for v2 bidi — Nova v2 needs ≥1600ms.
    /// Below that, Nova accepts the audio + tool result + narration
    /// injection but produces ZERO TTS output for the rest of the
    /// session (presents as the "AWS-confirmed TTS hallucination" but
    /// is actually deterministic and triggered here).
    private static let trailingSilenceChunks: Int = 10

    private let client: VSABidiClient

    init(client: VSABidiClient) {
        self.client = client
    }

    /// Stream the named bundled clip through the audio path. The clip
    /// name is the WAV filename without extension (e.g. "tire-pressure-low"
    /// for "tire-pressure-low.wav"). Throws if the clip is missing or
    /// can't be parsed; the caller handles user-facing error UI.
    func play(clipName: String) async throws {
        guard let url = Bundle.main.url(
            forResource: clipName, withExtension: "wav"
        ) else {
            throw InjectionError.clipNotFound(clipName)
        }
        let wavData = try Data(contentsOf: url)
        guard let pcmData = Self.extractDataChunk(from: wavData) else {
            throw InjectionError.invalidWAV(clipName)
        }
        // Walk the PCM payload in chunkDurationMs slices, base64
        // each slice, send. Sleep between chunks so we stay close to
        // real-time pacing — sending all 1-2s worth of audio in one
        // burst sometimes makes ASR hold the transcript until idle.
        var offset = 0
        while offset < pcmData.count {
            let end = min(offset + Self.bytesPerChunk, pcmData.count)
            let chunk = pcmData.subdata(in: offset..<end)
            let base64 = chunk.base64EncodedString()
            try await client.sendAudioChunk(base64, sampleRate: Self.sampleRate)
            offset = end
            if offset < pcmData.count {
                // Pace at 90% of chunk duration so we stay slightly
                // ahead of real-time — keeps the server's jitter
                // buffer fed without overrunning it.
                try await Task.sleep(
                    nanoseconds: UInt64(Self.chunkDurationMs * 900_000)
                )
            }
        }
        // Trailing silence — critical for fast end-of-utterance.
        //
        // Nova Sonic's server-side VAD detects end-of-turn from
        // trailing silence in the audio stream, not from us simply
        // stopping the chunk stream. If we send 1-2s of speech bytes
        // and then nothing, VAD's silence-detection timer never
        // arms and Nova waits ~50s for its overall idle timeout
        // before emitting the user transcript and replying.
        //
        // Sending ~800ms of zero-filled PCM after the speech triggers
        // VAD immediately (typical threshold ~400-600ms) so the
        // transcript and response come back in sub-second time —
        // matching what the live mic flow does naturally when the
        // user stops talking but capture is still streaming silence.
        let silentChunk = Data(count: Self.bytesPerChunk)  // all zeros
        let silentB64 = silentChunk.base64EncodedString()
        for _ in 0..<Self.trailingSilenceChunks {
            try await client.sendAudioChunk(silentB64, sampleRate: Self.sampleRate)
            try await Task.sleep(
                nanoseconds: UInt64(Self.chunkDurationMs * 900_000)
            )
        }
    }

    /// Errors surfaced for clip-loading / format problems. Demo-only
    /// path so we don't need full error semantics — just enough to
    /// let the UI show "couldn't play that one".
    enum InjectionError: LocalizedError {
        case clipNotFound(String)
        case invalidWAV(String)

        var errorDescription: String? {
            switch self {
            case .clipNotFound(let name):
                return "Demo clip '\(name).wav' not bundled with the app."
            case .invalidWAV(let name):
                return "Demo clip '\(name).wav' isn't a valid WAV file."
            }
        }
    }

    // MARK: - WAV parsing

    /// Walks a RIFF/WAVE container to find the `data` chunk's payload.
    /// Returns nil if the file isn't a WAVE or has no data chunk.
    ///
    /// Why a custom parser instead of AVAudioFile: AVAudioFile decodes
    /// to 32-bit float by default, which we'd then have to re-quantize
    /// back to Int16 to send. Since `say`'s output is already Int16 LE
    /// at 16 kHz, parsing the data chunk directly is simpler and
    /// preserves the bit-exact bytes the server expects.
    ///
    /// Tolerates `say`'s extra JUNK/FLLR padding chunks that sit
    /// between the `fmt ` and `data` chunks. A naive "skip 44 bytes"
    /// (which works for minimal WAVs from most encoders) breaks on
    /// `say`'s output, which prepends ~36 bytes of JUNK + 4-byte FLLR.
    private static func extractDataChunk(from wav: Data) -> Data? {
        // RIFF header: "RIFF" (4) + size (4) + "WAVE" (4) = 12 bytes.
        guard wav.count >= 12 else { return nil }
        guard wav.subdata(in: 0..<4) == "RIFF".data(using: .ascii),
              wav.subdata(in: 8..<12) == "WAVE".data(using: .ascii) else {
            return nil
        }
        var cursor = 12
        while cursor + 8 <= wav.count {
            let chunkId = wav.subdata(in: cursor..<cursor + 4)
            let chunkSize = wav.subdata(in: cursor + 4..<cursor + 8)
                .withUnsafeBytes { $0.load(as: UInt32.self) }
            let payloadStart = cursor + 8
            let payloadEnd = payloadStart + Int(chunkSize)
            if chunkId == "data".data(using: .ascii),
               payloadEnd <= wav.count {
                return wav.subdata(in: payloadStart..<payloadEnd)
            }
            // Chunks are word-aligned; if the size is odd, advance one
            // extra byte. Most WAVs have even sizes but the spec
            // allows otherwise.
            let advance = Int(chunkSize) + (Int(chunkSize) % 2)
            cursor = payloadStart + advance
        }
        return nil
    }
}
