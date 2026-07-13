import { useEffect, useRef, useState } from 'react'
import { foldTo } from '../replay'
import { Transcript } from './Transcript'
import type { RecordedEvent } from '../api'

const SPEEDS = [1, 2, 4]

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
    timer.current = window.setTimeout(() => onCursor(cursor + 1), gap / speed)
    return () => {
      if (timer.current != null) window.clearTimeout(timer.current)
    }
  }, [playing, cursor, speed, events, onCursor])

  const state = foldTo(events, cursor)

  return (
    <div className="replay">
      <div className="replay-controls">
        <button onClick={() => setPlaying((p) => !p)}>{playing ? 'Pause' : 'Play'}</button>
        <button onClick={() => { setPlaying(false); onCursor(0) }}>Reset</button>
        <input
          type="range"
          min={0}
          max={events.length}
          value={cursor}
          onChange={(e) => { setPlaying(false); onCursor(Number(e.target.value)) }}
        />
        <span className="muted">{cursor}/{events.length}</span>
        {SPEEDS.map((s) => (
          <button key={s} className={s === speed ? 'active' : ''} onClick={() => setSpeed(s)}>
            {s}×
          </button>
        ))}
      </div>
      <Transcript
        messages={state.messages}
        isThinking={state.status === 'thinking' && !state.assistantInProgress}
        toolActivity={state.toolActivity}
        sttPartial={state.sttPartial}
      />
    </div>
  )
}
