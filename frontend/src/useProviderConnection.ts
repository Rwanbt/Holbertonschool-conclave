import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  disconnectOAuth as apiDisconnectOAuth,
  fetchOAuthStatus,
  fetchProviderCatalog,
  startOAuth,
  testProviderConnection,
} from './api/client'
import { toErrorMessage } from './errors'
import type {
  ProviderConnectionStatus,
  ProviderInfo,
  ProviderModelInfo,
} from './types'

export type ProviderCatalogStatus = 'loading' | 'ready' | 'error'

export interface ProviderConnection {
  catalogStatus: ProviderCatalogStatus
  providers: readonly ProviderInfo[]
  catalogError: string | null
  selectedProviderId: string | null
  selectedModelId: string | null
  selectedProvider: ProviderInfo | null
  selectedModel: ProviderModelInfo | null
  apiKey: string
  connection: ProviderConnectionStatus
  connectionMessage: string | null
  needsInference: boolean
  isReady: boolean
  oauthSupported: boolean
  oauthConfigured: boolean
  oauthConnected: boolean
  selectProvider: (providerId: string) => void
  selectModel: (modelId: string) => void
  setApiKey: (value: string) => void
  test: () => Promise<void>
  connectOAuth: () => void
  disconnectOAuth: () => Promise<void>
  disconnect: () => void
  refresh: () => Promise<void>
}

/**
 * État de connexion au fournisseur IA (BYOK).
 *
 * La clé API vit UNIQUEMENT en mémoire React : elle n'est jamais écrite dans
 * localStorage, sessionStorage, l'URL ou un `VITE_*`. Un rechargement la perd
 * volontairement (l'utilisateur la ressaisit) : c'est préférable à une
 * persistance non maîtrisée.
 */
