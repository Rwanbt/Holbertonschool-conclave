import { describe, expect, it } from 'vitest'
import {
  parseAgentResponseCompletedPayload,
  parseAgentResponseDeltaPayload,
  parseAgentResponseFailedPayload,
  parseAgentResponseStartedPayload,
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
    security: { prompt_injection_suspected: false, signals: [] },
  }
}

describe('parseAnalysisCreated', () => {
  it('accepte une création valide', () => {
    const parsed = parseAnalysisCreated({
      analysis_id: 'xx',
      status: 'queued',
      created_at: '2026-08-19T10:00:00+00:00',
      tool_configuration: {
        enabled_tools: ['measure_current_document'],
        disabled_tools: [
          'find_security_indicators_in_current_document',
          'estimate_current_analysis_cost',
        ],
      },
      security: {
        prompt_injection_suspected: true,
        signals: ['override_instructions'],
      },
    })
    expect(parsed.analysis_id).toBe('xx')
    expect(parsed.status).toBe('queued')
    expect(parsed.tool_configuration.enabled_tools).toEqual(['measure_current_document'])
    expect(parsed.security.prompt_injection_suspected).toBe(true)
    expect(parsed.security.signals).toEqual(['override_instructions'])
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

  it('accepte llm_rounds=0 (analyse queued, aucun appel MiniMax encore effectué)', () => {
    const fixture = snapshotFixture() as Record<string, unknown>
    fixture.status = 'queued'
    fixture.verdict = null
    fixture.started_at = null
    fixture.completed_at = null
    ;(fixture.avocat as Record<string, unknown>).status = 'pending'
    ;(fixture.avocat as Record<string, unknown>).output = null
    ;(fixture.procureur as Record<string, unknown>).status = 'pending'
    ;(fixture.procureur as Record<string, unknown>).output = null
    ;(fixture.comptable as Record<string, unknown>).status = 'pending'
    ;(fixture.comptable as Record<string, unknown>).output = null
    fixture.usage = {
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      estimated_cost_usd: null,
      total_latency_ms: 0,
      llm_rounds: 0,
    }
    const parsed = parseAnalysisSnapshot(fixture)
    expect(parsed.status).toBe('queued')
    expect(parsed.usage.llm_rounds).toBe(0)
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
  it('accepte une réponse list complète avec catalogue', () => {
    const parsed = parseToolCommandResponse({
      action: 'list',
      message: 'Catalogue des outils.',
      tool_name: null,
      enabled: null,
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
    expect(parsed.action).toBe('list')
    expect(parsed.tool_name).toBeNull()
    expect(parsed.enabled).toBeNull()
    expect(parsed.tools).toHaveLength(3)
  })

  it('accepte une réponse enable avec l’outil visé et le catalogue', () => {
    const parsed = parseToolCommandResponse({
      action: 'enable',
      message: 'Outil activé.',
      tool_name: 'measure_current_document',
      enabled: true,
      tools: [
        { tool_name: 'measure_current_document', enabled: true, description: 'a' },
      ],
    })
    expect(parsed.tool_name).toBe('measure_current_document')
    expect(parsed.enabled).toBe(true)
  })

  it('rejette une action inconnue', () => {
    expect(() =>
      parseToolCommandResponse({
        action: 'toggle' as never,
        message: 'x',
        tool_name: null,
        enabled: null,
        tools: [],
      }),
    ).toThrow(ResponseValidationError)
  })

  it('rejette un catalogue de plus de trois outils', () => {
    const tools = [
      { tool_name: 'measure_current_document', enabled: true, description: 'a' },
      {
        tool_name: 'find_security_indicators_in_current_document',
        enabled: true,
        description: 'b',
      },
      { tool_name: 'estimate_current_analysis_cost', enabled: true, description: 'c' },
      { tool_name: 'measure_current_document', enabled: true, description: 'd' },
    ]
    expect(() =>
      parseToolCommandResponse({
        action: 'list',
        message: 'x',
        tool_name: null,
        enabled: null,
        tools,
      }),
    ).toThrow(ResponseValidationError)
  })

  it('rejette un tool_name inconnu', () => {
    expect(() =>
      parseToolCommandResponse({
        action: 'disable',
        message: 'x',
        tool_name: 'measure_nothing',
        enabled: false,
        tools: [],
      }),
    ).toThrow(ResponseValidationError)
  })
})

describe('événements live agent.response.*', () => {
  const deltaPayload = {
    analysis_id: 'abc',
    role: 'avocat',
    sequence: 3,
    delta: 'le texte',
  }

  it('accepte les quatre événements via parseAnalysisEvent', () => {
    const started = parseAnalysisEvent(1, 'agent.response.started', {
      analysis_id: 'abc',
      role: 'procureur',
    })
    expect(started.type).toBe('agent.response.started')

    const delta = parseAnalysisEvent(2, 'agent.response.delta', deltaPayload)
    expect(delta.type).toBe('agent.response.delta')
    expect(delta.payload.sequence).toBe(3)
    expect(delta.payload.delta).toBe('le texte')

    const completed = parseAnalysisEvent(3, 'agent.response.completed', {
      analysis_id: 'abc',
      role: 'comptable',
    })
    expect(completed.type).toBe('agent.response.completed')

    const failed = parseAnalysisEvent(4, 'agent.response.failed', {
      analysis_id: 'abc',
      role: 'arbitre',
      error_code: 'protocol_error',
    })
    expect(failed.type).toBe('agent.response.failed')
    expect(failed.payload.error_code).toBe('protocol_error')
  })

  it('valide directement les payloads dédiés', () => {
    expect(parseAgentResponseStartedPayload({ analysis_id: 'a', role: 'avocat' }).role).toBe(
      'avocat',
    )
    expect(
      parseAgentResponseDeltaPayload(deltaPayload).sequence,
    ).toBe(3)
    expect(
      parseAgentResponseCompletedPayload({ analysis_id: 'a', role: 'arbitre' }).role,
    ).toBe('arbitre')
    expect(
      parseAgentResponseFailedPayload({
        analysis_id: 'a',
        role: 'comptable',
        error_code: 'expert_timeout',
      }).error_code,
    ).toBe('expert_timeout')
  })

  it('rejette un rôle inconnu dans un delta', () => {
    expect(() =>
      parseAnalysisEvent(2, 'agent.response.delta', {
        ...deltaPayload,
        role: 'notaire',
      }),
    ).toThrow(ResponseValidationError)
  })

  it('rejette une séquence non strictement positive', () => {
    expect(() =>
      parseAnalysisEvent(2, 'agent.response.delta', { ...deltaPayload, sequence: 0 }),
    ).toThrow(ResponseValidationError)
    expect(() =>
      parseAnalysisEvent(2, 'agent.response.delta', { ...deltaPayload, sequence: -1 }),
    ).toThrow(ResponseValidationError)
    expect(() =>
      parseAnalysisEvent(2, 'agent.response.delta', { ...deltaPayload, sequence: 1.5 }),
    ).toThrow(ResponseValidationError)
  })

  it('rejette un delta vide', () => {
    expect(() =>
      parseAnalysisEvent(2, 'agent.response.delta', { ...deltaPayload, delta: '' }),
    ).toThrow(ResponseValidationError)
  })

  it('rejette un delta dépassant la borne du contrat', () => {
    expect(() =>
      parseAnalysisEvent(2, 'agent.response.delta', {
        ...deltaPayload,
        delta: 'x'.repeat(513),
      }),
    ).toThrow(ResponseValidationError)
  })

  it("rejette un analysis_id vide", () => {
    expect(() =>
      parseAnalysisEvent(2, 'agent.response.delta', {
        ...deltaPayload,
        analysis_id: '',
      }),
    ).toThrow(ResponseValidationError)
  })

  it("rejette un failed sans error_code", () => {
    expect(() =>
      parseAnalysisEvent(4, 'agent.response.failed', {
        analysis_id: 'abc',
        role: 'arbitre',
        error_code: '',
      }),
    ).toThrow(ResponseValidationError)
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