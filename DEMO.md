# Démo CONCLAVE v1.0 — 5:00, deux voix

Objectif : montrer le problème résolu avant d'expliquer la mécanique. Le script
prévoit environ quatre minutes de parcours nominal et une minute d'échec géré.
Les phrases en italique sont dites ; les actions entre crochets ne le sont pas.

## Préparation hors chronomètre

- Brancher et tester l'écran et l'adaptateur vidéo.
- Lancer backend et frontend depuis le tag `v1.0`, avec Mesure, Sécurité et
  Coût activés et les tarifs `0.30/1.20` configurés.
- Ouvrir trois onglets : formulaire neuf, analyse nominale déjà terminée,
  analyse `provider_unavailable` déjà terminée.
- Préparer `Happy_path.md` dans le presse-papiers.
- Générer l'analyse d'échec avant le passage en lançant temporairement le
  backend avec une clé volontairement invalide, soumettre un court document,
  conserver son URL, puis relancer le backend avec la vraie configuration. La
  panne reste dans SQLite et n'est pas rejouée pendant la démo.
- Fermer éditeur, terminal, notifications et gestionnaire de mots de passe.
- Mettre le navigateur à 100 %, panneau « Pourquoi ? » replié, thème lisible.
- Démarrer l'enregistrement du plan B avant la première répétition complète.

## Script chronométré

### 0:00 → 0:30 — Erwan — le problème

[Afficher le formulaire neuf, sans défiler.]

*« Une décision importante ne devrait pas dépendre d'une seule réponse d'IA.
CONCLAVE prend un document, le fait défendre, attaquer et chiffrer par trois
experts, puis oblige un Arbitre à rendre un verdict exploitable. Je vous montre
d'abord le produit ; l'architecture viendra seulement si vous la demandez. »*

### 0:30 → 1:10 — Erwan — contrôle avant soumission

[Montrer les trois switches, désactiver puis réactiver Sécurité. Coller
`Happy_path.md` et cliquer « Convoquer le Conclave ».]

*« Avant l'envoi, je choisis les capacités autorisées. Cette configuration est
figée pour l'analyse : le document ne peut pas activer un outil lui-même. Je
soumets maintenant un document inédit. Le serveur renvoie un identifiant, ouvre
le flux, puis démarre une seule fois le travail. »*

### 1:10 → 2:10 — Erwan — preuves en direct

[Montrer le stepper, les trois colonnes, un appel d'outil puis les textes qui
grandissent réellement. Ne pas lire les réponses.]

*« Les trois experts travaillent en parallèle. Ce texte n'est pas une animation
machine à écrire : ce sont les deltas MiniMax reçus, persistés puis envoyés par
SSE. Le Comptable doit mesurer le document puis calculer le coût avant de
conclure. Une carte structurée n'apparaît qu'après validation Pydantic ; le
brouillon live, lui, n'est jamais présenté comme validé. »*

### 2:10 → 2:30 — transition préparée

[Montrer brièvement la chronologie des outils et des deltas.]

*« Nous avons maintenant les trois lectures et la preuve de leur exécution.
Yohan, montre ce qui se passe quand le navigateur ou le modèle n'est pas
parfait. »*

### 2:30 → 3:15 — Yohan — persistance et reprise

[Faire F5. Si le run courant n'est pas terminé à 2:50, passer à l'onglet nominal
déjà terminé.]

*« Un rechargement ne relance pas l'analyse. Le navigateur relit le snapshot et
l'historique SQLite, puis reprend seulement après le dernier événement connu.
Il n'y a ni doublon ni second coût. Le panneau “Pourquoi ce résultat ?” montre
les tours, les outils exécutés et les réparations automatiques, sans exposer la
chaîne de pensée ni le JSON brut. »*

### 3:15 → 4:00 — Yohan — verdict exploitable

[Afficher le verdict, les risques, actions, agents disponibles, tokens, coût et
latence.]

*« L'Arbitre ne reçoit que les sorties d'experts validées. Il rend une décision,
un score, les désaccords, trois risques maximum et des actions concrètes. Ici,
le coût observé est affiché séparément de l'estimation conservatrice : ce n'est
pas une facture. Si un expert manque, le verdict le déclare et l'analyse devient
dégradée au lieu de faire semblant d'être complète. »*

### 4:00 → 4:40 — Yohan — panne correctement gérée

[Ouvrir l'onglet `provider_unavailable`, puis son panneau « Pourquoi ? ».]

*« Voici le même produit avec une clé fournisseur invalide. Il ne reste pas sur
un spinner et n'invente aucun résultat : chaque expert quitte l'état running,
l'analyse nomme `provider_unavailable` et l'interface indique de vérifier la
connexion et la clé serveur. Les événements déjà persistés restent lisibles. »*

### 4:40 → 5:00 — Erwan — conclusion

[Revenir au verdict nominal.]

*« CONCLAVE ne promet pas que le modèle ne se trompe jamais. Il garantit que ses
capacités sont bornées, que ses sorties sont validées, que ses échecs sont
visibles et qu'une décision peut être auditée. C'est le produit que nous
livrons en v1.0. »*

Arrêt impératif à 5:00.

## Répétitions obligatoires

À remplir par le binôme, chronomètre réel en main. Si une répétition dépasse
5:00, couper une phrase — jamais une preuve produit.

| Passage | Durée | Incident observé | Ajustement effectué | Validé par les deux |
| --- | --- | --- | --- | --- |
| Répétition 1 | ____ | ____________________ | ____________________ | ☐ |
| Répétition 2 | ____ | ____________________ | ____________________ | ☐ |

## Plan B vidéo

- Enregistrer une répétition complète en 1080p, curseur visible et son des deux
  personnes, sans terminal ni secret à l'écran.
- Nom recommandé : `conclave-v1.0-demo-5min.mp4` ; conserver deux copies locales
  et vérifier la lecture hors ligne avec le câble de soutenance.
- La vidéo ne doit pas être commitée : le dépôt reste léger et public.
- Si le run live dépasse 2:50, utiliser l'onglet nominal préchargé ; si le réseau
  tombe, lancer immédiatement la vidéo au lieu de diagnostiquer devant le jury.

## Réponses courtes aux questions prévisibles

- **Cent utilisateurs :** la limite de trois analyses concurrentes et le débit
  MiniMax saturent d'abord ; ensuite SQLite/SSE mono-instance. Première évolution :
  file durable, workers, quotas, PostgreSQL/Redis.
- **Partie la moins fière :** l'enveloppe live + JSON reste un compromis imposé
  par le fournisseur ; la normalisation séparée est robuste mais coûte un appel
  supplémentaire. À terme : deux phases explicites dès le départ.
- **Temps perdu avec l'IA :** lui faire respecter simultanément streaming,
  outils et JSON final ; répéter le même prompt ne réparait pas le protocole.
- **Trois jours de plus :** authentification et isolation par utilisateur avant
  toute nouvelle fonction, car l'URL donne actuellement accès au document.
- **Coût complet :** dépend des tours et réparations. Le dernier run réel du
  document de référence a consommé 25 423 tokens, estimés à 0,011742 USD avec
  les tarifs configurés ; l'outil affiche aussi sa borne conservatrice.
