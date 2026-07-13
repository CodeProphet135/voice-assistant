import type { RecordedEvent } from '../api'

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
  return (
    <div className="inspector">
      <ol className="inspector-list">
        {events.map((e, i) => (
          <li key={i} className={i === selected ? 'active' : ''}>
            <button onClick={() => onSelect(i)}>
              <span className="muted">{e.seq}</span> {e.type}
            </button>
          </li>
        ))}
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
