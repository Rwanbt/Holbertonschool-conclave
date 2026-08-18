# Happy path de la démo finale — 6 étapes

**Entrée figée :** `demo_document.txt`  
**Objectif visible :** montrer en moins de deux minutes qu'un même choix paraît défendable, risqué et coûteux selon l'angle, puis obtenir un arbitrage.

1. **Soumettre.** Nous ouvrons CONCLAVE, collons sans la modifier la proposition contenue dans `demo_document.txt`, puis montrons que le compteur reste sous 12 000 caractères.
2. **Convoquer.** Nous cliquons sur « Convoquer le Conclave » ; l'interface crée une analyse et affiche immédiatement les trois colonnes Avocat, Procureur et Comptable en cours.
3. **Observer.** Les trois agents travaillent en parallèle dans leur propre colonne, puis chaque résultat validé apparaît dès qu'il est prêt : l'Avocat défend le pilote limité, le Procureur localise les risques d'accès et de données, le Comptable évalue la complexité de l'OCR, du RAG et de l'exploitation.
4. **Comparer.** Les agents terminent dans leur ordre réel ; chaque colonne affiche 2 à 5 constats structurés, une note clairement nommée et au plus trois recommandations.
5. **Arbitrer.** Après validation des trois sorties, l'Arbitre démarre et rend un verdict `go_with_conditions` : conserver le pilote, mais sécuriser les accès, minimiser les données et réduire la première version avant déploiement.
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
