import { describe, it, expect } from 'vitest'
import { foldTo } from './replay'
import type { RecordedEvent } from './api'

function ev(seq: number, payload: object): RecordedEvent {
  return {
    seq,
    ts: new Date(seq * 100).toISOString(),
    turn_id: null,
    trace_id: null,
    span_id: null,
    type: (payload as { type: string }).type,
    payload: payload as never,
  }
}

const LOG: RecordedEvent[] = [
  ev(0, { type: 'ready', session_id: 's1' }),
  ev(1, { type: 'stt_final', text: 'hi' }),
  ev(2, { type: 'assistant_delta', text: 'he' }),
  ev(3, { type: 'assistant_delta', text: 'llo' }),
  ev(4, { type: 'assistant_done', text: 'hello' }),
  ev(5, { type: 'state', state: 'idle' }),
]

describe('foldTo', () => {
  it('reproduces the live end-state when folding the whole log', () => {
    const final = foldTo(LOG, LOG.length)
    expect(final.sessionId).toBe('s1')
    expect(final.status).toBe('idle')
    expect(final.messages).toEqual([
      { role: 'user', text: 'hi' },
      { role: 'assistant', text: 'hello' },
    ])
  })

  it('scrubbing matches an incremental fold at every cursor', () => {
    for (let k = 0; k <= LOG.length; k++) {
      const viaFold = foldTo(LOG, k)
      const incremental = foldTo(LOG.slice(0, k), k)
      expect(viaFold).toEqual(incremental)
    }
  })

  it('mid-stream cursor shows the partial assistant message', () => {
    const mid = foldTo(LOG, 3) // ready, stt_final, one delta
    expect(mid.messages[1]).toEqual({ role: 'assistant', text: 'he' })
    expect(mid.assistantInProgress).toBe(true)
  })
})
