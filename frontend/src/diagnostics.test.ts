import { describe, expect, it } from 'vitest'
import {
  collectFailures,
  collectRounds,
  explainErrorCode,
  outcomeLabel,
} from './diagnostics'
import { resolveTheme } from './useTheme'
import type { AnalysisEvent, AnalysisSnapshot } from './types'

function event(overrides: Partial<AnalysisEvent>): AnalysisEvent {
  return { id: 1, type: 'expert.started', payload: {}, ...overrides }
}

function snapshot(overrides: Partial<AnalysisSnapshot> = {}): AnalysisSnapshot {
  return {
    analysis_id: 'a1',
    document: 'doc',
    status: 'failed',
    created_at: 't',
    started_at: 't',
    completed_at: 't',
    error_code: null,
    avocat: { role: 'avocat', status: 'error', output: null, error_code: null },
    procureur: { role: 'procureur', status: 'error', output: null, error_code: null },
    comptable: { role: 'comptable', status: 'error', output: null, error_code: null },
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
    tool_configuration: { enabled_tools: [], disabled_tools: [] },
    security: { prompt_injection_suspected: false, signals: [] },
    ...overrides,
  }
}

describe('explainErrorCode', () => {
  it('traduit une panne fournisseur en explication actionnable', () => {
    const explanation = explainErrorCode('provider_unavailable')
    expect(explanation.what).toContain('MiniMax')
    expect(explanation.action).toContain('MINIMAX_API_KEY')
  })

  it('rend un code inconnu tel quel plutôt que de prétendre l’avoir compris', () => {
    const explanation = explainErrorCode('code_jamais_vu')
    expect(explanation.what).toContain('code_jamais_vu')
    expect(explanation.action).toBeNull()
  })
})

describe('collectFailures', () => {
  it('remonte la cause de l’analyse ET celle de chaque rôle, sans doublon', () => {
    const failures = collectFailures(
      snapshot({
        error_code: 'provider_unavailable',
        avocat: {
          role: 'avocat',
          status: 'error',
          output: null,
          error_code: 'provider_unavailable',
        },
      }),
      [
        event({
          id: 2,
          type: 'expert.failed',
          payload: { role: 'avocat', error_code: 'provider_unavailable' },
        }),
        event({
          id: 3,
          type: 'tool.failed',
          payload: {
            agent_role: 'comptable',
            tool_name: 'estimate_current_analysis_cost',
            error_code: 'missing_prerequisite',
          },
        }),
      ],
    )
    const scopes = failures.map((failure) => failure.scope)
    expect(scopes).toContain('Analyse')
    expect(scopes).toContain('Avocat')
    expect(scopes.some((scope) => scope.startsWith('Outil'))).toBe(true)
    // Le même couple portée/code n'apparaît qu'une fois.
    const keys = failures.map((f) => `${f.scope}:${f.code}`)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it('ne signale rien quand tout a abouti', () => {
    expect(collectFailures(snapshot({ status: 'completed' }), [])).toEqual([])
  })
})

describe('collectRounds', () => {
  it('associe chaque tour ouvert à sa décision et sa latence', () => {
    const rounds = collectRounds([
      event({
        id: 1,
        type: 'agent.round.started',
        payload: { role: 'comptable', round: 1, max_rounds: 5 },
      }),
      event({
        id: 2,
        type: 'agent.round.completed',
        payload: { role: 'comptable', round: 1, outcome: 'tool_calls', latency_ms: 42 },
      }),
      event({
        id: 3,
        type: 'agent.round.started',
        payload: { role: 'comptable', round: 2, max_rounds: 5 },
      }),
    ])
    expect(rounds).toHaveLength(2)
    expect(rounds[0]).toMatchObject({ round: 1, outcome: 'tool_calls', latencyMs: 42 })
    // Un tour encore ouvert reste sans décision : on ne devine pas.
    expect(rounds[1]).toMatchObject({ round: 2, outcome: null, latencyMs: null })
    expect(outcomeLabel(rounds[1].outcome)).toBe('en cours')
    expect(outcomeLabel('tool_calls')).toContain('outil')
  })
})

describe('resolveTheme', () => {
  it('suit le système tant qu’aucun choix explicite n’est fait', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })

  it('un choix explicite l’emporte sur la préférence système', () => {
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })
})
