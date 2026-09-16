import type {
  AgentRequest,
  AgentResponse,
  AnalysisCreated,
  AnalysisSnapshot,
  ApiError,
  EventsHistoryResponse,
  ProviderCatalogResponse,
  StartAnalysisResponse,
  TestConnectionResponse,
  ToolCatalogResponse,
  ToolCommandResponse,
} from '../types'
import {
  parseAgentResponse,
  parseAnalysisCreated,
  parseAnalysisSnapshot,
  parseEventsHistoryResponse,
  parseProviderCatalogResponse,
  parseStartAnalysisResponse,
  parseTestConnectionResponse,
  parseToolCatalogResponse,
  parseToolCommandResponse,
  ResponseValidationError,
} from '../validation'
import { readStoredSessionToken, writeStoredSessionToken } from '../storage'

const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

const BASE_URL: string = API_BASE_URL.replace(/\/+$/, '')

const AGENT_ENDPOINT: string = `${BASE_URL}/api/p3/agent`
const ANALYSES_ENDPOINT: string = `${BASE_URL}/api/analyses`
const TOOLS_ENDPOINT: string = `${BASE_URL}/api/tools`
const TOOL_COMMANDS_ENDPOINT: string = `${BASE_URL}/api/tool-commands`
const PROVIDERS_ENDPOINT: string = `${BASE_URL}/api/providers`
const PROVIDER_TEST_ENDPOINT: string = `${BASE_URL}/api/providers/test-connection`

// Le cookie de session anonyme (isolation multi-utilisateur) doit accompagner
// TOUTES les requêtes : `credentials: 'include'`. Le token de session est
// AUSSI envoyé en en-tête `X-Session-Token` pour rester fonctionnel en
// cross-site (Netlify ↔ backend), indépendamment du SameSite du cookie.
const CREDENTIALS: RequestCredentials = 'include'

function sessionHeaders(): Record<string, string> {
  const token = readStoredSessionToken()
  return token !== null && token.length > 0 ? { 'X-Session-Token': token } : {}
}

export async function establishSession(): Promise<void> {
  try {
    const response = await fetch(`${BASE_URL}/api/session`, {
      method: 'POST',
      credentials: CREDENTIALS,
      headers: sessionHeaders(),
    })
    if (response.ok) {
      const body: unknown = await response.json()
      if (
        typeof body === 'object' &&
        body !== null &&
        'session_token' in body &&
        typeof (body as Record<string, unknown>).session_token === 'string'
      ) {
        writeStoredSessionToken((body as Record<string, string>).session_token)
      }
    }
  } catch {
    // Le backend posera la session à la première analyse ; ceci n'est qu'une
    // anticipation pour isoler dès l'ouverture les préférences d'outils.
  }
}

