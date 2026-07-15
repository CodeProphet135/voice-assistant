import { computeTurns, type SegmentKind } from '../timeline'
import type { RecordedEvent } from '../api'

// One hue per pipeline state — the same tokens the status pulse and event
// inspector use, so color means the same thing everywhere.
const COLORS: Record<SegmentKind, string> = {
  listening: 'var(--c-listening)',
  thinking: 'var(--c-thinking)',
  tool: 'var(--c-tool)',
  speaking: 'var(--c-speaking)',
}

const KINDS: SegmentKind[] = ['listening', 'thinking', 'tool', 'speaking']

export function Timeline({ events }: { events: RecordedEvent[] }) {
  const tl = computeTurns(events)
  if (tl.turns.length === 0) return <p className="transcript-empty">No turns to plot.</p>
  const span = Math.max(tl.end, 1)
  const pct = (v: number) => `${(v / span) * 100}%`

  return (
    <div className="timeline">
      <div className="timeline-legend">
        {KINDS.map((kind) => (
          <span key={kind} className="legend-chip">
            <i style={{ background: COLORS[kind] }} />
            {kind}
          </span>
        ))}
        <span className="legend-chip legend-cancelled">
          <span aria-hidden="true">✕</span> cancelled
        </span>
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
              <div className="timeline-cancel" style={{ left: pct(turn.tEnd) }} title="Turn cancelled">
                ✕
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
