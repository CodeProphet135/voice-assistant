import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEvents, type RecordedEvent } from '../api'
import { Timeline } from '../components/Timeline'
import { Replay } from '../components/Replay'
import { EventInspector } from '../components/EventInspector'

export function SessionDetail() {
  const { id } = useParams<{ id: string }>()
  const [events, setEvents] = useState<RecordedEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [cursor, setCursor] = useState(0)

  useEffect(() => {
    if (!id) return
    fetchEvents(id).then(setEvents).catch((e) => setError(String(e)))
  }, [id])

  return (
    <main>
      <header className="app-header">
        <h1>Session</h1>
        <Link className="nav-link" to="/sessions">All sessions</Link>
      </header>
      {error && <p className="error-banner">{error}</p>}
      {events.length > 0 && (
        <>
          <Timeline events={events} />
          <div className="session-panels">
            <Replay events={events} cursor={cursor} onCursor={setCursor} />
            <EventInspector
              events={events}
              selected={Math.min(cursor, events.length - 1)}
              onSelect={setCursor}
            />
          </div>
        </>
      )}
    </main>
  )
}
