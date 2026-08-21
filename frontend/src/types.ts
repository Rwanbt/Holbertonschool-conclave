export interface AgentRequest {
  instruction: string
  document: string
}

export type ToolStatus = 'success' | 'error'

export interface ToolTraceEntry {
  sequence: number
  tool_name: string
  status: ToolStatus
  input_summary: Record<string, unknown>
  output_summary: Record<string, unknown> | null
  duration_ms: number
  error_code: string | null
}

export interface ExecutionUsage {
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  estimated_cost_usd: number | null
  total_latency_ms: number
  llm_rounds: number
}

export interface AgentResponse {
  answer: string
  model: string
  trace: ToolTraceEntry[]
  usage: ExecutionUsage
}

// ---------------------------------------------------------------------------
// Palier 4 — analyse persistante, experts, arbitre, événements, outils
// ---------------------------------------------------------------------------

export type AnalysisStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'degraded'
  | 'failed'
  | 'interrupted'

export type ExpertStatus = 'pending' | 'running' | 'completed' | 'error' | 'timeout'

export type ExpertRole = 'avocat' | 'procureur' | 'comptable'

export type AgentRole = ExpertRole | 'arbitre'

export type Priority = 'low' | 'medium' | 'high'

export type VerdictDecision = 'go' | 'go_with_conditions' | 'no_go'

export interface Finding {
  title: string
  evidence: string
  impact: string
  priority: Priority
}

export interface AgentOutput {
  role: ExpertRole
  summary: string
  findings: Finding[]
  score_label: string
  score: number
  recommendations: string[]
  unavailable_tools: string[]
}

export interface ArbiterVerdict {
  decision: VerdictDecision
  score: number
  main_disagreement: string
  priority_risks: string[]
  actions: string[]
  accepted_tradeoff: string
  unavailable_agents: ExpertRole[]
}

export interface ExpertRun {
  role: ExpertRole
  status: ExpertStatus
  output: AgentOutput | null
  error_code: string | null
}

export interface GuardrailStatuses {
  analysis: AnalysisStatus[]
  expert: ExpertStatus[]
}

export interface GuardrailInfo {
  expert_timeout_seconds: number
  arbiter_timeout_seconds: number
  analysis_timeout_seconds: number
  agent_max_rounds: number
  structured_repair_attempts: number
  document_max_length: number
  statuses: GuardrailStatuses
}

export interface ToolConfiguration {
  enabled_tools: ToolName[]
  disabled_tools: ToolName[]
}

export interface SecurityReport {
  prompt_injection_suspected: boolean
  signals: string[]
}

export interface AnalysisCreated {
  analysis_id: string
  status: AnalysisStatus
  created_at: string
  tool_configuration: ToolConfiguration
  security: SecurityReport
}

export interface StartAnalysisResponse {
  analysis_id: string
  status: AnalysisStatus
  already_started: boolean
}

export interface AnalysisEventEnvelope {
  id: number
  event_type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface EventsHistoryResponse {
  events: AnalysisEventEnvelope[]
  last_event_id: number
  has_more: boolean
}

export interface AnalysisSnapshot {
  analysis_id: string
  document: string
  status: AnalysisStatus
  created_at: string
  started_at: string | null
  completed_at: string | null
  error_code: string | null
  avocat: ExpertRun
  procureur: ExpertRun
  comptable: ExpertRun
  verdict: ArbiterVerdict | null
  usage: ExecutionUsage
  guardrails: GuardrailInfo
  tool_configuration: ToolConfiguration
  security: SecurityReport
}

export type AnalysisEventType =
  | 'analysis.created'
  | 'analysis.started'
  | 'agent.round.started'
  | 'agent.round.completed'
  | 'agent.repair.started'
  | 'agent.repair.completed'
  | 'agent.repair.failed'
  | 'expert.started'
  | 'tool.started'
  | 'tool.completed'
  | 'tool.failed'
  | 'expert.completed'
  | 'expert.failed'
  | 'expert.timeout'
  | 'arbiter.started'
  | 'arbiter.completed'
  | 'arbiter.failed'
  | 'agent.response.started'
  | 'agent.response.delta'
  | 'agent.response.completed'
  | 'agent.response.failed'
  | 'analysis.completed'
  | 'analysis.degraded'
  | 'analysis.failed'
  | 'analysis.interrupted'

export type AgentResponseStartedPayload = Record<string, unknown> & {
  analysis_id: string
  role: AgentRole
}

export type AgentResponseDeltaPayload = Record<string, unknown> & {
  analysis_id: string
  role: AgentRole
  sequence: number
  delta: string
}

export type AgentResponseCompletedPayload = Record<string, unknown> & {
  analysis_id: string
  role: AgentRole
}

export type AgentResponseFailedPayload = Record<string, unknown> & {
  analysis_id: string
  role: AgentRole
  error_code: string
  error_detail?: string
}

export type LiveResponseStatus = 'idle' | 'streaming' | 'completed' | 'failed'

export interface LiveResponseView {
  role: AgentRole
  status: LiveResponseStatus
  text: string
  lastSequence: number
  errorCode: string | null
}

export interface AnalysisEvent {
  id: number
  type: AnalysisEventType
  payload: Record<string, unknown>
}

export type ToolName =
  | 'measure_current_document'
  | 'find_security_indicators_in_current_document'
  | 'estimate_current_analysis_cost'

export interface ToolState {
  tool_name: ToolName
  enabled: boolean
  description: string
}

export interface ToolCatalogResponse {
  tools: ToolState[]
}

export interface ToolCommandResponse {
  action: 'list' | 'enable' | 'disable'
  message: string
  tool_name: ToolName | null
  enabled: boolean | null
  tools: ToolState[]
}

export type ApiError =
  | { kind: 'network'; message: string }
  | { kind: 'http'; status: number; message: string }
  | { kind: 'malformed'; message: string }
