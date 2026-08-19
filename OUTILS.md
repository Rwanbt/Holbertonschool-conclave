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
  estimated_cost_usd: float,   # arrondi à 6 décimales
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

### 1. `measure_document`

```text
measure_document(text: str) -> DocumentMetrics
```

- **Utilisé par :** adaptateur `measure_current_document()` de la boucle agent ; métriques partagées avec Avocat et Comptable.
- **But :** calculer une base factuelle commune et refuser un texte hors limite.
- **Effet de bord : NON.** Calcul local pur, sans réseau ni écriture.
- **Échec défini :** `InvalidDocumentError` si le texte est vide ou dépasse 12 000 caractères.
- **Estimation des jetons :** heuristique documentée de 1 jeton ≈ 4 caractères, arrondie à l'entier supérieur (minimum 1). Simple et reproductible ; elle ne remplace pas le tokeniseur réel de MiniMax.

### 2. `find_security_indicators`

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

### 3. `estimate_analysis_cost`

```text
estimate_analysis_cost(
  input_tokens: int,
  output_token_budget: int,
  pricing: ModelPricing
) -> CostEstimate
```

- **Utilisé par :** adaptateur `estimate_current_analysis_cost()` de la boucle agent ; résultat fourni au Comptable.
- **But :** fournir une estimation reproductible du coût d'un appel, séparée de l'interprétation LLM. Devise explicite (USD), arrondi documenté à 6 décimales.
- **Effet de bord : NON.** Calcul arithmétique local.
- **Échec défini :** `UnknownPricingError` si le modèle n'a aucun tarif configuré (tarif absent, non numérique, négatif ou à zéro) — le coût reste alors `null` dans `usage`.
- **Tarifs :** `MINIMAX_INPUT_USD_PER_MILLION` / `MINIMAX_OUTPUT_USD_PER_MILLION` dans `.env` ; estiment les tarifs officiels MiniMax-M3 (standard ≤512K : 0,30 / 1,20 USD par million de jetons, promo de lancement, vérifiés le 19/08/2026 via agrégateurs tiers). Estimatifs : les seules valeurs fiables viennent de la facturation réelle MiniMax.

### 4. `run_expert` (contrat de palier ultérieur — non implémenté en P3)

```text
run_expert(
  role: "avocat" | "procureur" | "comptable",
  document: str,
  context: ExpertContext,
  timeout_seconds: int
) -> AgentOutput
```

- **Utilisé par :** les trois experts via la passerelle MiniMax unique et le modèle `MiniMax-M3`.
- **But :** interroger le fournisseur avec le rôle et le schéma `AgentOutput` attendus.
- **Effet de bord : OUI.** Appel réseau, transmission du document à un tiers et coût API potentiel.
- **Échec défini :** `ProviderError`, `TimeoutError` ou `StructuredOutputError` ; aucune réponse invalide n'est transmise à l'Arbitre.

### 5. `run_arbiter` (contrat de palier ultérieur — non implémenté en P3)

```text
run_arbiter(
  document: str,
  outputs: tuple[AgentOutput, AgentOutput] | tuple[AgentOutput, AgentOutput, AgentOutput],
  timeout_seconds: int
) -> ArbiterVerdict
```

- **Utilisé par :** Arbitre, seulement avec deux ou trois sorties validées.
- **But :** produire le verdict final conforme à `ArbiterVerdict`.
- **Effet de bord : OUI.** Appel réseau, transmission du document et des analyses, coût API potentiel.
- **Échec défini :** mêmes erreurs que `run_expert` ; les analyses déjà terminées restent visibles.

## Boucle agent (Palier 3)

MiniMax reçoit le prompt système et les trois schémas d'outils (jamais le document). Il choisit seul d'appeler un outil (`tool_choice="auto"`) ; le serveur valide le nom (`unknown_tool` sinon) et les arguments (`invalid_arguments` sinon), applique `DISABLED_TOOLS` (`tool_disabled`), exécute l'adaptateur, puis renvoie le résultat réel dans un message `role="tool"` avec `tool_call_id`. La boucle fait au plus `MINIMAX_MAX_TOOL_ROUNDS` appels ; un appel identique répété (`repeated_tool_call`) ou la limite atteinte (`max_rounds_reached`) arrête proprement la boucle avec une réponse de non-vérification. `usage` accumule jetons et latence ; `estimated_cost_usd` reste `null` si les statistiques du fournisseur sont absentes ou si aucun tarif n'est configuré.

## Matrice d'accès

| Composant | Métriques | Indices sécurité | Estimation coût | Appel LLM |
| --- | :---: | :---: | :---: | :---: |
| Agent P3 | appel | appel | appel | boucle `tool_calls` |
| Avocat | lecture | — | — | `run_expert` |
| Procureur | lecture | lecture | — | `run_expert` |
| Comptable | lecture | — | lecture | `run_expert` |
| Arbitre | via les sorties validées | — | — | `run_arbiter` |

## Décision de scope

Ces outils ne font ni recherche web, ni exécution de code, ni écriture en base. Ajouter un outil qui possède un nouvel effet de bord impose de mettre à jour ce fichier, le schéma d'architecture et le hors-scope avant l'implémentation. Ajouter un outil SANS effet de bord impose au minimum de mettre à jour ce fichier, le registre (`agent.py`), les descriptions exposées et AGENTS.md.
