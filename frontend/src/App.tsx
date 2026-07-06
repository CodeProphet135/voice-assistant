import { useEffect, useReducer, useRef, useState } from 'react'
import { Transcript } from './components/Transcript'
import { initialState, reducer } from './state'
import { VoiceAssistantClient, type ConnectionStatus } from './ws'

function App() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting')
  const [inputText, setInputText] = useState('')
  const clientRef = useRef<VoiceAssistantClient | null>(null)

  useEffect(() => {
    const client = new VoiceAssistantClient({
      onEvent: (event) => dispatch(event),
      onStatusChange: (status) => {
        setConnectionStatus(status)
        dispatch({ type: status === 'open' ? 'connected' : 'disconnected' })
      },
    })
    clientRef.current = client
    client.connect()

    return () => {
      client.disconnect()
      clientRef.current = null
    }
  }, [])

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const text = inputText.trim()
    if (!text || connectionStatus !== 'open') return

    dispatch({ type: 'user_submit', text })
    clientRef.current?.send({ type: 'text_input', text })
    setInputText('')
  }

  const isThinking = state.status === 'thinking' && !state.assistantInProgress
  const canSend = connectionStatus === 'open' && inputText.trim().length > 0

  return (
    <main>
      <header className="app-header">
        <h1>Voice Assistant</h1>
        <span className={`status-badge status-${connectionStatus}`}>
          {connectionStatus === 'open' ? state.status : connectionStatus}
        </span>
      </header>

      {state.error && <p className="error-banner">{state.error}</p>}

      <Transcript messages={state.messages} isThinking={isThinking} toolActivity={state.toolActivity} />

      <form className="input-row" onSubmit={handleSubmit}>
        <input
          type="text"
          value={inputText}
          onChange={(event) => setInputText(event.target.value)}
          placeholder="Type a message…"
          disabled={connectionStatus !== 'open'}
          autoFocus
        />
        <button type="submit" disabled={!canSend}>
          Send
        </button>
      </form>
    </main>
  )
}

export default App
