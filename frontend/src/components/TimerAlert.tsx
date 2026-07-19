import { useEffect, useRef } from 'react'
import { AlarmSound } from '../audio/alarm'
import type { TimerNotification } from '../state'

interface TimerAlertProps {
  notifications: TimerNotification[]
  onDismiss: (id: string) => void
}

/** How long the alarm sound loops before auto-silencing (the modal stays until dismissed). */
const SOUND_MAX_MS = 10_000

/**
 * Modal alert for fired timers. Fired timers queue up in `notifications`; the
 * oldest is shown as a blocking, must-dismiss popup while an alarm chime loops.
 * The sound auto-stops after 10s but the popup stays until the user dismisses
 * it; dismissing cuts the sound immediately. Sound is a pure side effect here —
 * it never touches the reducer, so Replay (which shares that reducer) is silent.
 */
export function TimerAlert({ notifications, onDismiss }: TimerAlertProps) {
  const active = notifications[0] ?? null
  const activeId = active?.id ?? null
  const alarmRef = useRef<AlarmSound | null>(null)

  useEffect(() => {
    if (!activeId) return

    const alarm = new AlarmSound()
    alarmRef.current = alarm
    alarm.start()
    // Loop for at most 10s, then fall silent while the popup persists.
    const silenceTimer = setTimeout(() => alarm.stop(), SOUND_MAX_MS)

    return () => {
      clearTimeout(silenceTimer)
      alarm.stop()
      alarmRef.current = null
    }
  }, [activeId])

  // Escape dismisses, matching the button — a standard modal affordance.
  useEffect(() => {
    if (!activeId) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onDismiss(activeId!)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeId, onDismiss])

  if (!active) return null

  const queued = notifications.length - 1

  return (
    <div className="timer-alert-overlay" role="presentation">
      <div
        className="timer-alert"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="timer-alert-title"
      >
        <div className="timer-alert-bell" aria-hidden="true">
          ⏰
        </div>
        <p className="timer-alert-eyebrow">Time's up</p>
        <h2 id="timer-alert-title" className="timer-alert-title">
          {active.text}
        </h2>
        {queued > 0 && (
          <p className="timer-alert-queued">
            +{queued} more timer{queued === 1 ? '' : 's'} waiting
          </p>
        )}
        <button
          type="button"
          className="timer-alert-dismiss"
          onClick={() => onDismiss(active.id)}
          autoFocus
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
