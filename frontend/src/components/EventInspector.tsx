import { Fragment, useEffect, useRef } from 'react'
import type { RecordedEvent } from '../api'
import { findSeqGaps } from '../gaps'

/** Maps an event type to its pipeline-state hue so the list reads like the timeline. */
function eventHue(type: string): string {
  if (type.startsWith('stt') || type === 'speech_started') return 'var(--c-listening)'
  if (type.startsWith('assistant') || type === 'llm_request') return 'var(--c-thinking)'
  if (type.startsWith('tool')) return 'var(--c-tool)'
  if (type.startsWith('tts')) return 'var(--c-speaking)'
  if (type === 'error') return 'var(--c-danger)'
  return 'var(--muted)'
}

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
  const listRef = useRef<HTMLOListElement>(null)

  // Keep the selected event visible while replay advances the cursor.
  useEffect(() => {
    listRef.current
      ?.querySelector('li.active')
      ?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  return (
    <div className="inspector">
      <ol className="inspector-list" ref={listRef}>
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
                  <i className="evt-dot" style={{ background: eventHue(e.type) }} />
                  <span className="evt-seq">{e.seq}</span>
                  <span className="evt-type">{e.type}</span>
                </button>
              </li>
            </Fragment>
          )
        })}
      </ol>
      {current && (
        <pre className="inspector-payload">
          {JSON.stringify(
            {
              ts: current.ts,
              turn_id: current.turn_id,
              trace_id: current.trace_id,
              span_id: current.span_id,
              payload: current.payload,
            },
            null,
            2,
          )}
        </pre>
      )}
    </div>
  )
}
