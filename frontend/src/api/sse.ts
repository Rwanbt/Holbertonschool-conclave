import type { AnalysisEvent } from '../types'
import { parseAnalysisEvent, ResponseValidationError, SSE_EVENT_TYPES } from '../validation'

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

  // `EventSource` ne remet un événement NOMMÉ (`event: <type>`) qu'aux
  // écouteurs enregistrés pour ce type exact : l'écouteur `message` ne reçoit
  // QUE les événements sans champ `event:`. Un type émis par le backend mais
  // absent d'ici est donc silencieusement jeté par le navigateur, sans erreur
  // ni `onMalformed` — bug invisible en test unitaire.
  //
  // La liste est donc DÉRIVÉE de `SSE_EVENT_TYPES` (la même source de vérité
  // que le validateur) au lieu d'être recopiée à la main : ajouter un type au
  // contrat l'abonne automatiquement, les deux ne peuvent plus diverger.
  source.addEventListener('message', onMessage)
  for (const eventType of SSE_EVENT_TYPES) {
    source.addEventListener(eventType, onMessage)
  }

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