import { useState, type FormEvent } from 'react'
import { runAgent } from './api/client'
import { ExecutionPanel } from './components/ExecutionPanel'
import { ToolTrace } from './components/ToolTrace'
import { EXAMPLE_REQUESTS, type ExampleRequest } from './examples'
import type { ApiError, UiState } from './types'
import { isNonEmptyTrimmed, MAX_DOCUMENT_LENGTH } from './utils'
import './App.css'

function App() {
  const [document, setDocument] = useState('')
  const [instruction, setInstruction] = useState('')
  const [uiState, setUiState] = useState<UiState>({ status: 'idle' })

  const isRequesting = uiState.status === 'loading'
  const canSubmit =
    !isRequesting &&
    isNonEmptyTrimmed(document) &&
    document.length <= MAX_DOCUMENT_LENGTH &&
    isNonEmptyTrimmed(instruction)

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()

    const trimmedDocument = document.trim()
    const trimmedInstruction = instruction.trim()
    if (
      !isNonEmptyTrimmed(trimmedDocument) ||
      trimmedDocument.length > MAX_DOCUMENT_LENGTH ||
      !isNonEmptyTrimmed(trimmedInstruction) ||
      isRequesting
    ) {
      return
    }

    setUiState({ status: 'loading' })

    try {
      const response = await runAgent(trimmedInstruction, trimmedDocument)
      setUiState({ status: 'success', response })
    } catch (error) {
      setUiState({ status: 'error', message: toErrorMessage(error) })
    }
  }

  function applyExample(example: ExampleRequest): void {
    setInstruction(example.instruction)
    setDocument(example.document)
    setUiState({ status: 'idle' })
  }

  function resetStatus(): void {
    setUiState({ status: 'idle' })
  }

  return (
    <main className="conclave">
      <h1>CONCLAVE</h1>
      <p className="tagline">
        Un document, une instruction, une réponse tracée de MiniMax-M3.
      </p>

      <form className="conclave-form" onSubmit={handleSubmit}>
        <fieldset className="conclave-field" disabled={isRequesting}>
          <legend>Document</legend>
          <textarea
            id="document"
            name="document"
            value={document}
            onChange={(event) => {
              setDocument(event.target.value)
              resetStatus()
            }}
            placeholder="Collez ici le document à analyser (1 à 12 000 caractères)."
            rows={6}
            maxLength={MAX_DOCUMENT_LENGTH}
          />
          <p className="field-counter">
            {document.length} / {MAX_DOCUMENT_LENGTH} caractères
          </p>
        </fieldset>

        <fieldset className="conclave-field" disabled={isRequesting}>
          <legend>Instruction</legend>
          <textarea
            id="instruction"
            name="instruction"
            value={instruction}
            onChange={(event) => {
              setInstruction(event.target.value)
              resetStatus()
            }}
            placeholder="Exprimez votre demande : par exemple « Calcule les métriques du document »."
            rows={3}
          />
        </fieldset>

        <button type="submit" disabled={!canSubmit}>
          Tester l&apos;agent
        </button>
      </form>

      <section className="examples" aria-label="Exemples de requêtes">
        <h2>Exemples</h2>
        <p className="examples-note">
          Une instruction naturelle suffit : le modèle choisit lui-même l’outil.
        </p>
        <ul className="examples-list">
          {EXAMPLE_REQUESTS.map((example) => (
            <li key={example.label} className="example-item">
              <div className="example-text">
                <strong>{example.label}</strong>
                <span>{example.instruction}</span>
              </div>
              <button
                type="button"
                onClick={() => applyExample(example)}
                disabled={isRequesting}
              >
                Utiliser
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="conclave-status" role="status" aria-live="polite">
        {uiState.status === 'idle' && (
          <p className="status-idle">
            Prêt. Renseignez un document et une instruction.
          </p>
        )}
        {uiState.status === 'loading' && (
          <p className="status-loading">Appel de l&apos;agent en cours…</p>
        )}
        {uiState.status === 'error' && (
          <p className="status-error">{uiState.message}</p>
        )}
        {uiState.status === 'success' && (
          <div className="status-success">
            <p className="model-label">Réponse du modèle {uiState.response.model}</p>
            <p className="answer">{uiState.response.answer}</p>
            <h2 className="panel-title">Trace des outils</h2>
            <ToolTrace trace={uiState.response.trace} />
            <h2 className="panel-title">Exécution</h2>
            <ExecutionPanel usage={uiState.response.usage} />
          </div>
        )}
      </section>
    </main>
  )
}

function isApiError(error: unknown): error is ApiError {
  if (typeof error !== 'object' || error === null) {
    return false
  }
  if (!('kind' in error) || !('message' in error)) {
    return false
  }
  const kind = error.kind
  const errorMessage = error.message
  return (
    typeof errorMessage === 'string' &&
    (kind === 'network' || kind === 'http' || kind === 'malformed')
  )
}

function toErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    return error.message
  }
  return 'Une erreur inattendue s\'est produite. Réessayez.'
}

export default App