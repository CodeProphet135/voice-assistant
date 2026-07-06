// AudioWorkletProcessor that downsamples the mic's native-rate mono Float32
// audio to 16kHz Int16LE PCM and posts it back to the main thread as raw
// ArrayBuffers (transferred, not copied). See CLAUDE.md: mic capture is
// AudioWorklet -> 16kHz mono Int16 PCM, not MediaRecorder.
//
// Ambient declarations: the AudioWorklet global scope (AudioWorkletProcessor,
// registerProcessor, sampleRate) is not part of the default TS lib, and this
// file is typechecked standalone by `tsc -b`. No new npm deps — declare the
// minimal surface we use.
declare const sampleRate: number
declare function registerProcessor(name: string, ctor: unknown): void
declare abstract class AudioWorkletProcessor {
  readonly port: MessagePort
  constructor()
  process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>,
  ): boolean
}

const TARGET_SAMPLE_RATE = 16000
// ~30ms of 16kHz audio per posted chunk (480 samples * 2 bytes = 960 bytes).
const CHUNK_SIZE_SAMPLES = 480

class PCMWorklet extends AudioWorkletProcessor {
  private readonly ratio: number
  /** Fractional input-sample position of the next output sample, relative to the start of `pending`. */
  private inputCursor = 0
  /** Leftover input samples (Float32, native rate) not yet fully consumed by the resampler. */
  private pending: Float32Array = new Float32Array(0)
  /** Accumulates resampled Int16 output until it reaches CHUNK_SIZE_SAMPLES. */
  private outBuffer: Int16Array = new Int16Array(CHUNK_SIZE_SAMPLES)
  private outLength = 0

  constructor() {
    super()
    this.ratio = sampleRate / TARGET_SAMPLE_RATE
  }

  process(inputs: Float32Array[][]): boolean {
    const input = inputs[0]?.[0]
    if (!input || input.length === 0) return true

    // Concatenate leftover samples from the previous block with this block.
    const combined = new Float32Array(this.pending.length + input.length)
    combined.set(this.pending, 0)
    combined.set(input, this.pending.length)

    let cursor = this.inputCursor
    // Index of the last i0 actually used for interpolation this block (-1 if
    // none). We rebase `pending`/`inputCursor` relative to this index, NOT
    // the post-loop `cursor` value — `cursor` can overshoot past the last
    // valid i1 by up to `ratio - 1` samples once the loop's break condition
    // triggers, and rebasing against that overshot value would silently
    // "consume" input samples that were never actually read, permanently
    // drifting the resampler's output rate (verified: causes ~0.8% excess
    // output samples over a long capture at 48kHz->16kHz).
    let lastI0 = -1

    // Linear-interpolation resample: step through output sample positions in
    // input-sample units (`ratio` input samples per output sample), so no
    // samples are dropped across process() block boundaries.
    while (true) {
      const i0 = Math.floor(cursor)
      const i1 = i0 + 1
      if (i1 >= combined.length) break

      const frac = cursor - i0
      const sample = combined[i0] + (combined[i1] - combined[i0]) * frac

      const clamped = Math.max(-1, Math.min(1, sample))
      this.outBuffer[this.outLength] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff
      this.outLength += 1

      if (this.outLength >= CHUNK_SIZE_SAMPLES) {
        this.flush()
      }

      lastI0 = i0
      cursor += this.ratio
    }

    // Retain input samples starting at the last i0 we actually read (so the
    // next block can still interpolate between it and the next arriving
    // sample), rebasing the cursor to be relative to that retained tail. If
    // no sample was produced this block, keep everything and leave the
    // cursor as-is (still relative to the start of `combined`).
    const retainFrom = lastI0 === -1 ? 0 : lastI0
    this.pending = combined.slice(retainFrom)
    this.inputCursor = cursor - retainFrom

    return true
  }

  private flush(): void {
    if (this.outLength === 0) return
    const buffer = this.outBuffer.slice(0, this.outLength).buffer
    this.port.postMessage(buffer, [buffer])
    this.outBuffer = new Int16Array(CHUNK_SIZE_SAMPLES)
    this.outLength = 0
  }
}

registerProcessor('pcm-worklet', PCMWorklet)
