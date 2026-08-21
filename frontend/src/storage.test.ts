import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ANALYSIS_ID_KEY,
  analysisIdFromUrl,
  buildUrlWithAnalysisId,
  buildUrlWithoutAnalysisId,
  clearStoredAnalysisId,
  readStoredAnalysisId,
  readStoredLastEventId,
  writeStoredAnalysisId,
  writeStoredLastEventId,
  type BrowserStorage,
} from './storage'

function memoryStorage(): BrowserStorage {
  const store = new Map<string, string>()
  return {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, value)
    },
    removeItem: (key) => {
      store.delete(key)
    },
  }
}

let storage: BrowserStorage

beforeEach(() => {
  storage = memoryStorage()
  vi.stubGlobal('window', { localStorage: storage })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('référence locale de l’analyse', () => {
  it('écrit puis lit l’UUID', () => {
    expect(readStoredAnalysisId()).toBeNull()
    writeStoredAnalysisId('abcdef')
    expect(readStoredAnalysisId()).toBe('abcdef')
  })

  it('nettoie la référence sans re-poster', () => {
    writeStoredAnalysisId('abcdef')
    clearStoredAnalysisId()
    expect(readStoredAnalysisId()).toBeNull()
  })

  it('présente une valeur vide comme absente', () => {
    storage.setItem(ANALYSIS_ID_KEY, '')
    expect(readStoredAnalysisId()).toBeNull()
  })
})

describe('dernier identifiant d’événement par analyse', () => {
  it('est par défaut 0', () => {
    expect(readStoredLastEventId('ab')).toBe(0)
  })

  it('est conservé par analyse distincte', () => {
    writeStoredLastEventId('ab', 5)
    writeStoredLastEventId('cd', 9)
    expect(readStoredLastEventId('ab')).toBe(5)
    expect(readStoredLastEventId('cd')).toBe(9)
    expect(readStoredLastEventId('ef')).toBe(0)
  })
})

describe('URL UUID', () => {
  it('lit un uuid présent dans la query', () => {
    expect(analysisIdFromUrl('?uuid=abc123')).toBe('abc123')
    expect(analysisIdFromUrl('?page=1&uuid=zz')).toBe('zz')
  })

  it('renvoie null sans uuid', () => {
    expect(analysisIdFromUrl('')).toBeNull()
    expect(analysisIdFromUrl('?page=1')).toBeNull()
    expect(analysisIdFromUrl('?uuid=')).toBeNull()
  })

  it('ajoute puis retire le uuid de l’URL', () => {
    const withId = buildUrlWithAnalysisId(new URL('http://localhost:5173/'), 'abc')
    expect(withId).toContain('uuid=abc')
    const without = buildUrlWithoutAnalysisId(new URL(withId))
    expect(analysisIdFromUrl(new URL(without).search)).toBeNull()
  })
})