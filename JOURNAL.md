# Journal

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