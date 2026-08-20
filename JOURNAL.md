# Journal

## 2026-08-20 — R1 : streaming réel et switches indépendants des outils

Corrections livrées sur `claude/corrections-branche-erwan-3jm4q7` (base `origin/erwan`
== `origin/dev`@171be14), en réponse à `PLAN_R1_CORRECTIF_STREAMING_SWITCHES_TOOLS.md`.

- **Outils, configuration figée par analyse** : schéma SQLite v2
  (`analysis_tool_states`) — chaque analyse fige, à sa création, une copie
  immuable du registre global `tool_states` ; une modification ultérieure du
  registre n'affecte plus jamais une analyse déjà créée. Le backfill de
  `tool_states` est désormais un `INSERT OR IGNORE` inconditionnel par outil
  (au lieu de sauter entièrement dès qu'une ligne existe) : une base
  ancienne/partielle est complétée sans écraser un choix persisté.
- **Outils, non-transmission au modèle** : `registry_tool_schemas()` filtre
  désormais par la configuration figée de l'analyse (un ensemble vide omet
  `tools`/`tool_choice` plutôt que d'envoyer une liste vide) ; `execute_tool`
  revérifie cette même configuration à l'exécution, défensivement, sans
  requête SQLite supplémentaire. `estimate_current_analysis_cost` ne mesure
  plus implicitement le document : sans mesure préalable, il renvoie
  `missing_prerequisite` ; le Comptable n'exige une preuve réelle que pour
  les outils réellement activés pour l'analyse.
- **Cycle de vie queued → running** : `POST /api/analyses` crée l'analyse en
  `queued` et fige sa configuration d'outils dans la même requête, sans
  lancer de tâche de fond. `POST /api/analyses/{id}/start` effectue un
  compare-and-set SQL atomique `queued → running`, idempotent (202 au premier
  appel, 200 `already_started` ensuite, 404 si inconnue), et insère
  `analysis.started` avant `expert.started`. `GET …/events/history` expose un
  historique JSON paginé (`after`, `limit`, `has_more`) pour l'hydratation F5,
  indépendant du flux SSE vivant.
- **Streaming obligatoire** : un tour final sans `<LIVE_RESPONSE>` non vide
  est maintenant un `protocol_error` (une réponse JSON-only ne peut plus
  réussir silencieusement avec zéro événement live) ; une seule requête de
  réparation en streaming est tentée avant d'échouer proprement.
  `normalize_delta` ne supprime plus une sous-chaîne répétée légitime (retrait
  de la règle générale `buffer.endswith(incoming)`). `agent.response.started`
  est émis dès la reconnaissance du marqueur live, plus au premier paquet
  bufferisé. Nouveaux événements `agent.round.started`/`agent.round.completed`
  et délai de flush configurable (`STREAM_FLUSH_INTERVAL_MS`, 50 ms par défaut).
- **Frontend, switches avant soumission** : nouveau hook `useToolCatalog`
  (chargement automatique, mutations sérialisées, compteur de requêtes
  monotone contre les réponses obsolètes), `ToolsPanel` accessible
  (`role="switch"`, `aria-checked`, état textuel) rendu sur la première page,
  avant le document — et en lecture seule sur la configuration figée pendant
  une analyse `queued`/`running`.
- **Frontend, progression pilotée par événements** : `useAnalysisController`
  hydrate le snapshot et l'historique paginé en parallèle au montage,
  n'ouvre plus jamais le SSE pour une analyse déjà terminale, ne rappelle
  `/start` qu'une fois après `onopen`. `calculateActiveStep` avance
  uniquement sur les événements observés : un snapshot terminal qui devance
  son événement SSE ne fait plus sauter le stepper à la dernière étape.
- **Tests** : 132 tests backend (+ 1 smoke MiniMax réel opt-in, skip sans
  clé) et 84 tests frontend (nouveaux : `@testing-library/react` + `jsdom`
  pour exercer les hooks et les switches par interaction réelle). Workflow
  CI GitHub Actions ajouté (`backend` + `frontend`, sans clé MiniMax).

## 2026-08-20 — Palier 4 bonus : streaming natif MiniMax-M3 + SSE temps réel

- **Streaming** : nouveau module `backend/app/streaming.py` — `StreamCollector`,
  `EnvelopeParser` (automate 6 états), `ToolCallAssembler`, `normalize_delta`
  (contenu MiniMax potentiellement cumulatif dédupliqué), `stream_chat_completion`
  (stream=True, include_usage, thinking désactivé via extra_body).
- **Enveloppe** : `<LIVE_RESPONSE>…</LIVE_RESPONSE><FINAL_JSON>{…}</FINAL_JSON>`
  ajoutée aux prompts experts/Arbitre (`_EXPERT_ENVELOPE`/`_ARBITER_ENVELOPE`,
  recopiés dans AGENTS.md). Réponse d'outil sans balise = pas une erreur ;
  marqueurs inversés / double live / JSON final manquant / id-nom d'outil invalide
  / live+tool_calls mélangés = `protocol_error` sans exécution d'outil.
- **Événements live persistés** : `agent.response.started`, `agent.response.delta`
  (paquets bornés `STREAM_DELTA_BATCH_CHARS`, flush temporel 0,1 s),
  `agent.response.completed`, `agent.response.failed` ; séquence croissante par
  rôle ; JSON final jamais dans un delta ; `completed` avant l'événement d'étape.
- **SSE** : polling `SSE_POLL_INTERVAL_MS=100` (au lieu d'un sleep fixe),
  keep-alive `SSE_KEEPALIVE_SECONDS=10`, reprise `max(Last-Event-ID, ?after)`.
- **Atomique** : `db.finish_analysis` committe statut + usage + verdict +
  événement terminal en une transaction (supprime la course où un client voyait
  un statut terminal sans événement terminal).
- **/tools list** : `POST /api/tool-commands` `"/tools"`/`"/tools list"` → 200 avec
  catalogue complet (`ToolCommandResponse` étendu : action, message, tool_name,
  enabled, tools) ; enable/disable gardent tool_name/enabled non nuls (compat front).
- **Réglages** : `sse_poll_interval_ms`, `sse_keepalive_seconds`,
  `stream_max_draft_chars`, `stream_delta_batch_chars` (bornés) dans config.py et
  .env.example.
- **Tests** : +40 tests streaming (collecteur, parser, assembleur, boucle agent,
  persistance des événements, analyse complète en streaming) → 111 verts, stables
  sur 3 exécutions ; `compileall` et `git diff --check` propres.
- Poussé sur origin/yo. PR proposée yo → dev (titre : « feat(p4): stream MiniMax
  responses through persistent SSE »).

## 2026-08-19 — Palier 4 : persistance SQLite et orchestration complète

- **Git** : sync préalable (yo = origin/dev = bb2d776), travail exclusivement sur `backend/**`, `.env.example`, SPEC.md, OUTILS.md, AGENTS.md, architecture.mmd, JOURNAL.md (+ `.gitignore` pour `data/*.db`).
- **Persistance** : module `backend/app/db.py` (aiosqlite piné) — schéma idempotent, WAL, FK ON, transactions, tables `analyses`, `expert_runs`, `tool_events`, `analysis_events`, `tool_states` ; `running → interrupted` au redémarrage ; base temporaire par test.
- **Outils** : `backend/app/toolkit.py` — registre/adaptateurs/exécuteur asynchrone lisant `tool_states` à chaque appel, grammaire `/tools` stricte (422 sans modification partielle).
- **Boucle partagée** : `run_agent_loop` dans `agent.py` (for round_number visible, `one_tool_per_round`, `max_rounds_reached`, `repeated_tool_call`, `stop_reason`) ; wrapper P3 `run_agent` préservé.
- **Experts/Arbitre** : `backend/app/experts.py` — 3 experts en `asyncio.gather` (sans ordre supposé), validation Pydantic + 1 réparation structurée, vérificateur d'évidence Comptable, Arbitre sur ≥ 2 sorties (degraded si 1 expert absent), garde-fous de temps (30/20/60 s).
- **API** : `main.py` — lifespan (init DB + override test), tâches de fond dans `app.state` (F5 sans annulation), POST /api/analyses (201), GET snapshot, GET SSE (Last-Event-ID / ?after, backlog, fermeture terminale), GET /api/tools, POST /api/tool-commands.
- **Budget de sortie** : `EXPERT_MAX_OUTPUT_TOKENS=1500` ajouté (le JSON AgentOutput est tronqué à 300 tokens → échecs structurés en réel).
- **Tests** : 82 verts (51 P1-P3 + 7 db + 13 experts + 11 API), `compileall` et `git diff --check` propres.
- **Tests manuels réels MiniMax-M3** (19/08, clé réelle) : analyse complète (3 experts → arbitre no_go → completed, ~$0.0065, 37 s), multi-tour Comptable (métriques → coût → sortie), outil sécurité désactivé pendant une analyse (trace `tool_disabled`, analyse completes sans crash), réactivation sans redémarrage, redémarrage → analyses et états d'outils persistés, aucun document dans les événements/traces.
- Poussé sur origin/yo. PR proposée yo → dev.