import type {
  AgentResponse,
  ExecutionUsage,
  ToolStatus,
  ToolTraceEntry,
} from './types'

export class ResponseValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ResponseValidationError'
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

export function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

export function isToolStatus(value: unknown): value is ToolStatus {
  return value === 'success' || value === 'error'
}

export function parseAgentResponse(body: unknown): AgentResponse {
  if (!isRecord(body)) {
    throw new ResponseValidationError('la réponse n\'est pas un objet JSON.')
  }

  const answer = readString(body, 'answer')
  const model = readString(body, 'model')
  const trace = readTrace(body.trace)
  const usage = readUsage(body.usage)

  return { answer, model, trace, usage }
}

function readString(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string') {
    throw new ResponseValidationError(`le champ "${key}" n'est pas une chaîne.`)
  }
  return value
}

function readNullableString(
  value: unknown,
  key: string,
): string | null {
  if (value === null) {
    return null
  }
  if (typeof value !== 'string') {
    throw new ResponseValidationError(
      `le champ "${key}" n'est ni une chaîne ni null.`,
    )
  }
  return value
}

function readTrace(value: unknown): ToolTraceEntry[] {
  if (!Array.isArray(value)) {
    throw new ResponseValidationError('le champ "trace" n\'est pas un tableau.')
  }
  return value.map(parseToolTraceEntry)
}

function parseToolTraceEntry(value: unknown): ToolTraceEntry {
  if (!isRecord(value)) {
    throw new ResponseValidationError('une entrée de trace n\'est pas un objet.')
  }

  const sequence = readPositiveInteger(value.sequence, 'sequence')
  const tool_name = readString(value, 'tool_name')

  if (!isToolStatus(value.status)) {
    throw new ResponseValidationError(
      `statut d'outil inconnu : ${String(value.status)}.`,
    )
  }

  const input_summary = readSummary(value.input_summary, 'input_summary')
  const output_summary = readNullableSummary(
    value.output_summary,
    'output_summary',
  )
  const duration_ms = readNonNegativeInteger(value.duration_ms, 'duration_ms')
  const error_code = readNullableString(value.error_code, 'error_code')

  return {
    sequence,
    tool_name,
    status: value.status,
    input_summary,
    output_summary,
    duration_ms,
    error_code,
  }
}

function readSummary(
  value: unknown,
  key: string,
): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ResponseValidationError(
      `le champ "${key}" n'est pas un objet JSON.`,
    )
  }
  return value
}

function readNullableSummary(
  value: unknown,
  key: string,
): Record<string, unknown> | null {
  if (value === null) {
    return null
  }
  return readSummary(value, key)
}

function readUsage(value: unknown): ExecutionUsage {
  if (!isRecord(value)) {
    throw new ResponseValidationError('le champ "usage" n\'est pas un objet.')
  }

  const input_tokens = readNonNegativeInteger(value.input_tokens, 'input_tokens')
  const output_tokens = readNonNegativeInteger(
    value.output_tokens,
    'output_tokens',
  )
  const total_tokens = readNonNegativeInteger(value.total_tokens, 'total_tokens')
  const estimated_cost_usd = readNullableNonNegativeNumber(
    value.estimated_cost_usd,
    'estimated_cost_usd',
  )
  const total_latency_ms = readNonNegativeInteger(
    value.total_latency_ms,
    'total_latency_ms',
  )
  const llm_rounds = readPositiveInteger(value.llm_rounds, 'llm_rounds')

  return {
    input_tokens,
    output_tokens,
    total_tokens,
    estimated_cost_usd,
    total_latency_ms,
    llm_rounds,
  }
}

function readNonNegativeInteger(value: unknown, key: string): number {
  if (!isNonNegativeInteger(value)) {
    throw new ResponseValidationError(
      `le champ "${key}" n'est pas un entier positif ou nul.`,
    )
  }
  return value
}

function readPositiveInteger(value: unknown, key: string): number {
  if (!isPositiveInteger(value)) {
    throw new ResponseValidationError(
      `le champ "${key}" n'est pas un entier strictement positif.`,
    )
  }
  return value
}

function readNullableNonNegativeNumber(
  value: unknown,
  key: string,
): number | null {
  if (value === null) {
    return null
  }
  if (typeof value !== 'number' || Number.isNaN(value) || value < 0) {
    throw new ResponseValidationError(
      `le champ "${key}" n'est pas un nombre positif ou null.`,
    )
  }
  return value
}