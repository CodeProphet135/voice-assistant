// Mic capture controller: getUserMedia -> AudioWorklet -> 16kHz Int16 PCM
// chunks delivered to a caller-supplied callback. See CLAUDE.md: AudioWorklet
// capture, not MediaRecorder (Deepgram wants raw linear16, and MediaRecorder's
// chunk granularity is too coarse for low latency).
//
// Vite only transpiles + emits worklet/worker sources when the special
// `?worker&url` query suffix is used on the import specifier itself; a plain
// `new URL('./pcm-worklet.ts', import.meta.url)` passed straight to
// `addModule()` is NOT recognized as the worker-constructor shortcut (that
// detection only fires inside a literal `new Worker(...)` call), so Vite
// falls back to generic static-asset handling — which for an unrecognized
// extension like `.ts` means inlining the raw, untranspiled file contents as
// a base64 data: URL. `?worker&url` forces Vite to run the file through its
// normal transform pipeline (stripping types) and emit it as a real JS
// chunk, resolving to a URL string import — in both dev and build.
import pcmWorkletUrl from './pcm-worklet.ts?worker&url'

/** Thin controller around getUserMedia + AudioWorklet mic capture. */
export class MicCapture {
  private audioContext: AudioContext | null = null
  private sourceNode: MediaStreamAudioSourceNode | null = null
  private workletNode: AudioWorkletNode | null = null
  private stream: MediaStream | null = null

  /**
   * Starts microphone capture. Invokes `onPcm` with each 16kHz mono Int16LE
   * PCM chunk (as an ArrayBuffer) produced by the worklet. Throws if the user
   * denies microphone permission or audio setup otherwise fails — callers
   * should catch and surface this to the UI.
   */
  async start(onPcm: (buf: ArrayBuffer) => void): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        channelCount: 1,
      },
    })
    this.stream = stream

    const audioContext = new AudioContext()
    this.audioContext = audioContext

    await audioContext.audioWorklet.addModule(pcmWorkletUrl)

    const sourceNode = audioContext.createMediaStreamSource(stream)
    this.sourceNode = sourceNode

    const workletNode = new AudioWorkletNode(audioContext, 'pcm-worklet')
    workletNode.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      onPcm(event.data)
    }
    this.workletNode = workletNode

    // Source -> worklet only; we never connect to destination since we don't
    // want to play the mic back through the speakers.
    sourceNode.connect(workletNode)
  }

  /** Tears down capture. Idempotent and safe to call even if start() was never called. */
  async stop(): Promise<void> {
    this.sourceNode?.disconnect()
    this.workletNode?.disconnect()
    if (this.workletNode) {
      this.workletNode.port.onmessage = null
    }
    this.sourceNode = null
    this.workletNode = null

    for (const track of this.stream?.getTracks() ?? []) {
      track.stop()
    }
    this.stream = null

    if (this.audioContext) {
      await this.audioContext.close()
      this.audioContext = null
    }
  }
}
