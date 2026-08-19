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
  input_tokens: number
  output_tokens: number
  total_tokens: number
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

export type ApiError =
  | { kind: 'network'; message: string }
  | { kind: 'http'; status: number; message: string }
  | { kind: 'malformed'; message: string }

export type UiState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; response: AgentResponse }
  | { status: 'error'; message: string }
