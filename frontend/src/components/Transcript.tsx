import type { Message, ToolActivity } from '../state'

interface TranscriptProps {
  messages: Message[]
  isThinking: boolean
  toolActivity: ToolActivity[]
}

export function Transcript({ messages, isThinking, toolActivity }: TranscriptProps) {
  return (
    <div className="transcript">
      {messages.length === 0 && !isThinking && (
        <p className="transcript-empty">Say hello — type a message below to start.</p>
      )}
      {messages.map((message, index) => (
        <div key={index} className={`bubble bubble-${message.role}`}>
          <span className="bubble-role">{message.role === 'user' ? 'You' : 'Assistant'}</span>
          <p className="bubble-text">{message.text}</p>
        </div>
      ))}
      {isThinking && (
        <div className="bubble bubble-assistant bubble-thinking">
          <span className="bubble-role">Assistant</span>
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
              {tool.name}
              {tool.status === 'running' ? '…' : ' ✓'}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
