import type { ToolTraceEntry } from '../types'

interface ToolTraceProps {
  trace: ToolTraceEntry[]
}

function formatJson(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2)
}

function formatDuration(durationMs: number): string {
  return `${durationMs} ms`
}

export function ToolTrace({ trace }: ToolTraceProps) {
  if (trace.length === 0) {
    return <p className="trace-empty">Aucun outil n’a été appelé.</p>
  }

  return (
    <ol className="trace-list">
      {trace.map((entry) => {
        const isError = entry.status === 'error'
        return (
          <li
            key={entry.sequence}
            className={`trace-entry${isError ? ' trace-entry--error' : ''}`}
            aria-label={`Outil ${entry.tool_name}, séquence ${entry.sequence}, statut ${
              isError ? 'échec' : 'succès'
            }`}
          >
            <div className="trace-entry-header">
              <span className="trace-sequence">#{entry.sequence}</span>
              <span className="trace-tool">{entry.tool_name}</span>
              <span className={`trace-badge${isError ? ' trace-badge--error' : ''}`}>
                {isError ? 'ÉCHEC' : 'OK'}
              </span>
              <span className="trace-duration">
                {formatDuration(entry.duration_ms)}
              </span>
            </div>

            {entry.error_code !== null && (
              <p className="trace-error-code">
                Code d’erreur : {entry.error_code}
              </p>
            )}

            <details className="trace-summary">
              <summary>Résumé d’entrée</summary>
              <pre>{formatJson(entry.input_summary)}</pre>
            </details>

            <details className="trace-summary">
              <summary>
                Résumé de sortie
                {entry.output_summary === null ? ' (indisponible)' : ''}
              </summary>
              {entry.output_summary === null ? (
                <p className="trace-no-output">Aucune sortie.</p>
              ) : (
                <pre>{formatJson(entry.output_summary)}</pre>
              )}
            </details>
          </li>
        )
      })}
    </ol>
  )
}