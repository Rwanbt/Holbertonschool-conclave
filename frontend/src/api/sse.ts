import type { AnalysisEvent } from '../types'
import { parseAnalysisEvent, ResponseValidationError } from '../validation'

const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

const BASE_URL: string = API_BASE_URL.replace(/\/+$/, '')

export interface SseHandlers {
  onOpen: () => void
  onEvent: (event: AnalysisEvent) => void
  onMalformed: (detail: string) => void
  onError: () => void
}

export interface SseClient {
  close: () => void
}

export function openAnalysisEventSource(
  analysisId: string,
  afterEventId: number,
  handlers: SseHandlers,
): SseClient {
  const url = `${BASE_URL}/api/analyses/${analysisId}/events?after=${Math.max(0, afterEventId)}`
  const source = new EventSource(url)

  const onOpen = (): void => handlers.onOpen()
  const onError = (): void => handlers.onError()

  const onMessage = (message: MessageEvent<string>): void => {
    let data: unknown
    try {
      data = JSON.parse(message.data)
    } catch {
      handlers.onMalformed('Le serveur a envoyé un événement non-JSON.')
      return
    }
    try {
      const id = readEventId(message)
      const event: AnalysisEvent = parseAnalysisEvent(id, message.type, data)
      handlers.onEvent(event)
    } catch (error) {
      handlers.onMalformed(
        error instanceof ResponseValidationError
          ? error.message
          : 'Événement SSE invalide.',
      )
    }
  }

  source.addEventListener('message', onMessage)
  source.addEventListener('analysis.created', onMessage)
  source.addEventListener('expert.started', onMessage)
  source.addEventListener('tool.started', onMessage)
  source.addEventListener('tool.completed', onMessage)
  source.addEventListener('tool.failed', onMessage)
  source.addEventListener('expert.completed', onMessage)
  source.addEventListener('expert.failed', onMessage)
  source.addEventListener('expert.timeout', onMessage)
  source.addEventListener('arbiter.started', onMessage)
  source.addEventListener('arbiter.completed', onMessage)
  source.addEventListener('arbiter.failed', onMessage)
  source.addEventListener('analysis.completed', onMessage)
  source.addEventListener('analysis.degraded', onMessage)
  source.addEventListener('analysis.failed', onMessage)
  source.addEventListener('analysis.interrupted', onMessage)

  source.onopen = onOpen
  source.onerror = onError

  return {
    close: () => source.close(),
  }
}

function readEventId(message: MessageEvent): number {
  // Repli : le serveur écrit `id: <entier>` ; le repli 0 préserve l'ordre.
  const raw = message.lastEventId
  if (raw === '') {
    return 0
  }
  const parsed = Number.parseInt(raw, 10)
  if (Number.isNaN(parsed)) {
    return 0
  }
  return parsed
}