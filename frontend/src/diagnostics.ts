import type { AnalysisEvent, AnalysisSnapshot, AgentRole } from './types'

/**
 * Traduction des codes d'erreur en explication ACTIONNABLE.
 *
 * Le checkpoint exige de répondre à « pourquoi l'agent a fait ça ? » depuis
 * l'application, pas depuis le code. Un code brut (`provider_unavailable`)
 * ne suffit pas : il faut dire ce qui s'est passé ET quoi faire.
 *
 * Tout code inconnu est rendu tel quel plutôt que masqué : mieux vaut
 * afficher un code non traduit que prétendre l'avoir compris.
 */
export interface Explanation {
  /** Ce qui s'est passé, en une phrase. */
  what: string
  /** Ce que l'utilisateur peut faire. Null quand il n'y a rien à faire. */
  action: string | null
}

const EXPLANATIONS: Record<string, Explanation> = {
  provider_unavailable: {
    what: "Le fournisseur MiniMax n'a pas répondu (réseau coupé, clé invalide ou service indisponible).",
    action:
      'Vérifiez la connexion réseau et la valeur de MINIMAX_API_KEY côté serveur, puis relancez une analyse.',
  },
  expert_timeout: {
    what: "L'expert a dépassé son délai maximal avant de conclure.",
    action:
      'Vérifiez le délai côté backend (90 s recommandé), puis augmentez ANALYSIS_TIMEOUT_SECONDS si nécessaire.',
  },
  arbiter_timeout: {
    what: "L'arbitre a dépassé son délai maximal avant de rendre un verdict.",
    action: 'Réessayez, ou augmentez ARBITER_TIMEOUT_SECONDS.',
  },
  analysis_timeout: {
    what: "L'analyse complète a dépassé son délai global.",
    action: 'Réessayez avec un document plus court, ou augmentez ANALYSIS_TIMEOUT_SECONDS.',
  },
  protocol_error: {
    what: "Le modèle n'a pas respecté l'enveloppe de réponse imposée, même après une tentative de correction.",
    action:
      'Relancez. Si cela se répète, vérifiez le modèle MiniMax configuré, le streaming et le budget EXPERT_MAX_OUTPUT_TOKENS.',
  },
  structured_output_error: {
    what: "Le modèle a répondu, mais sa sortie ne validait pas le schéma exigé — elle a donc été refusée plutôt qu'affichée.",
    action:
      'Relancez avec un budget de sortie suffisant ; pour le Comptable, vérifiez aussi que mesure_current_document puis estimate_current_analysis_cost ont été exécutés.',
  },
  max_rounds_reached: {
    what: "L'agent a atteint la limite de tours d'outils sans jamais conclure.",
    action: 'Augmentez AGENT_MAX_ROUNDS, ou simplifiez le document.',
  },
  repeated_tool_call: {
    what: "L'agent a redemandé exactement le même outil : la boucle a été coupée pour éviter un cycle infini.",
    action: 'Relancez l’analyse.',
  },
  one_tool_per_round: {
    what: "L’agent a demandé plusieurs outils dans le même tour. Le garde-fou n’en a exécuté qu’un et lui a demandé de redemander les suivants aux tours suivants.",
    action: null,
  },
  insufficient_expertise: {
    what: 'Moins de deux experts ont produit une sortie exploitable : aucun verdict ne pouvait être rendu honnêtement.',
    action: 'Consultez le détail par expert ci-dessous pour voir lequel a échoué et pourquoi.',
  },
  arbiter_error: {
    what: "Les experts ont abouti, mais l'arbitre n'a pas pu rendre de verdict valide.",
    action: 'Les sorties des experts restent consultables ci-dessus.',
  },
  start_timeout: {
    what: "L’analyse est restée en attente sans recevoir l’ordre de démarrage.",
    action: "Relancez une nouvelle analyse après avoir vérifié la connexion au backend.",
  },
  tool_disabled: {
    what: "L'outil demandé était désactivé dans la configuration figée de cette analyse.",
    action: 'Activez-le depuis le panneau des outils avant de lancer la prochaine analyse.',
  },
  missing_prerequisite: {
    what: "L'estimation de coût a été demandée sans que le document ait été mesuré au préalable.",
    action: 'Activez « Mesurer le document » en plus de « Estimer le coût ».',
  },
  unknown_tool: {
    what: "Le modèle a demandé un outil qui n'existe pas ; l'appel a été refusé.",
    action: null,
  },
  invalid_arguments: {
    what: "Les arguments d'appel d'outil produits par le modèle n'étaient pas un objet JSON valide.",
    action: null,
  },
  internal_error: {
    what: "Une erreur inattendue est survenue côté serveur ; elle a été tracée dans les journaux.",
    action: 'Consultez les journaux du backend pour le détail de la trace.',
  },
  server_restart: {
    what: 'Le serveur a redémarré alors que cette analyse était en cours.',
    action: 'Relancez une nouvelle analyse : les résultats déjà persistés restent consultables.',
  },
  empty_output: {
    what: "Le modèle a renvoyé une réponse vide.",
    action: 'Relancez l’analyse.',
  },
}

export function explainErrorCode(code: string): Explanation {
  return (
    EXPLANATIONS[code] ?? {
      what: `Code d'erreur non répertorié : ${code}.`,
      action: null,
    }
  )
}

export interface RoundView {
  role: AgentRole
  round: number
  outcome: string | null
  latencyMs: number | null
  protocolError: string | null
  finishReason: string | null
}

