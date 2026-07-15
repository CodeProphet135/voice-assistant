/**
 * The app's signature element: a small equalizer that renders the pipeline
 * state as motion. Each state has its own animation personality (defined in
 * index.css): idle breathes, listening jumps, thinking ripples left-to-right,
 * speaking dances. Pure CSS — cheap, and static under prefers-reduced-motion.
 */
export function Pulse({ state, hero = false }: { state: string; hero?: boolean }) {
  const bars = hero ? 7 : 5
  return (
    <span className={`pulse pulse-${state}${hero ? ' pulse-hero' : ''}`} aria-hidden="true">
      {Array.from({ length: bars }, (_, i) => (
        <i key={i} />
      ))}
    </span>
  )
}
