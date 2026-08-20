import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolsPanel } from './components/ToolsPanel'
import type { ToolCatalog } from './useToolCatalog'
import type { ToolState } from './types'

function tool(overrides: Partial<ToolState>): ToolState {
  return {
    tool_name: 'measure_current_document',
    enabled: true,
    description: 'Mesure le document.',
    ...overrides,
  }
}

const THREE_TOOLS: readonly ToolState[] = [
  tool({ tool_name: 'measure_current_document', enabled: true }),
  tool({
    tool_name: 'find_security_indicators_in_current_document',
    enabled: false,
    description: 'Cherche des indices de sécurité.',
  }),
  tool({
    tool_name: 'estimate_current_analysis_cost',
    enabled: true,
    description: 'Estime le coût.',
  }),
]

function catalogFixture(overrides: Partial<ToolCatalog> = {}): ToolCatalog {
  return {
    status: 'ready',
    tools: THREE_TOOLS,
    pendingToolName: null,
    errorMessage: null,
    refresh: vi.fn().mockResolvedValue(undefined),
    toggle: vi.fn().mockResolvedValue(undefined),
    runCommand: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

describe('ToolsPanel', () => {
  it('affiche les trois switches avec les états reçus, sans aucun clic', () => {
    render(<ToolsPanel catalog={catalogFixture()} />)
    const switches = screen.getAllByRole('switch')
    expect(switches).toHaveLength(3)
    const byLabel = Object.fromEntries(
      switches.map((el) => [el.getAttribute('aria-label'), el.getAttribute('aria-checked')]),
    )
    expect(byLabel['Mesurer le document : activé']).toBe('true')
    expect(byLabel['Rechercher les indicateurs de sécurité : désactivé']).toBe('false')
    expect(byLabel['Estimer le coût de l’analyse : activé']).toBe('true')
  })

  it('un toggle ne modifie que l’outil ciblé', async () => {
    const toggle = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<ToolsPanel catalog={catalogFixture({ toggle })} />)
    const measureSwitch = screen.getByRole('switch', { name: 'Mesurer le document : activé' })
    await user.click(measureSwitch)
    expect(toggle).toHaveBeenCalledTimes(1)
    expect(toggle).toHaveBeenCalledWith('measure_current_document')
  })

  it('désactive tous les switches pendant une mutation', () => {
    render(
      <ToolsPanel
        catalog={catalogFixture({ status: 'mutating', pendingToolName: 'measure_current_document' })}
      />,
    )
    for (const el of screen.getAllByRole('switch')) {
      expect(el).toBeDisabled()
    }
    expect(screen.getByText('Modification…')).toBeInTheDocument()
  })

  it('une erreur affiche un message actionnable et conserve l’état précédent', () => {
    render(
      <ToolsPanel
        catalog={catalogFixture({ status: 'error', errorMessage: 'Le serveur a répondu 500.' })}
      />,
    )
    expect(screen.getByText('Le serveur a répondu 500.')).toBeInTheDocument()
    // L'état affiché reste celui du dernier catalogue connu (pas de vidage).
    expect(screen.getAllByRole('switch')).toHaveLength(3)
  })

  it('figé pendant une analyse : les switches reflètent la configuration figée et sont désactivés', () => {
    render(
      <ToolsPanel
        catalog={catalogFixture()}
        frozenConfiguration={{
          enabled_tools: ['measure_current_document'],
          disabled_tools: [
            'find_security_indicators_in_current_document',
            'estimate_current_analysis_cost',
          ],
        }}
      />,
    )
    const switches = screen.getAllByRole('switch')
    expect(switches).toHaveLength(3)
    for (const el of switches) {
      expect(el).toBeDisabled()
    }
    expect(
      screen.getByRole('switch', { name: 'Mesurer le document : activé' }),
    ).toBeInTheDocument()
  })

  it('catalogue indisponible : message explicite plutôt qu’une liste vide muette', () => {
    // Sans ce cas, un backend injoignable rendait une <ul> vide : aucun
    // switch visible, et rien n'expliquait pourquoi.
    render(
      <ToolsPanel
        catalog={catalogFixture({
          status: 'error',
          tools: [],
          errorMessage: 'Impossible de joindre le backend.',
        })}
      />,
    )
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    expect(screen.getByText(/Catalogue des outils indisponible/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Actualiser' })).toBeInTheDocument()
  })

  it('affiche l’indication de dépendance du coût envers la mesure', () => {
    render(<ToolsPanel catalog={catalogFixture()} />)
    expect(screen.getByText('Nécessite Mesurer le document')).toBeInTheDocument()
  })
})
