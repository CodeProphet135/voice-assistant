import type { RecordedEvent } from './api'

export interface SeqGap {
  afterSeq: number
  beforeSeq: number
  missingCount: number
}

// Mirrors backend/src/voice_assistant/gaps.py: seq is assigned before a
// batch write is attempted and never reused, so a permanently dropped
// batch leaves a real, computable hole in an already seq-ordered event list.
export function findSeqGaps(events: RecordedEvent[]): SeqGap[] {
  const gaps: SeqGap[] = []
  for (let i = 1; i < events.length; i++) {
    const prev = events[i - 1].seq
    const next = events[i].seq
    if (next - prev > 1) {
      gaps.push({ afterSeq: prev, beforeSeq: next, missingCount: next - prev - 1 })
    }
  }
  return gaps
}
