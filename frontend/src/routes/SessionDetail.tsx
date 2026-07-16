import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEvents, type RecordedEvent } from '../api'
import { Timeline } from '../components/Timeline'
import { Replay } from '../components/Replay'
import { EventInspector } from '../components/EventInspector'

export function SessionDetail() {
  const { id } = useParams<{ id: string }>()
  const [events, setEvents] = useState<RecordedEvent[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cursor, setCursor] = useState(0)

  useEffect(() => {
    if (!id) return
    setError(null)
    fetchEvents(id)
      .then((evts) => {
        setEvents(evts)
        setLoaded(true)
      })
      .catch((e) => setError(String(e)))
  }, [id])

  return (
    <main className="page">
      <div className="shell shell-wide">
        <header className="app-header">
          <h1 className="brand">Session replay</h1>
          <div className="header-actions">
            <Link className="btn-ghost" to="/">
              Live
            </Link>
            <Link className="btn-ghost" to="/sessions">
              All sessions
            </Link>
          </div>
        </header>
        {error && <p className="error-banner">{error}</p>}
        {loaded && events.length === 0 && (
          <p className="transcript-empty">No events were recorded for this session.</p>
        )}
        {events.length > 0 && (
          <>
            <section className="panel">
              <h2 className="panel-title">Turns</h2>
              <Timeline events={events} />
            </section>
            <div className="session-panels">
              <section className="panel replay-panel">
                <h2 className="panel-title">Replay</h2>
                <Replay events={events} cursor={cursor} onCursor={setCursor} />
              </section>
              <section className="panel inspector-panel">
                <h2 className="panel-title">Events</h2>
                <EventInspector
                  events={events}
                  selected={cursor - 1}
                  onSelect={(i) => setCursor(i + 1)}
                />
              </section>
            </div>
          </>
        )}
      </div>
    </main>
  )
}
