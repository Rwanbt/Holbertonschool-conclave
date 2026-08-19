# Journal

## 2026-08-19 — Palier 3 : premier outil et boucle agent

- Ajout de trois fonctions métier déterministes (sans effet de bord) :
  `measure_document`, `find_security_indicators`, `estimate_analysis_cost`
  (backend/app/tools.py).
- Boucle agent MiniMax avec `tool_calls` automatiques : registre + validation
  nom/arguments, `DISABLED_TOOLS`, détection des appels répétés, limite de
  tours, accumulation jetons/latence (backend/app/agent.py).
- Route POST /api/p3/agent renvoyant `answer`, `model`, `trace`, `usage`
  (backend/app/main.py).
- Docs : OUTILS.md aligné (USD, bornes, masquage), AGENTS.md créé.
- Tests : 51 verts (outils jamais simulés, MiniMax simulé en réseau).
- Tests manuels réels MiniMax-M3 : métriques, indices de sécurité, coût,
  trajet `tool_disabled` — tous conformes.
- Poussé sur origin/yo (5 commits Palier 3). PR proposée yo → dev.