import { describe, it, expect } from 'vitest'
import { computeTurns } from './timeline'
import type { RecordedEvent } from './api'

function ev(seq: number, tsMs: number, type: string, turn: string | null, payload: object = {}): RecordedEvent {
  return {
    seq,
    ts: new Date(tsMs).toISOString(),
    turn_id: turn,
    trace_id: null,
    span_id: null,
    type,
    payload: { type, ...payload } as never,
  }
}

const T = 'turn-1'

describe('computeTurns', () => {
  it('builds one turn with speech_started as t0 and phase segments', () => {
    const events: RecordedEvent[] = [
      ev(0, 1000, 'speech_started', null),
      ev(1, 2000, 'stt_final', T, { text: 'hi' }),
      ev(2, 2000, 'state', T, { state: 'thinking' }),
      ev(3, 2600, 'assistant_delta', T, { text: 'hello' }),
      ev(4, 2700, 'tts_start', T, { sentence_index: 0, text: 'hello' }),
      ev(5, 3500, 'tts_end', T),
    ]
    const tl = computeTurns(events)
    expect(tl.turns).toHaveLength(1)
    const turn = tl.turns[0]
    expect(turn.t0).toBe(0) // speech_started at 1000ms == session start
    expect(turn.cancelled).toBe(false)
    expect(turn.utterance).toBe('hi')
    const kinds = turn.segments.map((s) => s.kind)
    expect(kinds).toContain('listening')
    expect(kinds).toContain('thinking')
    expect(kinds).toContain('speaking')
    const speaking = turn.segments.find((s) => s.kind === 'speaking')!
    expect(speaking.end).toBe(2500) // 3500ms - 1000ms session start
  })

  it('marks a barge-in turn cancelled when it ends in tts_cancel', () => {
    const events: RecordedEvent[] = [
      ev(0, 0, 'stt_final', T, { text: 'go' }),
      ev(1, 100, 'tts_start', T, { sentence_index: 0, text: 'a long' }),
      ev(2, 900, 'tts_cancel', T),
    ]
    const tl = computeTurns(events)
    expect(tl.turns[0].cancelled).toBe(true)
    expect(tl.turns[0].segments.find((s) => s.kind === 'speaking')?.end).toBe(900)
  })
})
