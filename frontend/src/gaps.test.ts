import { describe, it, expect } from 'vitest'
import { findSeqGaps } from './gaps'
import type { RecordedEvent } from './api'

function ev(seq: number, type = 'state'): RecordedEvent {
  return {
    seq,
    ts: new Date(0).toISOString(),
    turn_id: null,
    trace_id: null,
    span_id: null,
    type,
    payload: { type, state: 'idle' } as never,
  }
}

describe('findSeqGaps', () => {
  it('returns nothing for an empty or single-event list', () => {
    expect(findSeqGaps([])).toEqual([])
    expect(findSeqGaps([ev(5)])).toEqual([])
  })

  it('returns nothing for contiguous seq', () => {
    expect(findSeqGaps([ev(0), ev(1), ev(2)])).toEqual([])
  })

  it('finds a single gap', () => {
    expect(findSeqGaps([ev(0), ev(1), ev(4)])).toEqual([
      { afterSeq: 1, beforeSeq: 4, missingCount: 2 },
    ])
  })

  it('finds multiple gaps', () => {
    expect(findSeqGaps([ev(0), ev(2), ev(3), ev(7)])).toEqual([
      { afterSeq: 0, beforeSeq: 2, missingCount: 1 },
      { afterSeq: 3, beforeSeq: 7, missingCount: 3 },
    ])
  })
})
