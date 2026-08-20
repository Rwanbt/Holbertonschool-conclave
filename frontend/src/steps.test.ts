import { describe, expect, it } from 'vitest'
import {
  appendAnalysisEvent,
  calculateActiveStep,
  collectToolCalls,
  isIdempotentDuplicate,
  isTerminalAnalysisStatus,
  isTerminalEventType,
  liveExpertRun,
} from './steps'
import type { AnalysisEvent, AnalysisSnapshot } from './types'

function makeEvent(overrides: Partial<AnalysisEvent>): AnalysisEvent {
  return {
    id: 1,
    type: 'expert.started',
    payload: { analysis_id: 'a', role: 'avocat' },
    ...overrides,
  }
}

function runningSnapshot(): AnalysisSnapshot {
  return {
    analysis_id: 'a',
    document: 'd',
    status: 'running',
    created_at: 't',
    started_at: null,
    completed_at: null,
    error_code: null,
    avocat: { role: 'avocat', status: 'pending', output: null, error_code: null },
    procureur: { role: 'procureur', status: 'pending', output: null, error_code: null },
    comptable: { role: 'comptable', status: 'pending', output: null, error_code: null },
    verdict: null,
    usage: {
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      estimated_cost_usd: null,
      total_latency_ms: 0,
      llm_rounds: 0,
    },
    guardrails: {
      expert_timeout_seconds: 30,
      arbiter_timeout_seconds: 20,
      analysis_timeout_seconds: 60,
      agent_max_rounds: 5,
      document_max_length: 12000,
      statuses: {
        analysis: ['queued', 'running', 'completed', 'degraded', 'failed', 'interrupted'],
        expert: ['pending', 'running', 'completed', 'error', 'timeout'],
      },
    },
    tool_configuration: {
      enabled_tools: [
        'measure_current_document',
        'find_security_indicators_in_current_document',
        'estimate_current_analysis_cost',
      ],
      disabled_tools: [],
    },
  }
}

describe('isTerminalAnalysisStatus / isTerminalEventType', () => {
  it('reconnaît les états terminaux de l’analyse', () => {
    expect(isTerminalAnalysisStatus('completed')).toBe(true)
    expect(isTerminalAnalysisStatus('degraded')).toBe(true)
    expect(isTerminalAnalysisStatus('failed')).toBe(true)
    expect(isTerminalAnalysisStatus('interrupted')).toBe(true)
    expect(isTerminalAnalysisStatus('running')).toBe(false)
  })

  it('reconnaît les événements terminaux', () => {
    expect(isTerminalEventType('analysis.completed')).toBe(true)
    expect(isTerminalEventType('analysis.failed')).toBe(true)
    expect(isTerminalEventType('tool.started')).toBe(false)
  })
})

describe('appendAnalysisEvent / isIdempotentDuplicate', () => {
  it('ignore un doublon par identifiant', () => {
    const event = makeEvent({ id: 4, type: 'tool.started' })
    const once = appendAnalysisEvent([], event)
    const twice = appendAnalysisEvent(once, event)
    expect(twice).toHaveLength(1)
    expect(isIdempotentDuplicate(once, event)).toBe(true)
  })

  it('ignore un delta SSE dupliqué par identifiant (rejeu/reconnexion)', () => {
    const delta = makeEvent({
      id: 7,
      type: 'agent.response.delta',
      payload: { analysis_id: 'a', role: 'avocat', sequence: 3, delta: 'texte' },
    })
    const once = appendAnalysisEvent([], delta)
    const twice = appendAnalysisEvent(once, delta)
    expect(twice).toHaveLength(1)
    expect(isIdempotentDuplicate(once, delta)).toBe(true)
  })

  it('conserve les événements dans l’ordre reçu', () => {
    const first = makeEvent({ id: 1 })
    const second = makeEvent({ id: 2, type: 'tool.completed' })
    const result = appendAnalysisEvent(appendAnalysisEvent([], first), second)
    expect(result.map((e) => e.id)).toEqual([1, 2])
  })
})

