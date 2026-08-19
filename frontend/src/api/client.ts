import type {
  AgentRequest,
  AgentResponse,
  AnalysisCreated,
  AnalysisSnapshot,
  ApiError,
  ToolCatalogResponse,
  ToolCommandResponse,
} from '../types'
import {
  parseAgentResponse,
  parseAnalysisCreated,
  parseAnalysisSnapshot,
  parseToolCatalogResponse,
  parseToolCommandResponse,
  ResponseValidationError,
} from '../validation'

const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

const BASE_URL: string = API_BASE_URL.replace(/\/+$/, '')

const AGENT_ENDPOINT: string = `${BASE_URL}/api/p3/agent`
const ANALYSES_ENDPOINT: string = `${BASE_URL}/api/analyses`
const TOOLS_ENDPOINT: string = `${BASE_URL}/api/tools`
const TOOL_COMMANDS_ENDPOINT: string = `${BASE_URL}/api/tool-commands`

export async function runAgent(
  instruction: string,
  document: string,
): Promise<AgentResponse> {
  const requestBody: AgentRequest = { instruction, document }

  let response: Response
  try {
    response = await fetch(AGENT_ENDPOINT, {
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

  return parseWith<AgentResponse>(body, parseAgentResponse)
}

export async function createAnalysis(document: string): Promise<AnalysisCreated> {
  let response: Response
  try {
    response = await fetch(ANALYSES_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document }),
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

export async function fetchAnalysisSnapshot(
  analysisId: string,
): Promise<AnalysisSnapshot> {
  let response: Response
  try {
    response = await fetch(`${ANALYSES_ENDPOINT}/${analysisId}`)
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
    response = await fetch(TOOLS_ENDPOINT)
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
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
  if (status === 404) {
    return 'Analyse introuvable sur le serveur (code 404).'
  }
  if (status === 422) {
    return 'Commande ou document refusé par le backend (code 422).'
  }
  if (status === 500) {
    return 'Configuration serveur absente côté backend (code 500).'
  }
  if (status === 502) {
    return 'Le fournisseur MiniMax est momentanément indisponible (code 502).'
  }
  return `Le serveur a répondu avec le code HTTP ${status}.`
}