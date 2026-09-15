# Architecture de production CONCLAVE (v1.1)

## Séparation des responsabilités

```
Utilisateur
  │  BYOK : clé API personnelle (mémoire navigateur)
  ▼
Frontend React/Vite (Netlify, aucun secret)
  │  HTTPS direct, cookie de session (credentials include)
  ▼
Backend FastAPI (ASGI/Docker, persistant, aucun secret propriétaire)
  │
  ├─ Registre provider contrôlé (allowlist d'endpoints, anti-SSRF)
  │    Agent → Provider abstraction → Provider concret
  │    ├─ MiniMax (SDK openai, thinking désactivé)
  │    ├─ OpenAI (SDK openai)
  │    ├─ Anthropic (API Messages, httpx)
  │    └─ Gemini (API generateContent, httpx)
  │
  ├─ SQLite (mono-instance, volume durable) — POSTGRES pour le multi-instance
  └─ Streaming SSE réel, persisté, rejouable
```

## Cycle de vie des données

1. **Création** `POST /api/analyses` : fige la sélection NON secrète
   (`provider_id`, `model_id`, `enabled_tools`), attribue un `owner_session`,
   enregistre `analysis.created`. Aucune tâche de fond, aucun credential.
2. **Démarrage** `POST /api/analyses/{id}/start` : reçoit le credential BYOK,
   le VALIDE (adapter construit, fail-fast 400 sans transition), puis transmet
   EN MÉMOIRE à la tâche `run_analysis`. Jamais persisté, loggué, diffusé.
3. **Exécution** : 3 experts parallèles → sorties validées → Arbitre séquentiel
   → événements persistés → SSE réel.
4. **Libération** : les références au credential sont relâchées à la fin de la
   tâche (`provider.close()`). Un redémarrage serveur invalide les sessions
   (documenté) ; la reconnexion provider est demandée à l'utilisateur.

## Sélection figée par analyse

Chaque analyse stocke uniquement les métadonnées non secrètes (provider,
modèle, configuration des outils). Ces valeurs sont immuables : modifier les
réglages globaux ou l'interface ne change jamais une analyse déjà créée.

## Isolation multi-utilisateur

- Session anonyme signée (cookie HttpOnly, HMAC) : `owner_session` sur chaque
  analyse. Routes snapshot/historique/SSE/start → 404 si non propriétaire.
- Préférences d'outils PAR SESSION (`session_tool_states`) : aucun croisement
  entre utilisateurs.
- Plafonds : concurrence globale + par session (429 explicite).

## Choix assumés

- **SQLite en mono-instance** : acceptable UNIQUEMENT avec un volume durable.
  Jamais sur un filesystem éphémère (serverless). Plan de migration
  PostgreSQL dans `docs/PRODUCTION_ARCHITECTURE.md` si passage multi-instance.
- **CORS strict** : origines explicites + credentials, jamais `*`.
- **Redaction centralisée** : toute erreur contenant une clé/un en-tête est
  nettoyée avant log/SSE/DB/réponse HTTP.

## Migration PostgreSQL (plan)

1. Introduire `DATABASE_URL` (abstraction de connexion).
2. Remplacer `aiosqlite` par un client PostgreSQL async (`asyncpg`), préserver
   l'API de `db.py` (mêmes fonctions, mêmes contrats).
3. Système de migrations (Alembic) versionnant le schéma v3.
4. Test CI sur PostgreSQL de service + garder SQLite pour les tests unitaires.
5. Scale-out multi-instance : remplacer `app.state.analysis_tasks` par une
   file externe (Redis/Postgres) et le polling SSE SQLite par un canal pub/sub.