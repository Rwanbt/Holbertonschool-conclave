import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  START_FALLBACK_MS,
  START_MAX_ATTEMPTS,
  START_RETRY_DELAY_MS,
  useAnalysisController,
} from './useAnalysisController'
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
    provider_id: 'minimax',
    model_id: 'MiniMax-M3',
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
      structured_repair_attempts: 2,
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
    ...overrides,
  }
}

function emptyHistory(): EventsHistoryResponse {
  return { events: [], last_event_id: 0, has_more: false }
}

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

/** Flux SSE contrôlable : `push`/`close` pilotent la connexion fetch. */
function sseStream(): {
  response: Response
  push: (chunk: string) => void
  close: () => void
} {
  let controller: ReadableStreamDefaultController<Uint8Array>
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  const response = new Response(stream, { status: 200 })
  return {
    response,
    push: (chunk) => controller.enqueue(encoder.encode(chunk)),
    close: () => controller.close(),
  }
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
    if (url.includes('/events?after=')) {
      return sseStream().response
    }
    if (url.endsWith('/start') && init?.method === 'POST') {
      return jsonResponse({ analysis_id: snapshot.analysis_id, status: 'running', already_started: false })
    }
    return jsonResponse(snapshot)
  })
}

describe('useAnalysisController', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('ouvre le flux SSE (fetch) puis démarre l’analyse une seule fois', async () => {
    const snapshot = baseSnapshot({ status: 'queued' })
    const fetchMock = routeFetch(snapshot, emptyHistory())
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useAnalysisController('a1'))

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([u]) => String(u).includes('/events?after=0')),
      ).toBe(true),
    )
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u, init]) => String(u).endsWith('/start') && (init as RequestInit)?.method === 'POST')).toBe(true),
    )
    const startCalls = fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/start'))
    expect(startCalls).toHaveLength(1)
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
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/events?after='))).toBe(false)
    expect(result.current.events.map((e) => e.type)).toEqual([
      'analysis.created',
      'analysis.completed',
    ])
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).endsWith('/start')),
    ).toBe(false)
  })

  it('démarre quand même l’analyse si le flux SSE ne s’ouvre jamais', async () => {
    const snapshot = baseSnapshot({ status: 'queued' })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/events/history')) return jsonResponse(emptyHistory())
      if (url.includes('/events?after=')) {
        // Ne se résout JAMAIS : le flux ne s'ouvre pas.
        return new Promise(() => {}) as Promise<Response>
      }
      if (url.endsWith('/start') && init?.method === 'POST') {
        return jsonResponse({ analysis_id: snapshot.analysis_id, status: 'running', already_started: false })
      }
      return jsonResponse(snapshot)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useAnalysisController('a1'))

    const startCalls = () =>
      fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/start')).length
    expect(startCalls()).toBe(0)

    await waitFor(() => expect(startCalls()).toBe(1), {
      timeout: START_FALLBACK_MS + 2000,
    })
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

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/events?after=2'))).toBe(true),
    )
  })

  it('ne rouvre pas SSE quand l’historique est terminal mais le snapshot est en retard', async () => {
    const stale = baseSnapshot({ status: 'running', started_at: 't1' })
    const completed = baseSnapshot({
      status: 'completed',
      started_at: 't1',
      completed_at: 't2',
    })
    const history: EventsHistoryResponse = {
      events: [
        { id: 1, event_type: 'analysis.created', payload: { analysis_id: 'a1' }, created_at: 't0' },
        { id: 2, event_type: 'analysis.completed', payload: { analysis_id: 'a1' }, created_at: 't2' },
      ],
      last_event_id: 2,
      has_more: false,
    }
    let snapshotReads = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/events/history')) return jsonResponse(history)
      snapshotReads += 1
      return jsonResponse(snapshotReads === 1 ? stale : completed)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAnalysisController('a1'))

    await waitFor(() => expect(result.current.connection.status).toBe('closed'))
    await waitFor(() => expect(result.current.snapshot?.status).toBe('completed'))
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/events?after='))).toBe(false)
  })

  it('rend l’échec de /start visible après un nombre borné de tentatives', async () => {
    const snapshot = baseSnapshot({ status: 'queued' })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/events/history')) return jsonResponse(emptyHistory())
      if (url.includes('/events?after=')) return sseStream().response
      if (url.endsWith('/start') && init?.method === 'POST') {
        throw new TypeError('network down')
      }
      return jsonResponse(snapshot)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useAnalysisController('a1'))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/events?after='))).toBe(true),
    )

    await waitFor(() => expect(result.current.connection.status).toBe('error'), {
      timeout: START_RETRY_DELAY_MS * START_MAX_ATTEMPTS + 3000,
    })
    const startCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/start'))
    expect(startCalls).toHaveLength(START_MAX_ATTEMPTS)
    expect(result.current.connection).toMatchObject({
      status: 'error',
      message: expect.stringContaining('Réessayez la connexion'),
    })
  })

  it('transmet le credential BYOK au démarrage quand il est fourni', async () => {
    const snapshot = baseSnapshot({ status: 'queued' })
    const fetchMock = routeFetch(snapshot, emptyHistory())
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useAnalysisController('a1', undefined, 'sk-user-secret'))

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/start'))).toBe(true),
    )
    const startInit = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/start'))![1] as RequestInit
    expect(startInit.body).toContain('sk-user-secret')
  })
})