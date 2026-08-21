import type { ApiError } from './types'

export function isApiError(error: unknown): error is ApiError {
  if (typeof error !== 'object' || error === null) {
    return false
  }
  if (!('kind' in error) || !('message' in error)) {
    return false
  }
  const kind = error.kind
  const message = error.message
  return (
    typeof message === 'string' &&
    (kind === 'network' || kind === 'http' || kind === 'malformed')
  )
}

export function toErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    return error.message
  }
  return 'Une erreur inattendue s\'est produite. Réessayez.'
}

export function httpStatusOf(error: unknown): number | null {
  if (isApiError(error) && error.kind === 'http') {
    return error.status
  }
  return null
}