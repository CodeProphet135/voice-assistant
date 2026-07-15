import type React from 'react'

export interface ToolCatalogEntry {
  id: string
  name: string
  description: string
  samplePrompt: string
  Icon: (props: { className?: string }) => React.ReactElement
}

function iconProps(className?: string) {
  return {
    viewBox: '0 0 24 24',
    width: 20,
    height: 20,
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
    'aria-hidden': true,
  }
}

function WeatherIcon({ className }: { className?: string }) {
  return (
    <svg {...iconProps(className)}>
      <path d="M17.5 19a4.5 4.5 0 0 0 0-9 6 6 0 0 0-11.3-2A4.5 4.5 0 0 0 6.5 19h11Z" />
    </svg>
  )
}

function NoteIcon({ className }: { className?: string }) {
  return (
    <svg {...iconProps(className)}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  )
}

function ListIcon({ className }: { className?: string }) {
  return (
    <svg {...iconProps(className)}>
      <path d="M9 6h11M9 12h11M9 18h11" />
      <path d="M4 6h.01M4 12h.01M4 18h.01" />
    </svg>
  )
}

function TimerIcon({ className }: { className?: string }) {
  return (
    <svg {...iconProps(className)}>
      <circle cx="12" cy="13" r="8" />
      <path d="M12 9v4l3 2" />
      <path d="M9 2h6" />
    </svg>
  )
}

export const TOOL_CATALOG: ToolCatalogEntry[] = [
  {
    id: 'weather',
    name: 'Weather',
    description: 'Check the current weather anywhere.',
    samplePrompt: "What's the weather in Tokyo?",
    Icon: WeatherIcon,
  },
  {
    id: 'save_note',
    name: 'Save a note',
    description: 'Save a short note to recall later.',
    samplePrompt: 'Remind me to call the dentist tomorrow',
    Icon: NoteIcon,
  },
  {
    id: 'list_notes',
    name: 'Recall notes',
    description: "List the notes you've saved.",
    samplePrompt: 'What notes do I have?',
    Icon: ListIcon,
  },
  {
    id: 'set_timer',
    name: 'Set a timer',
    description: 'Start a countdown timer.',
    samplePrompt: 'Set a timer for 10 minutes',
    Icon: TimerIcon,
  },
]
