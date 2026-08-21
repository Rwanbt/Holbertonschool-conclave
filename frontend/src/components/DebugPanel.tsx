import { collectToolCalls, EXPERT_ROLE_LABELS } from '../steps'
import { AGENT_ROLES, collectResponseFlows } from '../liveResponses'
import type { AnalysisConnection } from '../useAnalysisController'
import type { AnalysisEvent, AnalysisEventType, ExecutionUsage, LiveResponseStatus } from '../types'

interface DebugPanelProps {
  events: readonly AnalysisEvent[]
  usage: ExecutionUsage | null
  connection: AnalysisConnection
  limitSeconds: number
  lastEventId: number
}

interface TimelineEntry {
  id: number
  label: string
}

const RESPONSE_STATUS_LABELS: Record<LiveResponseStatus, string> = {
  idle: '—',
  streaming: 'En direct',
  completed: 'Terminé',
  failed: 'Échec',
}

function flowLimitLabel(flowStatus: LiveResponseStatus): string {
  if (flowStatus === 'idle') {
    return '—'
  }
  return RESPONSE_STATUS_LABELS[flowStatus] ?? flowStatus
}

function agentLabel(role: string): string {
  return EXPERT_ROLE_LABELS[role as keyof typeof EXPERT_ROLE_LABELS] ?? 'Arbitre'
}

function timelineLabel(event: AnalysisEvent): string | null {
  const payload = event.payload
  const role = payload.agent_role ?? payload.role
  const roleLabel = typeof role === 'string' ? agentLabel(role) : ''
  switch (event.type) {
    case 'tool.started':
      return `outil démarré — ${roleLabel} · tour ${String(payload.llm_round)} · ${String(payload.tool_name)}`
    case 'tool.completed':
      return `outil terminé — ${roleLabel} · tour ${String(payload.llm_round)} · ${String(payload.tool_name)}`
    case 'tool.failed':
      return `outil en échec — ${roleLabel} · tour ${String(payload.llm_round)} · ${String(payload.tool_name)}`
    case 'agent.response.started':
      return `réponse démarrée — ${roleLabel}`
    case 'agent.response.delta':
      return `delta ${String(payload.sequence)} — ${roleLabel}`
    case 'agent.response.completed':
      return `réponse validée — ${roleLabel}`
    case 'agent.response.failed':
      return `réponse en échec — ${roleLabel}`
    case 'expert.started':
      return `expert démarré — ${roleLabel}`
    case 'expert.completed':
      return `expert terminé — ${roleLabel}`
    case 'expert.failed':
      return `expert en échec — ${roleLabel}`
    case 'expert.timeout':
      return `expert timeout — ${roleLabel}`
    case 'arbiter.started':
      return 'arbitrage démarré'
    case 'arbiter.completed':
      return 'arbitrage terminé'
    case 'arbiter.failed':
      return 'arbitrage en échec'
    case 'analysis.created':
      return 'analyse créée'
    case 'analysis.completed':
      return 'analyse terminée'
    case 'analysis.degraded':
      return 'analyse dégradée'
    case 'analysis.failed':
      return 'analyse en échec'
    case 'analysis.interrupted':
      return 'analyse interrompue'
    default:
      return null
  }
}

const TIMELINE_TYPES: readonly AnalysisEventType[] = [
  'tool.started',
  'tool.completed',
  'tool.failed',
  'agent.response.started',
  'agent.response.delta',
  'agent.response.completed',
  'agent.response.failed',
  'expert.started',
  'expert.completed',
  'expert.failed',
  'expert.timeout',
  'arbiter.started',
  'arbiter.completed',
  'arbiter.failed',
  'analysis.created',
  'analysis.completed',
  'analysis.degraded',
  'analysis.failed',
  'analysis.interrupted',
]

function collectTimeline(events: readonly AnalysisEvent[]): readonly TimelineEntry[] {
  const entries: TimelineEntry[] = []
  for (const event of events) {
    if (!TIMELINE_TYPES.includes(event.type)) {
      continue
    }
    const label = timelineLabel(event)
    if (label !== null) {
      entries.push({ id: event.id, label })
    }
  }
  return entries
}

function connectionLabel(connection: AnalysisConnection): string {
  switch (connection.status) {
    case 'idle':
      return 'En attente'
    case 'loading':
      return 'Rechargement du snapshot…'
    case 'live':
      return 'Flux connecté'
    case 'closed':
      return 'Analyse terminale : flux fermé'
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
  const flows = collectResponseFlows(events)
  const timeline = collectTimeline(events)

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

      <div className="debug-flows">
        <h3>Flux des réponses</h3>
        <div className="debug-grid">
          {AGENT_ROLES.map((role) => {
            const flow = flows[role]
            const sequenceRange =
              flow.firstSequence === null ? '—' : `#${flow.firstSequence} → #${flow.lastSequence}`
            return (
              <div className="debug-item" key={role}>
                <span className="debug-label">{agentLabel(role)}</span>
                <span className="debug-value">
                  {flow.deltas} delta{flow.deltas > 1 ? 's' : ''} · {flow.chars} car.
                </span>
                <span className="debug-sub">
                  séquences {sequenceRange} · statut {flowLimitLabel(flow.status)}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {timeline.length > 0 && (
        <ol className="debug-timeline" aria-label="Ordre relatif des outils et des réponses">
          {timeline.map((entry) => (
            <li className="debug-timeline-item" key={entry.id}>
              <span className="debug-timeline-id">#{entry.id}</span>
              <span className="debug-timeline-label">{entry.label}</span>
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