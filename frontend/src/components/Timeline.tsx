import { computeTurns, type SegmentKind } from '../timeline'
import type { RecordedEvent } from '../api'

const COLORS: Record<SegmentKind, string> = {
  listening: '#378add',
  thinking: '#ef9f27',
  tool: '#7f77dd',
  speaking: '#639922',
}

export function Timeline({ events }: { events: RecordedEvent[] }) {
  const tl = computeTurns(events)
  if (tl.turns.length === 0) return <p className="muted">No turns to plot.</p>
  const span = Math.max(tl.end, 1)
  const pct = (v: number) => `${(v / span) * 100}%`

  return (
    <div className="timeline">
      <div className="timeline-legend">
        <span style={{ color: COLORS.listening }}>■ listening</span>
        <span style={{ color: COLORS.thinking }}>■ thinking</span>
        <span style={{ color: COLORS.tool }}>■ tool</span>
        <span style={{ color: COLORS.speaking }}>■ speaking</span>
        <span style={{ color: '#a32d2d' }}>✕ cancelled</span>
      </div>
      {tl.turns.map((turn, i) => (
        <div key={turn.turnId} className="timeline-row">
          <div className="timeline-label" title={turn.utterance ?? ''}>
            Turn {i + 1}
          </div>
          <div className="timeline-track">
            {turn.segments.map((s, j) => (
              <div
                key={j}
                className={`timeline-seg ${s.kind === 'tool' ? 'is-tool' : ''}`}
                style={{
                  left: pct(s.start),
                  width: pct(Math.max(s.end - s.start, 0)),
                  background: COLORS[s.kind],
                }}
                title={`${s.kind} ${(s.end - s.start).toFixed(0)}ms`}
              />
            ))}
            {turn.cancelled && (
              <div className="timeline-cancel" style={{ left: pct(turn.tEnd) }}>
                ✕
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
