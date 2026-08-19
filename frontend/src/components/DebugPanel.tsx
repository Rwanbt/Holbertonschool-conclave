import { collectToolCalls, EXPERT_ROLE_LABELS } from '../steps'
import type { AnalysisConnection } from '../useAnalysisController'
import type { AnalysisEvent, ExecutionUsage } from '../types'

interface DebugPanelProps {
  events: readonly AnalysisEvent[]
  usage: ExecutionUsage | null
  connection: AnalysisConnection
  limitSeconds: number
  lastEventId: number
}

function connectionLabel(connection: AnalysisConnection): string {
  switch (connection.status) {
    case 'idle':
      return 'En attente'
    case 'loading':
      return 'Rechargement du snapshot…'
    case 'live':
      return 'Flux connecté'
    case 'reconnecting':
      return 'Flux interrompu, reconnexion…'
    case 'error':
      return 'Erreur'
  }
}

function formatDuration(durationMs: number): string {
  return `${Math.round(durationMs / 100) / 10} s`
}

export function DebugPanel({
  events,
  usage,
  connection,
  limitSeconds,
  lastEventId,
}: DebugPanelProps) {
  const toolCalls = collectToolCalls(events)

  return (
    <section className="debug-panel" aria-label="Panneau de démonstration">
      <h2 className="panel-title">Démonstration</h2>

      <div className="debug-grid">
        <div className="debug-item">
          <span className="debug-label">Connexion SSE</span>
          <span className="debug-value">{connectionLabel(connection)}</span>
        </div>
        <div className="debug-item">
          <span className="debug-label">Dernier événement reçu</span>
          <span className="debug-value">#{lastEventId}</span>
        </div>
        <div className="debug-item">
          <span className="debug-label">Limite de temps</span>
          <span className="debug-value">{formatDuration(limitSeconds * 1000)}</span>
        </div>
        <div className="debug-item">
          <span className="debug-label">Appels d’outils observés</span>
          <span className="debug-value">{toolCalls.length}</span>
        </div>
      </div>

      {toolCalls.length > 0 && (
        <ol className="debug-tools" aria-label="Ligne temporelle des outils">
          {toolCalls.map((call) => (
            <li className="debug-tool" key={call.key}>
              <span className="debug-tool-role">
                {EXPERT_ROLE_LABELS[call.agent_role as keyof typeof EXPERT_ROLE_LABELS] ?? call.agent_role}
              </span>
              <span className="debug-tool-round">tour {call.llm_round}</span>
              <code className="debug-tool-name">{call.tool_name}</code>
              <span className={`debug-tool-status debug-tool-status--${call.status}`}>
                {call.status === 'running' ? 'En cours' : call.status === 'success' ? 'Succès' : 'Échec'}
              </span>
            </li>
          ))}
        </ol>
      )}

      {usage !== null && (
        <div className="debug-usage">
          <h3>Usage agrégé</h3>
          <dl className="execution-grid">
            <div className="execution-item">
              <dt>Tokens d’entrée</dt>
              <dd>{usage.input_tokens ?? '—'}</dd>
            </div>
            <div className="execution-item">
              <dt>Tokens de sortie</dt>
              <dd>{usage.output_tokens ?? '—'}</dd>
            </div>
            <div className="execution-item">
              <dt>Coût estimé</dt>
              <dd>{usage.estimated_cost_usd === null ? '—' : `${usage.estimated_cost_usd} USD`}</dd>
            </div>
            <div className="execution-item">
              <dt>Tours LLM</dt>
              <dd>{usage.llm_rounds}</dd>
            </div>
          </dl>
        </div>
      )}
    </section>
  )
}