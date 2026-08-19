import type { ExecutionUsage } from '../types'

interface ExecutionPanelProps {
  usage: ExecutionUsage
}

function formatCost(value: number | null): string {
  if (value === null) {
    return 'indisponible'
  }
  const text = value < 0.01 ? value.toFixed(6) : value.toFixed(2)
  return `${text} USD`
}

function formatLatency(durationMs: number): string {
  return `${durationMs} ms`
}

export function ExecutionPanel({ usage }: ExecutionPanelProps) {
  return (
    <dl className="execution-grid">
      <div className="execution-item">
        <dt>Tokens d’entrée</dt>
        <dd>{usage.input_tokens}</dd>
      </div>
      <div className="execution-item">
        <dt>Tokens de sortie</dt>
        <dd>{usage.output_tokens}</dd>
      </div>
      <div className="execution-item">
        <dt>Tokens totaux</dt>
        <dd>{usage.total_tokens}</dd>
      </div>
      <div className="execution-item">
        <dt>Coût estimé</dt>
        <dd>{formatCost(usage.estimated_cost_usd)}</dd>
      </div>
      <div className="execution-item">
        <dt>Latence totale</dt>
        <dd>{formatLatency(usage.total_latency_ms)}</dd>
      </div>
      <div className="execution-item">
        <dt>Tours LLM</dt>
        <dd>{usage.llm_rounds}</dd>
      </div>
    </dl>
  )
}