# Tools Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent tools panel to the Live Assistant page that shows the 4 available assistant capabilities (weather, save note, list notes, set timer) with a clickable sample prompt per tool.

**Architecture:** A static data file (`toolCatalog.ts`) feeds a new presentational component (`ToolsPanel.tsx`). `LiveAssistant.tsx` renders it beside the existing chat column inside a new `.live-layout` flex wrapper — a sticky sidebar at ≥960px, a slide-in drawer (toggled by a header button) below that. Selecting a sample prompt fills the composer input and focuses it; it does not auto-send.

**Tech Stack:** React 19 + TypeScript (frontend/), plain CSS (no CSS-in-JS, matches existing `index.css`), Vite, `tsc -b` for typechecking, `oxlint` for linting.

## Global Constraints

- No backend changes — the tool list is hardcoded in the frontend (spec:
  Non-goals), matching the current 4 tools in
  `backend/src/voice_assistant/agent/tools/{weather,notes,timers}.py`.
- No new component-test dependency. The frontend has no
  `@testing-library/react` and no existing `.test.tsx` files — only logic
  modules are unit-tested (`state.test.ts`, `replay.test.ts`, etc.). Per the
  spec's Testing section, this feature is verified by `tsc -b` (typecheck),
  `oxlint` (lint), and manual browser verification — not new automated
  tests. Don't invent a testing-library dependency to force TDD here.
- Reuse existing design tokens only (`--accent`, `--border`, `--surface`,
  `--muted`, `color-mix(in srgb, var(--accent) N%, transparent)` patterns) —
  no new CSS custom properties.
- Sample prompt selection fills the composer input and focuses it; it must
  never call `client?.send(...)` or submit the form.
- Existing `Transcript.tsx` hero/empty-state is untouched — the tools panel
  is additive alongside it, not a replacement.

---

### Task 1: Tool catalog data

**Files:**
- Create: `frontend/src/toolCatalog.ts`

**Interfaces:**
- Produces: `export interface ToolCatalogEntry { id: string; name: string; description: string; samplePrompt: string; Icon: (props: { className?: string }) => JSX.Element }` and `export const TOOL_CATALOG: ToolCatalogEntry[]` (4 entries, ids `weather`, `save_note`, `list_notes`, `set_timer`) — consumed by Task 2's `ToolsPanel.tsx`.

- [ ] **Step 1: Write `frontend/src/toolCatalog.ts`**

```ts
export interface ToolCatalogEntry {
  id: string
  name: string
  description: string
  samplePrompt: string
  Icon: (props: { className?: string }) => JSX.Element
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
```

Note: this file contains JSX, so it must have a `.tsx`-compatible build target. Vite's TypeScript config compiles `.ts` files with JSX inside them only if `jsx` support is enabled project-wide (it is, via `@vitejs/plugin-react`) — but to avoid any ambiguity, **name the file `frontend/src/toolCatalog.tsx`** (not `.ts`), since it returns JSX elements.

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: exits 0, no errors.

- [ ] **Step 3: Run lint**

Run: `cd frontend && npm run lint`
Expected: exits 0, no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/toolCatalog.tsx
git commit -m "feat: add static tool catalog for the tools panel"
```

---

### Task 2: `ToolsPanel` component

**Files:**
- Create: `frontend/src/components/ToolsPanel.tsx`

**Interfaces:**
- Consumes: `TOOL_CATALOG`, `ToolCatalogEntry` from `../toolCatalog` (Task 1).
- Produces: `export function ToolsPanel(props: { open: boolean; onSelectPrompt: (text: string) => void; onClose: () => void }): JSX.Element` — consumed by Task 4's `LiveAssistant.tsx`. Renders root element `<aside className="tools-panel">` (plus `tools-panel-open` when `open`), a `.tools-panel-header` with an `.tools-panel-close` button, and a `.tool-card-list` of `.tool-card` elements each containing `.tool-card-icon`, `.tool-card-title`, `.tool-card-desc`, and a `.tool-prompt-chip` button.

- [ ] **Step 1: Write `frontend/src/components/ToolsPanel.tsx`**

```tsx
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
                “{tool.samplePrompt}”
              </button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: exits 0, no errors.

- [ ] **Step 3: Run lint**

