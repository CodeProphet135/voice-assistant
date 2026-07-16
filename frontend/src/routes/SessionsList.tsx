import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchSessions, type SessionSummary } from '../api'

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

function formatDuration(startedAt: string, endedAt: string | null): string | null {
  if (!endedAt) return null
  const seconds = Math.round((Date.parse(endedAt) - Date.parse(startedAt)) / 1000)
  if (!Number.isFinite(seconds) || seconds < 0) return null
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

function WaveGlyph() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="currentColor" aria-hidden="true">
      <rect x="1" y="7.5" width="2.5" height="5" rx="1.25" />
      <rect x="5.5" y="5" width="2.5" height="10" rx="1.25" />
      <rect x="10" y="2.5" width="2.5" height="15" rx="1.25" />
      <rect x="14.5" y="6" width="2.5" height="8" rx="1.25" />
    </svg>
  )
}

export function SessionsList() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchSessions().then(setSessions).catch((e) => setError(String(e)))
  }, [])

  return (
    <main className="page">
      <div className="shell">
        <header className="app-header">
          <h1 className="brand">Sessions</h1>
          <div className="header-actions">
            <Link className="btn-ghost" to="/">
              Live
            </Link>
          </div>
        </header>
        {error && <p className="error-banner">{error}</p>}
        <ul className="session-list">
          {sessions.map((s) => {
            const duration = formatDuration(s.started_at, s.ended_at)
            return (
              <li key={s.id}>
                <Link className="session-card" to={`/sessions/${s.id}`}>
                  <span className="session-glyph">
                    <WaveGlyph />
                  </span>
                  <span className="session-info">
                    <span className={`session-title${s.title ? '' : ' session-untitled'}`}>
                      {s.title ?? 'Untitled session'}
                    </span>
                    <span className="session-meta">
                      {dateFormat.format(new Date(s.started_at))}
                      {duration && <span className="session-duration">{duration}</span>}
                    </span>
                  </span>
                  <span className="session-chevron" aria-hidden="true">
                    →
                  </span>
                </Link>
              </li>
            )
          })}
          {sessions.length === 0 && !error && (
            <li className="transcript-empty">No sessions recorded yet — talk to the assistant to create one.</li>
          )}
        </ul>
      </div>
    </main>
  )
}
