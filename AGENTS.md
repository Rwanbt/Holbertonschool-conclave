# Agent CONCLAVE — Palier 3 (outils et boucle agent)

Ce fichier documente l'agent `POST /api/p3/agent`. Toute modification du
prompt système, des descriptions d'outils ou des garde-fous doit être faite
dans `backend/app/agent.py` PUIS recopiée ici (et inversement).

## Prompt système (recopié tel quel)

```text
Tu es un agent d'analyse documentaire du backend CONCLAVE.
Le document est déjà chargé sur le serveur : tu ne le reçois jamais dans la conversation.
Tu peux utiliser les trois outils suivants, sans argument : measure_current_document,
find_security_indicators_in_current_document et estimate_current_analysis_cost.
Tu ne fabriques jamais un résultat : si un outil est indisponible ou en erreur,
tu déclares que tu ne peux pas vérifier l'information plutôt que d'inventer une valeur.
Tu n'inventes jamais de chemin de fichier ni de route.
Chaque demande est traitée étape par étape : 1) décider quels outils sont nécessaires,
2) les appeler, 3) examiner les résultats réels obtenus,
4) rédiger la réponse finale en français, courte et précise,
en citant uniquement des valeurs observées dans les résultats d'outils.
```

## Descriptions d'outils (recopiées telles quelles)

Tous les outils ne prennent **aucun argument** : schéma `{"type": "object",
"properties": {}, "additionalProperties": false}`. Le document reste côté
serveur : il n'apparaît ni dans la conversation, ni dans les schémas, ni
dans la trace.

```text
measure_current_document :
Analyse le document chargé sur le serveur et renvoie ses métriques : nombre de
caractères, de mots, de lignes et une estimation du nombre de jetons d'entrée.
Cet outil ne prend aucun argument.

find_security_indicators_in_current_document :
Analyse le document chargé sur le serveur et renvoie jusqu'à dix indices de sécurité
locaux (clé, authentification, autorisation, injection, vie privée, disponibilité).
Cet outil ne prend aucun argument.

estimate_current_analysis_cost :
Estime le coût en dollars d'une analyse du document chargé sur le serveur, en fonction
des tarifs MiniMax configurés et du budget de sortie. Cet outil ne prend aucun argument.
```

## Signatures

```text
# Fonctions métier (pures, aucun effet de bord, jamais simulées en test) — tools.py
measure_document(text: str) -> DocumentMetrics
find_security_indicators(text: str, patterns: tuple[SecurityPattern, ...]) -> list[SecurityFinding]
estimate_analysis_cost(input_tokens: int, output_token_budget: int, pricing: ModelPricing) -> CostEstimate

# Adaptateurs (exposent le document chargé, sans argument) — agent.py
measure_current_document() -> DocumentMetrics
find_security_indicators_in_current_document() -> list[SecurityFinding]
estimate_current_analysis_cost() -> CostEstimate
```

Voir OUTILS.md pour les types exacts (`DocumentMetrics`, `SecurityFinding`,
`ModelPricing`, `CostEstimate`) et le catalogue de motifs
`DEFAULT_SECURITY_PATTERNS`.

## Garde-fous

- **Aucune invention** : si un outil est indisponible, en erreur ou si la
  limite est atteinte, l'agent le déclare ; il ne fabrique jamais de valeur,
  de chemin de fichier ou de route.
- **Document jamais transmis** : ni au modèle, ni dans les arguments d'outil,
  ni dans les `input_summary`/`output_summary` de la trace.
- **Sorties bornées et masquées** : 10 indices maximum ; `matched_text`
  toujours masqué puis tronqué à 40 caractères ; résumés de trace JSON bornés.
- **DISABLED_TOOLS** : liste CSV dans `.env`, modifiable uniquement côté
  serveur (jamais via un endpoint public). Un outil désactivé reste décrit
  au modèle mais son exécuteur renvoie `error_code: "tool_disabled"`.
- **Erreurs d'outil ≠ 500** : chaque erreur d'outil devient une entrée de
  trace `status="error"` présentée au modèle, qui décide de la suite.
- **Boucle bornée** : `MINIMAX_MAX_TOOL_ROUNDS` appels maximum ; appel
  identique répété (`repeated_tool_call`) ou limite atteinte
  (`max_rounds_reached`) arrêtent proprement la boucle.

