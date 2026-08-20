import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openAnalysisEventSource } from './api/sse'
import { SSE_EVENT_TYPES } from './validation'
import type { AnalysisEvent } from './types'

class RecordingEventSource {
  static last: RecordingEventSource | null = null
  url: string
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  readonly registered = new Set<string>()
  private handlers: Record<string, ((e: { data: string; lastEventId: string; type: string }) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    RecordingEventSource.last = this
  }

  addEventListener(
    type: string,
    handler: (e: { data: string; lastEventId: string; type: string }) => void,
  ): void {
    this.registered.add(type)
    ;(this.handlers[type] ??= []).push(handler)
  }

  close(): void {}

  /** Reproduit le comportement réel du navigateur : un événement NOMMÉ n'est
   * remis qu'aux écouteurs de ce type exact ; `message` ne reçoit que les
   * événements sans champ `event:`. */
  dispatchNamed(type: string, id: number, data: Record<string, unknown>): boolean {
    const handlers = this.handlers[type]
    if (!handlers || handlers.length === 0) {
      return false
    }
    for (const handler of handlers) {
      handler({ data: JSON.stringify(data), lastEventId: String(id), type })
    }
    return true
  }
}

describe('openAnalysisEventSource — couverture des types d’événements', () => {
  beforeEach(() => {
    RecordingEventSource.last = null
    vi.stubGlobal('EventSource', RecordingEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function open(onEvent: (event: AnalysisEvent) => void = () => {}) {
    return openAnalysisEventSource('a1', 0, {
      onOpen: () => {},
      onEvent,
      onMalformed: () => {},
      onError: () => {},
    })
  }

  it('enregistre un écouteur pour CHAQUE type du contrat SSE', () => {
    open()
    const source = RecordingEventSource.last!
    const missing = SSE_EVENT_TYPES.filter((type) => !source.registered.has(type))
    // Un type émis par le backend mais non écouté est jeté SILENCIEUSEMENT par
    // le navigateur : ni erreur, ni onMalformed. C'est exactement le bug qui
    // faisait disparaître analysis.started et agent.round.* de l'interface.
    expect(missing).toEqual([])
  })

  it('délivre réellement les événements de cycle de vie et de tours agentiques', () => {
    const received: AnalysisEvent[] = []
    open((event) => received.push(event))
    const source = RecordingEventSource.last!

    const cases: Array<[string, Record<string, unknown>]> = [
      ['analysis.created', { analysis_id: 'a1' }],
      ['analysis.started', { analysis_id: 'a1', started_at: 't' }],
      ['agent.round.started', { analysis_id: 'a1', role: 'avocat', round: 1, max_rounds: 5 }],
      ['agent.round.completed', { analysis_id: 'a1', role: 'avocat', round: 1, outcome: 'final_response', latency_ms: 12 }],
      ['expert.started', { analysis_id: 'a1', role: 'avocat' }],
      ['analysis.completed', { analysis_id: 'a1', status: 'completed' }],
    ]

    cases.forEach(([type, payload], index) => {
      const delivered = source.dispatchNamed(type, index + 1, payload)
      expect(delivered, `aucun écouteur enregistré pour ${type}`).toBe(true)
    })

    expect(received.map((event) => event.type)).toEqual(cases.map(([type]) => type))
  })
})
