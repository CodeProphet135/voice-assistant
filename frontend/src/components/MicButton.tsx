import { useEffect, useRef, useState } from 'react'
import { MicCapture } from '../audio/mic'
import type { Action } from '../state'
import type { ConnectionStatus, VoiceAssistantClient } from '../ws'

interface MicButtonProps {
  client: VoiceAssistantClient | null
  dispatch: (action: Action) => void
  connectionStatus: ConnectionStatus
}

/** Toggle button that starts/stops microphone capture and streams PCM frames over the WS. */
export function MicButton({ client, dispatch, connectionStatus }: MicButtonProps) {
  const [recording, setRecording] = useState(false)
  const micRef = useRef<MicCapture | null>(null)

  // Stop the mic on unmount so we never leak an open MediaStream/AudioContext.
  useEffect(() => {
    return () => {
      micRef.current?.stop()
      micRef.current = null
    }
  }, [])

  async function handleClick() {
    if (!client || connectionStatus !== 'open') return

    if (recording) {
      const mic = micRef.current
      micRef.current = null
      setRecording(false)
      await mic?.stop()
      client.send({ type: 'stop' })
      return
    }

    const mic = new MicCapture()
    try {
      client.send({ type: 'start', sample_rate: 16000 })
      await mic.start((buf) => client.sendAudio(buf))
      micRef.current = mic
      setRecording(true)
    } catch (error) {
      micRef.current = null
      setRecording(false)
      const message = error instanceof Error ? error.message : 'Microphone access failed.'
      dispatch({ type: 'error', message })
    }
  }

  const disabled = connectionStatus !== 'open'

  return (
    <button
      type="button"
      className={`mic-button${recording ? ' mic-button-recording' : ''}`}
      onClick={handleClick}
      disabled={disabled}
      aria-pressed={recording}
      aria-label={recording ? 'Stop recording' : 'Start recording'}
      title={recording ? 'Stop recording' : 'Start recording'}
    >
      {recording ? <span className="mic-recording-dot" /> : '🎤'}
    </button>
  )
}
