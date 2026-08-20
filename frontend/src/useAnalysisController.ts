import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAnalysisSnapshot, fetchEventsHistory, startAnalysis } from './api/client'
import { openAnalysisEventSource, type SseClient } from './api/sse'
import { writeStoredLastEventId } from './storage'
import { isTerminalAnalysisStatus, isTerminalEventType } from './steps'
import type { AnalysisEvent, AnalysisEventEnvelope, AnalysisSnapshot } from './types'
import { isApiError } from './errors'

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
}

/** Délai après lequel `/start` est déclenché même sans `onopen` (filet de
 * sécurité contre un flux SSE qui ne s'ouvrirait jamais). */
export const START_FALLBACK_MS = 3000

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
 * ouvert du tout pour une analyse déjà terminale. `POST /start` n'est
 * appelé qu'une fois `onopen` reçu, et au plus une fois par montage (il est
 * de toute façon idempotent côté serveur).
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

  const sseRef = useRef<SseClient | null>(null)
  const snapshotRef = useRef<AnalysisSnapshot | null>(null)
  const startedRef = useRef(false)
  const disconnectedRef = useRef(false)

  snapshotRef.current = snapshot

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
    let startFallbackTimer: ReturnType<typeof setTimeout> | null = null
    startedRef.current = false
    disconnectedRef.current = false
    setMalformedMessage(null)
    setConnection({ status: 'loading' })

    function startOnce(): void {
      if (startedRef.current || cancelled) {
        return
      }
      startedRef.current = true
      if (startFallbackTimer !== null) {
        clearTimeout(startFallbackTimer)
        startFallbackTimer = null
      }
      void startAnalysis(id).catch(() => {
        // Idempotent côté serveur : une erreur réseau ici n'empêche pas le
        // flux SSE de continuer à refléter l'état réel.
      })
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
          disconnectedRef.current = false
          setConnection({ status: 'live' })
          startOnce()
        },
        onEvent: (event: AnalysisEvent) => {
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
          if (isTerminalEventType(event.type)) {
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
          setMalformedMessage(detail)
        },
        onError: () => {
          disconnectedRef.current = true
          setConnection({ status: 'reconnecting' })
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

      if (isTerminalAnalysisStatus(initialSnapshot.status)) {
        // Analyse déjà terminée : on affiche l'état final sans rouvrir le
        // flux (ce n'est pas une animation live à rejouer).
        setConnection({ status: 'closed' })
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
      sseRef.current?.close()
      sseRef.current = null
    }
  }, [analysisId, onNotFound, reloadSnapshot])

  return { snapshot, events, connection, malformedMessage, lastEventId }
}
