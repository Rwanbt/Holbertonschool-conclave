import type {
  AgentRole,
  AnalysisEvent,
  LiveResponseStatus,
  LiveResponseView,
} from './types'

export const AGENT_ROLES: readonly AgentRole[] = [
  'avocat',
  'procureur',
  'comptable',
  'arbitre',
]

export const CLIENT_MAX_LIVE_CHARS = 20_000

export interface LiveResponseFlow {
  role: AgentRole
  status: LiveResponseStatus
  deltas: number
  chars: number
  firstSequence: number | null
  lastSequence: number | null
}

function idleView(role: AgentRole): LiveResponseView {
  return { role, status: 'idle', text: '', lastSequence: 0, errorCode: null }
}

function idleFlow(role: AgentRole): LiveResponseFlow {
  return {
    role,
    status: 'idle',
    deltas: 0,
    chars: 0,
    firstSequence: null,
    lastSequence: null,
  }
}

function roleOf(event: AnalysisEvent): AgentRole | null {
  const role = event.payload.role
  if (
    role === 'avocat' ||
    role === 'procureur' ||
    role === 'comptable' ||
    role === 'arbitre'
  ) {
    return role
  }
  return null
}

function readSequence(event: AnalysisEvent): number | null {
  const sequence = event.payload.sequence
  return typeof sequence === 'number' && Number.isInteger(sequence) && sequence > 0
    ? sequence
    : null
}

function readDelta(event: AnalysisEvent): string | null {
  const delta = event.payload.delta
  return typeof delta === 'string' && delta.length > 0 ? delta : null
}

function readErrorCode(event: AnalysisEvent): string | null {
  const error_code = event.payload.error_code
  return typeof error_code === 'string' && error_code.length > 0 ? error_code : null
}

function isClosed(view: LiveResponseView): boolean {
  return view.status === 'completed' || view.status === 'failed'
}

/**
 * Reconstruit les brouillons des réponses live par rôle à partir des
 * événements SSE mémorisés. Pure et idempotente : un delta n'est concaténé
 * que si sa séquence est strictement plus grande que la dernière vue, et les
 * duplicates d'identifiant d'événement sont déjà filtrés en amont.
 */
export function collectLiveResponses(
  events: readonly AnalysisEvent[],
): Record<AgentRole, LiveResponseView> {
  const views: Record<AgentRole, LiveResponseView> = {
    avocat: idleView('avocat'),
    procureur: idleView('procureur'),
    comptable: idleView('comptable'),
    arbitre: idleView('arbitre'),
  }

  for (const event of events) {
    if (event.type === 'arbiter.started') {
      if (views.arbitre.status === 'idle') {
        views.arbitre = { ...idleView('arbitre'), status: 'streaming' }
      }
      continue
    }
    const role = roleOf(event)
    if (role === null) {
      continue
    }
    const view = views[role]

    switch (event.type) {
      case 'agent.response.started':
        views[role] = { ...idleView(role), status: 'streaming' }
        break
      case 'agent.response.delta': {
        if (isClosed(view)) {
          break
        }
        const sequence = readSequence(event)
        if (sequence === null || sequence <= view.lastSequence) {
          break
        }
        const delta = readDelta(event)
        if (delta === null) {
          break
        }
        const concatenated = (view.text + delta).slice(0, CLIENT_MAX_LIVE_CHARS)
        views[role] = {
          role,
          status: 'streaming',
          text: concatenated,
          lastSequence: sequence,
          errorCode: view.errorCode,
        }
        break
      }
      case 'agent.response.completed': {
        const base = view.status === 'idle' ? { ...idleView(role), status: 'streaming' } : view
        views[role] = { ...base, status: 'completed', errorCode: null }
        break
      }
      case 'agent.response.failed':
        views[role] = {
          ...view,
          status: 'failed',
          errorCode: readErrorCode(event) ?? view.errorCode,
        }
        break
      default:
        break
    }
  }

  return views
}

/**
 * Statistiques du flux de réponses par rôle pour le panneau de
 * démonstration : nombre de deltas réellement intégrés, caractères reçus et
 * séquences première/dernière, en suivant les mêmes règles idempotentes.
 */
export function collectResponseFlows(
  events: readonly AnalysisEvent[],
): Record<AgentRole, LiveResponseFlow> {
  const flows: Record<AgentRole, LiveResponseFlow> = {
    avocat: idleFlow('avocat'),
    procureur: idleFlow('procureur'),
    comptable: idleFlow('comptable'),
    arbitre: idleFlow('arbitre'),
  }

  for (const event of events) {
    if (event.type === 'arbiter.started') {
      if (flows.arbitre.status === 'idle') {
        flows.arbitre = { ...idleFlow('arbitre'), status: 'streaming' }
      }
      continue
    }
    const role = roleOf(event)
    if (role === null) {
      continue
    }
    const flow = flows[role]

    switch (event.type) {
      case 'agent.response.started':
        flows[role] = { ...idleFlow(role), status: 'streaming' }
        break
      case 'agent.response.delta': {
        if (flow.status === 'completed' || flow.status === 'failed') {
          break
        }
        const sequence = readSequence(event)
        const delta = readDelta(event)
        if (sequence === null || delta === null) {
          break
        }
        if (flow.lastSequence !== null && sequence <= flow.lastSequence) {
          break
        }
        flows[role] = {
          role,
          status: flow.status === 'idle' ? 'streaming' : flow.status,
          deltas: flow.deltas + 1,
          chars: Math.min(
            CLIENT_MAX_LIVE_CHARS,
            flow.chars + delta.length,
          ),
          firstSequence: flow.firstSequence ?? sequence,
          lastSequence: sequence,
        }
        break
      }
      case 'agent.response.completed':
        flows[role] = { ...flow, status: 'completed' }
        break
      case 'agent.response.failed':
        flows[role] = { ...flow, status: 'failed' }
        break
      default:
        break
    }
  }

  return flows
}