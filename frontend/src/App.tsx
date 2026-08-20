import { useCallback, useState, type FormEvent } from 'react'
import { createAnalysis } from './api/client'
import { ArbiterLivePanel } from './components/ArbiterLivePanel'
import { ConclaveStepper } from './components/ConclaveStepper'
import { DebugPanel } from './components/DebugPanel'
import { ExpertColumn } from './components/ExpertColumn'
import { ToolsPanel } from './components/ToolsPanel'
import { VerdictPanel } from './components/VerdictPanel'
import { httpStatusOf, toErrorMessage } from './errors'
import { collectLiveResponses } from './liveResponses'
import {
  analysisIdFromUrl,
  buildUrlWithAnalysisId,
  buildUrlWithoutAnalysisId,
  clearStoredAnalysisId,
  readStoredAnalysisId,
  writeStoredAnalysisId,
} from './storage'
import { isTerminalAnalysisStatus, liveExpertRun } from './steps'
import type { AnalysisStatus } from './types'
import { useAnalysisController } from './useAnalysisController'
import { useToolCatalog } from './useToolCatalog'
import { isNonEmptyTrimmed, MAX_DOCUMENT_LENGTH } from './utils'
import './App.css'

type SubmitState =
  | { status: 'idle' }
  | { status: 'submitting' }
  | { status: 'error'; message: string }

function statusNotice(status: AnalysisStatus): string | null {
  switch (status) {
    case 'completed':
      return 'Analyse terminée : verdict validé.'
    case 'degraded':
      return 'Analyse dégradée : un expert est manquant, verdict rendu avec conditions.'
    case 'failed':
      return 'Analyse échouée : aucun verdict exploitable n’a pu être rendu.'
    case 'interrupted':
      return 'Analyse interrompue par un redémarrage du serveur.'
    case 'queued':
    case 'running':
      return null
  }
}

