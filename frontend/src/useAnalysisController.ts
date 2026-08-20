import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAnalysisSnapshot, fetchEventsHistory, startAnalysis } from './api/client'
import { openAnalysisEventSource, type SseClient } from './api/sse'
import { writeStoredLastEventId } from './storage'
import { isTerminalAnalysisStatus, isTerminalEventType } from './steps'
import type { AnalysisEvent, AnalysisEventEnvelope, AnalysisSnapshot } from './types'
import { isApiError, toErrorMessage } from './errors'

export type AnalysisConnection =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'live' }
  | { status: 'closed' }
  | { status: 'reconnecting' }
  | { status: 'error'; message: string }

export interface AnalysisController {
  snapshot: AnalysisSnapshot | null
  events: readonly AnalysisEvent[]
  connection: AnalysisConnection
  malformedMessage: string | null
  lastEventId: number
  retry: () => void
}

/** Délai après lequel `/start` est déclenché même sans `onopen` (filet de
 * sécurité contre un flux SSE qui ne s'ouvrirait jamais). */
export const START_FALLBACK_MS = 3000
export const START_MAX_ATTEMPTS = 3
export const START_RETRY_DELAY_MS = 500
export const SSE_RECONNECT_TIMEOUT_MS = 10000

function wait(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs))
}

function toAnalysisEvent(envelope: AnalysisEventEnvelope): AnalysisEvent {
  return {
    id: envelope.id,
    type: envelope.event_type as AnalysisEvent['type'],
    payload: envelope.payload,
  }
}

async function loadFullHistory(analysisId: string): Promise<AnalysisEventEnvelope[]> {
  const events: AnalysisEventEnvelope[] = []
  let after = 0
  for (;;) {
    const page = await fetchEventsHistory(analysisId, after, 500)
    events.push(...page.events)
    if (!page.has_more || page.events.length === 0) {
      break
    }
    after = page.last_event_id
  }
  return events
}

/**
 * Cycle de vie R1 : au montage (nouvelle analyse tout juste créée, ou F5 sur
 * une analyse existante), on hydrate le snapshot ET l'historique JSON en
 * parallèle (`readStoredLastEventId` reste une optimisation de reprise,
 * jamais la seule source : l'historique serveur est autoritaire). Le flux
 * SSE ne rouvre jamais depuis `after=0` après hydratation, et n'est jamais
 * ouvert du tout pour une analyse déjà terminale. `POST /start` est lancé
 * après `onopen` (ou après le délai de secours), avec trois tentatives
 * bornées ; l'endpoint reste idempotent côté serveur.
 */
