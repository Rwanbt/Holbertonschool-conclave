import { EXPERT_ROLE_LABELS, EXPERT_STATUS_LABELS } from '../steps'
import type { ExpertRun, ExpertRole, LiveResponseView } from '../types'

interface ExpertColumnProps {
  role: ExpertRole
  run: ExpertRun
  live: LiveResponseView
}

function priorityLabel(priority: string): string {
  const labels: Record<string, string> = {
    low: 'Faible',
    medium: 'Moyenne',
    high: 'Élevée',
  }
  return labels[priority] ?? priority
}

export function ExpertColumn({ role, run, live }: ExpertColumnProps) {
  const output = run.output
  const hasDraft = live.text.length > 0
  const streaming = live.status === 'streaming' && run.status === 'running'
  const interrupted = live.status === 'failed' && hasDraft
  const runFailed = (run.status === 'error' || run.status === 'timeout') && output === null
  const awaitingValidation =
    !output && hasDraft && !interrupted && !streaming && run.status !== 'pending'

  return (
    <section className={`expert-column expert-column--${role}`} aria-label={EXPERT_ROLE_LABELS[role]}>
      <header className="expert-header">
        <h2>{EXPERT_ROLE_LABELS[role]}</h2>
        <span className={`expert-status expert-status--${run.status}`}>
          {EXPERT_STATUS_LABELS[run.status]}
        </span>
        {run.error_code !== null && (
          <span className="expert-error-code">Code : {run.error_code}</span>
        )}
      </header>

      {run.status !== 'completed' && (
        <p className="live-status" aria-live="polite">
          {interrupted && 'Réponse interrompue — non validée.'}
          {!interrupted && runFailed && 'Aucune sortie validée.'}
          {!interrupted && !runFailed && streaming && hasDraft && 'Génération MiniMax en direct — validation en attente'}
          {!interrupted && !runFailed && !streaming && awaitingValidation && 'Brouillon reçu — validation en attente'}
          {!interrupted &&
            !runFailed &&
            !(streaming && hasDraft) &&
            !awaitingValidation &&
            !output &&
            run.status === 'running' &&
            'Préparation de l’analyse…'}
        </p>
      )}

      {run.status === 'pending' && (
        <p className="expert-empty">En attente de démarrage.</p>
      )}

      {hasDraft && output === null && (
        <div
          className={`live-draft${interrupted ? ' live-draft--failed' : ''}`}
          aria-label={`Brouillon live de ${EXPERT_ROLE_LABELS[role]}`}
        >
          <p className="live-draft-text">{live.text}</p>
          {streaming && (
            <span className="live-cursor" aria-hidden="true" />
          )}
        </div>
      )}

      {interrupted && (
        <p className="live-draft-failed">Brouillon conservé, interrompu — non validé.</p>
      )}

      {runFailed && !hasDraft && (
        <p className="expert-empty">Aucune sortie validée.</p>
      )}

      {output !== null && (
        <div className="expert-output">
          <p className="expert-summary">{output.summary}</p>
          <p className="expert-score">
            {output.score_label} — {output.score}/100
          </p>
          <ol className="expert-findings">
            {output.findings.map((finding, index) => (
              <li key={`${finding.title}-${index}`} className="expert-finding">
                <span className={`finding-priority finding-priority--${finding.priority}`}>
                  {priorityLabel(finding.priority)}
                </span>
                <strong>{finding.title}</strong>
                <p>{finding.evidence}</p>
                <p>{finding.impact}</p>
              </li>
            ))}
          </ol>
          {output.recommendations.length > 0 && (
            <div className="expert-recommendations">
              <h3>Recommandations</h3>
              <ol>
                {output.recommendations.map((recommendation, index) => (
                  <li key={index}>{recommendation}</li>
                ))}
              </ol>
            </div>
          )}
          {output.unavailable_tools.length > 0 && (
            <p className="expert-unavailable">
              Outils indisponibles : {output.unavailable_tools.join(', ')}
            </p>
          )}
        </div>
      )}
    </section>
  )
}