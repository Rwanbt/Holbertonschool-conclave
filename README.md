<div align="center">

![CONCLAVE](docs/assets/conclave_banner.png)

</div>

# CONCLAVE — trois lectures contradictoires, un verdict exploitable

CONCLAVE confronte trois experts **antagonistes** (**Avocat**, **Procureur**,
**Comptable**) sur un même document, puis un **Arbitre** rend un verdict
structuré et priorisé. Issu d'un hackathon Holberton (`v1.0`, voir
[`docs/HACKATHON_V1_CONTRACT.md`](docs/HACKATHON_V1_CONTRACT.md)), le projet est
passé en **version production (v1.1)** : multi-fournisseur **BYOK**, aucune clé
propriétaire en production, isolation multi-utilisateur et déploiement Netlify
+ backend ASGI.

- **Front** : React 18 + TypeScript + Vite (`frontend/`), déployable sur Netlify.
- **Back** : FastAPI + Python + Pydantic, SQLite (`backend/`), ASGI/Docker.
- **Fournisseurs** : catalogue de **24 providers / ~350 modèles** (généré depuis
  [models.dev](https://models.dev), la source d'opencode) — OpenAI, Anthropic,
  Google Gemini, MiniMax, DeepSeek, Groq, Mistral, xAI, Together, Cerebras,
  DeepInfra, Fireworks, OpenRouter, Novita, Nebius, SiliconFlow, Hugging Face,
  NVIDIA, Baseten, Z.AI/Zhipu, Alibaba (Qwen), Moonshot, Xiaomi… Connectables
  par **clé API BYOK**, plus **OAuth Google Gemini** officiel. Voir
  [`docs/PROVIDERS.md`](docs/PROVIDERS.md).

## Démarrage local

```bash
git clone https://github.com/Rwanbt/Holbertonschool-conclave.git
cd Holbertonschool-conclave
```

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ..
cp .env.example .env          # puis éditez vos variables locales
uvicorn backend.app.main:app --reload   # http://localhost:8000
```

Le backend démarre **sans aucune clé LLM**. En local, pour utiliser la clé
serveur de développement (DEV/SMOKE uniquement), définissez
`ALLOW_SERVER_PROVIDER_CREDENTIALS=true` et `MINIMAX_API_KEY=…` dans `.env`.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

Dans l'interface, ouvrez le panneau **Fournisseur IA** : choisissez un
fournisseur, un modèle, collez votre clé API personnelle (BYOK) puis testez la
connexion. La clé ne quitte jamais la mémoire : elle est oubliée au rechargement.

## Utilisation

1. Connectez un fournisseur IA (fournisseur + modèle + clé BYOK).
2. Configurez les outils pour **cette** analyse (figés à la création).
3. Collez un document (1 à 12 000 caractères) et **Convoquez le Conclave**.
4. Observez les trois experts en parallèle (streaming réel), puis le verdict.
5. Le flux survit à un **F5** : snapshot + historique JSON sont rejoués.

## Configuration (`.env`)

Voir [`.env.example`](.env.example). Points clés production :

| Variable | Rôle |
|-|-|
| `FRONTEND_ORIGINS` | Origines CORS autorisées (virgules), jamais `*` |
| `DATABASE_PATH` | Chemin SQLite (volume **durable** en production) |
| `ALLOW_SERVER_PROVIDER_CREDENTIALS` | `false` en production (BYOK uniquement) |
| `SESSION_COOKIE_SECRET` | Secret stable de signature des sessions |
| `MINIMAX_*`, `OPENAI_API_KEY`, … | Clés serveur DEV/SMOKE, désactivées par défaut |

## Déploiement

- **Frontend Netlify** : voir [`docs/DEPLOY_NETLIFY.md`](docs/DEPLOY_NETLIFY.md)
  (`netlify.toml` fourni, aucun secret, CSP à ajuster pour l'origine backend).
- **Backend ASGI/Docker** : `backend/Dockerfile`, volume durable sur
  `/app/data`, voir [`docs/DEPLOY_NETLIFY.md`](docs/DEPLOY_NETLIFY.md).
- L'architecture complète : [`docs/PRODUCTION_ARCHITECTURE.md`](docs/PRODUCTION_ARCHITECTURE.md).

## Sécurité

- **BYOK** : la clé utilisateur vit en mémoire uniquement, transmise HTTPS au
  `/start`, jamais persistée/logguée/diffusée/renvoyée.
- **Isolation** : session anonyme signée (cookie HttpOnly) ; routes non
  propriétaires → 404 ; préférences d'outils par session ; plafonds globaux et
  par session.
- **Anti-SSRF** : endpoints providers issus d'une allowlist serveur.
- **Redaction** : erreurs contenant une clé/un header nettoyées avant
  log/SSE/DB/réponse.
- **Prompt injection** : défenses structurelles (outils figés sans argument,
  sorties validées Pydantic, document encadré par un nonce).
- Détails : [`SECURITY.md`](SECURITY.md).

## Limitations connues

- **SQLite mono-instance** : nécessite un volume durable, pas de scale-out
  (plan PostgreSQL dans [`docs/PRODUCTION_ARCHITECTURE.md`](docs/PRODUCTION_ARCHITECTURE.md)).
- Un **redémarrage serveur** invalide les sessions anonymes et peut exiger une
  reconnexion provider (documenté, acceptable).
- Anthropic et Gemini disposent d'adapters réels testés par contrat (simulation)
  et d'un test de clé sans inférence ; un smoke test réel n'est exécuté que si
  la clé correspondante est explicitement fournie (jamais dans la CI).

## Commandes

```bash
make test     # tests backend + frontend
make lint     # eslint frontend
make build    # build frontend
make eval     # évaluation hostile (fournisseur simulé, 5 cas)
make all      # test + lint + build + eval
```

## Pourquoi pas ChatGPT OAuth ?

« Sign in with ChatGPT » est un fournisseur d'identité, pas un moyen de
financer les appels API OpenAI : un abonnement ChatGPT ne finance pas les
inférences OpenAI API. Les inférences OpenAI utilisent une clé API BYOK
(voir [`docs/PROVIDERS.md`](docs/PROVIDERS.md)).