import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEvents, type RecordedEvent } from '../api'
import { Timeline } from '../components/Timeline'

export function SessionDetail() {
  const { id } = useParams<{ id: string }>()
  const [events, setEvents] = useState<RecordedEvent[]>([])
  const [error, setError] = useState<string | null>(null)

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
      <p className="muted">{events.length} events</p>
      {events.length > 0 && <Timeline events={events} />}
      {/* Replay + Inspector (Task 6) mount here. */}
    </main>
  )
}
