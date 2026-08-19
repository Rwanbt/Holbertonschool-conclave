import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAnalysisSnapshot } from './api/client'
import { openAnalysisEventSource, type SseClient } from './api/sse'
import {
  readStoredLastEventId,
  writeStoredLastEventId,
} from './storage'
import { appendAnalysisEvent, isTerminalEventType } from './steps'
import type { AnalysisEvent, AnalysisSnapshot } from './types'
import { isApiError } from './errors'

export type AnalysisConnection =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'live' }
  | { status: 'reconnecting' }
  | { status: 'error'; message: string }

export interface AnalysisController {
  snapshot: AnalysisSnapshot | null
  events: readonly AnalysisEvent[]
  connection: AnalysisConnection
  malformedMessage: string | null
  lastEventId: number
}

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
  const eventsRef = useRef<readonly AnalysisEvent[]>([])
  const disconnectedRef = useRef(false)

  snapshotRef.current = snapshot
  eventsRef.current = events

  const loadSnapshot = useCallback(
    async (id: string): Promise<void> => {
      setConnection({ status: 'loading' })
      try {
        const next = await fetchAnalysisSnapshot(id)
        snapshotRef.current = next
        setSnapshot(next)
        if (disconnectedRef.current) {
          setConnection({ status: 'live' })
        }
      } catch (error) {
        if (isApiError(error)) {
          if (error.kind === 'http' && error.status === 404) {
            setConnection({
              status: 'error',
              message: 'Analyse introuvable : la référence locale a été nettoyée.',
            })
            onNotFound?.()
            return
          }
          setConnection({
            status: 'error',
            message: error.message,
          })
        } else {
          setConnection({
            status: 'error',
            message: 'Une erreur inattendue s\'est produite au rechargement.',
          })
        }
      }
    },
    [onNotFound],
  )

  const openStream = useCallback(
    (id: string) => {
      const after = readStoredLastEventId(id)
      const client = openAnalysisEventSource(id, after, {
        onOpen: () => {
          disconnectedRef.current = false
          setConnection({ status: 'live' })
        },
        onEvent: (event: AnalysisEvent) => {
          const snapshotNow = snapshotRef.current
          if (snapshotNow !== null && event.payload.analysis_id !== snapshotNow.analysis_id) {
            return
          }
          setEvents((previous) => {
            const next = appendAnalysisEvent(previous, event)
            eventsRef.current = next
            return next
          })
          if (event.id > 0) {
            writeStoredLastEventId(id, event.id)
            setLastEventId(event.id)
          }
          if (isTerminalEventType(event.type)) {
            client.close()
            void loadSnapshot(id)
            return
          }
          if (
            event.type === 'expert.completed' ||
            event.type === 'expert.failed' ||
            event.type === 'expert.timeout' ||
            event.type === 'arbiter.completed' ||
            event.type === 'arbiter.failed'
          ) {
            void loadSnapshot(id)
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
    },
    [loadSnapshot],
  )

  useEffect(() => {
    const id = analysisId
    if (id === null || id === '') {
      sseRef.current?.close()
      sseRef.current = null
      setSnapshot(null)
      snapshotRef.current = null
      setEvents([])
      eventsRef.current = []
      setMalformedMessage(null)
      setLastEventId(0)
      setConnection({ status: 'idle' })
      return
    }

    setMalformedMessage(null)
    disconnectedRef.current = false
    void loadSnapshot(id)
    openStream(id)

    return () => {
      sseRef.current?.close()
      sseRef.current = null
    }
  }, [analysisId, loadSnapshot, openStream])

  return { snapshot, events, connection, malformedMessage, lastEventId }
}