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
