import type {
  AnalysisEvent,
  AnalysisEventType,
  AnalysisSnapshot,
  ExpertStatus,
  ExpertRole,
} from './types'

export const FLOW_STEPS: readonly {
  id: string
  label: string
  description: string
}[] = [
  { id: 'soumettre', label: 'Soumettre', description: 'Le document est accepté.' },
  { id: 'convoquer', label: 'Convoquer', description: 'Les trois experts sont lancés.' },
  { id: 'observer', label: 'Observer', description: 'Les experts travaillent en parallèle.' },
  { id: 'comparer', label: 'Comparer', description: 'Les résultats validés arrivent colonne par colonne.' },
  { id: 'arbitrer', label: 'Arbitrer', description: "L'arbitre départage les désaccords." },
  { id: 'decider', label: 'Décider', description: 'Le verdict final est exploitable.' },
]

export function isTerminalAnalysisStatus(status: AnalysisSnapshot['status']): boolean {
  return status === 'completed' || status === 'degraded' || status === 'failed' || status === 'interrupted'
}

export function isTerminalEventType(type: AnalysisEventType): boolean {
  return (
    type === 'analysis.completed' ||
    type === 'analysis.degraded' ||
    type === 'analysis.failed' ||
    type === 'analysis.interrupted'
  )
}

export function isIdempotentDuplicate(
  events: readonly AnalysisEvent[],
  candidate: AnalysisEvent,
): boolean {
  return events.some((current) => current.id === candidate.id)
}

export function appendAnalysisEvent(
  events: readonly AnalysisEvent[],
  candidate: AnalysisEvent,
): readonly AnalysisEvent[] {
  if (isIdempotentDuplicate(events, candidate)) {
    return events
  }
  return [...events, candidate]
}

function hasEventType(
  events: readonly AnalysisEvent[],
  types: readonly AnalysisEventType[],
): boolean {
  return events.some((event) => types.includes(event.type))
}

export function calculateActiveStep(
  snapshot: AnalysisSnapshot | null,
  events: readonly AnalysisEvent[],
): number {
  if (snapshot === null) {
    return 0
  }
  if (isTerminalAnalysisStatus(snapshot.status)) {
    return FLOW_STEPS.length - 1
  }
  if (hasEventType(events, ['arbiter.started', 'arbiter.completed', 'arbiter.failed'])) {
    return FLOW_STEPS.length - 2
  }
  if (hasEventType(events, ['expert.completed', 'expert.failed', 'expert.timeout'])) {
    return FLOW_STEPS.length - 3
  }
  if (hasEventType(events, ['expert.started'])) {
    return FLOW_STEPS.length - 4
  }
  return 1
}

export type ToolCallView = {
  key: string
  agent_role: string
  llm_round: number
  tool_name: string
  status: 'running' | 'success' | 'error'
}

export function collectToolCalls(events: readonly AnalysisEvent[]): readonly ToolCallView[] {
  const views: ToolCallView[] = []
  for (const event of events) {
    if (event.type !== 'tool.started' && event.type !== 'tool.completed' && event.type !== 'tool.failed') {
      continue
    }
    const payload = event.payload
    const agent_role = payload.agent_role
    const llm_round = payload.llm_round
    const tool_name = payload.tool_name
    if (typeof agent_role !== 'string' || typeof tool_name !== 'string') {
      continue
    }
    const round = typeof llm_round === 'number' && Number.isInteger(llm_round) ? llm_round : 0
    const key = `${agent_role}-${round}-${tool_name}`
    if (event.type === 'tool.started') {
      views.push({ key, agent_role, llm_round: round, tool_name, status: 'running' })
    } else {
      const matching = views.filter((view) => view.key === key && view.status === 'running')
      if (matching.length > 0) {
        matching[0].status = event.type === 'tool.completed' ? 'success' : 'error'
      }
    }
  }
  return views
}

export function countToolRounds(views: readonly ToolCallView[], role: string): number {
  return views.filter((view) => view.agent_role === role).length
}

export const EXPERT_STATUS_LABELS: Record<ExpertStatus, string> = {
  pending: 'En attente',
  running: 'En cours',
  completed: 'Terminé',
  error: 'Erreur',
  timeout: 'Délai dépassé',
}

export const EXPERT_ROLE_LABELS: Record<ExpertRole, string> = {
  avocat: 'Avocat',
  procureur: 'Procureur',
  comptable: 'Comptable',
}

export function liveExpertRun(
  run: {
    role: ExpertRole
    status: ExpertStatus
    error_code: string | null
  },
  events: readonly AnalysisEvent[],
): { status: ExpertStatus; error_code: string | null } {
  const roleEvents = events.filter(
    (event) =>
      event.type === 'expert.started' ||
      event.type === 'expert.completed' ||
      event.type === 'expert.failed' ||
      event.type === 'expert.timeout',
  )
  const relevant = roleEvents.filter((event) => event.payload.role === run.role)
  if (relevant.length === 0) {
    return { status: run.status, error_code: run.error_code }
  }
  const lastEvent = relevant[relevant.length - 1]
  switch (lastEvent.type) {
    case 'expert.started':
      return { status: 'running', error_code: null }
    case 'expert.completed':
      return { status: 'completed', error_code: null }
    case 'expert.failed':
      return {
        status: 'error',
        error_code:
          typeof lastEvent.payload.error_code === 'string'
            ? lastEvent.payload.error_code
            : run.error_code,
      }
    case 'expert.timeout':
      return { status: 'timeout', error_code: 'expert_timeout' }
    default:
      return { status: run.status, error_code: run.error_code }
  }
}