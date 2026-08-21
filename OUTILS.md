# Outils des agents — contrats typés

## Règle

Un **effet de bord** signifie qu'un outil modifie ou expose quelque chose hors de son calcul local : appel réseau, coût facturé, transmission de données ou écriture persistante. Les outils déterministes (Palier 3) sont purs : ils sont appelés par la boucle agent après une décision explicite de MiniMax (`tool_calls`), puis leurs résultats réels sont fournis au modèle. Aucun routage par mots-clés côté serveur : le nom de l'outil choisi par MiniMax est le seul critère de dispatch.

## Validation des sorties

Toute sortie produite par un appel LLM (expert ou arbitre) est validée par un schéma typé avant d'être consommée : `AgentOutput` pour les trois experts, `ArbiterVerdict` pour l'Arbitre. Une réponse qui ne respecte pas le contrat — champ manquant, type incorrect, hors bornes ou illisible — est rejetée et traitée comme `StructuredOutputError` : elle n'est ni transmise à l'Arbitre ni exposée au front. Seules les sorties validées alimentent la file d'événements. Cette validation est distincte de la validation d'entrée : l'entrée protège le serveur (texte vide, taille, types), la sortie protège le contrat de consommation du front.

Le Palier 3 applique en plus des **sorties bornées et masquées** pour chaque outil : `matched_text` est toujours masqué (jamais la valeur brute) puis tronqué à 40 caractères ; les résultats sont plafonnés (10 indices maximum) ; les `input_summary`/`output_summary` de la trace sont des résumés JSON bornés qui ne contiennent jamais le document ni une clé.

## Types utilisés dans les signatures

```text
AgentRole = "avocat" | "procureur" | "comptable" | "arbitre"
SecurityCategory = "secret" | "authentication" | "authorization" | "injection" | "privacy" | "availability"

DocumentMetrics = {
  character_count: int,
  word_count: int,
  line_count: int,
  estimated_input_tokens: int
}

SecurityPattern = {
  name: str,
  literal_or_regex: str,   # préfixe "re:" -> regex compilée, sinon sous-chaîne littérale
  category: SecurityCategory
}

SecurityFinding = {
  pattern_name: str,
  matched_text: str,        # toujours masqué puis tronqué, jamais la valeur brute
  line_number: int,
  category: SecurityCategory
}

ModelPricing = {
  model_name: str,
  input_usd_per_million_tokens: float,
  output_usd_per_million_tokens: float
}

CostEstimate = {
  model_name: str,
  input_tokens: int,
  output_token_budget: int,
  estimated_cost_usd: float | null,   # arrondi à 6 décimales si configuré
  pricing_configured: bool,
  currency: "USD"
}

ExpertContext = {
  metrics: DocumentMetrics,
  security_findings: list[SecurityFinding],
  cost_estimate: CostEstimate | null
}
```

## Registre

Le registre du Palier 3 expose trois outils SANS argument à MiniMax. Le document reste côté serveur (jamais dans la conversation, ni dans les schémas `tools`, ni dans la trace). Chaque outil possède une **description sémantique exacte** (recopiée dans AGENTS.md) et un **type d'erreur court et explicite** ; toute erreur d'outil devient une entrée de trace `status="error"` avec `error_code`, présentée au modèle — jamais un 500 automatique.

### 1. `measure_document` (fonction métier pure)

```text
measure_document(text: str) -> DocumentMetrics
```

- **Utilisé par :** adaptateur `measure_current_document()` de la boucle agent ; métriques partagées avec Avocat et Comptable.
- **But :** calculer une base factuelle commune et refuser un texte hors limite.
- **Effet de bord : NON.** Calcul local pur, sans réseau ni écriture.
- **Échec défini :** `InvalidDocumentError` si le texte est vide ou dépasse 12 000 caractères.
- **Estimation des jetons :** heuristique documentée de 1 jeton ≈ 4 caractères, arrondie à l'entier supérieur (minimum 1). Simple et reproductible ; elle ne remplace pas le tokeniseur réel de MiniMax.

### 2. `find_security_indicators` (fonction métier pure)

```text
find_security_indicators(
  text: str,
  patterns: tuple[SecurityPattern, ...]
) -> list[SecurityFinding]
```

