import type { AnalysisEvent } from '../types'
import { parseAnalysisEvent, ResponseValidationError } from '../validation'
import { readStoredSessionToken } from '../storage'

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

/**
 * Lecteur SSE basé sur `fetch()` + `ReadableStream` (remplace `EventSource`).
 *
 * Objectifs : permettre un en-tête `Authorization` (et non un token dans
 * l'URL), transmettre le cookie de session (`credentials: 'include'`),
 * conserver strictement le protocole SSE serveur (`event:`, `id:`, `data:`),
 * la reprise via `after=` et l'idempotence. Le parseur ci-dessous est un
 * parseur SSE correct : multi-lignes `data`, commentaires `:`, CRLF.
 */
export function openAnalysisEventSource(
  analysisId: string,
  afterEventId: number,
  handlers: SseHandlers,
): SseClient {
  const url = `${BASE_URL}/api/analyses/${analysisId}/events?after=${Math.max(0, afterEventId)}`
  const controller = new AbortController()
  let closed = false
  let opened = false

  const emit = (event: AnalysisEvent): void => {
    if (!closed) {
      handlers.onEvent(event)
    }
  }

  const malformed = (detail: string): void => {
    if (!closed) {
      handlers.onMalformed(detail)
    }
  }

  const fail = (): void => {
    if (!closed) {
      handlers.onError()
    }
  }

  const run = async (): Promise<void> => {
    let response: Response
    try {
      const sessionToken = readStoredSessionToken()
      response = await fetch(url, {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
          ...(sessionToken !== null && sessionToken.length > 0
            ? { 'X-Session-Token': sessionToken }
            : {}),
        },
        credentials: 'include',
        signal: controller.signal,
      })
    } catch {
      if (!closed && !controller.signal.aborted) {
        fail()
      }
      return
    }

    if (!response.ok || response.body === null) {
      fail()
      return
    }

    opened = true
    handlers.onOpen()

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) {
          break
        }
        buffer += decoder.decode(value, { stream: true })
        let separator = findSeparator(buffer)
        while (separator !== -1) {
          const rawEvent = buffer.slice(0, separator)
          buffer = buffer.slice(separator + separatorLength(buffer, separator))
          dispatchEvent(rawEvent, emit, malformed)
          separator = findSeparator(buffer)
        }
      }
    } catch {
      if (!closed && !controller.signal.aborted) {
        fail()
        return
      }
      return
    }

    // Flux terminé sans clôture explicite : le serveur a fermé la connexion.
    // Si le terminal n'a pas encore été vu, on remonte l'erreur (le contrôleur
    // décide de la reconnexion) ; sinon c'est une fin normale.
    if (!closed && opened) {
      fail()
    }
  }

  void run()

  return {
    close: () => {
      closed = true
      controller.abort()
    },
  }
}

function findSeparator(buffer: string): number {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (lf === -1) {
    return crlf
  }
  if (crlf === -1) {
    return lf
  }
  return Math.min(lf, crlf)
}

function separatorLength(buffer: string, index: number): number {
  return buffer.startsWith('\r\n\r\n', index) ? 4 : 2
}

/**
 * Parse et dispatche un bloc SSE brut. Exporté pour être testé unitairement.
 */
export function dispatchEvent(
  rawEvent: string,
  onEvent: (event: AnalysisEvent) => void,
  onMalformed: (detail: string) => void,
): void {
  let eventType = 'message'
  const dataLines: string[] = []
  let id: number | null = null

  for (const line of rawEvent.split(/\r?\n/)) {
    if (line === '' || line.startsWith(':')) {
      continue
    }
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) {
      value = value.slice(1)
    }
    if (field === 'event') {
      eventType = value
    } else if (field === 'data') {
      dataLines.push(value)
    } else if (field === 'id') {
      const parsed = Number.parseInt(value, 10)
      id = Number.isNaN(parsed) ? null : parsed
    }
  }

  if (dataLines.length === 0) {
    return
  }

  let data: unknown
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    onMalformed('Le serveur a envoyé un événement non-JSON.')
    return
  }

  try {
    const event = parseAnalysisEvent(id ?? 0, eventType, data)
    onEvent(event)
  } catch (error) {
    onMalformed(
      error instanceof ResponseValidationError
        ? error.message
        : 'Événement SSE invalide.',
    )
  }
}