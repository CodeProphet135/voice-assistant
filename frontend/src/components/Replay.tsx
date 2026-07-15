import { useEffect, useRef, useState } from 'react'
import { foldTo } from '../replay'
import { Transcript } from './Transcript'
import type { RecordedEvent } from '../api'

const SPEEDS = [1, 2, 4]

// Cap a single inter-event wait so playback skips dead air (e.g. a session
// that sat idle for minutes before the first turn) while keeping the real
// rhythm of sub-second event spacing.
const MAX_GAP_MS = 3000

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M7 4.8v14.4a1 1 0 0 0 1.53.85l11.1-7.2a1 1 0 0 0 0-1.7L8.53 3.95A1 1 0 0 0 7 4.8Z" />
    </svg>
  )
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
      <rect x="6" y="4" width="4" height="16" rx="1.5" />
      <rect x="14" y="4" width="4" height="16" rx="1.5" />
    </svg>
  )
}

function ResetIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 12a9 9 0 1 0 3-6.7" />
      <path d="M3 4v5h5" />
    </svg>
  )
}

export function Replay({
  events,
  cursor,
  onCursor,
}: {
  events: RecordedEvent[]
  cursor: number
  onCursor: (n: number) => void
}) {
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    if (!playing) return
    if (cursor >= events.length) {
      setPlaying(false)
      return
    }
    const cur = events[cursor]
    const next = events[cursor + 1]
    const gap = next ? Math.max(Date.parse(next.ts) - Date.parse(cur.ts), 0) : 0
    timer.current = window.setTimeout(() => onCursor(cursor + 1), Math.min(gap, MAX_GAP_MS) / speed)
    return () => {
      if (timer.current != null) window.clearTimeout(timer.current)
    }
  }, [playing, cursor, speed, events, onCursor])

  const state = foldTo(events, cursor)

  return (
    <div className="replay">
      <div className="replay-controls">
        <button
          type="button"
          className="icon-btn icon-btn-primary"
          onClick={() => setPlaying((p) => !p)}
          aria-label={playing ? 'Pause replay' : 'Play replay'}
        >
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <button
          type="button"
          className="icon-btn"
          onClick={() => {
            setPlaying(false)
            onCursor(0)
          }}
          aria-label="Reset replay"
        >
          <ResetIcon />
        </button>
        <input
          type="range"
          className="scrubber"
          min={0}
          max={events.length}
          value={cursor}
          aria-label="Replay position"
          onChange={(e) => {
            setPlaying(false)
            onCursor(Number(e.target.value))
          }}
        />
        <span className="replay-count">
          {cursor}/{events.length}
        </span>
        <div className="speed-group" role="group" aria-label="Replay speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              className={s === speed ? 'active' : ''}
              aria-pressed={s === speed}
              onClick={() => setSpeed(s)}
            >
              {s}×
            </button>
          ))}
        </div>
      </div>
      <Transcript
        messages={state.messages}
        isThinking={state.status === 'thinking' && !state.assistantInProgress}
        toolActivity={state.toolActivity}
        sttPartial={state.sttPartial}
        emptyText="Press play or scrub to step through the session."
      />
    </div>
  )
}
