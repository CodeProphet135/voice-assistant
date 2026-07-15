# Tools panel: show available assistant capabilities + sample prompts

## Problem

The main page currently gives no indication of what the assistant can
actually do — a first-time user sees "Say something" / "Tap the mic to talk,
or type a message below" with no hint that it can check the weather, take
notes, or set timers. There's no discoverability path for the 4 tools
already wired up in
`backend/src/voice_assistant/agent/tools/{weather,notes,timers}.py`.

## Goals

- Surface the assistant's tool capabilities (name + one-line description) on
  the main Live page.
- Give each tool a sample prompt the user can use to try it.
- Make it available throughout a conversation, not just on the empty state
  (unlike the existing `transcript-hero`, which disappears once messages
  exist).
- Clicking a sample prompt fills the composer input (does not auto-send).
- Match the existing "Signal studio" visual language (accent/border tokens,
  existing card/chip patterns) — no new design language.

## Non-goals

- Fetching the tool list from the backend. The registry
  (`agent/tools/registry.py`'s `definitions()`) is not exposed over HTTP/WS
  today, and adding that endpoint is out of scope — 4 tools is small enough
  to hardcode in the frontend and update by hand if tools are added later.
- Auto-sending a sample prompt on click.
- Persisting panel open/closed state across reloads (mobile drawer always
  starts closed).
- Any change to `Transcript.tsx`'s existing hero/empty-state — it's left
  as-is; the tools panel is additive, not a replacement.

## Design

### Data — `frontend/src/toolCatalog.ts`

A static array of 4 entries, one per backend tool:

```ts
interface ToolCatalogEntry {
  id: string
  name: string
  description: string
  samplePrompt: string
  icon: (props: { className?: string }) => JSX.Element
}
```

| id | name | description | samplePrompt |
|---|---|---|---|
| `weather` | Weather | Check the current weather anywhere | "What's the weather in Tokyo?" |
| `save_note` | Save a note | Save a short note to recall later | "Remind me to call the dentist tomorrow" |
| `list_notes` | Recall notes | List the notes you've saved | "What notes do I have?" |
| `set_timer` | Set a timer | Start a countdown timer | "Set a timer for 10 minutes" |

Descriptions are friendly rephrasings, not verbatim copies of the backend
`ToolSpec.description` strings (those are written for the model, not a user
reading a card).

### Component — `frontend/src/components/ToolsPanel.tsx`

Renders the catalog as a list of cards: icon, name, description, and a
clickable sample-prompt chip. Props:

```ts
interface ToolsPanelProps {
  onSelectPrompt: (text: string) => void
  onClose?: () => void  // only rendered/used in the mobile drawer variant
}
```

Clicking a sample-prompt chip calls `onSelectPrompt`, which the parent wires
to fill (not submit) the composer input and focus it. Icons are small inline
SVGs matching the stroke style already used by `SendIcon` in
`LiveAssistant.tsx` (`stroke="currentColor"`, `strokeWidth="2.5"`,
`strokeLinecap="round"`).

### Layout — sticky sidebar (desktop) / slide-in drawer (mobile)

`LiveAssistant.tsx` introduces a new wrapping element, `.live-layout`, that
holds the existing `.live-shell` (header + transcript + composer, unchanged
internally) and the new `<ToolsPanel>` side by side:

- **≥ 960px viewport**: `.live-layout` is a flex row inside a wider
  container (~1100px vs. the current 720px `.shell`); `.tools-panel` is a
  sticky ~300px column to the right of the chat. Always visible — nothing to
  toggle, satisfying "persistent."
- **< 960px viewport**: `.tools-panel` becomes a fixed-position drawer
  (`transform: translateX(100%)` closed / `translateX(0)` open), toggled by
  a "Tools" button that appears in `.header-actions` only at this
  breakpoint. Selecting a prompt on mobile also closes the drawer (calls
  `onClose`), since the composer needs to be visible to see the filled text.

State: one `const [toolsOpen, setToolsOpen] = useState(false)` in
`LiveAssistant`, only consulted by the mobile drawer's open class — the
desktop sidebar ignores it entirely (pure CSS visibility via media query).

### Styling

New classes only, reusing existing tokens — no new colors introduced:

- `.tools-panel`, `.tools-panel-header`
- `.tool-card`, `.tool-card-icon`, `.tool-card-title`, `.tool-card-desc`
- `.tool-prompt-chip` (button), hover/focus states via
  `color-mix(in srgb, var(--accent) …%, transparent)` matching the existing
  pattern at `index.css:169-170`.
- Drawer transition: `transition: transform 0.25s ease`.

## Testing

- No new component-test infra exists (frontend tests cover only logic
  modules — `state.test.ts`, `replay.test.ts`, etc.; no
  `@testing-library/react` dependency) — consistent with that, no automated
  test is added for `ToolsPanel`.
- Manual verification in the browser:
  - Desktop width: sidebar visible immediately, all 4 cards render, clicking
    a sample prompt fills the composer input without sending.
  - Narrow width: sidebar hidden by default, "Tools" button appears in the
    header, opens/closes the drawer, selecting a prompt fills input and
    auto-closes the drawer.
  - Panel stays visible/reachable after a conversation has messages (not
    just on the empty state).
  - Dark/light theme (`light-dark()` tokens) both render correctly.