export function useAnalysisController(
  analysisId: string | null,
  onNotFound?: () => void,
): AnalysisController {
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null)
  const [events, setEvents] = useState<readonly AnalysisEvent[]>([])
  const [connection, setConnection] = useState<AnalysisConnection>({ status: 'idle' })
  const [malformedMessage, setMalformedMessage] = useState<string | null>(null)
  const [lastEventId, setLastEventId] = useState(0)
  const [retryNonce, setRetryNonce] = useState(0)

  const sseRef = useRef<SseClient | null>(null)
  const snapshotRef = useRef<AnalysisSnapshot | null>(null)
  const startedRef = useRef(false)
  const disconnectedRef = useRef(false)

  snapshotRef.current = snapshot

  const retry = useCallback((): void => {
    setRetryNonce((current) => current + 1)
  }, [])

  const reloadSnapshot = useCallback(
    async (id: string): Promise<void> => {
      try {
        const next = await fetchAnalysisSnapshot(id)
        snapshotRef.current = next
        setSnapshot(next)
      } catch (error) {
        if (isApiError(error) && error.kind === 'http' && error.status === 404) {
          onNotFound?.()
        }
      }
    },
    [onNotFound],
  )

  useEffect(() => {
    if (analysisId === null || analysisId === '') {
      sseRef.current?.close()
      sseRef.current = null
      setSnapshot(null)
      snapshotRef.current = null
      setEvents([])
      setMalformedMessage(null)
      setLastEventId(0)
      setConnection({ status: 'idle' })
      return
    }
    const id: string = analysisId

    let cancelled = false
    let terminalObserved = false
    let executionObserved = false
    let startFallbackTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    startedRef.current = false
    disconnectedRef.current = false
    setMalformedMessage(null)
    setConnection({ status: 'loading' })

    function clearReconnectTimer(): void {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
    }

    function startOnce(): void {
      if (startedRef.current || cancelled) {
        return
      }
      startedRef.current = true
      if (startFallbackTimer !== null) {
        clearTimeout(startFallbackTimer)
        startFallbackTimer = null
      }
      void (async () => {
        let lastError: unknown = null
        for (let attempt = 1; attempt <= START_MAX_ATTEMPTS; attempt += 1) {
          try {
            await startAnalysis(id)
            return
          } catch (error) {
            lastError = error
            if (cancelled || terminalObserved || executionObserved) return
            if (attempt < START_MAX_ATTEMPTS) {
              await wait(START_RETRY_DELAY_MS * attempt)
            }
          }
        }

        if (cancelled || terminalObserved || executionObserved) return
        startedRef.current = false
        setConnection({
          status: 'error',
          message: `Le lancement de l’analyse a échoué après ${START_MAX_ATTEMPTS} tentatives. ${toErrorMessage(lastError)} Réessayez la connexion.`,
        })
      })()
    }

    function openStream(afterEventId: number): void {
      // Le démarrage est déclenché par `onopen` (contrat R1 : le job ne
      // commence qu'une fois le navigateur réellement à l'écoute). Filet de
      // sécurité : si `onopen` n'arrive jamais — flux bloqué par un proxy,
      // limite de connexions du navigateur, EventSource indisponible — on
      // démarre quand même après un délai borné, sinon l'analyse resterait
      // `queued` indéfiniment et l'application paraîtrait figée.
      startFallbackTimer = setTimeout(() => {
        startFallbackTimer = null
        startOnce()
      }, START_FALLBACK_MS)

      const client = openAnalysisEventSource(id, afterEventId, {
        onOpen: () => {
          if (cancelled || sseRef.current !== client) return
          disconnectedRef.current = false
          clearReconnectTimer()
          setConnection({ status: 'live' })
          startOnce()
        },
        onEvent: (event: AnalysisEvent) => {
          if (cancelled || sseRef.current !== client) return
          const snapshotNow = snapshotRef.current
          if (snapshotNow !== null && event.payload.analysis_id !== snapshotNow.analysis_id) {
            return
          }
          setEvents((previous) => {
            if (previous.some((existing) => existing.id === event.id)) {
              return previous
            }
            return [...previous, event]
          })
          if (event.id > 0) {
            writeStoredLastEventId(id, event.id)
            setLastEventId(event.id)
          }
          if (event.type !== 'analysis.created') {
            executionObserved = true
          }
          if (isTerminalEventType(event.type)) {
            terminalObserved = true
            disconnectedRef.current = false
            clearReconnectTimer()
            client.close()
            sseRef.current = null
            setConnection({ status: 'closed' })
            void reloadSnapshot(id)
            return
          }
          if (
            event.type === 'expert.completed' ||
            event.type === 'expert.failed' ||
            event.type === 'expert.timeout' ||
            event.type === 'arbiter.completed' ||
            event.type === 'arbiter.failed'
          ) {
            void reloadSnapshot(id)
          }
        },
        onMalformed: (detail: string) => {
          if (cancelled || sseRef.current !== client) return
          setMalformedMessage(detail)
        },
        onError: () => {
          if (cancelled || sseRef.current !== client) return
          disconnectedRef.current = true
          setConnection({ status: 'reconnecting' })
          if (reconnectTimer === null) {
            reconnectTimer = setTimeout(() => {
              reconnectTimer = null
              if (!cancelled && disconnectedRef.current) {
                client.close()
                sseRef.current = null
                setConnection({
                  status: 'error',
                  message: 'Le flux temps réel ne répond plus. Vérifiez le backend puis réessayez la connexion.',
                })
              }
            }, SSE_RECONNECT_TIMEOUT_MS)
          }
        },
      })
      sseRef.current = client
    }

    async function bootstrap(): Promise<void> {
      let initialSnapshot: AnalysisSnapshot
      try {
        initialSnapshot = await fetchAnalysisSnapshot(id)
      } catch (error) {
        if (cancelled) return
        if (isApiError(error)) {
          if (error.kind === 'http' && error.status === 404) {
            setConnection({
              status: 'error',
              message: 'Analyse introuvable : la référence locale a été nettoyée.',
            })
            onNotFound?.()
            return
          }
          setConnection({ status: 'error', message: error.message })
        } else {
          setConnection({
            status: 'error',
            message: "Une erreur inattendue s'est produite au rechargement.",
          })
        }
        return
      }

      let history: AnalysisEventEnvelope[] = []
      try {
        history = await loadFullHistory(id)
      } catch {
        // L'historique est une optimisation d'hydratation ; son échec ne
        // doit pas empêcher d'afficher le snapshot déjà chargé.
        history = []
      }
      if (cancelled) return

      const hydrated = history.map(toAnalysisEvent)
      setEvents(hydrated)
      snapshotRef.current = initialSnapshot
      setSnapshot(initialSnapshot)
      const maxId = hydrated.length > 0 ? hydrated[hydrated.length - 1].id : 0
      setLastEventId(maxId)
      if (maxId > 0) {
        writeStoredLastEventId(id, maxId)
      }

      const historyIsTerminal = hydrated.some((event) => isTerminalEventType(event.type))
      if (isTerminalAnalysisStatus(initialSnapshot.status) || historyIsTerminal) {
        terminalObserved = true
        // Analyse déjà terminée : on affiche l'état final sans rouvrir le
        // flux (ce n'est pas une animation live à rejouer).
        setConnection({ status: 'closed' })
        if (historyIsTerminal && !isTerminalAnalysisStatus(initialSnapshot.status)) {
          // L'événement terminal peut être validé juste avant que le snapshot
          // chargé en parallèle ne soit lu. On resynchronise sans rouvrir SSE.
          void reloadSnapshot(id)
        }
        return
      }

      openStream(maxId)
    }

    void bootstrap()

    return () => {
      cancelled = true
      if (startFallbackTimer !== null) {
        clearTimeout(startFallbackTimer)
        startFallbackTimer = null
      }
      clearReconnectTimer()
      sseRef.current?.close()
      sseRef.current = null
    }
  }, [analysisId, onNotFound, reloadSnapshot, retryNonce])

  return { snapshot, events, connection, malformedMessage, lastEventId, retry }
}
