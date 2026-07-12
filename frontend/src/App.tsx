import { useEffect, useReducer, useState } from 'react'
import { AudioPlayer } from './audio/player'
import { MicButton } from './components/MicButton'
import { StatusBadge } from './components/StatusBadge'
import { Transcript } from './components/Transcript'
import { initialState, reducer } from './state'
import { VoiceAssistantClient, type ConnectionStatus } from './ws'

function App() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting')
  const [inputText, setInputText] = useState('')
  const [client, setClient] = useState<VoiceAssistantClient | null>(null)

  useEffect(() => {
    const player = new AudioPlayer()

    const client = new VoiceAssistantClient({
      onEvent: (event) => {
        if (event.type === 'tts_cancel') player.flush()
        dispatch(event)
      },
      onStatusChange: (status) => {
        setConnectionStatus(status)
        dispatch({ type: status === 'open' ? 'connected' : 'disconnected' })
      },
      onAudio: (audio) => player.enqueue(audio),
    })
    setClient(client)
    client.connect()

    return () => {
      client.disconnect()
      setClient(null)
      void player.close()
    }
  }, [])

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const text = inputText.trim()
    if (!text || connectionStatus !== 'open') return

    dispatch({ type: 'user_submit', text })
    client?.send({ type: 'text_input', text })
    setInputText('')
  }

  const isThinking = state.status === 'thinking' && !state.assistantInProgress
  const canSend = connectionStatus === 'open' && inputText.trim().length > 0

  return (
    <main>
      <header className="app-header">
        <h1>Voice Assistant</h1>
        <StatusBadge connectionStatus={connectionStatus} pipelineState={state.status} />
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
      />

      <form className="input-row" onSubmit={handleSubmit}>
        <MicButton client={client} dispatch={dispatch} connectionStatus={connectionStatus} />
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