describe('calculateActiveStep', () => {
  it('renvoie l’étape 0 sans analyse', () => {
    expect(calculateActiveStep(null, [])).toBe(0)
  })

  it('progresse étape par étape selon les événements observés', () => {
    const snapshot = runningSnapshot()
    expect(calculateActiveStep(snapshot, [])).toBe(1)

    const started = [makeEvent({ type: 'expert.started', payload: { role: 'avocat' } })]
    expect(calculateActiveStep(snapshot, started)).toBe(2)

    const completed = [
      makeEvent({ type: 'expert.started', payload: { role: 'avocat' } }),
      makeEvent({ type: 'expert.completed', payload: { role: 'avocat' } }),
    ]
    expect(calculateActiveStep(snapshot, completed)).toBe(3)

    const arbitrated = [
      ...completed,
      makeEvent({ type: 'arbiter.started', payload: {} }),
    ]
    expect(calculateActiveStep(snapshot, arbitrated)).toBe(4)
  })

  it('passe à l’étape finale quand l’événement terminal est observé', () => {
    const snapshot = runningSnapshot()
    snapshot.status = 'degraded'
    const terminal = [makeEvent({ type: 'analysis.degraded', payload: {} })]
    expect(calculateActiveStep(snapshot, terminal)).toBe(5)
  })

  it('un snapshot terminal reçu trop tôt (sans son événement) ne force pas l’étape finale', () => {
    // R1 : le statut du snapshot seul ne doit jamais faire sauter le
    // stepper à la fin — seul l'événement terminal observé le fait.
    const snapshot = runningSnapshot()
    snapshot.status = 'degraded'
    expect(calculateActiveStep(snapshot, [])).toBe(1)
    const started = [makeEvent({ type: 'expert.started', payload: { role: 'avocat' } })]
    expect(calculateActiveStep(snapshot, started)).toBe(2)
  })
})

describe('collectToolCalls', () => {
  it('assemble début et fin d’un appel d’outil', () => {
    const events = [
      makeEvent({
        id: 1,
        type: 'tool.started',
        payload: { agent_role: 'comptable', llm_round: 1, tool_name: 'measure_current_document' },
      }),
      makeEvent({
        id: 2,
        type: 'tool.completed',
        payload: { agent_role: 'comptable', llm_round: 1, tool_name: 'measure_current_document' },
      }),
      makeEvent({
        id: 3,
        type: 'tool.started',
        payload: { agent_role: 'comptable', llm_round: 2, tool_name: 'estimate_current_analysis_cost' },
      }),
      makeEvent({
        id: 4,
        type: 'tool.failed',
        payload: { agent_role: 'comptable', llm_round: 2, tool_name: 'estimate_current_analysis_cost' },
      }),
    ]
    const calls = collectToolCalls(events)
    expect(calls).toHaveLength(2)
    expect(calls[0].status).toBe('success')
    expect(calls[1].status).toBe('error')
  })
})

describe('liveExpertRun', () => {
  it('suit un expert de pending à running puis completed', () => {
    const run = { role: 'avocat' as const, status: 'pending' as const, error_code: null }
    expect(liveExpertRun(run, []).status).toBe('pending')
    expect(
      liveExpertRun(run, [makeEvent({ type: 'expert.started', payload: { role: 'avocat' } })]).status,
    ).toBe('running')
    expect(
      liveExpertRun(run, [
        makeEvent({ type: 'expert.started', payload: { role: 'avocat' } }),
        makeEvent({ type: 'expert.completed', payload: { role: 'avocat' } }),
      ]).status,
    ).toBe('completed')
  })

  it('ignore les événements des autres rôles', () => {
    const run = { role: 'procureur' as const, status: 'pending' as const, error_code: null }
    expect(
      liveExpertRun(run, [makeEvent({ type: 'expert.started', payload: { role: 'avocat' } })]).status,
    ).toBe('pending')
  })
})