Run: `cd frontend && npm run lint`
Expected: exits 0, no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ToolsPanel.tsx
git commit -m "feat: add ToolsPanel component"
```

---

### Task 3: Layout + styling for the tools panel

**Files:**
- Modify: `frontend/src/index.css:100-102` (add `.live-layout`)
- Modify: `frontend/src/index.css:109-115` (`.live-shell` gets `max-width: 720px`)
- Modify: `frontend/src/index.css:155-171` (add `.tools-toggle` next to `.btn-ghost`)
- Modify: `frontend/src/index.css` — insert a new "Tools panel" section before the existing `/* ---------- Responsive ---------- */` comment (currently at line 1090)

**Interfaces:**
- Consumes: class names produced by Task 2 (`tools-panel`, `tools-panel-open`, `tools-panel-header`, `tools-panel-close`, `tool-card-list`, `tool-card`, `tool-card-icon`, `tool-card-body`, `tool-card-title`, `tool-card-desc`, `tool-prompt-chip`) and a new `live-layout` / `tools-toggle` class consumed by Task 4's `LiveAssistant.tsx`.

- [ ] **Step 1: Add `.live-layout` next to `.shell-wide`**

In `frontend/src/index.css`, find:

```css
.shell-wide {
  width: min(1100px, 100%);
}
```

Replace with:

```css
.shell-wide {
  width: min(1100px, 100%);
}

/* Live page: chat column + tools sidebar side by side. */
.live-layout {
  width: min(1100px, 100%);
  margin: 0 auto;
  padding: 0 1.25rem 1.5rem;
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 1.75rem;
}
```

- [ ] **Step 2: Cap `.live-shell` width so the chat column doesn't grow past its current size**

Find:

```css
.live-shell {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding-bottom: 0;
}
```

Replace with:

```css
.live-shell {
  flex: 1;
  min-height: 0;
  max-width: 720px;
  display: flex;
  flex-direction: column;
  padding-bottom: 0;
}
```

- [ ] **Step 3: Add the `.tools-toggle` header button (hidden on desktop by default)**

Find:

```css
.btn-ghost:hover {
  border-color: color-mix(in srgb, var(--accent) 50%, var(--border));
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}
```

Replace with:

```css
.btn-ghost:hover {
  border-color: color-mix(in srgb, var(--accent) 50%, var(--border));
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}

/* Shown only below the tools-panel breakpoint (see Responsive section). */
.tools-toggle {
  display: none;
}
```

- [ ] **Step 4: Add the tools panel section before the Responsive section**

Find:

```css
/* ---------- Responsive ---------- */

@media (max-width: 760px) {
```

Replace with:

```css
/* ---------- Tools panel ---------- */

.tools-panel {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1.1rem 0 1.5rem;
}

.tools-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.tools-panel-header h2 {
  font-family: var(--font-display);
  font-size: 0.85rem;
  font-weight: 650;
  margin: 0;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.tools-panel-close {
  display: none;
}

.tool-card-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  overflow-y: auto;
}

.tool-card {
  display: flex;
  gap: 0.75rem;
  padding: 0.9rem 1rem;
  border-radius: 1rem;
  border: 1px solid var(--border);
  background: var(--surface);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.tool-card:hover {
  border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
  box-shadow: 0 6px 20px color-mix(in srgb, var(--accent) 10%, transparent);
}

.tool-card-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.4rem;
  height: 2.4rem;
  flex-shrink: 0;
  border-radius: 0.75rem;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--accent);
}

.tool-card-body {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  min-width: 0;
}

.tool-card-title {
  font-weight: 600;
  font-size: 0.9rem;
  margin: 0;
}

.tool-card-desc {
  font-size: 0.8rem;
  color: var(--muted);
  margin: 0;
  line-height: 1.4;
}

.tool-prompt-chip {
  align-self: flex-start;
  margin-top: 0.15rem;
  font: inherit;
  font-size: 0.78rem;
  font-style: italic;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
  border-radius: 999px;
  padding: 0.3rem 0.7rem;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.tool-prompt-chip:hover {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
}

@media (max-width: 960px) {
  .live-layout {
    display: block;
  }

  .live-shell {
    max-width: none;
  }

  .tools-toggle {
    display: inline-flex;
    align-items: center;
  }

  .tools-panel {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(320px, 85vw);
    z-index: 20;
    background: var(--surface);
    border-left: 1px solid var(--border);
    padding: 1.25rem 1.25rem 1.5rem;
    transform: translateX(100%);
    transition: transform 0.25s ease;
    box-shadow: -8px 0 24px rgba(0, 0, 0, 0.12);
  }

  .tools-panel-open {
    transform: translateX(0);
  }

  .tools-panel-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.9rem;
    height: 1.9rem;
    border-radius: 50%;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    cursor: pointer;
  }
}

/* ---------- Responsive ---------- */

