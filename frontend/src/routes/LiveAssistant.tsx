import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { MicButton } from '../components/MicButton'
import { StatusBadge } from '../components/StatusBadge'
import { ToolsPanel } from '../components/ToolsPanel'
import { Transcript } from '../components/Transcript'
import { useLiveSession } from '../LiveSessionContext'

function SendIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 19V5" />
      <path d="M5 12l7-7 7 7" />
    </svg>
  )
}

export function LiveAssistant() {
  const { state, dispatch, client, connectionStatus, newSession } = useLiveSession()
  const [inputText, setInputText] = useState('')
  const [toolsOpen, setToolsOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // The socket outlives this route now, so leaving Live must stop microphone
  // capture on the backend (without disconnecting) — otherwise it would keep
  // listening in the background while the user is on another page. `stop` is a
  // no-op server-side when no capture is active, so this is always safe.
  useEffect(() => {
    return () => {
      client?.send({ type: 'stop' })
    }
  }, [client])

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const text = inputText.trim()
    if (!text || connectionStatus !== 'open') return

    dispatch({ type: 'user_submit', text })
    client?.send({ type: 'text_input', text })
    setInputText('')
  }

  function handleSelectPrompt(text: string) {
    setInputText(text)
    setToolsOpen(false)
    inputRef.current?.focus()
  }

  const isThinking = state.status === 'thinking' && !state.assistantInProgress
  const canSend = connectionStatus === 'open' && inputText.trim().length > 0
  const heroStatus = connectionStatus === 'open' ? state.status : connectionStatus

  return (
    <main className="live">
      <div className="live-layout">
        <div className="live-shell">
          <header className="app-header">
            <h1 className="brand">Smart Voice Assistant</h1>
            <StatusBadge connectionStatus={connectionStatus} pipelineState={state.status} />
            <div className="header-actions">
              <button
                type="button"
                className="btn-ghost tools-toggle"
                onClick={() => setToolsOpen((open) => !open)}
                aria-expanded={toolsOpen}
                aria-controls="tools-panel"
              >
                Tools
              </button>
              <button type="button" className="btn-ghost" onClick={newSession}>
                New session
              </button>
              {state.sessionId && (
                <Link className="btn-ghost" to={`/sessions/${state.sessionId}`}>
                  View replay
                </Link>
              )}
              <Link className="btn-ghost" to="/sessions">
                Sessions
              </Link>
            </div>
          </header>

          {state.error && <p className="error-banner">{state.error}</p>}

          {state.notifications.length > 0 && (
            <ul className="notifications">
              {state.notifications.map((note, index) => (
                <li key={index} className="notification">
                  ⏱ {note}
                </li>
              ))}
            </ul>
          )}

          <Transcript
            messages={state.messages}
            isThinking={isThinking}
            toolActivity={state.toolActivity}
            sttPartial={state.sttPartial}
            hero
            status={heroStatus}
          />

          <form className="composer" onSubmit={handleSubmit}>
            <MicButton client={client} dispatch={dispatch} connectionStatus={connectionStatus} />
            <input
              ref={inputRef}
              type="text"
              value={inputText}
              onChange={(event) => setInputText(event.target.value)}
              placeholder="Type a message…"
              disabled={connectionStatus !== 'open'}
              autoFocus
            />
            <button type="submit" className="btn-send" disabled={!canSend} aria-label="Send message">
              <SendIcon />
            </button>
          </form>
        </div>

        <ToolsPanel
          open={toolsOpen}
          onSelectPrompt={handleSelectPrompt}
          onClose={() => setToolsOpen(false)}
        />
      </div>
    </main>
  )
}