const OUTCOME_LABELS: Record<string, string> = {
  tool_calls: 'a demandé un outil',
  final_response: 'a rendu sa réponse finale',
  protocol_error: 'a produit une sortie finale à réparer',
  provider_error: 'a échoué côté fournisseur',
  max_rounds: 'a atteint la limite de tours',
  missing_required_tools: 'doit encore appeler un outil obligatoire',
}

export function outcomeLabel(outcome: string | null): string {
  if (outcome === null) {
    return 'en cours'
  }
  return OUTCOME_LABELS[outcome] ?? outcome
}

function readRole(event: AnalysisEvent): AgentRole | null {
  const role = event.payload.role
  return role === 'avocat' || role === 'procureur' || role === 'comptable' || role === 'arbitre'
    ? role
    : null
}

/**
 * Reconstruit les tours agentiques par rôle : combien de tours, ce que chacun
 * a produit, et en combien de temps. C'est la réponse directe à « pourquoi
 * l'agent a fait ça ? » — on voit la décision prise à chaque tour.
 */
export function collectRounds(events: readonly AnalysisEvent[]): readonly RoundView[] {
  const views: RoundView[] = []
  for (const event of events) {
    if (event.type !== 'agent.round.started' && event.type !== 'agent.round.completed') {
      continue
    }
    const role = readRole(event)
    const round = event.payload.round
    if (role === null || typeof round !== 'number') {
      continue
    }
    if (event.type === 'agent.round.started') {
      views.push({
        role,
        round,
        outcome: null,
        latencyMs: null,
        protocolError: null,
        finishReason: null,
      })
      continue
    }
    const open = views.find((view) => view.role === role && view.round === round)
    if (open !== undefined) {
      open.outcome = typeof event.payload.outcome === 'string' ? event.payload.outcome : null
      open.latencyMs =
        typeof event.payload.latency_ms === 'number' ? event.payload.latency_ms : null
      open.protocolError =
        typeof event.payload.protocol_error === 'string'
          ? event.payload.protocol_error
          : null
      open.finishReason =
        typeof event.payload.finish_reason === 'string'
          ? event.payload.finish_reason
          : null
    }
  }
  return views
}

export interface RepairView {
  role: AgentRole
  attempt: number
  maxAttempts: number
  status: 'running' | 'completed' | 'failed'
  reason: string | null
}

export function collectRepairs(events: readonly AnalysisEvent[]): readonly RepairView[] {
  const repairs: RepairView[] = []
  for (const event of events) {
    if (
      event.type !== 'agent.repair.started' &&
      event.type !== 'agent.repair.completed' &&
      event.type !== 'agent.repair.failed'
    ) {
      continue
    }
    const role = readRole(event)
    const attempt = event.payload.attempt
    const maxAttempts = event.payload.max_attempts
    if (
      role === null ||
      typeof attempt !== 'number' ||
      typeof maxAttempts !== 'number'
    ) {
      continue
    }
    const existing = repairs.find(
      (repair) => repair.role === role && repair.attempt === attempt,
    )
    const status =
      event.type === 'agent.repair.started'
        ? 'running'
        : event.type === 'agent.repair.completed'
          ? 'completed'
          : 'failed'
    const reason =
      typeof event.payload.reason === 'string'
        ? event.payload.reason
        : typeof event.payload.error_detail === 'string'
          ? event.payload.error_detail
          : null
    if (existing !== undefined) {
      existing.status = status
      existing.reason = reason ?? existing.reason
    } else {
      repairs.push({ role, attempt, maxAttempts, status, reason })
    }
  }
  return repairs
}

export interface FailureView {
  scope: string
  code: string
  explanation: Explanation
}

/**
 * Toutes les défaillances observées, de la plus globale à la plus locale.
 * Une analyse qui échoue expose ainsi sa cause ET celle de chaque rôle.
 */
export function collectFailures(
  snapshot: AnalysisSnapshot | null,
  events: readonly AnalysisEvent[],
): readonly FailureView[] {
  const failures: FailureView[] = []
  const seen = new Set<string>()

  function push(scope: string, code: string | null | undefined): void {
    if (!code) {
      return
    }
    const key = `${scope}:${code}`
    if (seen.has(key)) {
      return
    }
    seen.add(key)
    failures.push({ scope, code, explanation: explainErrorCode(code) })
  }

  if (snapshot !== null) {
    push('Analyse', snapshot.error_code)
    push('Avocat', snapshot.avocat.error_code)
    push('Procureur', snapshot.procureur.error_code)
    push('Comptable', snapshot.comptable.error_code)
  }

  for (const event of events) {
    const code = event.payload.error_code
    if (typeof code !== 'string') {
      continue
    }
    if (event.type === 'arbiter.failed') {
      push('Arbitre', code)
    } else if (event.type === 'expert.failed') {
      const role = readRole(event)
      push(role === null ? 'Expert' : role[0].toUpperCase() + role.slice(1), code)
    } else if (event.type === 'tool.failed') {
      const tool = event.payload.tool_name
      push(`Outil ${typeof tool === 'string' ? tool : '?'}`, code)
    }
  }

  return failures
}

const SIGNAL_LABELS: Record<string, string> = {
  override_instructions: 'demande d’ignorer les instructions',
  role_reassignment: 'tentative de changement de rôle',
  system_prompt_exfiltration: 'demande de révéler le prompt système',
  secret_exfiltration: 'demande de révéler un secret ou une clé',
  verdict_forcing: 'tentative d’imposer le verdict',
  fake_system_turn: 'faux tour « system » simulé',
  marker_forgery: 'contrefaçon des balises de protocole',
}

export function signalLabel(signal: string): string {
  return SIGNAL_LABELS[signal] ?? signal
}
