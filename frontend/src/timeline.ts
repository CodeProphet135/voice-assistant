import type { RecordedEvent } from './api'

export type SegmentKind = 'listening' | 'thinking' | 'tool' | 'speaking'

export interface TurnSegment {
  kind: SegmentKind
  start: number
  end: number
}

export interface TurnRow {
  turnId: string
  t0: number
  tEnd: number
  cancelled: boolean
  utterance: string | null
  segments: TurnSegment[]
}

export interface Timeline {
  turns: TurnRow[]
  start: number
  end: number
}

function ms(e: RecordedEvent): number {
  return Date.parse(e.ts)
}

export function computeTurns(events: RecordedEvent[]): Timeline {
  if (events.length === 0) return { turns: [], start: 0, end: 0 }
  const start = ms(events[0])
  const rel = (e: RecordedEvent) => ms(e) - start
  const end = rel(events[events.length - 1])

  const speechStarts = events.filter((e) => e.type === 'speech_started')

  const byTurn = new Map<string, RecordedEvent[]>()
  for (const e of events) {
    if (e.turn_id == null) continue
    const list = byTurn.get(e.turn_id) ?? []
    list.push(e)
    byTurn.set(e.turn_id, list)
  }

  // Map insertion order is the order turns first appear in the seq-ordered
  // event list, i.e. chronological — so prevEndAbs tracks the previous turn's
  // end as we go.
  const turns: TurnRow[] = []
  let prevEndAbs = -Infinity
  for (const [turnId, group] of byTurn) {
    const first = group[0]
    const sttFinal = group.find((e) => e.type === 'stt_final')
    const firstTts = group.find((e) => e.type === 'tts_start')
    const ended = group.find((e) => e.type === 'tts_end' || e.type === 'tts_cancel')
    const cancelled = group.some((e) => e.type === 'tts_cancel')

    // Only voice turns (those with an stt_final) have a listening phase; their
    // onset is the latest speech_started after the previous turn ended and
    // at/before this turn's stt_final. Text-input and timer turns have no onset
    // event and must not inherit a stale earlier one.
    let precedingOnset: RecordedEvent | undefined
    if (sttFinal) {
      precedingOnset = speechStarts
        .filter((s) => ms(s) > prevEndAbs && ms(s) <= ms(sttFinal))
        .sort((a, b) => ms(b) - ms(a))[0]
    }
    const t0 = rel(precedingOnset ?? first)

    const segments: TurnSegment[] = []
    if (sttFinal && precedingOnset) {
      segments.push({ kind: 'listening', start: t0, end: rel(sttFinal) })
    }
    const thinkingStart = rel(sttFinal ?? first)
    if (firstTts) {
      segments.push({ kind: 'thinking', start: thinkingStart, end: rel(firstTts) })
      const speakEnd = ended ? rel(ended) : rel(group[group.length - 1])
      segments.push({ kind: 'speaking', start: rel(firstTts), end: speakEnd })
    } else {
      const thinkEnd = ended ? rel(ended) : rel(group[group.length - 1])
      segments.push({ kind: 'thinking', start: thinkingStart, end: thinkEnd })
    }

    // tool sub-spans matched by call_id, using raw tool_call->tool_result
    // timestamps (not clipped to the thinking window).
    const calls = new Map<string, number>()
    for (const e of group) {
      if (e.type === 'tool_call') calls.set((e.payload as { call_id: string }).call_id, rel(e))
      else if (e.type === 'tool_result') {
        const cid = (e.payload as { call_id: string }).call_id
        const cs = calls.get(cid)
        if (cs != null) segments.push({ kind: 'tool', start: cs, end: rel(e) })
      }
    }

    const tEnd = ended ? rel(ended) : rel(group[group.length - 1])
    const utterance = (sttFinal?.payload as { text?: string } | undefined)?.text ?? null
    turns.push({ turnId, t0, tEnd, cancelled, utterance, segments })

    prevEndAbs = ms(ended ?? group[group.length - 1])
  }

  turns.sort((a, b) => a.t0 - b.t0)
  return { turns, start, end }
}
