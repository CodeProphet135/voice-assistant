// Drain-signal tests for AudioPlayer: onDrained must fire exactly once per
// drain episode — when the LAST tracked source ends, or when flush() discards
// the buffer — and never in between gapless chunks or twice for the same
// drain (a flushed source's late onended must not re-fire it). The Web Audio
// API isn't available under vitest's node environment, so a minimal fake
// AudioContext stands in; the player only touches the surface faked here.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AudioPlayer } from './player'

class FakeSource {
  buffer: unknown = null
  onended: (() => void) | null = null
  stopped = false
  connect(): void {}
  start(_at: number): void {}
  stop(): void {
    this.stopped = true
  }
}

class FakeAudioContext {
  state = 'running'
  currentTime = 0
  destination = {}
  readonly created: FakeSource[] = []

  createBuffer(_channels: number, numSamples: number, sampleRate: number) {
    return { duration: numSamples / sampleRate, copyToChannel: () => {} }
  }

  createBufferSource(): FakeSource {
    const source = new FakeSource()
    this.created.push(source)
    return source
  }

  resume(): Promise<void> {
    return Promise.resolve()
  }

  close(): Promise<void> {
    this.state = 'closed'
    return Promise.resolve()
  }
}

let ctx: FakeAudioContext

beforeEach(() => {
  vi.stubGlobal('window', {
    AudioContext: function () {
      ctx = new FakeAudioContext()
      return ctx
    },
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** An even-length PCM chunk (2 bytes per sample). */
function chunk(samples = 2): ArrayBuffer {
  return new ArrayBuffer(samples * 2)
}

describe('AudioPlayer.onDrained', () => {
  it('fires once when the last of several chunks ends, not between chunks', () => {
    const player = new AudioPlayer()
    const drained = vi.fn()
    player.onDrained = drained

    player.enqueue(chunk())
    player.enqueue(chunk())
    expect(ctx.created).toHaveLength(2)

    ctx.created[0].onended?.()
    expect(drained).not.toHaveBeenCalled()

    ctx.created[1].onended?.()
    expect(drained).toHaveBeenCalledTimes(1)
  })

  it('fires again for a later playback burst (once per drain episode)', () => {
    const player = new AudioPlayer()
    const drained = vi.fn()
    player.onDrained = drained

    player.enqueue(chunk())
    ctx.created[0].onended?.()
    expect(drained).toHaveBeenCalledTimes(1)

    player.enqueue(chunk())
    ctx.created[1].onended?.()
    expect(drained).toHaveBeenCalledTimes(2)
  })

  it('flush() reports the drain itself, and stopped sources late onended does not double-fire', () => {
    const player = new AudioPlayer()
    const drained = vi.fn()
    player.onDrained = drained

    player.enqueue(chunk())
    player.enqueue(chunk())
    player.flush()
    expect(ctx.created.every((s) => s.stopped)).toBe(true)
    expect(drained).toHaveBeenCalledTimes(1)

    // The real AudioContext fires onended asynchronously for stopped
    // sources — after flush already cleared the set and reported the drain.
    ctx.created[0].onended?.()
    ctx.created[1].onended?.()
    expect(drained).toHaveBeenCalledTimes(1)
  })

  it('a chunk enqueued after a flush gets its own drain report when it ends', () => {
    const player = new AudioPlayer()
    const drained = vi.fn()
    player.onDrained = drained

    player.enqueue(chunk())
    player.flush()
    expect(drained).toHaveBeenCalledTimes(1)

    player.enqueue(chunk())
    // The pre-flush source's late onended fires first — must not count.
    ctx.created[0].onended?.()
    expect(drained).toHaveBeenCalledTimes(1)

    ctx.created[1].onended?.()
    expect(drained).toHaveBeenCalledTimes(2)
  })
})
