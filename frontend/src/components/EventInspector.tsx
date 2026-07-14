import { Fragment } from 'react'
import type { RecordedEvent } from '../api'
import { findSeqGaps } from '../gaps'

export function EventInspector({
  events,
  selected,
  onSelect,
}: {
  events: RecordedEvent[]
  selected: number
  onSelect: (n: number) => void
}) {
  const current = events[selected]
  const gapsByBeforeSeq = new Map(findSeqGaps(events).map((g) => [g.beforeSeq, g]))
  return (
    <div className="inspector">
      <ol className="inspector-list">
        {events.map((e, i) => {
          const gap = gapsByBeforeSeq.get(e.seq)
          return (
            <Fragment key={i}>
              {gap && (
                <li className="inspector-gap" role="alert">
                  {gap.missingCount} event{gap.missingCount === 1 ? '' : 's'} missing (seq{' '}
                  {gap.afterSeq}–{gap.beforeSeq})
                </li>
              )}
              <li className={i === selected ? 'active' : ''}>
                <button onClick={() => onSelect(i)}>
                  <span className="muted">{e.seq}</span> {e.type}
                </button>
              </li>
            </Fragment>
          )
        })}
      </ol>
      {current && (
        <pre className="inspector-payload">
          {JSON.stringify(
            { ts: current.ts, turn_id: current.turn_id, trace_id: current.trace_id, span_id: current.span_id, payload: current.payload },
            null,
            2,
          )}
        </pre>
      )}
    </div>
  )
}
