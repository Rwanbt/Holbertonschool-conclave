import { useEffect, useRef, useState } from 'react'
import { applyToolCommand, fetchToolCatalog } from '../api/client'
import { httpStatusOf, toErrorMessage } from '../errors'
import { buildToolCommand, TOOL_LABELS, type ToolAction } from '../toolCommands'
import type { ToolName, ToolState } from '../types'
import type { ToolCommandResponse } from '../types'

interface ConfirmEntry {
  key: number
  action: string
  message: string
}

const MAX_HISTORY_ENTRIES = 5

const TOOLS_HELP =
  '/tools\n/tools list\n/tools enable <nom>\n/tools disable <nom>'

export function ToolsPanel() {
  const [tools, setTools] = useState<readonly ToolState[] | null>(null)
  const [pendingName, setPendingName] = useState<ToolName | null>(null)
  const [command, setCommand] = useState('')
  const [runningCommand, setRunningCommand] = useState(false)
  const [history, setHistory] = useState<readonly ConfirmEntry[]>([])
  const [helpCopied, setHelpCopied] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const historyKeyRef = useRef(0)

  async function refresh(): Promise<void> {
    setErrorMessage(null)
    try {
      const catalog = await fetchToolCatalog()
      setTools(catalog.tools)
    } catch (error) {
      setErrorMessage(toErrorMessage(error))
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  function pushConfirmation(response: ToolCommandResponse): void {
    historyKeyRef.current += 1
    const entry: ConfirmEntry = {
      key: historyKeyRef.current,
      action: response.action,
      message: response.message,
    }
    setHistory((current) => [...current, entry].slice(-MAX_HISTORY_ENTRIES))
  }

  async function runCommand(raw: string): Promise<void> {
    const trimmed = raw.trim()
    if (trimmed.length === 0 || runningCommand) {
      return
    }
    setErrorMessage(null)
    setRunningCommand(true)
    try {
      const response = await applyToolCommand(trimmed)
      setTools(response.tools)
      pushConfirmation(response)
      setCommand('')
    } catch (error) {
      if (httpStatusOf(error) !== 422) {
        setErrorMessage(toErrorMessage(error))
      } else {
        setErrorMessage(
          'Commande refusée (422) : utilisez /tools, /tools list, /tools enable <nom> ou /tools disable <nom>.',
        )
      }
    } finally {
      setRunningCommand(false)
    }
  }

  async function toggle(tool: ToolState): Promise<void> {
    const action: ToolAction = tool.enabled ? 'disable' : 'enable'
    setErrorMessage(null)
    setPendingName(tool.tool_name)
    try {
      const response = await applyToolCommand(buildToolCommand(action, tool.tool_name))
      setTools(response.tools)
      pushConfirmation(response)
    } catch (error) {
      setErrorMessage(toErrorMessage(error))
    } finally {
      setPendingName(null)
    }
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
        <p className="tools-empty">Lecture du catalogue des outils…</p>
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
            onChange={(event) => {
              setCommand(event.target.value)
              setErrorMessage(null)
            }}
            placeholder="/tools list"
            autoComplete="off"
            spellCheck={false}
          />
          <button type="submit" disabled={runningCommand || command.trim() === ''}>
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
        <pre className="tools-help-code" onClick={() => void copyHelp()}>{TOOLS_HELP}</pre>
      </div>

      {history.length > 0 && (
        <ul className="tools-history" aria-label="Dernières confirmations">
          {history.map((entry, index) => (
            <li key={entry.key} className="tools-history-item">
              <span className={`tools-history-action tools-history-action--${entry.action}`}>
                {entry.action}
              </span>
              <span>{entry.message}</span>
              {index === history.length - 1 && (
                <span className="tools-history-rectified">
                  Catalogue mis à jour depuis response.tools
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {tools !== null && (
        <p className="tools-list-hint">
          Les boutons envoient <code>/tools enable|disable &lt;nom&gt;</code>&nbsp;; le
          champ accepte <code>/tools</code>, <code>/tools list</code> et les deux
          formes précédentes.
        </p>
      )}
    </section>
  )
}