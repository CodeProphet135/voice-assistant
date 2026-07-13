import { initialState, reducer, type AppState } from './state'
import type { RecordedEvent } from './api'

/** Fold the first `count` recorded payloads through the live reducer. Pure —
 * the same reducer that drives the live UI, which is what makes replay
 * deterministic. Unknown event types (speech_started, llm_request, tts_*) fall
 * through the reducer's default case unchanged. */
export function foldTo(events: RecordedEvent[], count: number): AppState {
  let state = initialState
  for (let i = 0; i < count && i < events.length; i++) {
    state = reducer(state, events[i].payload as never)
  }
  return state
}
