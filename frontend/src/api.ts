import { validateServerEvent } from './eventValidation'
import type { ServerEvent } from './ws'

export interface SessionSummary {
  id: string
  started_at: string
  ended_at: string | null
  title: string | null
}

export interface RecordedEvent {
  seq: number
  ts: string
  turn_id: string | null
  trace_id: string | null
  span_id: string | null
  type: string
  payload: ServerEvent
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const res = await fetch('/api/sessions')
  if (!res.ok) throw new Error(`GET /api/sessions failed: ${res.status}`)
  return res.json()
}

/** The `/events` payload column is untyped JSONB on the server (it can
 * predate a protocol change), so this is the boundary where a persisted
 * event's shape is actually checked against what its `type` declares --
 * everything downstream (Timeline, Replay, the reducer) trusts `payload` is
 * well-formed because this filters out anything that isn't. */
export async function fetchEvents(id: string): Promise<RecordedEvent[]> {
  const res = await fetch(`/api/sessions/${id}/events`)
  if (!res.ok) throw new Error(`GET /api/sessions/${id}/events failed: ${res.status}`)
  const rows: RecordedEvent[] = await res.json()
  return rows.filter((row) => validateServerEvent(row.payload) !== null)
}
