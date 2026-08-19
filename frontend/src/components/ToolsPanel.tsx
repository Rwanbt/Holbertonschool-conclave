import { useState } from 'react'
import { applyToolCommand, fetchToolCatalog } from '../api/client'
import { toErrorMessage } from '../errors'
import { buildToolCommand, reduceToolState, TOOL_LABELS } from '../toolCommands'
import type { ToolName, ToolState } from '../types'

export function ToolsPanel() {
  const [tools, setTools] = useState<readonly ToolState[] | null>(null)
  const [pendingName, setPendingName] = useState<ToolName | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  async function refresh(): Promise<void> {
    setErrorMessage(null)
    try {
      const catalog = await fetchToolCatalog()
      setTools(catalog.tools)
    } catch (error) {
      setErrorMessage(toErrorMessage(error))
    }
  }

  async function toggle(tool: ToolState): Promise<void> {
    const action = tool.enabled ? 'disable' : 'enable'
    setErrorMessage(null)
    setPendingName(tool.tool_name)
    try {
      const response = await applyToolCommand(buildToolCommand(action, tool.tool_name))
      setTools((current) => (current === null ? current : reduceToolState(current, response)))
    } catch (error) {
      setErrorMessage(toErrorMessage(error))
    } finally {
      setPendingName(null)
    }
  }

  return (
    <section className="tools-panel" aria-label="Gestion des outils">
      <header className="tools-header">
        <h2>Outils du serveur</h2>
        <button type="button" onClick={() => void refresh()}>
          Actualiser
        </button>
      </header>
      <p className="tools-note">
        L’état vit dans la base SQLite ; il est lu à chaque exécution. Les commandes
        partent uniquement via <code>POST /api/tool-commands</code>.
      </p>
      {errorMessage !== null && <p className="status-error">{errorMessage}</p>}
      {tools === null && (
        <p className="tools-empty">Cliquez sur « Actualiser » pour lire l’état des outils.</p>
      )}
      {tools !== null && (
        <ul className="tools-list">
          {tools.map((tool) => (
            <li key={tool.tool_name} className="tools-item">
              <div className="tools-item-text">
                <span className="tools-item-name">{TOOL_LABELS[tool.tool_name]}</span>
                <code className="tools-item-slot">{tool.tool_name}</code>
                <span
                  className={`tools-badge${tool.enabled ? ' tools-badge--on' : ' tools-badge--off'}`}
                >
                  {tool.enabled ? 'Activé' : 'Désactivé'}
                </span>
              </div>
              <button
                type="button"
                onClick={() => void toggle(tool)}
                disabled={pendingName === tool.tool_name}
              >
                {pendingName === tool.tool_name ? '…' : tool.enabled ? 'Désactiver' : 'Activer'}
              </button>
            </li>
          ))}
        </ul>
      )}
      {tools !== null && (
        <p className="tools-list-hint">
          Chaque bouton envoie une commande <code>/tools enable|disable &lt;nom&gt;</code>&nbsp;;
          la commande <code>/tools list</code> renvoie un 422 côté serveur (GET /api/tools fait foi).
        </p>
      )}
    </section>
  )
}