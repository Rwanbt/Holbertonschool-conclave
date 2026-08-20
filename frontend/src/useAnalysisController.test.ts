import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAnalysisController } from './useAnalysisController'
import type { AnalysisSnapshot, EventsHistoryResponse } from './types'

function baseSnapshot(overrides: Partial<AnalysisSnapshot> = {}): AnalysisSnapshot {
  return {
    analysis_id: 'a1',
    document: 'doc',
    status: 'queued',
    created_at: 't0',
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
    ...overrides,
  }
}

function emptyHistory(): EventsHistoryResponse {
  return { events: [], last_event_id: 0, has_more: false }
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  private listeners: Record<string, ((event: { data: string; lastEventId: string }) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, handler: (event: { data: string; lastEventId: string }) => void): void {
    ;(this.listeners[type] ??= []).push(handler)
  }

  close(): void {
    this.closed = true
  }

  triggerOpen(): void {
    this.onopen?.()
  }

  emit(type: string, id: number, data: Record<string, unknown>): void {
    const event = { data: JSON.stringify(data), lastEventId: String(id) }
    for (const handler of this.listeners[type] ?? []) {
      handler(event)
    }
  }
}

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

function routeFetch(
  snapshot: AnalysisSnapshot,
  history: EventsHistoryResponse,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/events/history')) {
      return jsonResponse(history)
    }
    if (url.endsWith('/start') && init?.method === 'POST') {
      return jsonResponse({ analysis_id: snapshot.analysis_id, status: 'running', already_started: false })
    }
    return jsonResponse(snapshot)
  })
}

describe('useAnalysisController', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('ouvre le SSE puis démarre l’analyse une seule fois après onopen (queued)', async () => {
    const snapshot = baseSnapshot({ status: 'queued' })
    const fetchMock = routeFetch(snapshot, emptyHistory())
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useAnalysisController('a1'))

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    expect(source.url).toContain('after=0')

    source.triggerOpen()
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u, init]) => String(u).endsWith('/start') && (init as RequestInit)?.method === 'POST')).toBe(true),
    )
    const startCallsAfterFirstOpen = fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/start')).length
    expect(startCallsAfterFirstOpen).toBe(1)

    // Une reconnexion native (nouvel onopen) ne doit PAS redéclencher /start.
    source.triggerOpen()
    await new Promise((resolve) => setTimeout(resolve, 0))
    const startCallsAfterSecondOpen = fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/start')).length
    expect(startCallsAfterSecondOpen).toBe(1)
  })

  it('n’ouvre jamais le flux SSE pour une analyse déjà terminale (F5)', async () => {
    const snapshot = baseSnapshot({ status: 'completed' })
    const history: EventsHistoryResponse = {
      events: [
        { id: 1, event_type: 'analysis.created', payload: { analysis_id: 'a1' }, created_at: 't' },
        { id: 2, event_type: 'analysis.completed', payload: { analysis_id: 'a1' }, created_at: 't' },
      ],
      last_event_id: 2,
      has_more: false,
    }
    const fetchMock = routeFetch(snapshot, history)
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAnalysisController('a1'))

    await waitFor(() => expect(result.current.connection.status).toBe('closed'))
    expect(FakeEventSource.instances).toHaveLength(0)
    expect(result.current.events.map((e) => e.type)).toEqual([
      'analysis.created',
      'analysis.completed',
    ])
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).endsWith('/start')),
    ).toBe(false)
  })

  it('reprend le flux après le dernier identifiant hydraté (analyse running rechargée)', async () => {
    const snapshot = baseSnapshot({ status: 'running', started_at: 't1' })
    const history: EventsHistoryResponse = {
      events: [
        { id: 1, event_type: 'analysis.created', payload: { analysis_id: 'a1' }, created_at: 't' },
        { id: 2, event_type: 'analysis.started', payload: { analysis_id: 'a1' }, created_at: 't' },
      ],
      last_event_id: 2,
      has_more: false,
    }
    const fetchMock = routeFetch(snapshot, history)
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useAnalysisController('a1'))

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(FakeEventSource.instances[0].url).toContain('after=2')
  })
})