export function useProviderConnection(): ProviderConnection {
  const [catalogStatus, setCatalogStatus] = useState<ProviderCatalogStatus>('loading')
  const [providers, setProviders] = useState<readonly ProviderInfo[]>([])
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null)
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null)
  const [apiKey, setApiKeyState] = useState('')
  const [connection, setConnection] = useState<ProviderConnectionStatus>('disconnected')
  const [connectionMessage, setConnectionMessage] = useState<string | null>(null)
  const [needsInference, setNeedsInference] = useState(false)
  const [oauthConnected, setOauthConnected] = useState(false)

  const refreshOAuthStatus = useCallback(
    async (providerId: string): Promise<void> => {
      try {
        const status = await fetchOAuthStatus(providerId)
        setOauthConnected(status.connected)
      } catch {
        setOauthConnected(false)
      }
    },
    [],
  )

  useEffect(() => {
    if (selectedProviderId === null) {
      setOauthConnected(false)
      return
    }
    void refreshOAuthStatus(selectedProviderId)
  }, [selectedProviderId, refreshOAuthStatus])

  // Retour du flux OAuth : ?oauth=success|error — on nettoie l'URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const result = params.get('oauth')
    if (result === null) {
      return
    }
    if (result === 'success') {
      setConnection('connected')
      setConnectionMessage('Connexion OAuth réussie.')
    } else {
      setConnection('error')
      setConnectionMessage('La connexion OAuth a échoué. Réessayez.')
    }
    params.delete('oauth')
    params.delete('provider')
    params.delete('reason')
    const query = params.toString()
    history.replaceState(
      null,
      '',
      window.location.pathname + (query ? `?${query}` : ''),
    )
  }, [])

  const refresh = useCallback(async (): Promise<void> => {
    setCatalogStatus('loading')
    setCatalogError(null)
    try {
      const catalog = await fetchProviderCatalog()
      setProviders(catalog.providers)
      setCatalogStatus('ready')
      setSelectedProviderId((current) => {
        if (current !== null && catalog.providers.some((p) => p.provider_id === current)) {
          return current
        }
        return catalog.providers[0]?.provider_id ?? null
      })
    } catch (error) {
      setCatalogStatus('error')
      setCatalogError(toErrorMessage(error))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const selectedProvider = useMemo(
    () => providers.find((p) => p.provider_id === selectedProviderId) ?? null,
    [providers, selectedProviderId],
  )

  // À chaque changement de provider, on sélectionne son premier modèle.
  useEffect(() => {
    if (selectedProvider === null) {
      setSelectedModelId(null)
      return
    }
    setSelectedModelId((current) => {
      if (current !== null && selectedProvider.models.some((m) => m.model_id === current)) {
        return current
      }
      return selectedProvider.models[0]?.model_id ?? null
    })
  }, [selectedProvider])

  const selectedModel = useMemo(
    () =>
      selectedProvider?.models.find((m) => m.model_id === selectedModelId) ?? null,
    [selectedProvider, selectedModelId],
  )

  const resetConnection = useCallback((): void => {
    setConnection('disconnected')
    setConnectionMessage(null)
    setNeedsInference(false)
  }, [])

  const selectProvider = useCallback(
    (providerId: string): void => {
      setSelectedProviderId(providerId)
      resetConnection()
    },
    [resetConnection],
  )

  const selectModel = useCallback(
    (modelId: string): void => {
      setSelectedModelId(modelId)
      resetConnection()
    },
    [resetConnection],
  )

  const setApiKey = useCallback(
    (value: string): void => {
      setApiKeyState(value)
      resetConnection()
    },
    [resetConnection],
  )

  const test = useCallback(async (): Promise<void> => {
    if (selectedProviderId === null || selectedModelId === null || apiKey.trim() === '') {
      setConnection('error')
      setConnectionMessage(
        'Sélectionnez un fournisseur, un modèle et saisissez votre clé API avant de tester.',
      )
      return
    }
    setConnection('testing')
    setConnectionMessage(null)
    setNeedsInference(false)
    try {
      const result = await testProviderConnection(
        selectedProviderId,
        selectedModelId,
        apiKey.trim(),
      )
      if (result.ok) {
        setConnection('connected')
        setConnectionMessage(result.message)
        setNeedsInference(false)
      } else if (result.needs_inference) {
        setConnection('unverified')
        setConnectionMessage(result.message)
        setNeedsInference(true)
      } else {
        setConnection('error')
        setConnectionMessage(result.message)
        setNeedsInference(false)
      }
    } catch (error) {
      setConnection('error')
      setConnectionMessage(toErrorMessage(error))
    }
  }, [apiKey, selectedModelId, selectedProviderId])

  const disconnect = useCallback((): void => {
    setApiKeyState('')
    resetConnection()
  }, [resetConnection])

  const connectOAuth = useCallback((): void => {
    if (selectedProviderId !== null) {
      startOAuth(selectedProviderId)
    }
  }, [selectedProviderId])

  const disconnectOAuth = useCallback(async (): Promise<void> => {
    if (selectedProviderId === null) {
      return
    }
    await apiDisconnectOAuth(selectedProviderId)
    setOauthConnected(false)
    resetConnection()
  }, [selectedProviderId, resetConnection])

  const modelCompatible =
    selectedModel !== null &&
    selectedModel.supports_tools &&
    selectedModel.supports_streaming &&
    selectedModel.supports_structured_output

  const oauthSupported = selectedProvider?.oauth_supported ?? false
  const oauthConfigured = selectedProvider?.oauth_configured ?? false
  const hasCredential = apiKey.trim().length > 0 || oauthConnected

  const isReady =
    selectedProviderId !== null &&
    selectedModelId !== null &&
    hasCredential &&
    modelCompatible &&
    connection !== 'testing' &&
    connection !== 'error'

  return {
    catalogStatus,
    providers,
    catalogError,
    selectedProviderId,
    selectedModelId,
    selectedProvider,
    selectedModel,
    apiKey,
    connection,
    connectionMessage,
    needsInference,
    isReady,
    oauthSupported,
    oauthConfigured,
    oauthConnected,
    selectProvider,
    selectModel,
    setApiKey,
    test,
    connectOAuth,
    disconnectOAuth,
    disconnect,
    refresh,
  }
}