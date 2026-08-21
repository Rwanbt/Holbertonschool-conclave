# JOURNAL — usages critiques de l'IA

Ce journal distingue ce qui a été demandé à l'IA, ce qu'elle a produit de
faux, fragile ou inutile, et la décision humaine prise ensuite. Les anciens
comptes rendus techniques sont conservés plus bas comme traces chronologiques.

## 2026-08-21 — Une répétition d'outil ne devait pas invalider un expert

- **Demande faite à l'IA :** diagnostiquer pourquoi une analyse réelle finissait
  `degraded` alors que le Comptable avait déjà mesuré le document et calculé le
  coût.
- **Proposition médiocre identifiée :** la première boucle générée traitait
  tout appel identique comme `repeated_tool_call` terminal. C'était présenté
  comme une protection contre les boucles infinies, mais `AGENT_MAX_ROUNDS`
  bornait déjà la boucle. Une simple répétition MiniMax supprimait donc un
  expert pourtant exploitable.
- **Correction ou refus :** nous avons refusé cet arrêt immédiat. Un résultat
  déterministe déjà réussi est maintenant réutilisé sans réexécution ; un
  échec reste retentable si son prérequis change. Les outils sont aussi limités
  par rôle : le Comptable ne voit que métriques et coût, l'Arbitre aucun outil.
- **Preuve :** rejeu du document exact : `completed`, 3 experts valides,
  10 tours et 25 423 tokens, contre `degraded`, 13 tours et 33 684 tokens.

## 2026-08-21 — Réparer le JSON avec le même protocole répétait le défaut

- **Demande faite à l'IA :** corriger les `protocol_error` MiniMax provoqués par
  des balises `<FINAL_JSON>` absentes ou non fermées.
- **Proposition médiocre identifiée :** redemander au modèle, en streaming et
  avec les outils encore exposés, de reproduire exactement la même enveloppe.
  Cette « réparation » répétait la cause du défaut et pouvait déclencher de
  nouveaux appels d'outils.
- **Correction ou refus :** réparation séparée, non streamée, sans outil, à
  température zéro, avec `response_format=json_object`, le JSON Schema Pydantic
  exact et deux tentatives bornées. Le prompt système original est remplacé par
  un normalisateur dédié pour éviter les consignes contradictoires.
- **Ce que nous n'avons pas prétendu :** MiniMax accepte un paramètre de schéma
  sans toujours le respecter. La validation Pydantic côté serveur reste donc
  l'autorité finale.

## 2026-08-21 — Les tarifs absents ne sont pas une panne métier

- **Demande faite à l'IA :** résoudre l'échec du Comptable lorsque
  `MINIMAX_INPUT_USD_PER_MILLION` ou `MINIMAX_OUTPUT_USD_PER_MILLION` manque.
- **Proposition médiocre identifiée :** rendre les deux tarifs obligatoires au
  démarrage ou lever `UnknownPricingError`. Cela transformait une donnée
  facultative en indisponibilité de toute l'analyse.
- **Correction ou refus :** les valeurs recommandées `0.30/1.20` sont
  documentées, mais l'outil renvoie `estimated_cost_usd=null` et
  `pricing_configured=false` lorsqu'elles sont absentes. Le Comptable signale
  l'indisponibilité sans inventer de prix et l'analyse continue.
- **Amélioration supplémentaire :** l'ancien calcul estimait un seul appel de
  300 tokens. Il couvre maintenant les trois experts, l'Arbitre, les tours et
  les réparations configurées, avec ses hypothèses dans la réponse.

## 2026-08-20 — Un effet machine à écrire n'est pas du streaming

- **Demande faite à l'IA :** rendre les réponses des agents visibles en temps
  réel et restaurables après F5.
- **Proposition médiocre refusée :** révéler une réponse déjà complète avec un
  timer côté navigateur. Visuellement convaincant, mais faux : aucune preuve
  que les données arrivaient réellement du fournisseur.
- **Correction ou refus :** les deltas MiniMax sont streamés, validés, bornés,
  persistés en SQLite puis diffusés par SSE avec identifiants rejouables. Le
  frontend concatène les séquences reçues sans animation artificielle. Le
  panneau de démonstration affiche leur chronologie avant `completed`.
- **Dette évitée :** nous avons accepté plus de code de reprise SSE plutôt que
  de présenter une animation comme une propriété réseau inexistante.

## 2026-08-20 — Un snapshot terminal ne prouve pas que le navigateur a vu le parcours

- **Demande faite à l'IA :** corriger un stepper qui passait directement de la
  soumission au verdict et une analyse parfois bloquée en `queued`.
- **Proposition médiocre identifiée :** déduire l'étape visuelle uniquement du
  statut final du snapshot et démarrer le job avant l'ouverture du SSE. Sur une
  analyse rapide, le navigateur perdait les transitions qu'il devait démontrer.
- **Correction ou refus :** le POST crée et fige l'analyse sans la démarrer. Le
  frontend ouvre le SSE puis appelle `/start`; le serveur effectue un
  compare-and-set idempotent. Le stepper avance sur les événements réellement
  observés, tandis que snapshot et historique paginé restaurent un F5.
- **Compromis :** un délai de secours démarre quand même le job si `onopen`
  n'arrive jamais, avec tentatives bornées et idempotence serveur.

## 2026-08-20 — Détecter une injection ne suffit pas à la bloquer

- **Demande faite à l'IA :** gérer un document contenant « ignore les
  instructions précédentes » et expliquer la décision à l'utilisateur.
- **Proposition médiocre refusée :** présenter une liste de regex comme une
  protection contre l'injection de prompt. Elle est contournable par une autre
  langue, une translittération ou un encodage.
- **Correction ou refus :** le détecteur ne sert qu'à l'observabilité. Les
  barrières sont structurelles : nonce imprévisible autour du document, outils
  figés côté serveur et sans argument, clé absente du prompt, SQL paramétré et
  sortie Pydantic stricte.
- **Limite assumée :** une injection peut encore dégrader la qualité du texte
  produit par un expert, mais elle ne peut pas étendre ses capacités serveur.

## 2026-08-19 — Partager les types ne remplace pas la validation runtime

- **Demande faite à l'IA :** connecter le frontend TypeScript aux snapshots et
  événements SSE FastAPI.
- **Proposition médiocre identifiée :** caster directement les réponses HTTP
  vers les interfaces TypeScript. Un cast rassure le compilateur mais ne
  contrôle aucune donnée reçue.
- **Correction ou refus :** validateurs runtime stricts pour snapshots,
  verdicts et événements ; les événements inconnus ou malformés sont signalés
  et ignorés sans effacer l'état valide. Les tests couvrent les contrats des
  deux côtés.

## Carte bonus — trois dettes proposées par l'IA et refusées

1. **Arrêt au premier appel d'outil répété** — remplacé dans
   `backend/app/agent.py` par la réutilisation du dernier succès, car la limite
   de tours suffit contre une boucle.
2. **Réparation avec la même enveloppe streamée** — remplacée dans
   `backend/app/experts.py` par un normalisateur JSON isolé, car répéter le
   protocole fautif ne le rend pas plus fiable.
3. **Faux streaming par timer** — refusé dans le frontend au profit des deltas
   SSE persistés (`backend/app/streaming.py`, `frontend/src/liveResponses.ts`),
   car une animation ne démontre ni transport réel ni reprise après F5.

---

# Historique technique antérieur

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
