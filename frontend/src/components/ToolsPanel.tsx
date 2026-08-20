import { useState } from 'react'
import type { ToolCatalog } from '../useToolCatalog'
import { TOOL_LABELS } from '../toolCommands'
import type { ToolConfiguration, ToolName, ToolState } from '../types'

const TOOLS_HELP = '/tools\n/tools list\n/tools enable <nom>\n/tools disable <nom>'

const TOOL_DEPENDENCY: Partial<Record<ToolName, { on: ToolName; label: string }>> = {
  estimate_current_analysis_cost: {
    on: 'measure_current_document',
    label: 'Nécessite Mesurer le document',
  },
}

interface ToolsPanelProps {
  catalog: ToolCatalog
  /** Configuration figée de l'analyse en cours (queued/running), ou null
   * pour la première page / entre deux analyses : dans ce cas le panneau
   * pilote le registre global destiné à la PROCHAINE analyse. */
  frozenConfiguration?: ToolConfiguration | null
}

function frozenToolStates(
  frozen: ToolConfiguration,
  reference: readonly ToolState[],
): readonly ToolState[] {
  const descriptionOf = (name: ToolName): string =>
    reference.find((tool) => tool.tool_name === name)?.description ?? ''
  const rows: ToolState[] = [
    ...frozen.enabled_tools.map((tool_name) => ({
      tool_name,
      enabled: true,
      description: descriptionOf(tool_name),
    })),
    ...frozen.disabled_tools.map((tool_name) => ({
      tool_name,
      enabled: false,
      description: descriptionOf(tool_name),
    })),
  ]
  return rows.sort((a, b) => a.tool_name.localeCompare(b.tool_name))
}

export function ToolsPanel({ catalog, frozenConfiguration = null }: ToolsPanelProps) {
  const [command, setCommand] = useState('')
  const [helpCopied, setHelpCopied] = useState(false)

  const isFrozen = frozenConfiguration !== null
  const displayedTools = isFrozen
    ? frozenToolStates(frozenConfiguration, catalog.tools)
    : catalog.tools
  const interactive = !isFrozen
  const mutating = catalog.status === 'mutating'
  const loading = catalog.status === 'loading'

  async function runCommand(raw: string): Promise<void> {
    const trimmed = raw.trim()
    if (trimmed.length === 0 || mutating) {
      return
    }
    await catalog.runCommand(trimmed)
    setCommand('')
  }

  async function toggle(tool: ToolState): Promise<void> {
    if (!interactive || mutating) {
      return
    }
    await catalog.toggle(tool.tool_name)
  }

  async function copyHelp(): Promise<void> {
    try {
      await navigator.clipboard.writeText(TOOLS_HELP)
      setHelpCopied(true)
    } catch {
      setHelpCopied(false)
    }
  }

  return (
    <section className="tools-panel" aria-label="Outils disponibles pour la prochaine analyse">
      <header className="tools-header">
        <h2>{isFrozen ? "Outils figés pour cette analyse" : 'Outils disponibles pour la prochaine analyse'}</h2>
        {!isFrozen && (
          <button type="button" onClick={() => void catalog.refresh()} disabled={mutating}>
            Actualiser
          </button>
        )}
      </header>
      <p className="tools-note">
        {isFrozen
          ? "Configuration figée à la création de cette analyse : elle ne peut plus changer, même si le registre global change ensuite."
          : 'L’état vit dans la base SQLite ; il est lu à chaque exécution. Chaque switch modifie réellement l’état persistant, immédiatement.'}
      </p>
      {catalog.status === 'error' && catalog.errorMessage !== null && (
        <p className="status-error">{catalog.errorMessage}</p>
      )}
      {loading && <p className="tools-empty">Lecture du catalogue des outils…</p>}
      {!loading && displayedTools.length === 0 && (
        // Sans ce cas explicite, un catalogue vide (backend injoignable, CORS,
        // 500) rendait une liste vide : l'utilisateur ne voyait AUCUN switch
        // et pouvait croire que la fonctionnalité n'existe pas.
        <p className="status-error">
          Catalogue des outils indisponible : les switches ne peuvent pas être
          affichés. Vérifiez que le backend répond sur <code>GET /api/tools</code>,
          puis cliquez sur « Actualiser ».
        </p>
      )}
      {!loading && displayedTools.length > 0 && (
        <ul className="tools-list">
          {displayedTools.map((tool) => {
            const dependency = TOOL_DEPENDENCY[tool.tool_name]
            const dependencyUnmet =
              dependency !== undefined &&
              !displayedTools.some((t) => t.tool_name === dependency.on && t.enabled)
            const isPending = catalog.pendingToolName === tool.tool_name
            const disabled = !interactive || mutating
            const stateLabel = isPending
              ? 'Modification…'
              : tool.enabled
                ? 'Activé'
                : 'Désactivé'
            return (
              <li key={tool.tool_name} className="tools-item">
                <div className="tools-item-text">
                  <span className="tools-item-name">{TOOL_LABELS[tool.tool_name]}</span>
                  <code className="tools-item-slot">{tool.tool_name}</code>
                  {tool.description !== '' && (
                    <span className="tools-item-description">{tool.description}</span>
                  )}
                  {dependency !== undefined && (
                    <span className="tools-item-dependency">{dependency.label}</span>
                  )}
                  {dependency !== undefined && dependencyUnmet && tool.enabled && (
                    <span className="tools-item-dependency-warning">
                      Prérequis indisponible : l’outil renverra une erreur contrôlée.
                    </span>
                  )}
                </div>
                <div className="tools-item-control">
                  <button
                    type="button"
                    role="switch"
                    aria-checked={tool.enabled}
                    aria-label={`${TOOL_LABELS[tool.tool_name]} : ${tool.enabled ? 'activé' : 'désactivé'}`}
                    className={`tools-switch${tool.enabled ? ' tools-switch--on' : ''}`}
                    onClick={() => void toggle(tool)}
                    disabled={disabled}
                  >
                    <span className="tools-switch-track">
                      <span className="tools-switch-thumb" />
                    </span>
                  </button>
                  <span className="tools-item-state" aria-live="polite">
                    {stateLabel}
                  </span>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {!isFrozen && (
        <details className="tools-advanced">
          <summary>Commande avancée</summary>
          <form
            className="tools-command"
            onSubmit={(event) => {
              event.preventDefault()
              void runCommand(command)
            }}
          >
            <label className="tools-command-label" htmlFor="tools-command-input">
              Commande outils
            </label>
            <div className="tools-command-row">
              <input
                id="tools-command-input"
                name="tools-command"
                className="tools-command-input"
                type="text"
                value={command}
                onChange={(event) => setCommand(event.target.value)}
                placeholder="/tools list"
                autoComplete="off"
                spellCheck={false}
                disabled={mutating}
              />
              <button type="submit" disabled={mutating || command.trim() === ''}>
                Exécuter
              </button>
            </div>
          </form>

          <div className="tools-help">
            <div className="tools-help-header">
              <span>Aide copiable</span>
              <button type="button" onClick={() => void copyHelp()}>
                {helpCopied ? 'Copié' : 'Copier'}
              </button>
            </div>
            <pre className="tools-help-code" onClick={() => void copyHelp()}>
              {TOOLS_HELP}
            </pre>
          </div>
        </details>
      )}
    </section>
  )
}
