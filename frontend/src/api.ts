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

export async function fetchEvents(id: string): Promise<RecordedEvent[]> {
  const res = await fetch(`/api/sessions/${id}/events`)
  if (!res.ok) throw new Error(`GET /api/sessions/${id}/events failed: ${res.status}`)
  return res.json()
}
