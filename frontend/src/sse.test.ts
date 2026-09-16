import { afterEach, describe, expect, it, vi } from 'vitest'
import { dispatchEvent, openAnalysisEventSource } from './api/sse'
import { SSE_EVENT_TYPES } from './validation'
import type { AnalysisEvent } from './types'

function payloadFor(type: string): Record<string, unknown> {
  if (type === 'agent.response.delta') {
    return { analysis_id: 'a1', role: 'avocat', sequence: 1, delta: 'ok' }
  }
  if (type === 'agent.response.started' || type === 'agent.response.completed') {
    return { analysis_id: 'a1', role: 'avocat' }
  }
  if (type === 'agent.response.failed') {
    return { analysis_id: 'a1', role: 'avocat', error_code: 'protocol_error' }
  }
  return { analysis_id: 'a1' }
}

describe('dispatchEvent — parseur SSE correct', () => {
  it('délivre réellement CHAQUE type du contrat SSE', () => {
    const received: AnalysisEvent[] = []
    const malformed: string[] = []
    SSE_EVENT_TYPES.forEach((type, index) => {
      const raw =
        `id: ${index + 1}\n` +
        `event: ${type}\n` +
        `data: ${JSON.stringify(payloadFor(type))}\n`
      dispatchEvent(raw, (event) => received.push(event), (d) => malformed.push(d))
    })
    expect(malformed).toEqual([])
    expect(received.map((event) => event.type)).toEqual([...SSE_EVENT_TYPES])
  })

  it('reconstitue un data multi-lignes', () => {
    const received: AnalysisEvent[] = []
    dispatchEvent(
      'id: 7\nevent: analysis.created\ndata: {"analysis_id":\ndata: "a1"}',
      (event) => received.push(event),
      () => {},
    )
    expect(received).toHaveLength(1)
    expect(received[0].payload).toEqual({ analysis_id: 'a1' })
  })

  it('ignore les commentaires et remonte un JSON invalide', () => {
    const malformed: string[] = []
    dispatchEvent(': keep-alive', () => {}, (d) => malformed.push(d))
    dispatchEvent('id: 1\nevent: analysis.created\ndata: {oops', () => {}, (d) =>
      malformed.push(d),
    )
    expect(malformed).toEqual(['Le serveur a envoyé un événement non-JSON.'])
  })
})

describe('openAnalysisEventSource — fetch + ReadableStream', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lit un flux SSE et transmet les événements', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'id: 1\nevent: analysis.created\ndata: {"analysis_id":"a1"}\n\n',
          ),
        )
        controller.enqueue(
          encoder.encode(
            'id: 2\nevent: analysis.completed\ndata: {"analysis_id":"a1","status":"completed"}\n\n',
          ),
        )
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(body, { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const events: AnalysisEvent[] = []
    const opened = { value: false }
    const errored = { value: false }
    openAnalysisEventSource('a1', 0, {
      onOpen: () => {
        opened.value = true
      },
      onEvent: (event) => events.push(event),
      onMalformed: () => {},
      onError: () => {
        errored.value = true
      },
    })

    await vi.waitFor(() => expect(events.length).toBe(2))
    expect(opened.value).toBe(true)
    expect(events[0].type).toBe('analysis.created')
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/analyses/a1/events?after=0'),
      expect.objectContaining({ credentials: 'include' }),
    )
    // Fin de flux après un événement terminal : pas d'erreur parasite.
    expect(errored.value).toBe(true)
  })
})
