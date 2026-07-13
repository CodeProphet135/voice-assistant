// Typed WebSocket client mirroring backend/src/voice_assistant/protocol.py.
//
// Control frames are JSON text messages tagged by a `type` field. Binary WS
// frames carry audio: client→server mic PCM as of Phase 2 (sendAudio below),
// server→client TTS audio starting Phase 3. The event *shapes* below were
// final from Phase 1 so the reducer in state.ts never has to change shape
// later.

/** Messages the client sends to the server. */
export type ClientEvent =
  | { type: 'start'; sample_rate: number }
  | { type: 'stop' }
  | { type: 'text_input'; text: string }

/** Pipeline state broadcast by the server. */
export type PipelineState = 'idle' | 'listening' | 'thinking' | 'speaking'

/** Messages the server sends to the client. */
export type ServerEvent =
  | { type: 'ready'; session_id: string }
  | { type: 'state'; state: PipelineState }
  | { type: 'speech_started' }
  | { type: 'stt_partial'; text: string }
  | { type: 'stt_final'; text: string }
  | { type: 'assistant_delta'; text: string }
  | { type: 'assistant_done'; text: string }
  | { type: 'llm_request'; input: unknown[]; model: string; iteration: number }
  | { type: 'tool_call'; call_id: string; name: string; arguments: string }
  | { type: 'tool_result'; call_id: string; name: string; output: string }
  | { type: 'tts_start'; sentence_index: number; text: string }
  | { type: 'tts_end' }
  | { type: 'tts_cancel' }
  | { type: 'timer_fired'; timer_id: string; label: string }
  | { type: 'error'; message: string }

export type ConnectionStatus = 'connecting' | 'open' | 'closed'

/** Derives the `/ws` URL from the current page location so the Vite dev-server proxy handles it. */
function defaultWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}

export interface VoiceAssistantClientOptions {
  url?: string
  onEvent?: (event: ServerEvent) => void
  onStatusChange?: (status: ConnectionStatus) => void
  onAudio?: (audio: ArrayBuffer) => void
}

/** Thin, framework-agnostic wrapper around a WebSocket connection to the assistant backend. */
export class VoiceAssistantClient {
  private socket: WebSocket | null = null
  private status: ConnectionStatus = 'closed'
  private readonly url: string
  private readonly onEvent: (event: ServerEvent) => void
  private readonly onStatusChange: (status: ConnectionStatus) => void
  private readonly onAudio: (audio: ArrayBuffer) => void

  constructor(options: VoiceAssistantClientOptions = {}) {
    this.url = options.url ?? defaultWsUrl()
    this.onEvent = options.onEvent ?? (() => {})
    this.onStatusChange = options.onStatusChange ?? (() => {})
    this.onAudio = options.onAudio ?? (() => {})
  }

  connect(): void {
    if (this.socket) return
    this.setStatus('connecting')
    const socket = new WebSocket(this.url)
    socket.binaryType = 'arraybuffer'
    this.socket = socket

    socket.addEventListener('open', () => {
      this.setStatus('open')
    })

    socket.addEventListener('close', () => {
      this.socket = null
      this.setStatus('closed')
    })

    socket.addEventListener('error', () => {
      // The browser also fires a 'close' event after 'error', which will
      // update status — nothing further to do here.
    })

    socket.addEventListener('message', (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        this.onAudio(event.data)
        return
      }
      let parsed: unknown
      try {
        parsed = JSON.parse(event.data)
      } catch {
        return
      }
      this.onEvent(parsed as ServerEvent)
    })
  }

  disconnect(): void {
    this.socket?.close()
    this.socket = null
  }

  send(event: ClientEvent): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return
    this.socket.send(JSON.stringify(event))
  }

  /** Sends a raw little-endian Int16 mono 16kHz PCM chunk as a binary WS frame (no header/framing). */
  sendAudio(data: ArrayBuffer): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return
    this.socket.send(data)
  }

  getStatus(): ConnectionStatus {
    return this.status
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status
    this.onStatusChange(status)
  }
}
