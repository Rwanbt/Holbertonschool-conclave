import { afterEach, describe, expect, it, vi } from 'vitest'
import { establishSession, fetchAnalysisSnapshot, fetchProviderCatalog } from './client'
import { writeStoredSessionToken } from '../storage'

const SESSION_TOKEN_KEY = 'conclave.sessionToken.v1'

type FetchMock = ReturnType<typeof vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>>

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

function catalogFetch(): FetchMock {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void input
    void init
    return jsonResponse({ providers: [] })
  })
}

function findInit(fetchMock: FetchMock, needle: string): RequestInit | null {
  const call = fetchMock.mock.calls.find(([u]) => String(u).includes(needle))
  return (call?.[1] as RequestInit | undefined) ?? null
}

describe('session par en-tête X-Session-Token (cross-site)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    try {
      window.sessionStorage.clear()
    } catch {
      // jsdom peut ne pas exposer sessionStorage selon la config
    }
  })

  it('establishSession stocke le token et les requêtes l’envoient en en-tête', async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        void init
        const url = String(input)
        if (url.includes('/api/session')) {
          return jsonResponse({ status: 'ok', session_token: 'tok-abc-123' })
        }
        return jsonResponse({ providers: [] })
      },
    )
    vi.stubGlobal('fetch', fetchMock)

    await establishSession()
    expect(window.sessionStorage.getItem(SESSION_TOKEN_KEY)).toBe('tok-abc-123')

    await fetchProviderCatalog()
    const init = findInit(fetchMock, '/api/providers')
    expect(init).not.toBeNull()
    expect((init as RequestInit).headers).toMatchObject({
      'X-Session-Token': 'tok-abc-123',
    })
  })

  it('une requête sans token stocké n’envoie pas l’en-tête (repli cookie)', async () => {
    const fetchMock = catalogFetch()
    vi.stubGlobal('fetch', fetchMock)

    await fetchProviderCatalog()
    const init = findInit(fetchMock, '/api/providers')
    const headers = ((init?.headers ?? {}) as Record<string, string>)
    expect(headers['X-Session-Token']).toBeUndefined()
  })

  it('un token préexistant est envoyé sans repasser par /api/session', async () => {
    writeStoredSessionToken('tok-existing')
    const fetchMock = catalogFetch()
    vi.stubGlobal('fetch', fetchMock)

    await fetchProviderCatalog()
    const init = findInit(fetchMock, '/api/providers')
    expect((init as RequestInit).headers).toMatchObject({
      'X-Session-Token': 'tok-existing',
    })
  })

  it('fetchAnalysisSnapshot envoie le token (régression 404 post-création)', async () => {
    // Régression : ce fetch était le SEUL sans en-tête -> 404 juste après la
    // création, retour à l'accueil. Il DOIT transporter la session.
    writeStoredSessionToken('tok-snapshot')
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        void input
        void init
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: 'analysis not found' }),
        } as Response
      },
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchAnalysisSnapshot('a1')).rejects.toMatchObject({
      kind: 'http',
      status: 404,
    })
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/api/analyses/a1'))
    expect(call).toBeDefined()
    const init = call![1] as RequestInit
    expect(init.headers).toMatchObject({ 'X-Session-Token': 'tok-snapshot' })
    expect(init.credentials).toBe('include')
  })
})