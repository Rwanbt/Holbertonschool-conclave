import { useCallback, useEffect, useRef, useState } from 'react'
import { applyToolCommand, fetchToolCatalog } from './api/client'
import { toErrorMessage } from './errors'
import { buildToolCommand } from './toolCommands'
import type { ToolName, ToolState } from './types'

export type ToolCatalogStatus = 'loading' | 'ready' | 'mutating' | 'error'

export interface ToolCatalog {
  status: ToolCatalogStatus
  tools: readonly ToolState[]
  pendingToolName: ToolName | null
  errorMessage: string | null
  refresh: () => Promise<void>
  toggle: (toolName: ToolName) => Promise<void>
  runCommand: (command: string) => Promise<void>
}

/**
 * Source de vérité unique du catalogue d'outils, montée une seule fois dans
 * `App`. Chargée automatiquement à l'ouverture : aucune manipulation
 * initiale n'est nécessaire pour voir les états persistés. Les mutations
 * sont sérialisées (un seul `pendingToolName` à la fois) et une réponse
 * réseau obsolète ne peut jamais écraser un état plus récent (compteur de
 * requêtes monotone).
 */
export function useToolCatalog(): ToolCatalog {
  const [status, setStatus] = useState<ToolCatalogStatus>('loading')
  const [tools, setTools] = useState<readonly ToolState[]>([])
  const [pendingToolName, setPendingToolName] = useState<ToolName | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const requestCounter = useRef(0)
  const mutatingRef = useRef(false)

  const refresh = useCallback(async (): Promise<void> => {
    const requestId = ++requestCounter.current
    setStatus((current) => (current === 'mutating' ? current : 'loading'))
    setErrorMessage(null)
    try {
      const catalog = await fetchToolCatalog()
      if (requestId !== requestCounter.current) {
        // Une requête plus récente est déjà partie (ou une mutation a
        // commencé) : cette réponse est obsolète, on l'ignore.
        return
      }
      setTools(catalog.tools)
      setStatus('ready')
    } catch (error) {
      if (requestId !== requestCounter.current) {
        return
      }
      setStatus('error')
      setErrorMessage(toErrorMessage(error))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const applyCommand = useCallback(
    async (command: string, pending: ToolName | null): Promise<void> => {
      if (mutatingRef.current) {
        return
      }
      mutatingRef.current = true
      requestCounter.current += 1
      const requestId = requestCounter.current
      setPendingToolName(pending)
      setStatus('mutating')
      setErrorMessage(null)
      try {
        const response = await applyToolCommand(command)
        if (requestId !== requestCounter.current) {
          return
        }
        setTools(response.tools)
        setStatus('ready')
      } catch (error) {
        if (requestId !== requestCounter.current) {
          return
        }
        // Une erreur conserve l'ancien état (`tools` n'est pas modifié) et
        // affiche un message actionnable.
        setStatus('error')
        setErrorMessage(toErrorMessage(error))
      } finally {
        mutatingRef.current = false
        setPendingToolName(null)
      }
    },
    [],
  )

  const toggle = useCallback(
    async (toolName: ToolName): Promise<void> => {
      const tool = tools.find((candidate) => candidate.tool_name === toolName)
      const action = tool?.enabled ? 'disable' : 'enable'
      await applyCommand(buildToolCommand(action, toolName), toolName)
    },
    [applyCommand, tools],
  )

  const runCommand = useCallback(
    async (command: string): Promise<void> => {
      await applyCommand(command, null)
    },
    [applyCommand],
  )

  return { status, tools, pendingToolName, errorMessage, refresh, toggle, runCommand }
}
