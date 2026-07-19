// A self-contained, looping alarm chime for the timer-done alert. Generated
// with the Web Audio API so there's no binary asset to ship — a repeating
// rising two-tone beep, scheduled a beat ahead so it stays glitch-free even
// while the main thread is busy. start()/stop() are idempotent; stop() cuts
// the sound immediately (used both on manual dismiss and the 10s auto-stop).

type AudioContextCtor = typeof AudioContext

function getAudioContextCtor(): AudioContextCtor {
  const ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext
  if (!ctor) throw new Error('Web Audio API is not supported in this browser.')
  return ctor
}

/** One cycle of the alarm: two rising beeps, then a short rest. */
const CYCLE_SECONDS = 1.2
const PEAK_GAIN = 0.22

export class AlarmSound {
  private context: AudioContext | null = null
  private scheduler: ReturnType<typeof setInterval> | null = null
  private nextCycleTime = 0

  /** Starts (or restarts) the looping alarm. Safe to call repeatedly. */
  start(): void {
    if (this.context) return

    let context: AudioContext
    try {
      context = new (getAudioContextCtor())()
    } catch {
      // No Web Audio support — the visual alert still stands on its own.
      return
    }
    this.context = context
    // The user has already interacted with the page (typing / mic), so the
    // context is allowed to resume outside an explicit gesture handler.
    if (context.state === 'suspended') void context.resume().catch(() => {})

    this.nextCycleTime = context.currentTime + 0.05
    const pump = () => this.scheduleAhead()
    pump()
    // Re-fill the schedule well before the audio clock drains it.
    this.scheduler = setInterval(pump, (CYCLE_SECONDS * 1000) / 2)
  }

  /** Schedules any cycles that fall within the next lookahead window. */
  private scheduleAhead(): void {
    const ctx = this.context
    if (!ctx) return
    const horizon = ctx.currentTime + CYCLE_SECONDS
    while (this.nextCycleTime < horizon) {
      this.scheduleCycle(this.nextCycleTime)
      this.nextCycleTime += CYCLE_SECONDS
    }
  }

  private scheduleCycle(start: number): void {
    this.beep(start, 784) // G5
    this.beep(start + 0.28, 1047) // C6
  }

  private beep(startAt: number, freq: number): void {
    const ctx = this.context
    if (!ctx) return
    const duration = 0.2
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'triangle'
    osc.frequency.value = freq
    // Short attack/release envelope so beeps don't click.
    gain.gain.setValueAtTime(0, startAt)
    gain.gain.linearRampToValueAtTime(PEAK_GAIN, startAt + 0.02)
    gain.gain.setValueAtTime(PEAK_GAIN, startAt + duration - 0.04)
    gain.gain.linearRampToValueAtTime(0, startAt + duration)
    osc.connect(gain).connect(ctx.destination)
    osc.start(startAt)
    osc.stop(startAt + duration + 0.02)
  }

  /** Stops the alarm immediately and tears down its context. Idempotent. */
  stop(): void {
    if (this.scheduler !== null) {
      clearInterval(this.scheduler)
      this.scheduler = null
    }
    const ctx = this.context
    this.context = null
    if (ctx && ctx.state !== 'closed') void ctx.close().catch(() => {})
  }
}
