# Déploiement Netlify (frontend) + backend ASGI séparé

## Principes

- Le frontend React/Vite est déployé sur Netlify **sans aucun secret LLM** :
  toutes les clés sont BYOK côté navigateur.
- Le backend FastAPI est déployé **séparément** sur un hébergement ASGI/Docker
  persistant (Railway, Fly.io, Render, un VPS, etc.).
- Aucune réécriture proxy `/api/*` vers le backend externe : les limites des
  proxy rewrites Netlify sont incompatibles avec un flux SSE long. Le frontend
  parle **HTTPS direct** au backend via `VITE_API_BASE_URL`.

## Backend

1. Construire l'image : `docker build -f backend/Dockerfile -t conclave-backend backend`.
2. Lancer avec un **volume durable** monté sur `/app/data` (SQLite mono-instance).
3. Variables : `FRONTEND_ORIGINS` (origines Netlify, virgules),
   `SESSION_COOKIE_SECRET`, `ALLOW_SERVER_PROVIDER_CREDENTIALS=false`.
   Aucune clé LLM requise.
4. Healthcheck : `GET /api/health` (déjà dans le Dockerfile).

## Frontend (Netlify)

Le `netlify.toml` à la racine :
- commande de build `npm ci --prefix frontend && npm run build --prefix frontend` ;
- publish `frontend/dist` ;
- SPA fallback (`/* -> /index.html`) ;
- en-têtes de sécurité (CSP, nosniff, Referrer-Policy, Permissions-Policy,
  X-Frame-Options) ;
- cache immuable pour `/assets/*`.

Configurer la variable d'environnement Netlify `VITE_API_BASE_URL` =
`https://api.conclave.example` (l'URL réelle du backend). **Aucun secret** dans
cette variable.

> **CSP** : dans `netlify.toml`, la directive `connect-src` doit contenir
> l'origine réelle du backend (remplacer l'exemple `https://api.conclave.example`
> par votre domaine au déploiement).

## Vérifications

- `make test` et `make lint build` sont verts localement.
- `./scripts/check-no-secrets.sh` ne détecte aucun secret dans le bundle.
- Ouvrir le site Netlify : le panneau « Fournisseur IA » permet de se connecter
  avec une clé BYOK, de créer une analyse, de la voir streamer puis de
  récupérer l'état après un F5.