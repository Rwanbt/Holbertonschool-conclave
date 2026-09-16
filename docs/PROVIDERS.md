# Providers — BYOK multi-fournisseur

## Catalogue actuel (données models.dev / opencode)

Le registre est **piloté par un catalogue généré** depuis
[models.dev](https://models.dev) — la même source que opencode. Il est régénérable :

```bash
curl -sL https://models.dev/api.json -o /tmp/models_dev.json
python scripts/generate_provider_catalog.py /tmp/models_dev.json
```

Le catalogue commité (`backend/app/providers/catalog_data.json`) expose
**24 fournisseurs et ~350 modèles** (filtrés sur ceux qui supportent le tool
calling, requis par CONCLAVE), dont :

| Type | Fournisseurs |
|-|-|
| Natifs | OpenAI, Anthropic, Google Gemini, MiniMax |
| OpenAI-compatibles | DeepSeek, Groq, Mistral, xAI (Grok), Together AI, Cerebras, DeepInfra, Fireworks AI, OpenRouter, NovitaAI, Nebius, SiliconFlow, Hugging Face, NVIDIA NIM, Baseten, Z.AI (GLM), Zhipu AI, Alibaba (Qwen), Moonshot (Kimi), Xiaomi MiMo |

Pour **ajouter un provider** : l'ajouter à `CURATED` dans
`scripts/generate_provider_catalog.py` (avec son endpoint officiel), puis
régénérer. La `base_url` provient EXCLUSIVEMENT du catalogue — elle n'est
jamais saisie par l'utilisateur (anti-SSRF).

## Registre public — `GET /api/providers`

Renvoie uniquement des métadonnées sûres (provider, modèles, capacités,
tarifs connus ou `null`). **Aucun secret, aucune URL interne.** Le frontend
construit son sélecteur depuis ce contrat : ajouter un provider au catalogue le
fait apparaître automatiquement dans l'interface.

## Test de connexion — `POST /api/providers/test-connection`

Utilise la méthode la moins coûteuse (liste de modèles) pour vérifier la clé.
Si le fournisseur ne permet pas de vérifier sans inférence, l'API répond
`needs_inference=true` et ne prétend JAMAIS que la clé est valide.

## Clés BYOK

- Vit uniquement en mémoire navigateur puis en mémoire du runtime backend.
- Transmise au `POST /api/analyses/{id}/start` ; jamais persistée, logguée,
  diffusée ou renvoyée.
- Aucune clé du propriétaire en production : `ALLOW_SERVER_PROVIDER_CREDENTIALS`
  est `false` par défaut (clés serveur DEV/SMOKE uniquement).

## Authentification — état et limites

- **Clé API (BYOK)** : mode universel, pris en charge pour tous les providers.
- **OAuth** : le champ `auth_modes` du registre est **extensible** pour
  accueillir de véritables flux OAuth.

OAuth par fournisseur (cadrage honnête) :

| Fournisseur | OAuth pour l'inférence ? |
|-|-|
| Google Gemini | **Officiellement supporté** (l'API Generative Language accepte un jeton OAuth 2.0). Implémentable avec un client OAuth Google enregistré. |
| OpenAI / ChatGPT | **Non officiellement supporté pour l'inférence.** « Sign in with ChatGPT » est une IDENTITÉ, pas un moyen de financer les appels API. Utiliser l'API via un abonnement ChatGPT reposerait sur des endpoints non publics — interdit par le plan (§14/§44) et par les CGU OpenAI. |
| GitHub Copilot | **Non supporté pour un usage tiers.** L'API Copilot (`api.githubcopilot.com`) exige un jeton Copilot obtenu par un flux OAuth GitHub non prévu pour l'inférence tierce — interdit par le plan (« endpoint privé/reverse-engineered ») et par les CGU GitHub. |

En clair : **les clés API restent le moyen fiable et conforme** pour tous les
providers, y compris OpenAI et GitHub Copilot. Un OAuth Google peut être ajouté
sans refactor. Aucune capacité OAuth factice n'est affichée.
