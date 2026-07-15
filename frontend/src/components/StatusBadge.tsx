import type { ConnectionStatus, PipelineState } from '../ws'
import { Pulse } from './Pulse'

interface StatusBadgeProps {
  connectionStatus: ConnectionStatus
  pipelineState: PipelineState | 'connecting'
}

/** Live pipeline state, rendered as a labelled pulse: connection status while connecting, pipeline state once open. */
export function StatusBadge({ connectionStatus, pipelineState }: StatusBadgeProps) {
  const label = connectionStatus === 'open' ? pipelineState : connectionStatus
  return (
    <span className={`status status-${label}`}>
      <Pulse state={label} />
      <span className="status-label">{label}</span>
    </span>
  )
}
