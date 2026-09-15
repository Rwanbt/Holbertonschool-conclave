export const ANALYSIS_ID_KEY = 'conclave.currentAnalysisId.v1'
export const LAST_EVENT_ID_KEY = 'conclave.lastEventId.v1'
export const SESSION_TOKEN_KEY = 'conclave.sessionToken.v1'

export interface BrowserStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

function readStorage(): BrowserStorage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function readStoredAnalysisId(): string | null {
  const storage = readStorage()
  if (storage === null) {
    return null
  }
  const value = storage.getItem(ANALYSIS_ID_KEY)
  if (value === null || value === '') {
    return null
  }
  return value
}

export function writeStoredAnalysisId(analysisId: string): void {
  const storage = readStorage()
  if (storage !== null) {
    storage.setItem(ANALYSIS_ID_KEY, analysisId)
  }
}

export function clearStoredAnalysisId(): void {
  const storage = readStorage()
  if (storage !== null) {
    storage.removeItem(ANALYSIS_ID_KEY)
  }
}

export function readStoredLastEventId(analysisId: string): number {
  const storage = readStorage()
  if (storage === null) {
    return 0
  }
  const value = storage.getItem(lastEventKeyFor(analysisId))
  if (value === null) {
    return 0
  }
  const parsed = Number.parseInt(value, 10)
  if (Number.isNaN(parsed) || parsed < 0) {
    return 0
  }
  return parsed
}

export function writeStoredLastEventId(analysisId: string, eventId: number): void {
  const storage = readStorage()
  if (storage !== null) {
    storage.setItem(lastEventKeyFor(analysisId), String(eventId))
  }
}

function lastEventKeyFor(analysisId: string): string {
  return `${LAST_EVENT_ID_KEY}.${analysisId}`
}

// ---------------------------------------------------------------------------
// Token de session anonyme — sessionStorage (survit au F5, effacé à la fermeture
// de l'onglet). Transporté en en-tête `X-Session-Token` : indépendant du
// SameSite du cookie, donc fonctionne aussi en cross-site (Netlify ↔ backend).
// Ce n'est PAS une clé provider : aucun secret fournisseur n'est stocké ici.
// ---------------------------------------------------------------------------

export function readStoredSessionToken(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_TOKEN_KEY)
  } catch {
    return null
  }
}

export function writeStoredSessionToken(token: string): void {
  try {
    window.sessionStorage.setItem(SESSION_TOKEN_KEY, token)
  } catch {
    // stockage indisponible : le cookie (même-site) restera le transport.
  }
}

// ---------------------------------------------------------------------------
// URL — UUID exposé dans l'URL, lu au démarrage de l'application
// ---------------------------------------------------------------------------

export const URL_UUID_PARAM = 'uuid'

export function analysisIdFromUrl(search: string): string | null {
  const params = new URLSearchParams(search)
  const raw = params.get(URL_UUID_PARAM)
  if (raw === null || raw === '') {
    return null
  }
  return raw
}

export function buildUrlWithAnalysisId(
  currentUrl: URL,
  analysisId: string,
): string {
  const next = new URL(currentUrl.toString())
  next.searchParams.set(URL_UUID_PARAM, analysisId)
  return next.toString()
}

export function buildUrlWithoutAnalysisId(currentUrl: URL): string {
  const next = new URL(currentUrl.toString())
  next.searchParams.delete(URL_UUID_PARAM)
  return next.toString()
}