// Gapless playback of the raw linear16/24kHz/mono PCM audio the backend
// streams over the WS (see CLAUDE.md: Deepgram TTS, Aura REST, per-sentence,
// linear16 @ 24kHz, container=none). Each binary WS frame is an arbitrary
// byte slice of that PCM stream — a frame boundary may split a 2-byte
// sample, so a leftover odd byte is carried across enqueue() calls.
//
// Scheduling uses a `nextStartTime` cursor into the AudioContext clock: each
// chunk is scheduled to start exactly when the previous one ends (or "now"
// if we've fallen behind), which plays consecutive sentences back-to-back
// with no gap or overlap.

const SAMPLE_RATE = 24000

type AudioContextCtor = typeof AudioContext

function getAudioContextCtor(): AudioContextCtor {
  // Safari still ships AudioContext under the vendor-prefixed name.
  const ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext
  if (!ctor) throw new Error('Web Audio API is not supported in this browser.')
  return ctor
}

/** Plays a stream of raw linear16/24kHz/mono PCM chunks gaplessly; flush() supports barge-in. */
export class AudioPlayer {
  private context: AudioContext | null = null
  private nextStartTime = 0
  private readonly sources = new Set<AudioBufferSourceNode>()
  private leftoverByte: number | null = null

  /**
   * Fired whenever the playback buffer drains: the last scheduled source
   * finished playing, or flush() discarded everything. The live session uses
   * this to report ACTUAL playback end to the backend (`playback_finished`),
   * so the echo guard can stand down on real timing instead of holding its
   * wider server-side-estimate window. Gapless back-to-back chunks never
   * fire it spuriously — the next chunk's source is already tracked before
   * the previous one ends.
   */
  onDrained: (() => void) | null = null

  /** Lazily creates (or resumes) the AudioContext. Safe to call repeatedly. */
  private ensureContext(): AudioContext {
    if (!this.context) {
      const Ctor = getAudioContextCtor()
      this.context = new Ctor()
    }
    if (this.context.state === 'suspended') {
      // The mic button click that started this turn already satisfied the
      // browser's user-gesture requirement, so resume() is permitted here.
      void this.context.resume().catch(() => {})
    }
    return this.context
  }

  /** Enqueues a raw PCM chunk for gapless playback, carrying any leftover odd byte forward. */
  enqueue(pcm: ArrayBuffer): void {
    const ctx = this.ensureContext()

    let bytes = new Uint8Array(pcm)
    if (this.leftoverByte !== null) {
      const combined = new Uint8Array(bytes.length + 1)
      combined[0] = this.leftoverByte
      combined.set(bytes, 1)
      bytes = combined
      this.leftoverByte = null
    }

    if (bytes.length === 0) return

    if (bytes.length % 2 !== 0) {
      this.leftoverByte = bytes[bytes.length - 1]
      bytes = bytes.subarray(0, bytes.length - 1)
    }

    if (bytes.length === 0) return

    const numSamples = bytes.length / 2
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    const float32 = new Float32Array(numSamples)
    for (let i = 0; i < numSamples; i++) {
      float32[i] = view.getInt16(i * 2, true) / 32768
    }

    // Creating the buffer at the source's native 24kHz lets the browser
    // resample to the context's actual output rate automatically — do not
    // force the AudioContext itself to run at 24000.
    const buffer = ctx.createBuffer(1, numSamples, SAMPLE_RATE)
    buffer.copyToChannel(float32, 0)

    const source = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(ctx.destination)
    source.onended = () => {
      // flush() already emptied the set (and reported the drain itself), so
      // a stopped source's late onended must not double-fire onDrained —
      // only a source still tracked here can be the last one playing.
      if (!this.sources.delete(source)) return
      if (this.sources.size === 0) this.onDrained?.()
    }

    const startAt = Math.max(this.nextStartTime, ctx.currentTime)
    source.start(startAt)
    this.nextStartTime = startAt + buffer.duration
    this.sources.add(source)
  }

  /** Barge-in: stops all scheduled/playing audio immediately and resets scheduling state. */
  flush(): void {
    for (const source of this.sources) {
      try {
        source.stop()
      } catch {
        // Already stopped/ended — fine to ignore.
      }
    }
    this.sources.clear()
    this.nextStartTime = 0
    // A cancelled turn's trailing half-sample must not bleed into the next turn.
    this.leftoverByte = null
    // Discarding buffered audio ends playback just as surely as playing it
    // out — report the drain so the echo guard can stand down.
    this.onDrained?.()
  }

  /** Flushes and tears down the AudioContext. Idempotent. */
  async close(): Promise<void> {
    this.flush()
    if (this.context) {
      const context = this.context
      this.context = null
      if (context.state !== 'closed') {
        await context.close()
      }
    }
  }
}
