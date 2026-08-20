# Happy path de la démo finale — 6 étapes

**Entrée figée :** `demo_document.txt`  
**Objectif visible :** montrer en moins de deux minutes qu'un même choix paraît défendable, risqué et coûteux selon l'angle, puis obtenir un arbitrage.

1. **Soumettre.** Nous ouvrons CONCLAVE, collons sans la modifier la proposition contenue dans `demo_document.txt`, puis montrons que le compteur reste sous 12 000 caractères.
2. **Convoquer.** Nous cliquons sur « Convoquer le Conclave » ; l'interface crée une analyse et affiche immédiatement les trois colonnes Avocat, Procureur et Comptable en cours.
3. **Observer.** Les trois agents travaillent en parallèle dans leur propre colonne. Pendant la génération, nous voyons chaque expert appeler ses outils (`tool.started` / `tool.completed` dans le panneau de démonstration), puis sa réponse arriver **au fil de l'eau** : le texte de la carte grandit réellement avec les deltas SSE avant tout événement `expert.completed`. Si nous rechargeons la page (F5) à ce moment-là, le brouillon interrompu est reconstruit par le rejeu depuis zéro. Dès que le snapshot est rechargé, la carte structurée validée (constats, note, recommandations) remplace le brouillon.
4. **Comparer.** Les agents terminent dans leur ordre réel ; chaque colonne affiche 2 à 5 constats structurés, une note clairement nommée et au plus trois recommandations.
5. **Arbitrer.** Après validation des trois sorties, l'Arbitre démarre et son raisonnement apparaît en direct dans le panneau d'arbitrage avant la clôture ; puis le verdict validé `go_with_conditions` remplace le brouillon : conserver le pilote, mais sécuriser les accès, minimiser les données et réduire la première version avant déploiement.
6. **Décider.** Nous terminons sur le verdict final : score global, désaccord principal, risques prioritaires, trois actions ordonnées et compromis accepté ; l'état passe à « Analyse terminée » sans changer de page.

## Conditions de répétabilité

- Utiliser toujours le fichier fourni, le même fournisseur et le même modèle LLM.
- Régler la température à une valeur basse et conserver les prompts de rôle versionnés.
- Répéter la démo complète au moins trois fois avant le Palier 4.
- Ne jamais dépendre d'un ordre fixe de fin des trois agents.

## Résultat minimal acceptable

- Avocat : au moins deux forces liées au périmètre pilote et à la validation humaine.
- Procureur : au moins deux risques localisés liés aux accès, journaux ou données sensibles.
- Comptable : au moins deux coûts liés à l'OCR/RAG, à l'exploitation ou au délai.
- Arbitre : une vraie condition de passage et trois actions maximum, pas un simple résumé.
