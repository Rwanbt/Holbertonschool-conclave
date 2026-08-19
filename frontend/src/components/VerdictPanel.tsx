import type { ArbiterVerdict } from '../types'

const DECISION_LABELS: Record<ArbiterVerdict['decision'], string> = {
  go: 'GO — on avance',
  go_with_conditions: 'GO avec conditions',
  no_go: 'NO GO',
}

interface VerdictPanelProps {
  verdict: ArbiterVerdict
}

export function VerdictPanel({ verdict }: VerdictPanelProps) {
  return (
    <section className="verdict" aria-label="Verdict de l'arbitre">
      <h2>Verdict de l’Arbitre</h2>
      <p className={`verdict-decision verdict-decision--${verdict.decision}`}>
        {DECISION_LABELS[verdict.decision]} — {verdict.score}/100
      </p>
      <p className="verdict-disagreement">
        <strong>Désaccord principal :</strong> {verdict.main_disagreement}
      </p>
      {(verdict.priority_risks.length > 0 || verdict.actions.length > 0) && (
        <div className="verdict-grid">
          {verdict.priority_risks.length > 0 && (
            <div>
              <h3>Risques prioritaires</h3>
              <ol>
                {verdict.priority_risks.map((risk, index) => (
                  <li key={index}>{risk}</li>
                ))}
              </ol>
            </div>
          )}
          {verdict.actions.length > 0 && (
            <div>
              <h3>Actions ordonnées</h3>
              <ol>
                {verdict.actions.map((action, index) => (
                  <li key={index}>{action}</li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
      <p className="verdict-tradeoff">
        <strong>Compromis accepté :</strong> {verdict.accepted_tradeoff}
      </p>
      {verdict.unavailable_agents.length > 0 && (
        <p className="verdict-unavailable">
          Experts absents : {verdict.unavailable_agents.join(', ')}
        </p>
      )}
    </section>
  )
}