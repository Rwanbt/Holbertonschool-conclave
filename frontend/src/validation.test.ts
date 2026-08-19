import { describe, expect, it } from 'vitest'
import {
  parseAnalysisCreated,
  parseAnalysisEvent,
  parseAnalysisSnapshot,
  parseAgentOutput,
  parseArbiterVerdict,
  parseToolCatalogResponse,
  parseToolCommandResponse,
  ResponseValidationError,
} from './validation'

function snapshotFixture() {
  return {
    analysis_id: 'abc123',
    document: 'Un document officiel.',
    status: 'completed',
    created_at: '2026-08-19T10:00:00+00:00',
    started_at: '2026-08-19T10:00:01+00:00',
    completed_at: '2026-08-19T10:00:42+00:00',
    error_code: null,
    avocat: {
      role: 'avocat',
      status: 'completed',
      output: {
        role: 'avocat',
        summary: 'La solution est défendable.',
        findings: [
          {
            title: 'Périmètre pilote',
            evidence: 'Section 2',
            impact: 'Risque limité',
            priority: 'high',
          },
          {
            title: 'Validation humaine',
            evidence: 'Section 4',
            impact: 'Garde-fou utile',
            priority: 'medium',
          },
        ],
        score_label: 'Solide',
        score: 78,
        recommendations: ['Conserver le pilote'],
        unavailable_tools: [],
      },
      error_code: null,
    },
    procureur: {
      role: 'procureur',
      status: 'completed',
      output: {
        role: 'procureur',
        summary: 'Des accès trop larges.',
        findings: [
          {
            title: 'Accès',
            evidence: 'Section 6',
            impact: 'Exposition',
            priority: 'high',
          },
          {
            title: 'Données sensibles',
            evidence: 'Section 7',
            impact: 'Vie privée',
            priority: 'medium',
          },
        ],
        score_label: 'Risqué',
        score: 41,
        recommendations: [],
        unavailable_tools: [],
      },
      error_code: null,
    },
    comptable: {
      role: 'comptable',
      status: 'completed',
      output: {
        role: 'comptable',
        summary: 'Un coût modéré.',
        findings: [
          {
            title: 'OCR',
            evidence: 'Section 8',
            impact: 'Complexité',
            priority: 'medium',
          },
          {
            title: 'RAG',
            evidence: 'Section 9',
            impact: 'Maintenance',
            priority: 'low',
          },
        ],
        score_label: 'Acceptable',
        score: 66,
        recommendations: [],
        unavailable_tools: [],
      },
      error_code: null,
    },
    verdict: {
      decision: 'go_with_conditions',
      score: 72,
      main_disagreement: 'Périmètre pilote vs accès larges.',
      priority_risks: ['Accès trop larges'],
      actions: ['Restreindre les accès', 'Minimiser les données'],
      accepted_tradeoff: 'Réduire la première version.',
      unavailable_agents: [],
    },
    usage: {
      input_tokens: 100,
      output_tokens: 50,
      total_tokens: 150,
      estimated_cost_usd: 0.3,
      total_latency_ms: 12000,
      llm_rounds: 4,
    },
    guardrails: {
      expert_timeout_seconds: 30,
      arbiter_timeout_seconds: 20,
      analysis_timeout_seconds: 60,
      agent_max_rounds: 5,
      document_max_length: 12000,
      statuses: {
        analysis: ['running', 'completed', 'degraded', 'failed', 'interrupted'],
        expert: ['pending', 'running', 'completed', 'error', 'timeout'],
      },
    },
  }
}

describe('parseAnalysisCreated', () => {
  it('accepte une création valide', () => {
    const parsed = parseAnalysisCreated({
      analysis_id: 'xx',
      status: 'running',
      created_at: '2026-08-19T10:00:00+00:00',
    })
    expect(parsed.analysis_id).toBe('xx')
    expect(parsed.status).toBe('running')
  })

  it('rejette un statut inconnu', () => {
    expect(() =>
      parseAnalysisCreated({ analysis_id: 'x', status: 'paused', created_at: 't' }),
    ).toThrow(ResponseValidationError)
  })
})

