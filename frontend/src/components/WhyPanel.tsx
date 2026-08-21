import {
  collectFailures,
  collectRepairs,
  collectRounds,
  outcomeLabel,
  signalLabel,
} from '../diagnostics'
import { collectToolCalls, EXPERT_ROLE_LABELS } from '../steps'
import type { AnalysisEvent, AnalysisSnapshot } from '../types'

interface WhyPanelProps {
  snapshot: AnalysisSnapshot | null
  events: readonly AnalysisEvent[]
}

function roleLabel(role: string): string {
  return EXPERT_ROLE_LABELS[role as keyof typeof EXPERT_ROLE_LABELS] ?? 'Arbitre'
}

/**
 * « Pourquoi l'agent a fait ça ? » — répondu depuis l'application, en moins de
 * 30 secondes, sans ouvrir le code : ce que le serveur a repéré dans le
 * document, quels outils ont réellement tourné, quelle décision chaque rôle a
 * prise à chaque tour, et la cause exacte de chaque échec avec la marche à
 * suivre.
 */
export function WhyPanel({ snapshot, events }: WhyPanelProps) {
  const rounds = collectRounds(events)
  const toolCalls = collectToolCalls(events)
  const failures = collectFailures(snapshot, events)
  const repairs = collectRepairs(events)
  const security = snapshot?.security ?? null
  const config = snapshot?.tool_configuration ?? null

  return (
    <section className="why-panel" aria-label="Pourquoi ce résultat">
      <h2 className="panel-title">Pourquoi ce résultat&nbsp;?</h2>

      {failures.length > 0 && (
        <div className="why-block">
          <h3>Ce qui a échoué</h3>
          <ul className="why-failures">
            {failures.map((failure) => (
              <li key={`${failure.scope}-${failure.code}`} className="why-failure">
                <span className="why-scope">{failure.scope}</span>
                <code className="why-code">{failure.code}</code>
                <p className="why-what">{failure.explanation.what}</p>
                {failure.explanation.action !== null && (
                  <p className="why-action">→ {failure.explanation.action}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {failures.length === 0 && snapshot !== null && (
        <p className="why-none">Aucune défaillance : tous les rôles ont abouti.</p>
      )}

      {security !== null && (
        <div className="why-block">
          <h3>Contrôle du document soumis</h3>
          {security.prompt_injection_suspected ? (
            <>
              <p className="why-security-flagged">
                Tournures d’instruction repérées dans le document :{' '}
                {security.signals.map(signalLabel).join(', ')}.
              </p>
              <p className="why-what">
                L’analyse a été menée normalement. Le document est transmis au
                modèle encadré comme <strong>donnée</strong>, jamais comme
                consigne ; les outils autorisés sont figés côté serveur et ne
                peuvent pas être modifiés par son contenu.
              </p>
            </>
          ) : (
            <p className="why-none">
              Aucune tournure d’instruction repérée dans le document.
            </p>
          )}
        </div>
      )}

      {config !== null && (
        <div className="why-block">
          <h3>Outils disponibles pour cette analyse</h3>
          <p className="why-what">
            Activés :{' '}
            {config.enabled_tools.length > 0 ? (
              <code>{config.enabled_tools.join(', ')}</code>
            ) : (
              'aucun'
            )}
            {config.disabled_tools.length > 0 && (
              <>
                {' '}· Désactivés : <code>{config.disabled_tools.join(', ')}</code>{' '}
                (leur schéma n’a pas été envoyé au modèle)
              </>
            )}
          </p>
        </div>
      )}

      {rounds.length > 0 && (
        <div className="why-block">
          <h3>Décision prise à chaque tour</h3>
          <ol className="why-rounds">
            {rounds.map((round) => (
              <li key={`${round.role}-${round.round}`} className="why-round">
                <span className="why-round-role">{roleLabel(round.role)}</span>
                <span className="why-round-index">tour {round.round}</span>
                <span className="why-round-outcome">{outcomeLabel(round.outcome)}</span>
                {round.latencyMs !== null && (
                  <span className="why-round-latency">{round.latencyMs} ms</span>
                )}
                {round.protocolError !== null && (
                  <code className="why-code">{round.protocolError}</code>
                )}
                {round.finishReason !== null && (
                  <span className="debug-sub">fin : {round.finishReason}</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      {repairs.length > 0 && (
        <div className="why-block">
          <h3>Réparations structurées</h3>
          <ol className="why-rounds">
            {repairs.map((repair) => (
              <li key={`${repair.role}-${repair.attempt}`} className="why-round">
                <span className="why-round-role">{roleLabel(repair.role)}</span>
                <span className="why-round-index">
                  tentative {repair.attempt}/{repair.maxAttempts}
                </span>
                <span className="why-round-outcome">
                  {repair.status === 'running'
                    ? 'en cours'
                    : repair.status === 'completed'
                      ? 'JSON validé'
                      : 'sortie encore invalide'}
                </span>
                {repair.reason !== null && <code className="why-code">{repair.reason}</code>}
              </li>
            ))}
          </ol>
        </div>
      )}

      {toolCalls.length > 0 && (
        <div className="why-block">
          <h3>Outils réellement exécutés</h3>
          <ol className="why-tools">
            {toolCalls.map((call) => (
              <li key={call.key} className="why-tool">
                <span className="why-round-role">{roleLabel(call.agent_role)}</span>
                <code>{call.tool_name}</code>
                <span className={`why-tool-status why-tool-status--${call.status}`}>
                  {call.status === 'running'
                    ? 'en cours'
                    : call.status === 'success'
                      ? 'succès'
                      : 'échec'}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}
