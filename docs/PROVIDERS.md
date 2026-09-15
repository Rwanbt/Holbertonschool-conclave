# Providers — BYOK multi-fournisseur

## Fournisseurs pris en charge

| Provider | Adapter | Particularité isolée |
|-|-|-|
| MiniMax (`minimax`, MiniMax-M3) | SDK `openai` | `thinking` désactivé via `extra_body` ; morceaux cumulatifs dédupliqués |
| OpenAI (`openai`, gpt-4o-mini, gpt-4o, gpt-4.1-mini, gpt-4.1) | SDK `openai` | aucune |
| Anthropic (`anthropic`, claude-3-5-haiku-latest, claude-3-5-sonnet-latest) | API Messages (httpx) | `system` top-level, `max_tokens` obligatoire, blocs `tool_use`/`tool_result` |
| Google Gemini (`gemini`, gemini-2.0-flash, gemini-2.5-flash) | API generateContent (httpx) | `contents` user/model, `functionCall`/`functionResponse`, clé `x-goog-api-key` |

L'architecture permet d'ajouter tout provider **réellement OpenAI-compatible**
(DeepSeek, Mistral, Groq, …) en déclarant son endpoint OFFICIEL dans le
registre (`backend/app/providers/registry.py`) : la `base_url` n'est jamais
saisie par l'utilisateur (anti-SSRF).

## Registre public — `GET /api/providers`

Renvoie uniquement des métadonnées sûres (provider, modèles, capacités,
pricing connu ou `null`). **Aucun secret, aucune URL interne.** Le frontend
construit son sélecteur depuis ce contrat.

## Test de connexion — `POST /api/providers/test-connection`

Utilise la méthode la moins coûteuse (liste de modèles) pour vérifier la clé.
Si le fournisseur ne permet pas de vérifier sans inférence, l'API répond
`needs_inference=true` et ne prétend JAMAIS que la clé est valide : la
validation réelle se fait au premier appel d'exécution.

## Clé BYOK

- Vit uniquement en mémoire navigateur puis en mémoire du runtime backend.
- Transmise au `POST /api/analyses/{id}/start` ; jamais persistée, logguée,
  diffusée ou renvoyée.
- Un rechargement la fait disparaître volontairement (l'utilisateur la
  ressaisit) : préférable à une persistance non maîtrisée.
- Aucune clé du propriétaire en production : `ALLOW_SERVER_PROVIDER_CREDENTIALS`
  est `false` par défaut. Les clés serveur éventuelles sont DEV/SMOKE uniquement.

## Précision ChatGPT OAuth

« Sign in with ChatGPT » (si disponible) est un fournisseur d'IDENTITÉ, pas un
moyen de financer les inférences OpenAI API : un abonnement ChatGPT ne finance
pas les appels API OpenAI, sauf si OpenAI fournit officiellement un scope OAuth
délégué. L'interface ne prétend jamais le contraire. Les inférences OpenAI
nécessitent une clé API OpenAI (BYOK).

## Confidentialité

Le document soumis est transmis au fournisseur CHOISI PAR L'UTILISATEUR via son
propre compte API. L'interface l'affiche avant lancement. CONCLAVE ne garantit
pas la confidentialité imposée par le fournisseur.