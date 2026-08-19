import { describe, expect, it } from 'vitest'
import {
  buildListCommand,
  buildToolCommand,
  reduceToolState,
} from './toolCommands'
import type { ToolCommandResponse, ToolName, ToolState } from './types'

describe('construction des commandes /tools', () => {
  it('construit enable pour chaque outil', () => {
    expect(buildToolCommand('enable', 'measure_current_document')).toBe(
      '/tools enable measure_current_document',
    )
    expect(buildToolCommand('enable', 'find_security_indicators_in_current_document')).toBe(
      '/tools enable find_security_indicators_in_current_document',
    )
    expect(buildToolCommand('enable', 'estimate_current_analysis_cost')).toBe(
      '/tools enable estimate_current_analysis_cost',
    )
  })

  it('construit disable pour chaque outil', () => {
    expect(buildToolCommand('disable', 'estimate_current_analysis_cost')).toBe(
      '/tools disable estimate_current_analysis_cost',
    )
  })

  it('refuse une action inconnue', () => {
    expect(() =>
      buildToolCommand('list' as never, 'measure_current_document'),
    ).toThrow(Error)
  })

  it('construit la commande list', () => {
    expect(buildListCommand()).toBe('/tools list')
  })
})

describe('reduceToolState', () => {
  const tools: readonly ToolState[] = [
    { tool_name: 'measure_current_document', enabled: true, description: 'a' },
    { tool_name: 'estimate_current_analysis_cost', enabled: true, description: 'c' },
  ]

  it('met à jour uniquement l’outil visé', () => {
    const response: ToolCommandResponse = {
      tool_name: 'measure_current_document' as ToolName,
      enabled: false,
    }
    const next = reduceToolState(tools, response)
    expect(next[0].enabled).toBe(false)
    expect(next[1].enabled).toBe(true)
    expect(tools).not.toBe(next)
  })

  it('conserve l’état d’un outil si la commande échoue (pas de réponse)', () => {
    // En cas d'erreur réseau/422, la réponse est absente : l'état n'est jamais réduit.
    expect(tools[0].enabled).toBe(true)
    expect(tools[1].enabled).toBe(true)
  })
})