import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useProviderConnection } from './useProviderConnection'

const CATALOG = {
  providers: [
    {
      provider_id: 'minimax',
      label: 'MiniMax',
      auth_modes: ['api_key'],
      supports_tools: true,
      supports_streaming: true,
      supports_structured_output: true,
      supports_reasoning: false,
      oauth_supported: false,
      oauth_configured: false,
      models: [
        {
          model_id: 'MiniMax-M3',
          supports_tools: true,
          supports_streaming: true,
          supports_structured_output: true,
          pricing: null,
        },
      ],
    },
    {
      provider_id: 'openai',
      label: 'OpenAI',
      auth_modes: ['api_key'],
      supports_tools: true,
      supports_streaming: true,
      supports_structured_output: true,
      supports_reasoning: true,
      oauth_supported: false,
      oauth_configured: false,
      models: [
        {
          model_id: 'gpt-4o-mini',
          supports_tools: true,
          supports_streaming: true,
          supports_structured_output: true,
          pricing: { input_usd_per_million_tokens: 0.15 },
        },
      ],
    },
  ],
}

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

describe('useProviderConnection', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('charge le catalogue et sélectionne le premier provider/modèle', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(CATALOG)),
    )
    const { result } = renderHook(() => useProviderConnection())
    await waitFor(() => expect(result.current.catalogStatus).toBe('ready'))
    expect(result.current.selectedProviderId).toBe('minimax')
    expect(result.current.selectedModelId).toBe('MiniMax-M3')
    expect(result.current.isReady).toBe(false)
  })

  it('la clé n’est PAS prête tant que la clé est absente, puis le devient', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(CATALOG)),
    )
    const { result } = renderHook(() => useProviderConnection())
    await waitFor(() => expect(result.current.catalogStatus).toBe('ready'))
    expect(result.current.isReady).toBe(false)

    act(() => result.current.setApiKey('sk-user-secret'))
    expect(result.current.isReady).toBe(true)
    expect(result.current.connection).toBe('disconnected')

    act(() => result.current.disconnect())
    expect(result.current.apiKey).toBe('')
    expect(result.current.isReady).toBe(false)
  })

  it('teste la connexion et passe connecté sans écrire la clé au storage', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/test-connection')) {
          return jsonResponse({
            provider_id: 'openai',
            model_id: 'gpt-4o-mini',
            ok: true,
            message: 'Connecté',
            needs_inference: false,
          })
        }
        return jsonResponse(CATALOG)
      }),
    )
    const { result } = renderHook(() => useProviderConnection())
    await waitFor(() => expect(result.current.catalogStatus).toBe('ready'))

    act(() => result.current.selectProvider('openai'))
    act(() => result.current.setApiKey('sk-user-secret'))
    await act(async () => {
      await result.current.test()
    })
    expect(result.current.connection).toBe('connected')
    expect(result.current.isReady).toBe(true)

    const storageWrites = setItemSpy.mock.calls.map((call) => String(call[0]))
    expect(storageWrites.some((key) => /key|secret|credential/i.test(key))).toBe(
      false,
    )
  })
})