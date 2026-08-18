# Partage du travail — équipe de 2

## Responsabilités nommées

### Membre A — Erwan — Frontend, contrat client et démonstration

* React + TypeScript + Vite : page, saisie et trois colonnes.
* Validation UX de la taille du document et états de chargement/erreur.
* Client `POST /api/analyses`, ouverture `EventSource` et routage des événements par `source`.
* Affichage typé des `AgentOutput` et du `ArbiterVerdict`.
* Test d'intégration côté navigateur avec événements simulés : ordre variable, erreur et timeout.
* Maintenance de `demo\_document.txt`, du happy path et répétition de la soutenance.

### Membre B — Yo — Backend, agents et orchestration

* FastAPI : routes de création d'analyse et de flux SSE.
* Modèles Pydantic des entrées, sorties agents, verdict et événements.
* Orchestration `asyncio`, file d'événements, timeouts et nettoyage TTL.
* Prompts et appels LLM de l'Avocat, du Procureur, du Comptable et de l'Arbitre.
* Outils déterministes et validation des sorties structurées.
* Tests backend : parallélisme, réponse invalide, timeout et arbitrage dégradé.

## Travail obligatoirement commun

|Moment|Les deux membres produisent ensemble|Preuve de fin|
|-|-|-|
|Avant le code|Validation de `CONTRATS.md`, événements et règles d'erreur|Aucun champ n'est « deviné » par le front ou le back|
|Première intégration|Connexion sur un faux LLM ou des fixtures déterministes|Trois agents arrivent dans le désordre sans erreur UI|
|Stabilisation|Scénarios nominal + un timeout + sortie invalide|Comportement conforme au tableau d'erreurs|
|Avant checkpoint/soutenance|Chacun explique seul toute l'architecture et rejoue les 6 étapes|Deux répétitions sans aide de l'autre|

## Règle d'intégration

Le producteur et le consommateur d'un contrat sont modifiés dans la même pull request : changement Pydantic, type TypeScript, fixture et test associé restent synchronisés.

## Charge et entraide

Le backend porte davantage de logique ; Erwan prend donc en plus les fixtures SSE, les tests d'intégration et la préparation de démo. Si l'orchestration bloque, les deux travaillent d'abord sur le flux minimal `agent.started → agent.completed` avant toute animation ou finition visuelle.

