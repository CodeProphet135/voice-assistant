import { describe, it, expect } from 'vitest'
import { reducer, initialState, type AppState } from './state'
import type { ServerEvent } from './ws'

// A representative live-turn event sequence: ready -> user speaks -> assistant
// streams a reply -> a tool runs -> done.
const SCRIPT: Array<ServerEvent | { type: 'user_submit'; text: string }> = [
  { type: 'ready', session_id: 's1' },
  { type: 'stt_final', text: 'weather in Tokyo' },
  { type: 'state', state: 'thinking' },
  { type: 'tool_call', call_id: 'c1', name: 'get_weather', arguments: '{"city":"Tokyo"}' },
  { type: 'tool_result', call_id: 'c1', name: 'get_weather', output: 'sunny' },
  { type: 'assistant_delta', text: "It's " },
  { type: 'assistant_delta', text: 'sunny.' },
  { type: 'assistant_done', text: "It's sunny." },
  { type: 'state', state: 'idle' },
]

function run(script: typeof SCRIPT): AppState {
  return script.reduce((state, action) => reducer(state, action as never), initialState)
}

describe('reducer', () => {
  it('produces the expected final state for a full turn', () => {
    const final = run(SCRIPT)
    expect(final.sessionId).toBe('s1')
    expect(final.status).toBe('idle')
    expect(final.messages).toEqual([
      { role: 'user', text: 'weather in Tokyo' },
      { role: 'assistant', text: "It's sunny." },
    ])
    expect(final.assistantInProgress).toBe(false)
    expect(final.toolActivity).toEqual([
      {
        call_id: 'c1',
        name: 'get_weather',
        arguments: '{"city":"Tokyo"}',
        output: 'sunny',
        status: 'done',
      },
    ])
  })

  it('is deterministic — same script yields deep-equal state', () => {
    expect(run(SCRIPT)).toEqual(run(SCRIPT))
  })

  it('is pure — does not mutate its input state', () => {
    const before = structuredClone(initialState)
    reducer(initialState, { type: 'ready', session_id: 's1' } as never)
    expect(initialState).toEqual(before)
  })

  it('reset clears a populated state back to initialState', () => {
    const populated = run(SCRIPT)
    // sanity: the script really did populate messages, so the reset is meaningful
    expect(populated.messages.length).toBeGreaterThan(0)
    const afterReset = reducer(populated, { type: 'reset' })
    expect(afterReset).toEqual(initialState)
  })
})