- **Utilisé par :** adaptateur `find_security_indicators_in_current_document()` de la boucle agent (Procureur en palier ultérieur).
- **But :** localiser des indices factuels, jamais déclarer qu'une vulnérabilité est certaine. Les motifs viennent du catalogue local `DEFAULT_SECURITY_PATTERNS` (12 motifs couvrant les 6 catégories) ; ils ne sont jamais fournis par le client.
- **Effet de bord : NON.** Recherche locale dans le texte.
- **Échec défini :** renvoie une liste vide si aucun indice n'est trouvé ; `InvalidPatternError` si un motif est invalide (rejeté avant toute analyse) ; `InvalidDocumentError` si le texte dépasse la limite.
- **Bornes :** au plus 10 indices, triés par ligne puis nom de motif ; `matched_text` masqué (premier et dernier caractère conservés, intérieur remplacé par `*`) puis tronqué à 40 caractères.

### 3. `estimate_analysis_cost` (fonction métier pure)

```text
estimate_analysis_cost(
  input_tokens: int,
  output_token_budget: int,
  pricing: ModelPricing
) -> CostEstimate
```

- **Utilisé par :** adaptateur `estimate_current_analysis_cost()` de la boucle agent ; résultat fourni au Comptable.
- **But :** fournir une estimation reproductible et conservatrice de l'analyse complète, séparée de l'interprétation LLM. L'adaptateur agrège les budgets des trois experts, de l'Arbitre, des tours configurés et des réparations structurées ; `assumptions` expose ces hypothèses. Devise explicite (USD), arrondi documenté à 6 décimales.
- **Effet de bord : NON.** Calcul arithmétique local.
- **Tarifs absents :** renvoie un résultat contrôlé avec `estimated_cost_usd: null` et `pricing_configured: false` ; ce n'est pas une panne d'outil et ne doit jamais devenir `internal_error`.
- **Tarifs :** `MINIMAX_INPUT_USD_PER_MILLION` / `MINIMAX_OUTPUT_USD_PER_MILLION` dans `.env` ; estiment les tarifs officiels MiniMax-M3 (standard ≤512K : 0,30 / 1,20 USD par million de jetons, promo de lancement, vérifiés le 19/08/2026 via agrégateurs tiers). Estimatifs : les seules valeurs fiables viennent de la facturation réelle MiniMax.

### 4. `run_expert` (implémenté en Palier 4 — backend/app/experts.py)

```text
run_expert(
  role: "avocat" | "procureur" | "comptable",
  analysis_id: str,
  document: str,
  session: AgentSession,
  settings: Settings,
  get_connection: Callable
) -> ExpertRunResult   # output: AgentOutput | null, error_code, usage, trace
```

- **Utilisé par :** les trois experts via la passerelle MiniMax unique et le modèle `MiniMax-M3`.
- **But :** exécuter la boucle d'outils du rôle puis produire un `AgentOutput` JSON validé par Pydantic. Une enveloppe finale incomplète est normalisée par une requête JSON dédiée, sans outils, à température zéro, jusqu'à `STRUCTURED_REPAIR_ATTEMPTS` fois. Toutes les sorties d'experts et les événements sont persistés en SQLite ; le run est marqué `pending → running → completed|error|timeout`.
- **Effet de bord : OUI.** Appel réseau, transmission du document à un tiers, coût API potentiel, écriture SQLite.
- **Échec défini :** `ProviderError`, `TimeoutError` (code `expert_timeout`), `StructuredOutputError` (code `structured_output_error`) après échec de la réparation ; aucune réponse invalide n'est transmise à l'Arbitre.

### 5. `run_arbiter` (implémenté en Palier 4 — backend/app/experts.py)

```text
run_arbiter(
  analysis_id: str,
  document: str,
  valid_outputs: list[AgentOutput],      # 2 ou 3 sorties validées uniquement
  unavailable_agents: list[ExpertRole],
  settings: Settings,
  get_connection: Callable
) -> (ArbiterVerdict | null, ExecutionUsage)
```

