import type { ApiError, LlmRequest, LlmResponse } from '../types'

const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

const LLM_ENDPOINT: string = `${API_BASE_URL.replace(/\/+$/, '')}/api/p2/llm`

export async function sendMessage(message: string): Promise<LlmResponse> {
  const requestBody: LlmRequest = { message }

  let response: Response
  try {
    response = await fetch(LLM_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    })
  } catch {
    throw {
      kind: 'network',
      message: `Impossible de joindre le backend (${API_BASE_URL}). Lancez-le puis réessayez.`,
    } satisfies ApiError
  }

  if (!response.ok) {
    throw await httpError(response)
  }

  const body: unknown = await readJson(response)

  return parseResponse(body)
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    throw {
      kind: 'malformed',
      message: 'Le serveur a répondu, mais son contenu n\'est pas un JSON valide.',
    } satisfies ApiError
  }
}

async function httpError(response: Response): Promise<ApiError> {
  const status = response.status
  const detail = await tryReadDetail(response)
  return {
    kind: 'http',
    status,
    message: detail ?? defaultStatusMessage(status),
  }
}

async function tryReadDetail(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json()
    if (isRecord(body) && typeof body.detail === 'string') {
      return body.detail
    }
  } catch {
    return undefined
  }
  return undefined
}

function parseResponse(body: unknown): LlmResponse {
  if (!isRecord(body) || typeof body.answer !== 'string' || typeof body.model !== 'string') {
    throw {
      kind: 'malformed',
      message: 'Le serveur a répondu avec un format inattendu (champs answer/model attendus).',
    } satisfies ApiError
  }
  return { answer: body.answer, model: body.model }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function defaultStatusMessage(status: number): string {
  if (status === 422) {
    return 'Saisie invalide : le backend a refusé le message (code 422).'
  }
  if (status === 500) {
    return 'Configuration serveur absente côté backend (code 500).'
  }
  if (status === 502) {
    return 'Le fournisseur MiniMax est momentanément indisponible (code 502).'
  }
  return `Le serveur a répondu avec le code HTTP ${status}.`
}