import { useEffect, useRef } from 'react'
import type { Message, ToolActivity } from '../state'
import { Pulse } from './Pulse'

interface TranscriptProps {
  messages: Message[]
  isThinking: boolean
  toolActivity: ToolActivity[]
  sttPartial?: string
  /** Live view: show the big pulse hero while the conversation is empty. */
  hero?: boolean
  /** Pipeline state driving the hero pulse (idle when omitted). */
  status?: string
  /** Empty-state copy for non-hero contexts (Replay). */
  emptyText?: string
}

export function Transcript({
  messages,
  isThinking,
  toolActivity,
  sttPartial,
  hero = false,
  status = 'idle',
  emptyText = 'Nothing here yet.',
}: TranscriptProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keep the newest content in view as messages stream in.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, sttPartial, isThinking, toolActivity])

  const empty = messages.length === 0 && !isThinking && !sttPartial

  return (
    <div className="transcript" ref={scrollRef}>
      {empty &&
        (hero ? (
          <div className="transcript-hero">
            <Pulse hero state={status} />
            <p className="hero-title">Say something</p>
            <p className="hero-hint">Tap the mic to talk, or type a message below.</p>
          </div>
        ) : (
          <p className="transcript-empty">{emptyText}</p>
        ))}
      {messages.map((message, index) => (
        <div key={index} className={`bubble bubble-${message.role}`}>
          <span className="sr-only">{message.role === 'user' ? 'You' : 'Assistant'}</span>
          <p className="bubble-text">{message.text}</p>
        </div>
      ))}
      {sttPartial && (
        <div className="bubble bubble-user bubble-partial">
          <span className="sr-only">You</span>
          <p className="bubble-text">{sttPartial}</p>
        </div>
      )}
      {isThinking && (
        <div className="bubble bubble-assistant bubble-thinking">
          <span className="sr-only">Assistant</span>
          <p className="bubble-text thinking-indicator">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </p>
        </div>
      )}
      {toolActivity.length > 0 && (
        <div className="tool-activity">
          {toolActivity.map((tool) => (
            <span key={tool.call_id} className={`tool-chip tool-chip-${tool.status}`}>
              <i className="tool-dot" />
              {tool.name}
              {tool.status === 'running' ? '…' : ' ✓'}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
