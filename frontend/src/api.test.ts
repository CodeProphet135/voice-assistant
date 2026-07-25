import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fetchEvents } from './api'

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

describe('fetchEvents', () => {
  beforeEach(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('drops rows whose payload does not match its declared type, keeps the rest', async () => {
    const rows = [
      { seq: 0, ts: 't0', turn_id: null, trace_id: null, span_id: null, type: 'ready', payload: { type: 'ready', session_id: 's1' } },
      // Drifted: tool_call missing call_id -- would silently break timeline
      // tool-span matching if it reached computeTurns/the reducer unchecked.
      { seq: 1, ts: 't1', turn_id: 't', trace_id: null, span_id: null, type: 'tool_call', payload: { type: 'tool_call', name: 'get_weather' } },
      { seq: 2, ts: 't2', turn_id: null, trace_id: null, span_id: null, type: 'tts_end', payload: { type: 'tts_end' } },
    ]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(rows)))

    const events = await fetchEvents('session-1')

    expect(events.map((e) => e.seq)).toEqual([0, 2])
  })
})
