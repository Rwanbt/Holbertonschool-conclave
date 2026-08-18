import { useState, type FormEvent } from 'react'
import { sendMessage } from './api/client'
import type { ApiError, UiState } from './types'
import { isNonEmptyTrimmed } from './utils'
import './App.css'

function App() {
  const [message, setMessage] = useState('')
  const [uiState, setUiState] = useState<UiState>({ status: 'idle' })

  const isRequesting = uiState.status === 'loading'
  const canSubmit = !isRequesting && isNonEmptyTrimmed(message)

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()

    const trimmedMessage = message.trim()
    if (!isNonEmptyTrimmed(trimmedMessage) || isRequesting) {
      return
    }

    setUiState({ status: 'loading' })

    try {
      const response = await sendMessage(trimmedMessage)
      setUiState({
        status: 'success',
        answer: response.answer,
        model: response.model,
      })
    } catch (error) {
      setUiState({ status: 'error', message: toErrorMessage(error) })
    }
  }

  return (
    <main className="conclave">
      <h1>CONCLAVE</h1>
      <p className="tagline">Un message, une réponse réelle du modèle MiniMax-M3.</p>

      <form className="conclave-form" onSubmit={handleSubmit}>
        <label htmlFor="message">Votre message</label>
        <textarea
          id="message"
          name="message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Écrivez ici un message à envoyer au Conclave…"
          rows={5}
          disabled={isRequesting}
        />
        <button type="submit" disabled={!canSubmit}>
          Tester le Conclave
        </button>
      </form>

      <section className="conclave-status" role="status" aria-live="polite">
        {uiState.status === 'idle' && (
          <p className="status-idle">Prêt. Envoyez un message pour tester.</p>
        )}
        {uiState.status === 'loading' && (
          <p className="status-loading">Appel en cours auprès du backend…</p>
        )}
        {uiState.status === 'error' && (
          <p className="status-error">{uiState.message}</p>
        )}
        {uiState.status === 'success' && (
          <div className="status-success">
            <p className="model-label">Réponse du modèle {uiState.model}</p>
            <p className="answer">{uiState.answer}</p>
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