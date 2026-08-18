# SPEC — CONCLAVE

**Version :** Palier 1 — cadrage gelé  
**Équipe :** Erwan + Yo  
**Pitch :** un même document, trois lectures contradictoires, un verdict exploitable.

**Stack figée :** React + TypeScript + Vite ; FastAPI + Python + Pydantic + `asyncio` ; API Mistral avec le modèle `mistral-small-2603` ; état éphémère en mémoire.

## Le problème — exactement 5 lignes

1. Relire seul une spécification ou une décision technique renforce facilement le biais de confirmation.
2. Solliciter plusieurs avis séparément prend du temps et produit des réponses difficiles à comparer.
3. Les forces, les risques et les coûts d'un même choix sont rarement examinés avec la même rigueur.
4. Sans format commun, les désaccords restent implicites et la décision finale repose encore sur l'intuition.
5. CONCLAVE confronte trois analyses spécialisées puis les fait arbitrer pour livrer une décision priorisée et traçable.

## Hors scope — lu avant le scope

1. **⭐ Pas de RAG ni de recherche documentaire externe.** CONCLAVE juge uniquement le texte soumis : ajouter indexation, qualité des sources et citations déplacerait le risque principal vers la recherche, alors que la valeur à démontrer est la confrontation d'analyses. C'est notre « non argumenté » pour la carte bonus.
2. Pas de PDF, DOCX, image ni OCR : l'entrée est du texte brut collé dans l'interface.
3. Pas de navigation web ni d'enrichissement par une source externe.
4. Pas d'exécution du code analysé, de shell, de sandbox ou de scan réel d'une infrastructure.
5. Pas de compte, d'authentification, d'organisation ni de gestion de droits.
6. Pas d'historique durable, de base de données, d'export ou de reprise d'une analyse après redémarrage du serveur.
7. Pas de reprise automatique d'un flux SSE interrompu ; l'utilisateur relance l'analyse.
8. Pas de traitement parallèle de plusieurs analyses par un même utilisateur.
9. Pas de sélection dynamique entre plusieurs fournisseurs LLM ni de bascule automatique vers un autre modèle.
10. Pas d'édition des prompts, des rôles ou des critères de l'Arbitre depuis l'interface.
11. Pas de garantie d'exactitude, d'audit de sécurité certifié, ni de conseil juridique ou financier.
12. Pas de document confidentiel ou sensible : son contenu peut être transmis au fournisseur LLM externe et le projet ne fournit pas de garantie spécifique de confidentialité.
13. Pas d'application mobile native ; seule une interface web desktop responsive minimale est prévue.
14. Pas de quatrième expert, de débat récursif entre agents ou de seconde passe automatique de l'Arbitre.

## Scope

1. Saisir un document texte en français de **1 à 12 000 caractères**.
2. Lancer en parallèle trois rôles backend : **Avocat** (forces), **Procureur** (risques) et **Comptable** (coûts/complexité).
3. Afficher en temps réel leur démarrage, leur résultat validé dès qu'il est prêt et leur état dans trois colonnes distinctes via SSE.
4. Valider chaque résultat final avec un contrat structuré avant de le transmettre à l'Arbitre.
5. Faire produire à l'**Arbitre** un verdict structuré : décision, score, désaccords, risques, actions et compromis.
6. Continuer en mode dégradé si un agent échoue ou dépasse son délai, à condition d'obtenir au moins deux analyses valides.
7. Conserver uniquement un état temporaire en mémoire, supprimé au plus tard 15 minutes après la fin de l'analyse.

## User stories — 3 maximum

### US1 — Soumettre

En tant qu'étudiant ou développeur, je veux coller une spécification puis convoquer le Conclave afin d'obtenir plusieurs lectures du même texte sans organiser trois consultations séparées.

**Acceptation :** le bouton est désactivé si le texte est vide ou dépasse 12 000 caractères ; une soumission valide crée une analyse unique.

### US2 — Observer

En tant qu'utilisateur, je veux voir séparément l'Avocat, le Procureur et le Comptable travailler afin de comprendre l'origine de chaque argument et l'ordre réel d'arrivée des réponses.

**Acceptation :** chaque colonne montre un état explicite (`en attente`, `en cours`, `terminé`, `erreur` ou `timeout`) et n'affiche que les événements de son agent.

### US3 — Décider

En tant qu'utilisateur, je veux recevoir un verdict arbitré et priorisé afin de savoir quoi conserver, quoi corriger en premier et quel compromis j'accepte.

**Acceptation :** le verdict affiche la décision, un score sur 100, les désaccords, les risques prioritaires, au plus trois actions et les agents indisponibles.

## Rôles et frontières

|Rôle|Question unique|Produit|
|-|-|-|
|Avocat|Qu'est-ce qui est techniquement défendable et mérite d'être conservé ?|Forces, preuves, recommandations de conservation|
|Procureur|Qu'est-ce qui peut casser, être dangereux ou incohérent ?|Risques, indices localisés, corrections|
|Comptable|Quel est le coût réel en temps, argent, complexité et maintenance ?|Coûts, dette, compromis de simplification|
|Arbitre|Que faut-il décider au vu des analyses valides ?|Verdict, priorités, compromis et limites|

Les trois experts n'appellent jamais l'Arbitre. L'orchestrateur valide leurs sorties, attend leur état terminal, puis déclenche l'Arbitre.

## Contraintes de fonctionnement

* Un agent dispose de **30 secondes maximum** ; l'Arbitre dispose de **20 secondes** ; l'analyse entière est arrêtée à **60 secondes**.
* Ces valeurs sont configurables, mais ce sont les valeurs de référence de la démo.
* Deux analyses valides suffisent à produire un verdict dégradé ; avec zéro ou une analyse valide, aucun verdict n'est inventé.
* Le SSE diffuse des changements d'état et des résultats complets, pas les tokens bruts du LLM. Cela garde le temps réel sans exposer du JSON partiel ou invalide au front.
* Tout contenu LLM est rendu comme texte, jamais comme HTML.

## Critères de réussite du Palier 4

* Le document officiel de démo est accepté sans modification.
* Les trois analyses apparaissent dans la bonne colonne, même si elles terminent dans un ordre différent.
* Le verdict cite au moins un désaccord réel et propose au plus trois actions ordonnées.
* Un timeout simulé ne fait pas crasher l'application et est signalé dans le verdict dégradé.