export default function App() {
  const [analysisId, setAnalysisId] = useState<string | null>(() => {
    const fromUrl = analysisIdFromUrl(window.location.search)
    if (fromUrl !== null) {
      return fromUrl
    }
    return readStoredAnalysisId()
  })
  const [document, setDocument] = useState('')
  const [submitState, setSubmitState] = useState<SubmitState>({ status: 'idle' })
  const [notFoundNotice, setNotFoundNotice] = useState<string | null>(null)

  const handleNotFound = useCallback(() => {
    clearStoredAnalysisId()
    setAnalysisId((current) => {
      if (current !== null) {
        history.replaceState(null, '', buildUrlWithoutAnalysisId(new URL(window.location.href)))
      }
      return null
    })
    setNotFoundNotice(
      'Analyse introuvable (404) : la référence locale a été nettoyée. Nouvelle analyse possible.',
    )
  }, [])

  const controller = useAnalysisController(analysisId, handleNotFound)
  const toolCatalog = useToolCatalog()

  const isNew = analysisId === null
  const snapshot = controller.snapshot

  const catalogReady = toolCatalog.status === 'ready' || toolCatalog.status === 'mutating'
  const catalogBlocking = toolCatalog.status === 'loading' || toolCatalog.status === 'error'
  const canSubmit =
    submitState.status !== 'submitting' &&
    isNonEmptyTrimmed(document) &&
    document.length <= MAX_DOCUMENT_LENGTH &&
    catalogReady &&
    !catalogBlocking

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!canSubmit) {
      return
    }
    setSubmitState({ status: 'submitting' })
    setNotFoundNotice(null)
    try {
      const created = await createAnalysis(document.trim())
      writeStoredAnalysisId(created.analysis_id)
      history.replaceState(null, '', buildUrlWithAnalysisId(new URL(window.location.href), created.analysis_id))
      setAnalysisId(created.analysis_id)
      setSubmitState({ status: 'idle' })
    } catch (error) {
      const status = httpStatusOf(error)
      const message =
        status === 422
          ? 'Document refusé par le backend (code 422) : vérifiez la taille et le contenu.'
          : toErrorMessage(error)
      setSubmitState({ status: 'error', message })
    }
  }

  function newAnalysis(): void {
    clearStoredAnalysisId()
    history.replaceState(null, '', buildUrlWithoutAnalysisId(new URL(window.location.href)))
    setAnalysisId(null)
    setNotFoundNotice(null)
    setSubmitState({ status: 'idle' })
    void toolCatalog.refresh()
  }

  const liveResponses = collectLiveResponses(controller.events)
  const frozenConfiguration = !isNew && snapshot !== null ? snapshot.tool_configuration : null

  return (
    <main className="conclave">
      <header className="conclave-header">
        <h1>CONCLAVE</h1>
        <p className="tagline">Trois lectures contradictoires, un verdict exploitable.</p>
        {!isNew && (
          <button type="button" className="new-analysis" onClick={newAnalysis}>
            Nouvelle analyse
          </button>
        )}
      </header>

      {notFoundNotice !== null && <p className="status-error">{notFoundNotice}</p>}

      <ConclaveStepper snapshot={snapshot} events={controller.events} />

      <ToolsPanel catalog={toolCatalog} frozenConfiguration={frozenConfiguration} />

      {isNew && (
        <section className="submit-panel" aria-label="Soumettre un document">
          {submitState.status === 'error' && (
            <p className="status-error">{submitState.message}</p>
          )}
          <form className="conclave-form" onSubmit={(event) => void handleSubmit(event)}>
            <fieldset className="conclave-field" disabled={submitState.status === 'submitting'}>
              <legend>Document</legend>
              <textarea
                id="document"
                name="document"
                value={document}
                onChange={(event) => {
                  setDocument(event.target.value)
                  setSubmitState({ status: 'idle' })
                }}
                placeholder="Collez ici le document à analyser (1 à 12 000 caractères)."
                rows={8}
                maxLength={MAX_DOCUMENT_LENGTH}
              />
              <p className="field-counter">
                {document.length} / {MAX_DOCUMENT_LENGTH} caractères
              </p>
            </fieldset>
            <button type="submit" className="convoquer" disabled={!canSubmit}>
              Convoquer le Conclave
            </button>
          </form>
        </section>
      )}

      {!isNew && (
        <>
          {snapshot === null && controller.connection.status === 'loading' && (
            <p className="status-loading">Chargement de l’analyse persistée…</p>
          )}

          {snapshot !== null && (
            <>
              {controller.connection.status === 'reconnecting' && (
                <p className="status-warning">
                  Flux d’événements interrompu : reconnexion automatique en cours…
                </p>
              )}
              {controller.connection.status === 'error' && snapshot === null && (
                <p className="status-error">{controller.connection.message}</p>
              )}
              {controller.malformedMessage !== null && (
                <p className="status-warning">
                  Événement ignoré (format inattendu) : {controller.malformedMessage}
                </p>
              )}
              {isTerminalAnalysisStatus(snapshot.status) && (
                <p className="status-notice">{statusNotice(snapshot.status)}</p>
              )}

              <section className="experts" aria-label="Les trois experts">
                <ExpertColumn
                  role="avocat"
                  live={liveResponses.avocat}
                  run={{
                    ...snapshot.avocat,
                    ...liveExpertRun(snapshot.avocat, controller.events),
                  }}
                />
                <ExpertColumn
                  role="procureur"
                  live={liveResponses.procureur}
                  run={{
                    ...snapshot.procureur,
                    ...liveExpertRun(snapshot.procureur, controller.events),
                  }}
                />
                <ExpertColumn
                  role="comptable"
                  live={liveResponses.comptable}
                  run={{
                    ...snapshot.comptable,
                    ...liveExpertRun(snapshot.comptable, controller.events),
                  }}
                />
              </section>

              {snapshot.verdict !== null ? (
                <VerdictPanel verdict={snapshot.verdict} />
              ) : (
                liveResponses.arbitre.status !== 'idle' && (
                  <ArbiterLivePanel live={liveResponses.arbitre} />
                )
              )}

              <DebugPanel
                events={controller.events}
                usage={snapshot.usage}
                connection={controller.connection}
                limitSeconds={snapshot.guardrails.analysis_timeout_seconds}
                lastEventId={controller.lastEventId}
              />
            </>
          )}
        </>
      )}
    </main>
  )
}