## Schéma de la boucle

```text
MiniMax (system + tools, pas de document)
   │  tool_calls
   ▼
Serveur : registre → validation nom/arguments → DISABLED_TOOLS → exécution
   │  résultat réel (ou erreur bornée)
   ▼
message role="tool" (tool_call_id) → MiniMax
   │  (jusqu'à MINIMAX_MAX_TOOL_ROUNDS itérations)
   ▼
réponse finale (ou non-vérification) + trace + usage
```

## Tarifs MiniMax-M3 (estimatifs)

- `MINIMAX_INPUT_USD_PER_MILLION` / `MINIMAX_OUTPUT_USD_PER_MILLION`
  dans `.env` : valeurs par défaut d'exemple 0,30 / 1,20 USD par million de
  jetons (palier standard ≤512K, promo de lancement « permanent 50% off »).
- Vérifiés le 19/08/2026 via des agrégateurs tiers concordants (OpenRouter,
  TokenMix, TokenCost, AI//COST). **Estimatifs** : seule la facturation réelle
  MiniMax fait foi ; si les tarifs ne sont pas configurés (0.0 ou absents),
  `estimated_cost_usd` reste `null` et le coût n'est pas revendiqué.

---

# Agent CONCLAVE — Palier 4 (experts, arbitre, persistance SQLite, SSE)

Ce fichier documente le contrat des trois experts et de l'Arbitre. Toute
modification des prompts système, des schémas de sortie ou des garde-fous doit
être faite dans `backend/app/experts.py` PUIS recopiée ici (et inversement).

## Prompts système des experts (recopiés tels quels — backend/app/experts.py)

```text
AVOCAT :
Tu es l'expert AVOCAT de l'analyse documentaire CONCLAVE.
Le document te parvient dans le message utilisateur.
Tu peux utiliser les outils serveur sans argument (métriques, indices
de sécurité, coût) pour étayer ton argumentaire.
Un seul outil par tour : si tu as besoin de plusieurs outils, appelle-les
tour à tour.
Rédige une plaidoirie de la solution proposée par le document, en t'appuyant
sur des faits vérifiables.
Quand tu conclus (sans nouvel appel d'outil a ce tour), reponds obligatoirement
avec l'enveloppe suivante :
<LIVE_RESPONSE> ton raisonnement final de conclusion en francais, bref, lisible,
sans JSON, sans chaine de pensee, sans pretendre etre valide avant la fin
</LIVE_RESPONSE> puis
<FINAL_JSON> UNIQUEMENT l'objet JSON conforme au schema AgentOutput : role, summary,
findings (2 a 5 elements avec title, evidence, impact, priority low|medium|high),
score_label, score (0-100), recommendations (0 a 3), unavailable_tools </FINAL_JSON>.
Aucun texte hors de ces deux balises.

PROCUREUR :
Tu es l'expert PROCUREUR de l'analyse documentaire CONCLAVE.
Le document te parvient dans le message utilisateur.
Tu peux utiliser les outils serveur sans argument (métriques, indices
de sécurité, coût) pour étayer ton réquisitoire.
Un seul outil par tour : si tu as besoin de plusieurs outils, appelle-les
tour à tour.
Démontre les risques, faiblesses et objections que le document soulève,
en t'appuyant sur des faits vérifiables.
Quand tu conclus (sans nouvel appel d'outil a ce tour), reponds obligatoirement
avec l'enveloppe suivante :
<LIVE_RESPONSE> ton raisonnement final de conclusion en francais, bref, lisible,
sans JSON, sans chaine de pensee, sans pretendre etre valide avant la fin
</LIVE_RESPONSE> puis
<FINAL_JSON> UNIQUEMENT l'objet JSON conforme au schema AgentOutput : role, summary,
findings (2 a 5 elements avec title, evidence, impact, priority low|medium|high),
score_label, score (0-100), recommendations (0 a 3), unavailable_tools </FINAL_JSON>.
Aucun texte hors de ces deux balises.

COMPTABLE :
Tu es l'expert COMPTABLE de l'analyse documentaire CONCLAVE.
Le document te parvient dans le message utilisateur.
Tu dois d'abord observer les métriques du document, puis estimer le coût
d'une analyse, puis seulement conclure.
Un seul outil par tour : au premier tour, demande les métriques ; au tour
suivant, demande l'estimation du coût.
Tu ne produis JAMAIS une conclusion chiffrée sans avoir observé les métriques
réelles ni une estimation de coût sans données réelles : si ces mesures
manquent, tu le signales dans summary et findings sans inventer de valeur.
Quand tu conclus (sans nouvel appel d'outil a ce tour), reponds obligatoirement
avec l'enveloppe suivante :
<LIVE_RESPONSE> ton raisonnement final de conclusion en francais, bref, lisible,
sans JSON, sans chaine de pensee, sans pretendre etre valide avant la fin
</LIVE_RESPONSE> puis
<FINAL_JSON> UNIQUEMENT l'objet JSON conforme au schema AgentOutput : role, summary,
findings (2 a 5 elements avec title, evidence, impact, priority low|medium|high),
score_label, score (0-100), recommendations (0 a 3), unavailable_tools </FINAL_JSON>.
Aucun texte hors de ces deux balises.

ARBITRE :
Tu es l'ARBITRE de l'analyse documentaire CONCLAVE.
Tu reçois le document et les sorties validées des experts (avocat,
procureur, comptable).
Tu peux aussi utiliser les outils serveur sans argument si tu dois vérifier
un chiffre, mais ce n'est pas obligatoire.
Départage les désaccords, puis rends une décision finale.
Quand tu conclus (sans nouvel appel d'outil a ce tour), reponds obligatoirement
avec l'enveloppe suivante :
<LIVE_RESPONSE> ton raisonnement final de decision en francais, bref, lisible,
sans JSON, sans chaine de pensee, sans pretendre etre valide avant la fin
</LIVE_RESPONSE> puis
<FINAL_JSON> UNIQUEMENT l'objet JSON conforme au schema ArbiterVerdict : decision
(go|go_with_conditions|no_go), score (0-100), main_disagreement, priority_risks
(0 a 3), actions (0 a 3), accepted_tradeoff, unavailable_agents </FINAL_JSON>.
Aucun texte hors de ces deux balises.
```

## Boucle Palier 4

```text
POST /api/analyses → analysis.created (persisté) → 3 experts en asyncio.gather
   │  chaque expert : boucle générique run_agent_loop (1 outil/tour, bornée)
   │   → outil réel (état SQLite vérifié) → événements tool.* persistés
   │   → JSON AgentOutput validé (1 réparation structurée max)
   ▼
≥ 2 sorties valides ? ── non → analysis.failed (insufficient_expertise)
   │ oui
Arbitre (document + sorties validées + experts absents) → ArbiterVerdict validé
   │  verdict ok et 3 experts → analysis.completed
   │  verdict ok et 2 experts → analysis.degraded (unavailable_agents imposés)
   │  verdict absent après experts valides → analysis.failed (arbiter_error)
   ▼
événement terminal persisté → SSE fermé
```

## Streaming natif MiniMax-M3 (carte bonus Palier 4)

- **Enveloppe de réponse finale** : `<LIVE_RESPONSE>…</LIVE_RESPONSE><FINAL_JSON>{…}</FINAL_JSON>`.
  Le texte live est un résumé de conclusion en français, bref, lisible, sans
  JSON, borné à `STREAM_MAX_DRAFT_CHARS` (4000) ; le JSON final est
  `AgentOutput` (experts) ou `ArbiterVerdict` (Arbitre). Aucun texte hors de
  ces deux balises (cf. prompts recopiés ci-dessus, qui terminent par
  `_EXPERT_ENVELOPE` / `_ARBITER_ENVELOPE` dans `backend/app/experts.py`).
- **Diffusion en direct** : 4 événements persistés `agent.response.started`,
  `agent.response.delta` (charges de `STREAM_DELTA_BATCH_CHARS` caractères,
  32 par défaut), `agent.response.completed` (JSON validé Pydantic) et
  `agent.response.failed`. Séquence strictement croissante par rôle ; un seul
  `started` par rôle et par analyse ; le JSON final n'apparaît jamais dans un
  delta. `completed` est diffusé avant `expert.completed`/`arbiter.completed`.
- **Implémentation** : `backend/app/streaming.py` (`StreamCollector`,
  `EnvelopeParser`, `ToolCallAssembler`, `normalize_delta`). Le contenu
  MiniMax-M3 peut être cumulatif : `normalize_delta` déduplique les fragments
  qui se chevauchent. Le dernier chunk `choices=[]` avec `usage` est conservé ;
  l'usage est agrégé exactement une fois.
- **Erreurs de protocole** : marqueurs inversés, double section live, JSON final
  manquant, id/nom d'outil invalide → `protocol_error`, la boucle s'arrête sans
  exécuter d'outil. Réponse d'outil sans aucune balise : pas une erreur
  (enveloppe simplement non demandée). Un round live + `tool_calls` mélangés =
  `hybrid_live_and_tool_calls`.
- **Garde-fou de temps des deltas** : `_flush_time` force une diffusion après
  0,1 s même si le paquet n'est pas plein (réactivité du front, miniMax en
  temps réel).

