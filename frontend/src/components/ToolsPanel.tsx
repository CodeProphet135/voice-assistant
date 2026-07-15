import { TOOL_CATALOG } from '../toolCatalog'

interface ToolsPanelProps {
  open: boolean
  onSelectPrompt: (text: string) => void
  onClose: () => void
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
  return (
    <aside
      className={`tools-panel${open ? ' tools-panel-open' : ''}`}
      aria-label="Assistant capabilities"
    >
      <div className="tools-panel-header">
        <h2>What I can do</h2>
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
