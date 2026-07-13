// Owns the live conversation for the whole page, above the router, so that
// navigating between routes (Live <-> Sessions) never tears down the
// WebSocket or the transcript. One backend session lives exactly as long as
// this provider's socket; "New session" is the only thing that starts a fresh
// one. See docs/superpowers/specs/2026-07-13-live-session-persistence-design.md.

import { createContext, useCallback, useContext, useEffect, useReducer, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { AudioPlayer } from './audio/player'
import { initialState, reducer } from './state'
import type { Action, AppState } from './state'
import { VoiceAssistantClient, type ConnectionStatus } from './ws'

interface LiveSessionValue {
  state: AppState
  dispatch: (action: Action) => void
  client: VoiceAssistantClient | null
  connectionStatus: ConnectionStatus
  /** Ends the current session and starts a fresh one (clears the transcript). */
  newSession: () => void
}

const LiveSessionContext = createContext<LiveSessionValue | null>(null)

export function LiveSessionProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting')
  const [client, setClient] = useState<VoiceAssistantClient | null>(null)
  const playerRef = useRef<AudioPlayer | null>(null)
  const clientRef = useRef<VoiceAssistantClient | null>(null)

  // Build, wire, and connect a fresh client bound to `player`. Every callback
  // is guarded on this client still being the current one, so a superseded
  // client's late close/events (which can arrive after we've already swapped
  // in a new session) can't clobber the new connection's state.
  const makeClient = useCallback((player: AudioPlayer): VoiceAssistantClient => {
    const c = new VoiceAssistantClient({
      onEvent: (event) => {
        if (clientRef.current !== c) return
        if (event.type === 'tts_cancel') player.flush()
        dispatch(event)
      },
      onStatusChange: (status) => {
        if (clientRef.current !== c) return
        setConnectionStatus(status)
        dispatch({ type: status === 'open' ? 'connected' : 'disconnected' })
      },
      onAudio: (audio) => {
        if (clientRef.current !== c) return
        player.enqueue(audio)
      },
    })
    clientRef.current = c
    setClient(c)
    c.connect()
    return c
  }, [])

  useEffect(() => {
    const player = new AudioPlayer()
    playerRef.current = player
    makeClient(player)

    return () => {
      clientRef.current?.disconnect()
      clientRef.current = null
      setClient(null)
      playerRef.current = null
      void player.close()
    }
  }, [makeClient])

  const newSession = useCallback(() => {
    const player = playerRef.current
    if (player === null) return
    player.flush()
    dispatch({ type: 'reset' })
    // Drop the old client first (so its pending close is ignored by the
    // guards above), then stand up a new one — a new backend session.
    const previous = clientRef.current
    clientRef.current = null
    previous?.disconnect()
    makeClient(player)
  }, [makeClient])

  return (
    <LiveSessionContext.Provider
      value={{ state, dispatch, client, connectionStatus, newSession }}
    >
      {children}
    </LiveSessionContext.Provider>
  )
}

export function useLiveSession(): LiveSessionValue {
  const ctx = useContext(LiveSessionContext)
  if (ctx === null) {
    throw new Error('useLiveSession must be used within a LiveSessionProvider')
  }
  return ctx
}
