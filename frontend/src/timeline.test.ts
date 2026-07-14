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

  it('does not bind a stale speech_started to a later onsetless turn; keeps rows chronological', () => {
    const A = 'turn-A'
    const B = 'turn-B'
    const events: RecordedEvent[] = [
      ev(0, 1000, 'speech_started', null),
      ev(1, 2000, 'stt_final', A, { text: 'first' }),
      ev(2, 2100, 'tts_start', A, { sentence_index: 0, text: 'ok' }),
      ev(3, 3000, 'tts_end', A),
      ev(4, 50000, 'state', B, { state: 'thinking' }),
      ev(5, 50200, 'tts_start', B, { sentence_index: 0, text: 'sure' }),
      ev(6, 51000, 'tts_end', B),
    ]
    const tl = computeTurns(events)
    expect(tl.turns.map((t) => t.turnId)).toEqual([A, B])
    const turnB = tl.turns.find((t) => t.turnId === B)!
    expect(turnB.t0).toBe(49000)
    expect(turnB.segments.some((s) => s.kind === 'listening')).toBe(false)
  })

  it('emits a tool sub-span matched by call_id', () => {
    const events: RecordedEvent[] = [
      ev(0, 0, 'stt_final', T, { text: 'weather' }),
      ev(1, 500, 'tool_call', T, { call_id: 'c1', name: 'get_weather', arguments: '{}' }),
      ev(2, 900, 'tool_result', T, { call_id: 'c1', name: 'get_weather', output: 'sunny' }),
      ev(3, 1000, 'tts_start', T, { sentence_index: 0, text: 'its sunny' }),
      ev(4, 1800, 'tts_end', T),
    ]
    const tool = computeTurns(events).turns[0].segments.find((s) => s.kind === 'tool')!
    expect(tool.start).toBe(500)
    expect(tool.end).toBe(900)
  })

  it('returns empty for an empty event list', () => {
    expect(computeTurns([])).toEqual({ turns: [], start: 0, end: 0 })
  })

  it('groups a timer notification into one labelled turn', () => {
    const TT = 'turn-timer'
    const events: RecordedEvent[] = [
      ev(0, 1000, 'timer_fired', TT, { timer_id: 'x1', label: 'tea' }),
      ev(1, 1000, 'state', TT, { state: 'speaking' }),
      ev(2, 1000, 'tts_start', TT, { sentence_index: 0, text: 'Your tea timer is done.' }),
      ev(3, 1500, 'tts_end', TT),
    ]
    const tl = computeTurns(events)
    expect(tl.turns).toHaveLength(1)
    expect(tl.turns[0].turnId).toBe(TT)
    expect(tl.turns[0].utterance).toBe('⏰ tea timer')
  })
})
