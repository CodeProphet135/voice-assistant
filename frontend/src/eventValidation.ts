// Runtime validation for `ServerEvent` payloads, mirroring
// `backend/src/voice_assistant/protocol.py`'s field requirements.
//
// The wire types in ws.ts are compile-time only -- nothing stops a payload
// that doesn't actually match its declared `type` from reaching the reducer
// (state.ts) or the Timeline/Replay views, since it arrives as untyped JSON
// (fresh over the socket) or as an untyped Postgres JSONB column (persisted
// events, which can predate a protocol change). An `as ServerEvent` cast
// doesn't check anything at runtime: a payload missing a field its type
// declares as required doesn't crash, it just reads as `undefined`
// downstream -- a tool_result silently fails to match its tool_call, a
// span silently never renders. This is the same failure shape as
// providers/deepgram.py before this normalized it: validate once at the
// boundary, so drift is visible instead of silently wrong.
//
// Unknown/forward `type`s pass through unvalidated -- a future event type
// this file doesn't know about yet is not drift, and the reducer's default
// case already ignores it safely.

import type { ServerEvent } from './ws'

const _warned = new Set<string>()

function warnOnce(type: string, detail: string): void {
  const key = `${type}:${detail}`
  if (_warned.has(key)) return
  _warned.add(key)
  console.warn(
    `[eventValidation] dropping malformed "${type}" event payload -- missing/invalid ${detail} ` +
      '(the wire format may have drifted from protocol.py, or this is an old persisted event)',
  )
}

function isString(v: unknown): v is string {
  return typeof v === 'string'
}

function isNumber(v: unknown): v is number {
  return typeof v === 'number'
}

/** Field checks per `type`, matching the required fields in protocol.py's
 * `ServerEvent` union. Only the discriminator plus these fields are
 * checked -- optional/defaulted fields (e.g. `tool_result.arguments`) are
 * intentionally not required here, same as on the backend. */
const _REQUIRED: Record<string, (p: Record<string, unknown>) => string | null> = {
  ready: (p) => (isString(p.session_id) ? null : 'session_id'),
  state: (p) => (isString(p.state) ? null : 'state'),
  speech_started: () => null,
  stt_partial: (p) => (isString(p.text) ? null : 'text'),
  stt_final: (p) => (isString(p.text) ? null : 'text'),
  assistant_delta: (p) => (isString(p.text) ? null : 'text'),
  assistant_done: (p) => (isString(p.text) ? null : 'text'),
  llm_request: (p) =>
    Array.isArray(p.input) && isString(p.model) && isNumber(p.iteration)
      ? null
      : 'input/model/iteration',
  tool_call: (p) => (isString(p.call_id) && isString(p.name) ? null : 'call_id/name'),
  tool_result: (p) =>
    isString(p.call_id) && isString(p.name) && isString(p.output) ? null : 'call_id/name/output',
  tts_start: (p) =>
    isNumber(p.sentence_index) && isString(p.text) ? null : 'sentence_index/text',
  tts_end: () => null,
  tts_cancel: () => null,
  timer_fired: (p) => (isString(p.timer_id) ? null : 'timer_id'),
  error: (p) => (isString(p.message) ? null : 'message'),
}

/** Validate an untrusted payload (fresh off the socket, or read back out of
 * the event log) against the `ServerEvent` shape its own `type` declares.
 * Returns `null` -- warning once per (type, missing-field) combination --
 * rather than letting a malformed payload flow into the reducer or Timeline
 * as if it were well-formed. */
export function validateServerEvent(payload: unknown): ServerEvent | null {
  if (typeof payload !== 'object' || payload === null) {
    warnOnce('<unknown>', 'payload is not an object')
    return null
  }
  const p = payload as Record<string, unknown>
  if (!isString(p.type)) {
    warnOnce('<unknown>', '"type"')
    return null
  }
  const check = _REQUIRED[p.type]
  if (check === undefined) {
    // Unrecognized type: forward-compatible, not drift -- pass through.
    return payload as ServerEvent
  }
  const missing = check(p)
  if (missing !== null) {
    warnOnce(p.type, missing)
    return null
  }
  return payload as ServerEvent
}
