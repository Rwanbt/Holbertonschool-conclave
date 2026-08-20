## Objectif

Corriger le streaming de bout en bout et rendre les trois outils activables ou
désactivables indépendamment avant la soumission d'un document, conformément à
`PLAN_R1_CORRECTIF_STREAMING_SWITCHES_TOOLS.md`.

## Changements

- switches persistants et accessibles sur la première page (`useToolCatalog`,
  `ToolsPanel` accessible : `role="switch"`, `aria-checked`, état textuel)
- configuration des outils figée par analyse (`analysis_tool_states`, SQLite v2)
- outils désactivés retirés du payload envoyé à MiniMax (schémas filtrés, ou
  `tools`/`tool_choice` omis si aucun outil actif)
- `estimate_current_analysis_cost` ne mesure plus implicitement le document
  (`missing_prerequisite` sans mesure préalable)
- création `queued` puis démarrage (`POST …/start`, idempotent) après
  connexion SSE, `analysis.started` avant `expert.started`
- protocole `<LIVE_RESPONSE>` obligatoire (non vide) sur un tour final, avec
  une seule réparation en streaming avant échec propre
- reprise F5 via historique JSON paginé (`GET …/events/history`) + curseur,
  sans jamais rouvrir le SSE pour une analyse déjà terminale
- progression pilotée par les événements observés (`calculateActiveStep`),
  plus par le seul statut du snapshot
- tests backend (132 + 1 smoke MiniMax opt-in), frontend (84, avec
  `@testing-library/react`/`jsdom` pour les hooks et interactions), workflow CI

## Sécurité

- aucune clé ni document dans les événements/logs
- aucune chaîne de pensée affichée (le panneau expose « progression de
  l'agent », jamais `reasoning_content`/`<think>`)
- JSON final exclu des deltas (déjà vrai avant R1, revérifié par les tests)

## Validation

- [x] backend tests (`python -m pytest backend/tests -q` → 132 passed, 1 skipped)
- [x] frontend tests (`npm test -- --run` → 84 passed)
- [x] lint (`npm run lint`)
- [x] build (`npm run build`)
- [x] trois switches testés indépendamment (rendu, toggle isolé, mutation
      verrouillante, conservation d'état sur erreur — `toolsPanel.test.tsx`,
      `useToolCatalog.test.ts`)
- [x] reprise SSE / F5 (queued→start une seule fois, pas de réouverture sur
      analyse terminale, reprise au curseur hydraté — `useAnalysisController.test.ts`)
- [ ] E2E provider contrôlé avec un vrai navigateur (Playwright) — non
      exécuté dans cette session ; couvert à la place par les tests
      d'intégration backend (FakeClient/TestClient) et frontend
      (hooks + composants) ci-dessus, ainsi que la démonstration manuelle
      documentée dans le README
- [ ] smoke MiniMax réel — présent (`backend/tests/test_minimax_smoke.py`),
      opt-in via `MINIMAX_API_KEY`, non exécuté dans cette session (pas de
      clé disponible dans l'environnement) ; s'auto-`skip` proprement

## Démonstration

1. constater les trois switches dès la première page
2. désactiver un outil et recharger la page
3. lancer une analyse
4. observer étapes, tours, outils et deltas
5. recharger pendant le flux
6. obtenir le verdict persistant

Détail pas-à-pas dans le README (« Démonstration en six étapes »).

## Écarts assumés par rapport au plan

- **Granularité des commits** : le plan décrit sept lots (R1-01 à R1-07) avec
  des commits séparés. Les changements backend (contrats, persistance,
  cycle de vie, streaming) sont interdépendants au niveau du typage/des
  signatures (ex. `run_agent_loop(round_event_sink=…)` utilisé par
  `experts.py` dès son introduction) : les livrer en plusieurs commits
  aurait laissé des états intermédiaires qui ne compilent/passent pas les
  tests, ce que les garde-fous du plan interdisent explicitement (« chaque
  commit doit rester cohérent »). Ils sont donc livrés en **un commit
  backend** et **un commit frontend**, chacun vert (tests + lint + build),
  au lieu de quatre puis trois. Le détail lot par lot reste traçable dans
  `JOURNAL.md` et dans la description de chaque commit.
- **Couverture de tests** : la check-list de la section 14 du plan est très
  large (8 combinaisons de switches, tous les cas E2E provider programmés,
  etc.). Cette session couvre les scénarios représentatifs de chaque défaut
  corrigé (2.1 à 2.6) et les critères d'acceptation bloquants de la
  section 19, sans viser une couverture combinatoire exhaustive.
