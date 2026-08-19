export const MAX_DOCUMENT_LENGTH = 12_000

export function isNonEmptyTrimmed(value: string): boolean {
  return value.trim().length > 0
}