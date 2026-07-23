// PURE reducer over the ServerEvent stream (plus a few local UI actions).
//
// This is reused verbatim by the Replay view, so it must stay a pure
// function (state, action) => state with NO side effects — no WebSocket, no
// timers, no DOM access in here. Keep it that way.

import type { PipelineState, ServerEvent } from './ws'

export interface Message {
  role: 'user' | 'assistant'
  text: string
}

export type ToolActivityStatus = 'running' | 'done'

export interface TimerNotification {
  /** Stable id (the fired timer's id) so the alert can be dismissed by identity. */
  id: string
  /** Display text, e.g. "tea timer done" or "Timer done". */
  text: string
}

export interface ToolActivity {
  call_id: string
  name: string
  arguments: string
  output: string | null
  status: ToolActivityStatus
  /**
   * Index of the newest message when the tool_call arrived (-1 if none), i.e.
   * the message this activity chronologically follows. The wire events carry
   * no turn id, so this is how the transcript renders tool chips inside their
   * turn instead of pooling them all at the bottom.
   */
  anchorIndex: number
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
  /** Fired-timer notifications, appended as each set_timer elapses. */
  notifications: TimerNotification[]
}

export const initialState: AppState = {
  status: 'connecting',
  sessionId: null,
  messages: [],
  assistantInProgress: false,
  toolActivity: [],
  sttPartial: '',
  error: null,
  notifications: [],
}

/** Local UI actions dispatched alongside the server event stream. */
export type LocalAction =
  | { type: 'user_submit'; text: string }
  | { type: 'connected' }
  | { type: 'disconnected' }
  | { type: 'dismiss_notification'; id: string }
  | { type: 'reset' }

export type Action = ServerEvent | LocalAction

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'reset': {
      // Starting a fresh session (the "New session" button): drop all
      // transcript/tool/notification state and return to the clean slate.
      return initialState
    }

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
        anchorIndex: state.messages.length - 1,
      }
      return { ...state, toolActivity: [...state.toolActivity, entry] }
    }

    case 'tool_result': {
      // Backfill the final arguments: the tool_call frame is emitted before
      // the model finishes streaming its argument tokens, so it can carry
      // empty args; tool_result carries the complete value. Fall back to the
      // entry's existing args for older recordings that predate the field.
      const toolActivity = state.toolActivity.map((entry) =>
        entry.call_id === action.call_id
          ? {
              ...entry,
              status: 'done' as const,
              arguments: action.arguments ?? entry.arguments,
              output: action.output,
            }
          : entry,
      )
      return { ...state, toolActivity }
    }

    case 'error': {
      return { ...state, error: action.message }
    }

    case 'tts_cancel': {
      // Barge-in: the backend cancels the in-flight turn, so no
      // assistant_done will ever arrive for it. Close out the partial
      // assistant bubble (leaving its text in place as the interrupted
      // reply) so the next turn's first assistant_delta starts a fresh one
      // instead of appending to this one. Audio is flushed by the caller
      // (App.tsx) as a side effect — this is a pure state change only.
      return { ...state, assistantInProgress: false }
    }

    case 'timer_fired': {
      // Only append "timer" when the label doesn't already say it, so a model
      // that labels a timer "timer" doesn't produce "timer timer done".
      const trimmed = action.label?.trim()
      const label = trimmed
        ? /timer$/i.test(trimmed)
          ? trimmed
          : `${trimmed} timer`
        : 'Timer'
      const note: TimerNotification = { id: action.timer_id, text: `${label} done` }
      return { ...state, notifications: [...state.notifications, note] }
    }

    case 'dismiss_notification': {
      return {
        ...state,
        notifications: state.notifications.filter((note) => note.id !== action.id),
      }
    }

    // tts_start / tts_end: no UI yet.
    default: {
      return state
    }
  }
}