- **Utilisé par :** Arbitre, seulement avec deux ou trois sorties validées.
- **But :** produire le verdict final conforme à `ArbiterVerdict` ; l'orchestrateur impose `unavailable_agents` (informations structurelles qu'il est le seul à connaître).
- **Effet de bord : OUI.** Appel réseau, transmission du document et des sorties validées, coût API potentiel, écriture SQLite.
- **Échec défini :** mêmes erreurs que `run_expert` ; les analyses déjà terminées restent visibles (l'analyse passe en `failed` avec `error_code=arbiter_error`).

### 6. États d'outils et commandes `/tools` (Palier 4 — backend/app/toolkit.py)

```text
parse_tool_command(command: str) -> (action, tool_name | null)   # action: list|enable|disable
list_tool_states(conn) -> list[ToolState]
set_tool_state(conn, tool_name, enabled) -> None                 # atomique, idempotent
```

- **Source de vérité :** table SQLite `tool_states`, lue à chaque exécution d'outil via `execute_tool`. `DISABLED_TOOLS` n'initialise qu'une base neuve.
- **Grammaire :** `/tools`, `/tools list`, `/tools enable <name>`, `/tools disable <name>` (noms exacts du registre). Syntaxe ou nom inconnu → **422 sans modification partielle**.
- **Effet de bord : OUI.** Écriture SQLite persistante. Un outil désactivé reste décrit au modèle mais son exécuteur renvoie `error_code: "tool_disabled"` ; la trace et l'événement SSE `tool.failed` le reflètent sans faire crasher l'analyse.

## Boucle agent (Palier 3) et boucle des experts (Palier 4)

La boucle générique `run_agent_loop` (backend/app/agent.py) est partagée : le
wrapper public `run_agent` (P3) et chaque expert/arbitre (P4) l'utilisent avec
leur propre prompt système et leur propre plafond de tours.

MiniMax reçoit le prompt système et les trois schémas d'outils (jamais le
document). Il choisit seul d'appeler un outil (`tool_choice="auto"`) ; le
serveur valide le nom (`unknown_tool` sinon) et les arguments
(`invalid_arguments` sinon), vérifie l'état SQLite courant (`tool_disabled`
sinon), exécute l'adaptateur réel, puis renvoie le résultat dans un message
`role="tool"` avec `tool_call_id`. La boucle fait au plus `AGENT_MAX_ROUNDS`
tours ; un appel identique répété (`repeated_tool_call`), la limite atteinte
(`max_rounds_reached`) ou un dépassement de temps (`expert_timeout`,
`arbiter_timeout`, `analysis_timeout`) arrête proprement l'étape concernée.
Les experts utilisent la règle **un seul outil par tour** (`one_tool_per_round`) :
si MiniMax en demande plusieurs, seul le premier est exécuté et les autres
reçoivent `error_code="one_tool_per_round"` avec un `tool_call_id` toujours
répondu. `usage` accumule jetons et latence par rôle puis est agrégé pour
l'analyse ; `estimated_cost_usd` reste `null` si les statistiques du
fournisseur sont absentes ou si aucun tarif n'est configuré.

### Streaming des réponses (carte bonus Palier 4)

En Palier 4 la sortie finale des experts et de l'Arbitre est streamée via
l'enveloppe `<LIVE_RESPONSE>…</LIVE_RESPONSE><FINAL_JSON>{…}</FINAL_JSON>`
(`backend/app/streaming.py`). Le texte live (conclusion en français, bref,
sans JSON) est diffusé par paquets `agent.response.delta` (bornés par
`STREAM_DELTA_BATCH_CHARS`, flush temporel 0,1 s) et plafonné par
`STREAM_MAX_DRAFT_CHARS` ; le JSON final (conforme à `AgentOutput`/`ArbiterVerdict`)
n'est extrait qu'à la clôture du flux et n'apparaît jamais dans un delta. Un
round d'outil sans balise n'est pas une erreur ; un texte live accompagné d'un
appel d'outil laisse l'appel faire foi. Une enveloppe finale incomplète est
diagnostiquée précisément puis normalisée en JSON sans outils ; elle ne devient
un échec qu'après épuisement des tentatives bornées.
Les événements `agent.response.started|delta|completed|failed` sont persistés
avant diffusion, avec une séquence strictement croissante par rôle.

## Matrice d'accès

| Composant | Métriques | Indices sécurité | Estimation coût | Appel LLM |
| --- | :---: | :---: | :---: | :---: |
| Agent P3 | appel | appel | appel | boucle `tool_calls` |
| Avocat (P4) | appel | appel | appel | boucle d'outils + `AgentOutput` |
| Procureur (P4) | appel | appel | appel | boucle d'outils + `AgentOutput` |
| Comptable (P4) | appel | — | appel | boucle d'outils (métriques puis coût) + `AgentOutput` |
| Arbitre (P4) | appel (optionnel) | — | — | boucle d'outils + `ArbiterVerdict` |

## Décision de scope

Ces outils ne font ni recherche web, ni exécution de code. En revanche,
`run_expert`, `run_arbiter`, `parse_tool_command` et `set_tool_state` ont un
effet de bord (réseau LLM et/ou écriture SQLite), documenté ci-dessus. Ajouter
un outil qui possède un nouvel effet de bord impose de mettre à jour ce
fichier, le schéma d'architecture et le hors-scope avant l'implémentation.
Ajouter un outil SANS effet de bord impose au minimum de mettre à jour ce
fichier, le registre (`toolkit.py`), les descriptions exposées et AGENTS.md.
