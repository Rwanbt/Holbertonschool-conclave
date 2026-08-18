# Outils des agents — contrats typés

## Règle

Un **effet de bord** signifie qu'un outil modifie ou expose quelque chose hors de son calcul local : appel réseau, coût facturé, transmission de données ou écriture persistante. Les outils déterministes sont appelés par l'orchestrateur puis leurs résultats sont fournis aux agents ; le LLM ne décide pas s'il faut les exécuter.

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
  literal_or_regex: str,
  category: SecurityCategory
}

SecurityFinding = {
  pattern_name: str,
  matched_text: str,
  line_number: int,
  category: SecurityCategory
}

ModelPricing = {
  model_name: str,
  input_eur_per_million_tokens: float,
  output_eur_per_million_tokens: float
}

CostEstimate = {
  model_name: str,
  input_tokens: int,
  output_token_budget: int,
  estimated_cost_eur: float
}

ExpertContext = {
  metrics: DocumentMetrics,
  security_findings: list[SecurityFinding],
  cost_estimate: CostEstimate | null
}
```

## Registre

### 1. `measure_document`

```text
measure_document(text: str) -> DocumentMetrics
```

- **Utilisé par :** orchestrateur ; métriques partagées avec Avocat et Comptable.
- **But :** calculer une base factuelle commune et refuser un texte hors limite.
- **Effet de bord : NON.** Calcul local pur, sans réseau ni écriture.
- **Échec défini :** `InvalidDocumentError` si le texte est vide ou dépasse 12 000 caractères.

### 2. `find_security_indicators`

```text
find_security_indicators(
  text: str,
  patterns: tuple[SecurityPattern, ...]
) -> list[SecurityFinding]
```

- **Utilisé par :** Procureur.
- **But :** localiser des indices factuels, jamais déclarer qu'une vulnérabilité est certaine.
- **Effet de bord : NON.** Recherche locale dans le texte.
- **Échec défini :** renvoie une liste vide si aucun indice n'est trouvé ; un motif invalide est rejeté avant l'analyse.

### 3. `estimate_analysis_cost`

```text
estimate_analysis_cost(
  input_tokens: int,
  output_token_budget: int,
  pricing: ModelPricing
) -> CostEstimate
```

- **Utilisé par :** orchestrateur ; résultat fourni au Comptable.
- **But :** fournir une estimation reproductible du coût d'un appel, séparée de l'interprétation LLM.
- **Effet de bord : NON.** Calcul arithmétique local.
- **Échec défini :** `UnknownPricingError` si le modèle n'a aucun tarif configuré.

### 4. `run_expert`

```text
run_expert(
  role: "avocat" | "procureur" | "comptable",
  document: str,
  context: ExpertContext,
  timeout_seconds: int
) -> AgentOutput
```

- **Utilisé par :** les trois experts via la passerelle Mistral unique et le modèle `mistral-small-2603`.
- **But :** interroger le fournisseur avec le rôle et le schéma `AgentOutput` attendus.
- **Effet de bord : OUI.** Appel réseau, transmission du document à un tiers et coût API potentiel.
- **Échec défini :** `ProviderError`, `TimeoutError` ou `StructuredOutputError` ; aucune réponse invalide n'est transmise à l'Arbitre.

### 5. `run_arbiter`

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

## Matrice d'accès

| Composant | Métriques | Indices sécurité | Estimation coût | Appel LLM |
| --- | :---: | :---: | :---: | :---: |
| Avocat | lecture | — | — | `run_expert` |
| Procureur | lecture | lecture | — | `run_expert` |
| Comptable | lecture | — | lecture | `run_expert` |
| Arbitre | via les sorties validées | — | — | `run_arbiter` |

## Décision de scope

Ces outils ne font ni recherche web, ni exécution de code, ni écriture en base. Ajouter un outil qui possède un nouvel effet de bord impose de mettre à jour ce fichier, le schéma d'architecture et le hors-scope avant l'implémentation.