describe('parseAnalysisSnapshot', () => {
  it('accepte un snapshot complet', () => {
    const parsed = parseAnalysisSnapshot(snapshotFixture())
    expect(parsed.status).toBe('completed')
    expect(parsed.avocat.output?.score).toBe(78)
    expect(parsed.verdict?.decision).toBe('go_with_conditions')
    expect(parsed.guardrails.expert_timeout_seconds).toBe(30)
  })

  it('rejette un snapshot sans verdict invalide', () => {
    const fixture = snapshotFixture()
    fixture.verdict = { ...fixture.verdict, decision: 'probably' } as never
    expect(() => parseAnalysisSnapshot(fixture)).toThrow(ResponseValidationError)
  })

  it('accepte des champs nuls (analyse en cours)', () => {
    const fixture = snapshotFixture() as Record<string, unknown>
    fixture.status = 'running'
    fixture.verdict = null
    fixture.started_at = null
    fixture.completed_at = null
    ;(fixture.avocat as Record<string, unknown>).status = 'running'
    ;(fixture.avocat as Record<string, unknown>).output = null
    expect(() => parseAnalysisSnapshot(fixture)).not.toThrow()
  })
})

describe('parseAgentOutput', () => {
  it('rejette un score hors bornes', () => {
    const output = snapshotFixture().avocat.output as Record<string, unknown>
    expect(() =>
      parseAgentOutput({ ...output, score: 150 }),
    ).toThrow(ResponseValidationError)
  })

  it('rejette plus de 3 recommandations', () => {
    const output = snapshotFixture().avocat.output as Record<string, unknown>
    expect(() =>
      parseAgentOutput({ ...output, recommendations: ['a', 'b', 'c', 'd'] }),
    ).toThrow(ResponseValidationError)
  })
})

describe('parseArbiterVerdict', () => {
  it('rejette plus de 3 actions', () => {
    const verdict = snapshotFixture().verdict as Record<string, unknown>
    expect(() =>
      parseArbiterVerdict({ ...verdict, actions: ['a', 'b', 'c', 'd'] }),
    ).toThrow(ResponseValidationError)
  })

  it('rejette un rôle inconnu dans unavailable_agents', () => {
    const verdict = snapshotFixture().verdict as Record<string, unknown>
    expect(() =>
      parseArbiterVerdict({ ...verdict, unavailable_agents: ['notaire'] }),
    ).toThrow(ResponseValidationError)
  })
})

describe('parseToolCatalogResponse', () => {
  it('accepte les trois outils avec leur état', () => {
    const parsed = parseToolCatalogResponse({
      tools: [
        { tool_name: 'measure_current_document', enabled: true, description: 'a' },
        {
          tool_name: 'find_security_indicators_in_current_document',
          enabled: false,
          description: 'b',
        },
        { tool_name: 'estimate_current_analysis_cost', enabled: true, description: 'c' },
      ],
    })
    expect(parsed.tools).toHaveLength(3)
    expect(parsed.tools[1].enabled).toBe(false)
  })

  it('rejette un nom d’outil inconnu', () => {
    expect(() =>
      parseToolCatalogResponse({
        tools: [{ tool_name: 'measure_nothing', enabled: true, description: 'x' }],
      }),
    ).toThrow(ResponseValidationError)
  })
})

describe('parseToolCommandResponse', () => {
  it('accepte une réponse booléenne propre', () => {
    const parsed = parseToolCommandResponse({
      tool_name: 'measure_current_document',
      enabled: false,
    })
    expect(parsed.enabled).toBe(false)
  })
})

describe('parseAnalysisEvent', () => {
  it('accepte un événement connu', () => {
    const event = parseAnalysisEvent(3, 'expert.started', {
      analysis_id: 'x',
      role: 'avocat',
      started_at: '2026-08-19T10:00:01+00:00',
    })
    expect(event.id).toBe(3)
    expect(event.type).toBe('expert.started')
  })

  it('rejette un identifiant non entier', () => {
    expect(() =>
      parseAnalysisEvent('abc' as unknown as number, 'expert.started', {}),
    ).toThrow(ResponseValidationError)
  })

  it('rejette un type d’événement inconnu (malformé ignoré)', () => {
    expect(() => parseAnalysisEvent(1, 'unknown.event', {})).toThrow(
      ResponseValidationError,
    )
  })

  it('rejette une donnée non-objet', () => {
    expect(() => parseAnalysisEvent(1, 'expert.started', [])).toThrow(
      ResponseValidationError,
    )
  })
})