## Garde-fous Palier 4

- **Un seul outil par tour** pour les experts et l'Arbitre : si MiniMax demande
  plusieurs outils, seul le premier est exécuté, les autres reçoivent
  `one_tool_per_round` (chaque `tool_call_id` est toujours répondu).
- **Validation structurée** : toute sortie LLM passe par `AgentOutput` ou
  `ArbiterVerdict` (Pydantic) avant stockage, avant l'Arbitre, avant le front.
  Une seule réparation (message générique, ne nomme jamais d'outil) ; sinon le
  run passe en `error` (`structured_output_error`).
- **Comptable sans preuve = refusé** : une conclusion chiffrée sans les métriques
  et l'estimation de coût réellement exécutées est rejetée par le validateur
  (contrôle structurel de la trace, message générique sans nommer d'outil).
- **Garde-fous de temps** : `expert_timeout_seconds` (30), `arbiter_timeout_seconds`
  (20), `analysis_timeout_seconds` (60). Codes : `expert_timeout`,
  `arbiter_timeout`, `analysis_timeout`.
- **Boucle bornée** : `AGENT_MAX_ROUNDS` (5) ; codes `max_rounds_reached` et
  `repeated_tool_call` arrêtent proprement.
- **Outils désactivés pendant une analyse** : état lu depuis `tool_states`
  (SQLite) à chaque exécution ; un outil désactivé → `tool_disabled` (trace +
  événement `tool.failed`), sans 500.
- **Document jamais journalisé** : les événements SSE et les traces ne
  contiennent que des résumés bornés ; le document ne va à MiniMax que dans le
  message des rôles experts/arbitre.
- **Analyse en cours au redémarrage** → `interrupted` ; les résultats déjà
  persistés restent consultables.

## Commandes `/tools`

Grammaire stricte (`backend/app/toolkit.py::parse_tool_command`) :
`/tools`, `/tools list`, `/tools enable <name>`, `/tools disable <name>`.
Syntaxe ou nom inconnu → **422 sans modification partielle**. État persistant
dans `tool_states` (source de vérité) ; `DISABLED_TOOLS` ne fait qu'initialiser
une base neuve.

## Événements SSE

`analysis.created`, `expert.started`, `tool.started`, `tool.completed`,
`tool.failed`, `expert.completed`, `expert.failed`, `expert.timeout`,
`arbiter.started`, `arbiter.completed`, `arbiter.failed`,
`agent.response.started`, `agent.response.delta`, `agent.response.completed`,
`agent.response.failed`, `analysis.completed`, `analysis.degraded`,
`analysis.failed`, `analysis.interrupted` (au redémarrage). Chaque événement
est écrit en base avant d'être diffusé (identifiant entier croissant) ; reprise
via `Last-Event-ID` ou `?after=<id>` (le maximum des deux est pris en compte) ;
polling toutes les `SSE_POLL_INTERVAL_MS` ms avec keep-alive toutes les
`SSE_KEEPALIVE_SECONDS` s. Le terminal (`analysis.completed`/`degraded`/`failed`)
et son événement sont committés atomiquement par `db.finish_analysis` : un client
ne peut jamais voir un statut terminal sans son événement terminal.
