import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchSessions, type SessionSummary } from '../api'

export function SessionsList() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchSessions().then(setSessions).catch((e) => setError(String(e)))
  }, [])

  return (
    <main>
      <header className="app-header">
        <h1>Sessions</h1>
        <Link className="nav-link" to="/">Live</Link>
      </header>
      {error && <p className="error-banner">{error}</p>}
      <ul className="session-list">
        {sessions.map((s) => (
          <li key={s.id}>
            <Link to={`/sessions/${s.id}`}>
              {s.title ?? '(untitled session)'}
              <span className="session-date"> · {new Date(s.started_at).toLocaleString()}</span>
            </Link>
          </li>
        ))}
        {sessions.length === 0 && !error && <li className="muted">No sessions recorded yet.</li>}
      </ul>
    </main>
  )
}
