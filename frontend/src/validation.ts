import type {
  AgentResponse,
  ExecutionUsage,
  ToolStatus,
  ToolTraceEntry,
} from './types'
import type {
  AgentOutput,
  AnalysisCreated,
  AnalysisEvent,
  AnalysisEventType,
  AnalysisSnapshot,
  ArbiterVerdict,
  ExpertRun,
  GuardrailInfo,
  Priority,
  ToolCatalogResponse,
  ToolCommandResponse,
  ToolName,
  ToolState,
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

  const input_tokens = readNullableInteger(value.input_tokens, 'input_tokens')
  const output_tokens = readNullableInteger(value.output_tokens, 'output_tokens')
  const total_tokens = readNullableInteger(value.total_tokens, 'total_tokens')
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

function readNullableInteger(value: unknown, key: string): number | null {
  if (value === null) {
    return null
  }
  return readNonNegativeInteger(value, key)
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

// ---------------------------------------------------------------------------
// Palier 4 — validation des analyses persistantes, verdicts, outils et SSE
// ---------------------------------------------------------------------------

const ANALYSIS_STATUSES: readonly string[] = [
  'running',
  'completed',
  'degraded',
  'failed',
  'interrupted',
]

const EXPERT_STATUSES: readonly string[] = [
  'pending',
  'running',
  'completed',
  'error',
  'timeout',
]

const EXPERT_ROLES: readonly string[] = ['avocat', 'procureur', 'comptable']

const PRIORITIES: readonly string[] = ['low', 'medium', 'high']

const VERDICT_DECISIONS: readonly string[] = [
  'go',
  'go_with_conditions',
  'no_go',
]

const TOOL_NAMES: readonly string[] = [
  'measure_current_document',
  'find_security_indicators_in_current_document',
  'estimate_current_analysis_cost',
]

export const SSE_EVENT_TYPES: readonly AnalysisEventType[] = [
  'analysis.created',
  'expert.started',
  'tool.started',
  'tool.completed',
  'tool.failed',
  'expert.completed',
  'expert.failed',
  'expert.timeout',
  'arbiter.started',
  'arbiter.completed',
  'arbiter.failed',
  'analysis.completed',
  'analysis.degraded',
  'analysis.failed',
  'analysis.interrupted',
]

export type AnalysisStatus = AnalysisSnapshot['status']

function readEnum<T extends string>(
  record: Record<string, unknown>,
  key: string,
  values: readonly string[],
): T {
  const value = record[key]
  if (typeof value !== 'string' || !values.includes(value)) {
    throw new ResponseValidationError(
      `le champ "${key}" a une valeur inconnue : ${String(value)}.`,
    )
  }
  return value as T
}

function readBoolean(record: Record<string, unknown>, key: string): boolean {
  const value = record[key]
  if (typeof value !== 'boolean') {
    throw new ResponseValidationError(`le champ "${key}" n'est pas un booléen.`)
  }
  return value
}

function readBoundedNumber(
  value: unknown,
  key: string,
  min: number,
  max: number,
): number {
  if (typeof value !== 'number' || Number.isNaN(value) || value < min || value > max) {
    throw new ResponseValidationError(
      `le champ "${key}" doit être un nombre entre ${min} et ${max}.`,
    )
  }
  return value
}

function readStringList(value: unknown, key: string, max: number): string[] {
  if (!Array.isArray(value)) {
    throw new ResponseValidationError(
      `le champ "${key}" n'est pas un tableau.`,
    )
  }
  if (value.length > max) {
    throw new ResponseValidationError(
      `le champ "${key}" ne peut pas dépasser ${max} éléments.`,
    )
  }
  return value.map((item, index) => {
    if (typeof item !== 'string') {
      throw new ResponseValidationError(
        `le champ "${key}" contient un élément non textuel (index ${index}).`,
      )
    }
    return item
  })
}

function readNullableVerdict(value: unknown): ArbiterVerdict | null {
  if (value === null) {
    return null
  }
  return parseArbiterVerdict(value)
}

export function parseAnalysisCreated(body: unknown): AnalysisCreated {
  const record = requireRecord(body)
  const analysis_id = readString(record, 'analysis_id')
  const status = readEnum<AnalysisCreated['status']>(
    record,
    'status',
    ANALYSIS_STATUSES,
  )
  const created_at = readString(record, 'created_at')
  return { analysis_id, status, created_at }
}

export function parseAnalysisSnapshot(body: unknown): AnalysisSnapshot {
  const record = requireRecord(body)
  const analysis_id = readString(record, 'analysis_id')
  const document = readString(record, 'document')
  const status = readEnum<AnalysisSnapshot['status']>(
    record,
    'status',
    ANALYSIS_STATUSES,
  )
  const created_at = readString(record, 'created_at')
  const started_at = readNullableString(record.started_at, 'started_at')
  const completed_at = readNullableString(record.completed_at, 'completed_at')
  const error_code = readNullableString(record.error_code, 'error_code')
  const avocat = parseExpertRun(record.avocat)
  const procureur = parseExpertRun(record.procureur)
  const comptable = parseExpertRun(record.comptable)
  const verdict = readNullableVerdict(record.verdict)
  const usage = readUsage(record.usage)
  const guardrails = parseGuardrails(record.guardrails)
  return {
    analysis_id,
    document,
    status,
    created_at,
    started_at,
    completed_at,
    error_code,
    avocat,
    procureur,
    comptable,
    verdict,
    usage,
    guardrails,
  }
}

function parseGuardrails(value: unknown): GuardrailInfo {
  const record = requireRecord(value)
  const expert_timeout_seconds = readNonNegativeNumber(
    record.expert_timeout_seconds,
    'expert_timeout_seconds',
  )
  const arbiter_timeout_seconds = readNonNegativeNumber(
    record.arbiter_timeout_seconds,
    'arbiter_timeout_seconds',
  )
  const analysis_timeout_seconds = readNonNegativeNumber(
    record.analysis_timeout_seconds,
    'analysis_timeout_seconds',
  )
  const agent_max_rounds = readNonNegativeInteger(
    record.agent_max_rounds,
    'agent_max_rounds',
  )
  const document_max_length = readNonNegativeInteger(
    record.document_max_length,
    'document_max_length',
  )
  const statuses = requireRecord(record.statuses)
  const analysis = statuses.analysis
  const expert = statuses.expert
  if (
    !Array.isArray(analysis) ||
    !analysis.every((s) => ANALYSIS_STATUSES.includes(s as string)) ||
    !Array.isArray(expert) ||
    !expert.every((s) => EXPERT_STATUSES.includes(s as string))
  ) {
    throw new ResponseValidationError('le champ "statuses" des garde-fous est invalide.')
  }
  return {
    expert_timeout_seconds,
    arbiter_timeout_seconds,
    analysis_timeout_seconds,
    agent_max_rounds,
    document_max_length,
    statuses: {
      analysis: analysis as AnalysisStatus[],
      expert: expert as GuardrailInfo['statuses']['expert'],
    },
  }
}

export function parseExpertRun(value: unknown): ExpertRun {
  const record = requireRecord(value)
  const role = readEnum<ExpertRun['role']>(record, 'role', EXPERT_ROLES)
  const status = readEnum<ExpertRun['status']>(record, 'status', EXPERT_STATUSES)
  const output = readNullableAgentOutput(record.output)
  const error_code = readNullableString(record.error_code, 'error_code')
  return { role, status, output, error_code }
}

function readNullableAgentOutput(value: unknown): AgentOutput | null {
  if (value === null) {
    return null
  }
  return parseAgentOutput(value)
}

export function parseAgentOutput(value: unknown): AgentOutput {
  const record = requireRecord(value)
  const role = readEnum<AgentOutput['role']>(record, 'role', EXPERT_ROLES)
  const summary = readString(record, 'summary')
  const findings = parseFindings(record.findings)
  const score_label = readString(record, 'score_label')
  const score = readBoundedNumber(record.score, 'score', 0, 100)
  const recommendations = readStringList(record.recommendations, 'recommendations', 3)
  const unavailable_tools = readStringList(record.unavailable_tools, 'unavailable_tools', 10)
  return {
    role,
    summary,
    findings,
    score_label,
    score,
    recommendations,
    unavailable_tools,
  }
}

function parseFindings(value: unknown): AgentOutput['findings'] {
  if (!Array.isArray(value)) {
    throw new ResponseValidationError('le champ "findings" n\'est pas un tableau.')
  }
  if (value.length < 2 || value.length > 5) {
    throw new ResponseValidationError(
      'le champ "findings" doit contenir entre 2 et 5 constats.',
    )
  }
  return value.map((item) => {
    const record = requireRecord(item)
    const title = readString(record, 'title')
    const evidence = readString(record, 'evidence')
    const impact = readString(record, 'impact')
    const priority = readEnum<Priority>(record, 'priority', PRIORITIES)
    return { title, evidence, impact, priority }
  })
}

export function parseArbiterVerdict(value: unknown): ArbiterVerdict {
  const record = requireRecord(value)
  const decision = readEnum<ArbiterVerdict['decision']>(
    record,
    'decision',
    VERDICT_DECISIONS,
  )
  const score = readBoundedNumber(record.score, 'score', 0, 100)
  const main_disagreement = readString(record, 'main_disagreement')
  const priority_risks = readStringList(record.priority_risks, 'priority_risks', 3)
  const actions = readStringList(record.actions, 'actions', 3)
  const accepted_tradeoff = readString(record, 'accepted_tradeoff')
  const rawAgents = readStringList(record.unavailable_agents, 'unavailable_agents', 3)
  for (const agent of rawAgents) {
    if (!EXPERT_ROLES.includes(agent)) {
      throw new ResponseValidationError(
        `rôle inconnu dans "unavailable_agents" : ${agent}.`,
      )
    }
  }
  return {
    decision,
    score,
    main_disagreement,
    priority_risks,
    actions,
    accepted_tradeoff,
    unavailable_agents: rawAgents as ArbiterVerdict['unavailable_agents'],
  }
}

export function parseToolCatalogResponse(body: unknown): ToolCatalogResponse {
  const record = requireRecord(body)
  const toolsValue = record.tools
  if (!Array.isArray(toolsValue)) {
    throw new ResponseValidationError('le champ "tools" n\'est pas un tableau.')
  }
  const tools = toolsValue.map(parseToolState)
  return { tools }
}

function parseToolState(value: unknown): ToolState {
  const record = requireRecord(value)
  const tool_name = readEnum<ToolName>(record, 'tool_name', TOOL_NAMES)
  const enabled = readBoolean(record, 'enabled')
  const description = readString(record, 'description')
  return { tool_name, enabled, description }
}

export function parseToolCommandResponse(body: unknown): ToolCommandResponse {
  const record = requireRecord(body)
  const tool_name = readEnum<ToolName>(record, 'tool_name', TOOL_NAMES)
  const enabled = readBoolean(record, 'enabled')
  return { tool_name, enabled }
}

export function parseAnalysisEvent(
  idValue: unknown,
  eventType: unknown,
  dataValue: unknown,
): AnalysisEvent {
  if (!isPositiveInteger(idValue)) {
    throw new ResponseValidationError('identifiant d\'événement invalide.')
  }
  if (
    typeof eventType !== 'string' ||
    !SSE_EVENT_TYPES.includes(eventType as AnalysisEventType)
  ) {
    throw new ResponseValidationError(
      `type d'événement SSE inconnu : ${String(eventType)}.`,
    )
  }
  const payload = requireRecord(dataValue)
  return { id: idValue, type: eventType as AnalysisEventType, payload }
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ResponseValidationError('le corps JSON n\'est pas un objet.')
  }
  return value
}

function readNonNegativeNumber(value: unknown, key: string): number {
  if (typeof value !== 'number' || Number.isNaN(value) || value < 0) {
    throw new ResponseValidationError(
      `le champ "${key}" n'est pas un nombre positif ou nul.`,
    )
  }
  return value
}