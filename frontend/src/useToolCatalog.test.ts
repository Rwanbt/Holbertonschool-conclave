import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useToolCatalog } from './useToolCatalog'
import type { ToolCatalogResponse, ToolCommandResponse, ToolState } from './types'

function tool(overrides: Partial<ToolState>): ToolState {
  return {
    tool_name: 'measure_current_document',
    enabled: true,
    description: 'Mesure le document.',
    ...overrides,
  }
}

const INITIAL_TOOLS: readonly ToolState[] = [
  tool({ tool_name: 'estimate_current_analysis_cost', enabled: true }),
  tool({ tool_name: 'find_security_indicators_in_current_document', enabled: true }),
  tool({ tool_name: 'measure_current_document', enabled: true }),
]

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as Response
}

describe('useToolCatalog', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('charge le catalogue automatiquement à l’ouverture, sans manipulation initiale', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ tools: [...INITIAL_TOOLS] } satisfies ToolCatalogResponse),
    )

    const { result } = renderHook(() => useToolCatalog())

    expect(result.current.status).toBe('loading')
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.tools).toEqual(INITIAL_TOOLS)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('une réponse GET obsolète ne peut pas écraser une mutation plus récente', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    let resolveFirstRefresh!: (value: Response) => void
    const firstRefresh = new Promise<Response>((resolve) => {
      resolveFirstRefresh = resolve
    })
    fetchMock.mockReturnValueOnce(firstRefresh)

    const { result } = renderHook(() => useToolCatalog())
    expect(result.current.status).toBe('loading')

    // Une mutation démarre AVANT que le premier GET (lent) ne réponde.
    const mutationResponse: ToolCommandResponse = {
      action: 'disable',
      message: 'Outil désactivé.',
      tool_name: 'measure_current_document',
      enabled: false,
      tools: INITIAL_TOOLS.map((t) =>
        t.tool_name === 'measure_current_document' ? { ...t, enabled: false } : t,
      ),
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse))

    await act(async () => {
      await result.current.toggle('measure_current_document')
    })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.tools.find((t) => t.tool_name === 'measure_current_document')?.enabled).toBe(
      false,
    )

    // Le premier GET (obsolète) répond seulement maintenant : il ne doit
    // pas écraser l'état plus récent issu de la mutation.
    await act(async () => {
      resolveFirstRefresh(jsonResponse({ tools: [...INITIAL_TOOLS] } satisfies ToolCatalogResponse))
      await firstRefresh
    })
    await waitFor(() =>
      expect(
        result.current.tools.find((t) => t.tool_name === 'measure_current_document')?.enabled,
      ).toBe(false),
    )
  })

  it('une erreur réseau sur une mutation conserve l’état précédent', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ tools: [...INITIAL_TOOLS] } satisfies ToolCatalogResponse),
    )
    const { result } = renderHook(() => useToolCatalog())
    await waitFor(() => expect(result.current.status).toBe('ready'))

    fetchMock.mockRejectedValueOnce(new TypeError('network down'))
    await act(async () => {
      await result.current.toggle('measure_current_document')
    })

    expect(result.current.status).toBe('error')
    expect(result.current.errorMessage).not.toBeNull()
    expect(result.current.tools).toEqual(INITIAL_TOOLS)
  })
})
