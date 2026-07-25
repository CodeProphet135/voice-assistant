import { describe, it, expect, vi, beforeEach } from 'vitest'
import { validateServerEvent } from './eventValidation'

describe('validateServerEvent', () => {
  beforeEach(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('passes through a well-formed payload for every known type', () => {
    const cases = [
      { type: 'ready', session_id: 's1' },
      { type: 'state', state: 'idle' },
      { type: 'speech_started' },
      { type: 'stt_partial', text: 'hi' },
      { type: 'stt_final', text: 'hi' },
      { type: 'assistant_delta', text: 'hi' },
      { type: 'assistant_done', text: 'hi' },
      { type: 'llm_request', input: [], model: 'gpt-5-mini', iteration: 0 },
      { type: 'tool_call', call_id: 'c1', name: 'get_weather', arguments: '{}' },
      { type: 'tool_result', call_id: 'c1', name: 'get_weather', arguments: '{}', output: 'ok' },
      { type: 'tts_start', sentence_index: 0, text: 'hi' },
      { type: 'tts_end' },
      { type: 'tts_cancel' },
      { type: 'timer_fired', timer_id: 't1', label: 'tea' },
      { type: 'error', message: 'boom' },
    ]
    for (const payload of cases) {
      expect(validateServerEvent(payload)).toEqual(payload)
    }
  })

  it('rejects a required field arriving as the wrong type, per event type', () => {
    const cases = [
      { type: 'ready' }, // session_id missing
      { type: 'stt_final', text: null },
      { type: 'tool_call', call_id: 'c1' }, // name missing
      { type: 'tool_result', call_id: 'c1', name: 'x', output: 42 }, // output wrong type
      { type: 'tts_start', sentence_index: '0', text: 'hi' }, // sentence_index wrong type
      { type: 'timer_fired' }, // timer_id missing
    ]
    for (const payload of cases) {
      expect(validateServerEvent(payload)).toBeNull()
    }
  })

  it('rejects a non-object payload and a payload with no string type', () => {
    expect(validateServerEvent(null)).toBeNull()
    expect(validateServerEvent('nope')).toBeNull()
    expect(validateServerEvent({})).toBeNull()
    expect(validateServerEvent({ type: 1 })).toBeNull()
  })

  it('passes an unrecognized type through unvalidated (forward compatibility)', () => {
    const payload = { type: 'future_event', anything: true }
    expect(validateServerEvent(payload)).toBe(payload)
  })

  it('warns only once per (type, missing-field) combination', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // A type/field combination no other case in this file already triggered
    // -- the warn-once state is module-level, so it must be fresh here.
    for (let i = 0; i < 5; i++) {
      validateServerEvent({ type: 'state' })
    }
    expect(warn).toHaveBeenCalledTimes(1)
  })
})