export async function runAgent(
  instruction: string,
  document: string,
): Promise<AgentResponse> {
  const requestBody: AgentRequest = { instruction, document }

  let response: Response
  try {
    response = await fetch(AGENT_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
      body: JSON.stringify(requestBody),
      credentials: CREDENTIALS,
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

  return parseWith<AgentResponse>(body, parseAgentResponse)
}

export async function fetchProviderCatalog(): Promise<ProviderCatalogResponse> {
  let response: Response
  try {
    response = await fetch(PROVIDERS_ENDPOINT, { credentials: CREDENTIALS, headers: sessionHeaders() })
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
  return parseWith<ProviderCatalogResponse>(body, parseProviderCatalogResponse)
}

export async function testProviderConnection(
  providerId: string,
  modelId: string,
  apiKey: string,
): Promise<TestConnectionResponse> {
  let response: Response
  try {
    response = await fetch(PROVIDER_TEST_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
      body: JSON.stringify({
        provider_id: providerId,
        model_id: modelId,
        api_key: apiKey,
      }),
      credentials: CREDENTIALS,
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
  return parseWith<TestConnectionResponse>(body, parseTestConnectionResponse)
}

export async function createAnalysis(
  document: string,
  providerId: string,
  modelId: string,
  enabledTools: string[] | null,
): Promise<AnalysisCreated> {
  let response: Response
  try {
    response = await fetch(ANALYSES_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
      body: JSON.stringify({
        document,
        provider_id: providerId,
        model_id: modelId,
        enabled_tools: enabledTools,
      }),
      credentials: CREDENTIALS,
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
  return parseWith<AnalysisCreated>(body, parseAnalysisCreated)
}

export async function startAnalysis(
  analysisId: string,
  apiKey?: string | null,
): Promise<StartAnalysisResponse> {
  const hasCredential = typeof apiKey === 'string' && apiKey.length > 0
  let response: Response
  try {
    response = await fetch(`${ANALYSES_ENDPOINT}/${analysisId}/start`, {
      method: 'POST',
      headers: { ...sessionHeaders(), ...(hasCredential ? { 'Content-Type': 'application/json' } : {}) },
      body: hasCredential ? JSON.stringify({ api_key: apiKey }) : undefined,
      credentials: CREDENTIALS,
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
  return parseWith<StartAnalysisResponse>(body, parseStartAnalysisResponse)
}

export async function fetchEventsHistory(
  analysisId: string,
  after: number,
  limit = 500,
): Promise<EventsHistoryResponse> {
  let response: Response
  try {
    response = await fetch(
      `${ANALYSES_ENDPOINT}/${analysisId}/events/history?after=${Math.max(0, after)}&limit=${limit}`,
      { credentials: CREDENTIALS, headers: sessionHeaders() },
    )
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
  return parseWith<EventsHistoryResponse>(body, parseEventsHistoryResponse)
}

export async function fetchAnalysisSnapshot(
  analysisId: string,
): Promise<AnalysisSnapshot> {
  let response: Response
  try {
    response = await fetch(`${ANALYSES_ENDPOINT}/${analysisId}`, {
      credentials: CREDENTIALS,
      headers: sessionHeaders(),
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
  return parseWith<AnalysisSnapshot>(body, parseAnalysisSnapshot)
}

export async function fetchToolCatalog(): Promise<ToolCatalogResponse> {
  let response: Response
  try {
    response = await fetch(TOOLS_ENDPOINT, { credentials: CREDENTIALS, headers: sessionHeaders() })
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
  return parseWith<ToolCatalogResponse>(body, parseToolCatalogResponse)
}

export async function applyToolCommand(
  command: string,
): Promise<ToolCommandResponse> {
  let response: Response
  try {
    response = await fetch(TOOL_COMMANDS_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
      body: JSON.stringify({ command }),
      credentials: CREDENTIALS,
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
  return parseWith<ToolCommandResponse>(body, parseToolCommandResponse)
}

function parseWith<T>(body: unknown, parser: (value: unknown) => T): T {
  try {
    return parser(body)
  } catch (error) {
    if (error instanceof ResponseValidationError) {
      throw {
        kind: 'malformed',
        message: `Réponse du serveur dans un format inattendu : ${error.message}`,
      } satisfies ApiError
    }
    throw {
      kind: 'malformed',
      message: 'Réponse du serveur dans un format inattendu.',
    } satisfies ApiError
  }
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function defaultStatusMessage(status: number): string {
  if (status === 401 || status === 403) {
    return 'Accès refusé par le serveur (authentification du fournisseur).'
  }
  if (status === 404) {
    return 'Analyse introuvable ou non autorisée sur le serveur (code 404).'
  }
  if (status === 422) {
    return 'Commande ou document refusé par le backend (code 422).'
  }
  if (status === 429) {
    return 'Trop de requêtes : quota ou limite de débit atteint (code 429).'
  }
  if (status === 500) {
    return 'Erreur de configuration côté backend (code 500).'
  }
  if (status === 502) {
    return 'Le fournisseur IA est momentanément indisponible (code 502).'
  }
  return `Le serveur a répondu avec le code HTTP ${status}.`
}