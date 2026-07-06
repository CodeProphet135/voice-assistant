// PURE reducer over the ServerEvent stream (plus a few local UI actions).
//
// This is reused verbatim by the Phase 6 Replay view, so it must stay a pure
// function (state, action) => state with NO side effects — no WebSocket, no
// timers, no DOM access in here. Keep it that way.

import type { PipelineState, ServerEvent } from './ws'

export interface Message {
  role: 'user' | 'assistant'
  text: string
}

export type ToolActivityStatus = 'running' | 'done'

export interface ToolActivity {
  call_id: string
  name: string
  arguments: string
  output: string | null
  status: ToolActivityStatus
}

export interface AppState {
  /** Pipeline state; "connecting" covers the time before a "ready" frame arrives. */
  status: PipelineState | 'connecting'
  sessionId: string | null
  messages: Message[]
  /** True while the last assistant message is still streaming in (between assistant_delta and assistant_done). */
  assistantInProgress: boolean
  toolActivity: ToolActivity[]
  sttPartial: string
  error: string | null
}

export const initialState: AppState = {
  status: 'connecting',
  sessionId: null,
  messages: [],
  assistantInProgress: false,
  toolActivity: [],
  sttPartial: '',
  error: null,
}

/** Local UI actions dispatched alongside the server event stream. */
export type LocalAction =
  | { type: 'user_submit'; text: string }
  | { type: 'connected' }
  | { type: 'disconnected' }

export type Action = ServerEvent | LocalAction

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'user_submit': {
      return {
        ...state,
        messages: [...state.messages, { role: 'user', text: action.text }],
        status: 'thinking',
      }
    }

    case 'connected': {
      return { ...state, status: 'connecting' }
    }

    case 'disconnected': {
      return { ...state, status: 'connecting', assistantInProgress: false }
    }

    case 'ready': {
      return { ...state, sessionId: action.session_id, status: 'idle' }
    }

    case 'state': {
      return { ...state, status: action.state }
    }

    case 'stt_partial': {
      return { ...state, sttPartial: action.text }
    }

    case 'stt_final': {
      // Commit the utterance as a user message, mirroring user_submit, and
      // clear the live partial now that it's been superseded by the final.
      return {
        ...state,
        messages: [...state.messages, { role: 'user', text: action.text }],
        sttPartial: '',
      }
    }

    case 'assistant_delta': {
      if (!state.assistantInProgress) {
        return {
          ...state,
          messages: [...state.messages, { role: 'assistant', text: action.text }],
          assistantInProgress: true,
        }
      }
      const messages = state.messages.slice()
      const last = messages[messages.length - 1]
      messages[messages.length - 1] = { role: 'assistant', text: last.text + action.text }
      return { ...state, messages }
    }

    case 'assistant_done': {
      if (!state.assistantInProgress) {
        return {
          ...state,
          messages: [...state.messages, { role: 'assistant', text: action.text }],
          assistantInProgress: false,
        }
      }
      const messages = state.messages.slice()
      messages[messages.length - 1] = { role: 'assistant', text: action.text }
      return { ...state, messages, assistantInProgress: false }
    }

    case 'tool_call': {
      const entry: ToolActivity = {
        call_id: action.call_id,
        name: action.name,
        arguments: action.arguments,
        output: null,
        status: 'running',
      }
      return { ...state, toolActivity: [...state.toolActivity, entry] }
    }

    case 'tool_result': {
      const toolActivity = state.toolActivity.map((entry) =>
        entry.call_id === action.call_id
          ? { ...entry, status: 'done' as const, output: action.output }
          : entry,
      )
      return { ...state, toolActivity }
    }

    case 'error': {
      return { ...state, error: action.message }
    }

    // tts_start / tts_end / tts_cancel / timer_fired: no Phase 1 UI yet.
    default: {
      return state
    }
  }
}
