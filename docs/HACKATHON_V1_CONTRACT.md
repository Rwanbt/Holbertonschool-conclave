# Contrat hackathon CONCLAVE v1.0 (archivé)

Ce document fige le contrat **hackathon** (`SPEC.md`, tag `v1.0`) : il reste
reproductible et auditable, et n'est pas modifié par l'évolution production.

## Cœur technique

```
Utilisateur
→ document
→ trois experts antagonistes en parallèle (Avocat, Procureur, Comptable)
→ sorties structurées validées (AgentOutput, Pydantic)
→ Arbitre (séquentiel, après les experts)
→ verdict structuré exploitable (ArbiterVerdict, Pydantic)
```

Contrats conservés dans la version production :

- exécution parallèle des trois experts (`asyncio.gather`) puis Arbitre
  séquentiel ;
- état partagé cohérent (SQLite, événements persistés avant diffusion) ;
- streaming réel vers le navigateur (enveloppe
  `<LIVE_RESPONSE>…</LIVE_RESPONSE><FINAL_JSON>…</FINAL_JSON>`) ;
- validation des sorties avant utilisation (Pydantic + réparations bornées) ;
- outils réels et auditables, sans argument, jamais le document ;
- garde-fous de coût, nombre de tours et temps ;
- fonctionnement dégradé (2 experts valides → verdict) sans résultat inventé ;
- traçabilité « Pourquoi ce résultat ? » (traces, panneau Pourquoi) ;
- détection informative des prompt injections ;
- séparation brouillon live / sortie structurée validée ;
- reprise après rechargement (snapshot + historique JSON + SSE) ;
- erreurs visibles et actionnables, aucune exception avalée.

## Stack d'origine (v1.0)

- Front : React 18 + TypeScript + Vite (`frontend/`).
- Back : FastAPI + Python + Pydantic + `asyncio`, SQLite (`aiosqlite`).
- Fournisseur unique : MiniMax `MiniMax-M3` via le SDK `openai` (API
  OpenAI-compatible), `thinking` désactivé via `extra_body`.

## Hors scope historique

Le cadrage hackathon déclarait hors scope l'authentification, le
multi-utilisateur et le multi-provider. Ces fonctionnalités font désormais
partie de la version production (v1.1) — voir `docs/PRODUCTION_ARCHITECTURE.md`.