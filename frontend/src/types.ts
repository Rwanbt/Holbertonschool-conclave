export interface LlmRequest {
  message: string
}

export interface LlmResponse {
  answer: string
  model: string
}

export type ApiError =
  | { kind: 'network'; message: string }
  | { kind: 'http'; status: number; message: string }
  | { kind: 'malformed'; message: string }

export type UiState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; answer: string; model: string }
  | { status: 'error'; message: string }