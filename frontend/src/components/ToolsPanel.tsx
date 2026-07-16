import { useEffect, useState } from 'react'
import { TOOL_CATALOG } from '../toolCatalog'

interface ToolsPanelProps {
  open: boolean
  onSelectPrompt: (text: string) => void
  onClose: () => void
}

// Must match the tools-panel breakpoint in index.css: below it the panel is a
// slide-in drawer driven by `open`; above it the panel is always visible and
// `open` is irrelevant.
const DRAWER_QUERY = '(max-width: 960px)'

function useIsDrawer(): boolean {
  const [isDrawer, setIsDrawer] = useState(() => window.matchMedia(DRAWER_QUERY).matches)
  useEffect(() => {
    const mq = window.matchMedia(DRAWER_QUERY)
    const onChange = (event: MediaQueryListEvent) => setIsDrawer(event.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return isDrawer
}

function CloseIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

export function ToolsPanel({ open, onSelectPrompt, onClose }: ToolsPanelProps) {
  // inert only applies while the panel is a CLOSED drawer (off-screen on
  // narrow viewports). On wide viewports the panel is statically visible, so
  // inert would silently disable every sample-prompt chip.
  const isDrawer = useIsDrawer()
  return (
    <aside
      id="tools-panel"
      className={`tools-panel${open ? ' tools-panel-open' : ''}`}
      aria-label="Assistant capabilities"
      inert={isDrawer && !open}
    >
      <div className="tools-panel-header">
        <h2>Available Tools and Sample Prompts</h2>
        <button
          type="button"
          className="tools-panel-close"
          onClick={onClose}
          aria-label="Close tools panel"
        >
          <CloseIcon />
        </button>
      </div>
      <div className="tool-card-list">
        {TOOL_CATALOG.map((tool) => (
          <div key={tool.id} className="tool-card">
            <span className="tool-card-icon">
              <tool.Icon />
            </span>
            <div className="tool-card-body">
              <p className="tool-card-title">{tool.name}</p>
              <p className="tool-card-desc">{tool.description}</p>
              <button
                type="button"
                className="tool-prompt-chip"
                onClick={() => onSelectPrompt(tool.samplePrompt)}
              >
                "{tool.samplePrompt}"
              </button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}