@media (max-width: 760px) {
```

- [ ] **Step 5: Run lint**

Run: `cd frontend && npm run lint`
Expected: exits 0, no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/index.css
git commit -m "style: add tools panel layout, card, and drawer styles"
```

---

### Task 4: Wire `ToolsPanel` into `LiveAssistant`

**Files:**
- Modify: `frontend/src/routes/LiveAssistant.tsx`

**Interfaces:**
- Consumes: `ToolsPanel` from `../components/ToolsPanel` (Task 2); `.live-layout`, `.tools-toggle` classes (Task 3).

- [ ] **Step 1: Add the import and open/close state**

In `frontend/src/routes/LiveAssistant.tsx`, find:

```tsx
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { MicButton } from '../components/MicButton'
import { StatusBadge } from '../components/StatusBadge'
import { Transcript } from '../components/Transcript'
import { useLiveSession } from '../LiveSessionContext'
```

Replace with:

```tsx
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { MicButton } from '../components/MicButton'
import { StatusBadge } from '../components/StatusBadge'
import { ToolsPanel } from '../components/ToolsPanel'
import { Transcript } from '../components/Transcript'
import { useLiveSession } from '../LiveSessionContext'
```

- [ ] **Step 2: Add the input ref, tools-open state, and select-prompt handler**

Find:

```tsx
export function LiveAssistant() {
  const { state, dispatch, client, connectionStatus, newSession } = useLiveSession()
  const [inputText, setInputText] = useState('')
```

Replace with:

```tsx
export function LiveAssistant() {
  const { state, dispatch, client, connectionStatus, newSession } = useLiveSession()
  const [inputText, setInputText] = useState('')
  const [toolsOpen, setToolsOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
```

- [ ] **Step 3: Add the handler that fills (not sends) the composer input**

Find:

```tsx
  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const text = inputText.trim()
    if (!text || connectionStatus !== 'open') return

    dispatch({ type: 'user_submit', text })
    client?.send({ type: 'text_input', text })
    setInputText('')
  }
```

Replace with:

```tsx
  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const text = inputText.trim()
    if (!text || connectionStatus !== 'open') return

    dispatch({ type: 'user_submit', text })
    client?.send({ type: 'text_input', text })
    setInputText('')
  }

  function handleSelectPrompt(text: string) {
    setInputText(text)
    setToolsOpen(false)
    inputRef.current?.focus()
  }
```

- [ ] **Step 4: Add the header toggle button**

Find:

```tsx
          <div className="header-actions">
            <button type="button" className="btn-ghost" onClick={newSession}>
              New session
            </button>
            <Link className="nav-link" to="/sessions">
              Sessions
            </Link>
          </div>
```

Replace with:

```tsx
          <div className="header-actions">
            <button
              type="button"
              className="btn-ghost tools-toggle"
              onClick={() => setToolsOpen((open) => !open)}
            >
              Tools
            </button>
            <button type="button" className="btn-ghost" onClick={newSession}>
              New session
            </button>
            <Link className="nav-link" to="/sessions">
              Sessions
            </Link>
          </div>
```

- [ ] **Step 5: Wrap the page body in `.live-layout`, attach the input ref, and render `ToolsPanel`**

Find:

```tsx
  return (
    <main className="live">
      <div className="shell live-shell">
        <header className="app-header">
          <h1 className="brand">Voice Assistant</h1>
          <StatusBadge connectionStatus={connectionStatus} pipelineState={state.status} />
          <div className="header-actions">
            <button
              type="button"
              className="btn-ghost tools-toggle"
              onClick={() => setToolsOpen((open) => !open)}
            >
              Tools
            </button>
            <button type="button" className="btn-ghost" onClick={newSession}>
              New session
            </button>
            <Link className="nav-link" to="/sessions">
              Sessions
            </Link>
          </div>
        </header>

        {state.error && <p className="error-banner">{state.error}</p>}

        {state.notifications.length > 0 && (
          <ul className="notifications">
            {state.notifications.map((note, index) => (
              <li key={index} className="notification">
                ⏱ {note}
              </li>
            ))}
          </ul>
        )}

        <Transcript
          messages={state.messages}
          isThinking={isThinking}
          toolActivity={state.toolActivity}
          sttPartial={state.sttPartial}
          hero
          status={heroStatus}
        />

        <form className="composer" onSubmit={handleSubmit}>
          <MicButton client={client} dispatch={dispatch} connectionStatus={connectionStatus} />
          <input
            type="text"
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            placeholder="Type a message…"
            disabled={connectionStatus !== 'open'}
            autoFocus
          />
          <button type="submit" className="btn-send" disabled={!canSend} aria-label="Send message">
            <SendIcon />
          </button>
        </form>
      </div>
    </main>
  )
}
```

Replace with:

```tsx
  return (
    <main className="live">
      <div className="live-layout">
        <div className="live-shell">
          <header className="app-header">
            <h1 className="brand">Voice Assistant</h1>
            <StatusBadge connectionStatus={connectionStatus} pipelineState={state.status} />
            <div className="header-actions">
              <button
                type="button"
                className="btn-ghost tools-toggle"
                onClick={() => setToolsOpen((open) => !open)}
              >
                Tools
              </button>
              <button type="button" className="btn-ghost" onClick={newSession}>
                New session
              </button>
              <Link className="nav-link" to="/sessions">
                Sessions
              </Link>
            </div>
          </header>

          {state.error && <p className="error-banner">{state.error}</p>}

          {state.notifications.length > 0 && (
            <ul className="notifications">
              {state.notifications.map((note, index) => (
                <li key={index} className="notification">
                  ⏱ {note}
                </li>
              ))}
            </ul>
          )}

          <Transcript
            messages={state.messages}
            isThinking={isThinking}
            toolActivity={state.toolActivity}
            sttPartial={state.sttPartial}
            hero
            status={heroStatus}
          />

          <form className="composer" onSubmit={handleSubmit}>
            <MicButton client={client} dispatch={dispatch} connectionStatus={connectionStatus} />
            <input
              ref={inputRef}
              type="text"
              value={inputText}
              onChange={(event) => setInputText(event.target.value)}
              placeholder="Type a message…"
              disabled={connectionStatus !== 'open'}
              autoFocus
            />
            <button type="submit" className="btn-send" disabled={!canSend} aria-label="Send message">
              <SendIcon />
            </button>
          </form>
        </div>

        <ToolsPanel
          open={toolsOpen}
          onSelectPrompt={handleSelectPrompt}
          onClose={() => setToolsOpen(false)}
        />
      </div>
    </main>
  )
}
```

- [ ] **Step 6: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: exits 0, no errors.

- [ ] **Step 7: Run lint**

Run: `cd frontend && npm run lint`
Expected: exits 0, no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/LiveAssistant.tsx
git commit -m "feat: render tools panel on the live assistant page"
```

---

### Task 5: Manual browser verification

**Files:** none (verification only).

- [ ] **Step 1: Start the frontend dev server**

Run: `make dev-frontend` (or `cd frontend && npm run dev`)
Expected: Vite serves on `http://localhost:5173`.

- [ ] **Step 2: Desktop-width check (≥960px)**

Open `http://localhost:5173` at a viewport ≥960px wide. Confirm:
- The tools sidebar is visible immediately, to the right of the chat column, with no toggle needed.
- All 4 cards render: Weather, Save a note, Recall notes, Set a timer — each with an icon, description, and a sample-prompt chip.
- Clicking a sample-prompt chip fills the composer input with that text and focuses it, and does **not** send a message (no new bubble appears, `state.messages` unchanged).
- The "Tools" header button is not visible at this width.

- [ ] **Step 3: Narrow-width check (<960px)**

Resize the browser (or use the responsive/device toolbar) to <960px width. Confirm:
- The sidebar is hidden by default; a "Tools" button appears in the header.
- Clicking "Tools" slides the drawer in from the right; clicking it again (or selecting a prompt) closes it.
- Selecting a sample prompt in the drawer fills the input, focuses it, and auto-closes the drawer.

- [ ] **Step 4: Persistence-through-conversation check**

Send a message (typed or via a sample prompt) so the transcript is no longer empty. Confirm the tools panel (sidebar or the "Tools" toggle, depending on width) is still present and functional — it must not disappear once the hero empty-state goes away.

- [ ] **Step 5: Theme check**

Toggle the OS/browser color scheme between light and dark (or use `resize_window`'s `colorScheme` option if using the browser tool). Confirm the tools panel's colors adapt correctly (no hardcoded light-only or dark-only colors) — text stays readable and borders/backgrounds use the same `light-dark()` tokens as the rest of the page.

- [ ] **Step 6: Full test suite**

Run: `make test`
Expected: backend pytest and frontend typecheck both pass (no regressions from this change).

## Self-Review

- **Spec coverage:** catalog data (Task 1) ✓, ToolsPanel component + click-to-fill (Task 2, Task 4 Step 3) ✓, sticky sidebar + mobile drawer (Task 3, Task 4) ✓, persistent across conversation (Task 5 Step 4 verifies; no code gates the panel on `messages.length`) ✓, Signal-studio token reuse (Task 3 uses only existing `--accent`/`--border`/`--surface`/`--muted`/`color-mix`) ✓, no backend change ✓, no auto-send on click (Task 4 Step 3 only calls `setInputText`) ✓.
- **Placeholder scan:** none found — every step has literal code/commands.
- **Type consistency:** `ToolCatalogEntry`/`TOOL_CATALOG` (Task 1) match the import in Task 2; `ToolsPanelProps` (`open`, `onSelectPrompt`, `onClose`) match exactly how Task 4 Step 5 invokes `<ToolsPanel open={...} onSelectPrompt={...} onClose={...} />`.
