import type { ConnectionStatus, PipelineState } from '../ws'

interface StatusBadgeProps {
  connectionStatus: ConnectionStatus
  pipelineState: PipelineState | 'connecting'
}

/** Small pill showing connection status while connecting, then the live pipeline state once open. */
export function StatusBadge({ connectionStatus, pipelineState }: StatusBadgeProps) {
  const label = connectionStatus === 'open' ? pipelineState : connectionStatus
  return <span className={`status-badge status-${label}`}>{label}</span>
}
