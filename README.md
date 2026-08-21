# CONCLAVE — Palier 4 « Le Contrat Conclave » + R1 (streaming réel, outils indépendants)

Dès l'ouverture, le front affiche le stepper et les **trois switches d'outils**
(indépendants, persistants, chargés automatiquement). Le document est ensuite
soumis (`POST /api/analyses`, statut `queued`, configuration des outils figée
dans la même requête) ; trois experts (**Avocat**, **Procureur**, **Comptable**)
l'analysent en parallèle une fois le flux SSE ouvert (`POST …/start`, appelé par
le front après `EventSource.onopen`, jamais avant), puis un **Arbitre** rend un
verdict. L'interface parcourt six étapes — **Soumettre, Convoquer, Observer,
Comparer, Arbitrer, Décider** — pilotées par les événements SSE observés (pas
par le seul statut du snapshot), et survit à un rechargement de page : le
snapshot et l'historique JSON paginé (`GET …/events/history`) sont chargés en
parallèle, et le flux SSE ne rouvre jamais depuis zéro ni pour une analyse déjà
terminale.

- **Front** : React + TypeScript + Vite dans `frontend/` (branche `erwan`).
- **Back** : FastAPI + Python dans `backend/` (branche `yo`, travail de Yohan).

## Prérequis

- Node.js ≥ 18 et npm.
- Python ≥ 3.10.
- Deux terminaux : un pour le backend, un pour le frontend.

## Démarrage en moins de cinq minutes

```bash
git clone https://github.com/Rwanbt/Holbertonschool-conclave.git
cd Holbertonschool-conclave
git switch dev
```

### 1. Configuration locale

Copiez le fichier d'exemple à la racine vers `.env` (le fichier `.env` est
ignoré par git ; c'est lui qui porte la clé MiniMax, jamais commitée) :

```bash
cp .env.example .env
```

Ensuite, ouvrez `.env` et remplacez
`MINIMAX_API_KEY=replace_with_your_minimax_api_key` par votre clé MiniMax.
Pour activer les estimations de coût du Comptable, renseignez aussi
`MINIMAX_INPUT_USD_PER_MILLION` et `MINIMAX_OUTPUT_USD_PER_MILLION` (0.0 ou
absent = coût non revendiqué).

Options du front, dans `frontend/.env` (facultatif — la valeur par défaut
suffit au parcours local) :

```bash
cp frontend/.env.example frontend/.env
```

`VITE_API_BASE_URL` vaut `http://localhost:8000` par défaut. Cette variable
n'est pas secrète.

### 2. Terminal 1 — Backend

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Le backend écoute sur `http://localhost:8000`. Vérification de santé :

```bash
curl -s http://localhost:8000/api/health
# -> {"status":"ok"}
```

### 3. Terminal 2 — Frontend

```bash
cd frontend
npm install
npm run dev
```

Puis ouvrez **http://localhost:5173**.

## Utiliser le Contrat Conclave

- **Soumettre** : collez un document (1 à 12 000 caractères, compteur affiché)
  puis cliquez « Convoquer le Conclave ».
- **Observer / Comparer** : chaque expert travaille dans une colonne dédiée ;
  sa réponse est visible **au fil de l’eau** pendant la génération, puis la
  carte structurée validée (constats, note et recommandations) la remplace dès
  qu’elle est prête.
- **Arbitrer / Décider** : l'arbitre écrit son verdict en direct avant la
  clôture ; la décision validée (`go`, `go_with_conditions` ou `no_go`), le
  score, les désaccords, les risques, les actions et le compromis accepté
  s’affichent dès que `verdict` est présent dans le snapshot.

### Streaming natif MiniMax — pas une simulation

Le backend transmet à MiniMax les **deltas réels** de la réponse via des
événements SSE persistés dans SQLite :

- `agent.response.started` — le rôle commence à écrire ;
- `agent.response.delta` — fragment de texte borné (`sequence` strictement
  croissante par rôle, `delta` borné par `STREAM_DELTA_BATCH_CHARS`) ;
- `agent.response.completed` — la réponse du rôle est terminée ;
- `agent.response.failed` — la réponse s’est arrêtée (ex. erreur de protocole).
- `analysis.started`, `agent.round.started` et `agent.round.completed` — le
  démarrage et la progression bornée de chaque tour sont persistés.

Le front reconstruit par rôle (`collectLiveResponses`) un **brouillon live** :
les deltas sont concaténés dans l’ordre des séquences, une séquence dupliquée
ne duplique jamais le texte, et les trois experts (et l’Arbitre) restent
séparés même si leurs événements sont intercalés. **Aucun timer, aucune
animation de révélation** : le texte affiché est exactement la concaténation
des deltas SSE validés. Un brouillon terminé n’est **jamais** une sortie
validée : seule la carte Pydantic du snapshot fait foi après `expert.completed`
ou `arbiter.completed`.

**Panneau démonstration** : une section « Flux des réponses » montre par rôle le
nombre de deltas reçus, le nombre de caractères, les séquences première et
dernière et le statut, ainsi qu’une timeline ordonnée où chaque `tool.*` côtoie
les `agent.response.*` — la preuve que les deltas arrivent avant la clôture,
jamais rejoués après coup. Le panneau n’affiche ni le JSON final brut, ni le
reasoning du modèle.

### Cycle de vie d'une analyse : queued → running → terminal

`POST /api/analyses` crée l'analyse en `queued` et fige, dans la même
requête, une copie immuable du registre d'outils (`analysis_tool_states`) :
une modification ultérieure du registre global (via les switches, pour la
*prochaine* analyse) n'affecte jamais celle-ci. Le job ne démarre pas à la
création : le front affiche l'analyse `queued`, ouvre `EventSource(?after=0)`,
attend `onopen`, puis appelle `POST /api/analyses/{id}/start` — idempotent
(compare-and-set SQL `queued → running`, 202 la première fois, 200
`already_started` ensuite), qui insère `analysis.started` avant tout
`expert.started`. Un outil désactivé dans la configuration figée n'est même
pas proposé à MiniMax (`tools` filtré, ou omis si aucun outil actif).

### Persistance et rechargement (F5)

L'UUID de l'analyse est stocké dans `localStorage` (clé
`conclave.currentAnalysisId.v1`) et dans l'URL (`?uuid=`). Au montage, le front
charge en parallèle le snapshot (`GET /api/analyses/{id}`) et l'historique JSON
paginé (`GET …/events/history?after=0&limit=500`, paginé tant que `has_more`)
pour reconstruire brouillons et traces sans rejouer `after=0` sur le flux SSE
vivant. Le flux SSE **n'est jamais rouvert** pour une analyse déjà terminale
(le front affiche l'état final directement, ce n'est pas une animation à
rejouer) ; pour une analyse `queued`/`running`, il reprend depuis le plus grand
identifiant hydraté. `POST …/start` part après `onopen` (ou après un délai de
secours) et dispose de trois tentatives bornées ; l'idempotence serveur évite
tout double job si la réponse HTTP s'est perdue. L'idempotence par `event.id`
absorbe les rejeux. `EventSource` tente sa reconnexion avec `Last-Event-ID`,
mais le front coupe après dix secondes sans reprise et affiche un bouton
« Réessayer la connexion » au lieu d'un spinner infini. Le POST de création
n'est jamais relancé au rechargement ; une analyse introuvable (404) nettoie
la référence locale.

### Panneau outils — switches indépendants dès la première page

Les trois switches (**Mesurer le document**, **Rechercher les indicateurs de
sécurité**, **Estimer le coût de l'analyse**) sont visibles et pilotables
avant même de coller un document, sans manipulation initiale : le catalogue
(`GET /api/tools`) est chargé automatiquement, et chaque switch
(`role="switch"`, `aria-checked`, état textuel Activé/Désactivé/Modification…)
envoie directement `POST /api/tool-commands` — un seul changement à la fois,
les autres restent verrouillés pendant la mutation, et une erreur conserve
l'état précédent sans jamais être écrasée par une réponse réseau obsolète
(compteur de requêtes monotone). `estimate_current_analysis_cost` affiche sa
dépendance à `measure_current_document` (« Nécessite Mesurer le document ») :
activé seul, il renvoie proprement `missing_prerequisite` sans jamais mesurer
implicitement. Pendant une analyse `queued`/`running`, le panneau bascule en
lecture seule sur la configuration **figée** de cette analyse ; après
« Nouvelle analyse », il recharge le registre global pour la suivante. Une
section « Commande avancée » repliable garde l'accès à la grammaire brute
(`/tools`, `/tools list`, `/tools enable <nom>`, `/tools disable <nom>`).

## Comportements hors du contrat

Les garde-fous du Palier 4 s'appliquent : aucune valeur inventée, un seul
outil par tour, sorties validées avant affichage, événements SSE bornés. Une
chute du flux SSE déclenche une reconnexion bornée puis une erreur actionnable ;
un événement malformé est ignoré et signalé sans vider le snapshot. Un document
conforme à `Happy_path.md` doit parcourir les six étapes et finir sur
`go_with_conditions` (à condition que les tarifs MiniMax soient configurés pour
l'estimation du Comptable).

## Passer de la démo au produit (Palier 5)

### Répondre à « pourquoi l'agent a fait ça ? » sans ouvrir le code

Le panneau **« Pourquoi ce résultat ? »** s'affiche sous le verdict et donne,
dans l'ordre :

1. **ce qui a échoué** — chaque code d'erreur traduit en français avec l'action
   corrective (`provider_unavailable` → « MiniMax n'a pas répondu […] vérifiez
   MINIMAX_API_KEY ») ;
2. **le contrôle du document soumis** — tournures d'instruction repérées ;
3. **les outils disponibles pour cette analyse** — activés, et désactivés dont
   le schéma n'a pas été envoyé au modèle ;
4. **la décision prise à chaque tour** — par rôle : `a demandé un outil`,
   `a rendu sa réponse finale`, avec la latence ;
5. **les outils réellement exécutés** et leur résultat.

### Échouer bruyamment, jamais mentir

Deux issues acceptables quand on casse l'application : ça marche, ou ça refuse
proprement. Jamais « ça ment ».

| Entrée hostile | Réponse |
|---|---|
| Champ vide | 422, aucune analyse créée |
| Corps de 40 Mo | 413 sur `Content-Length`, ou dès que le flux sans longueur fiable dépasse 1 Mo |
| Émojis, cyrillique, SQL | acceptés, stockés à l'identique, requêtes paramétrées |
| Injection de prompt | analysée, **signalée**, sans extension de capacités (voir `SECURITY.md`) |
| Dix clics sur « Convoquer » | 429 au-delà de `MAX_CONCURRENT_ANALYSES`, avec la marche à suivre |
| Réseau coupé / fausse clé | `provider_unavailable` affiché, experts sortis de `running`, **aucun spinner infini** |

### Évaluation chiffrée

Cinq cas décrits à la main dans [`eval/cases.md`](eval/cases.md), rejouables en
une commande, **sans clé MiniMax** (le fournisseur est simulé) :

```bash
make eval          # ou, depuis frontend/ : npm run eval
```

Score courant : **5/5 invariants techniques avec fournisseur simulé** (2/5
avant le palier 5 — le détail et les limites de ce score sont dans
`eval/cases.md`). La CI exécute l'éval à chaque PR, donc une régression casse
le build.

### Sécurité

[`SECURITY.md`](SECURITY.md) répond en détail à « que se passe-t-il si
l'utilisateur écrit *ignore tes instructions précédentes* ? », et explique
pourquoi la détection heuristique n'est **pas** la défense — les vraies
barrières sont structurelles.

```bash
cd frontend && npm run build && cd ..
./scripts/check-no-secrets.sh     # aucune clé dans le bundle publié
```

### Thème clair / sombre

Un bouton dans l'en-tête bascule le thème. Par défaut l'application suit
`prefers-color-scheme` ; un choix explicite est mémorisé et l'emporte ensuite
sur le réglage système. Toutes les couleurs passent par des jetons CSS
(`src/index.css`), aucune valeur n'est codée en dur dans les composants.

## Procédure de test

```bash
cd frontend
npm ci
npm test -- --run
npm run lint
npm run build
```

Depuis la racine :

```bash
python -m pytest backend/tests -q   # 157 tests + 1 smoke MiniMax réel (skip sans clé)
git status --short
git diff --check
```

Le workflow CI (`.github/workflows/ci.yml`) exécute ces mêmes commandes sur
chaque PR et push vers `main`/`dev`, sans clé MiniMax (le smoke test réel
`backend/tests/test_minimax_smoke.py` s'auto-`skip` sans `MINIMAX_API_KEY`).

### Démonstration en six étapes

1. Ouvrir l'application : le stepper et les trois switches sont immédiatement
   visibles, avant tout document.
2. Désactiver l'outil Sécurité, conserver Mesure et Coût activés, puis
   recharger la page : les états restent identiques (persistés en SQLite).
3. Coller un document inédit et cliquer « Convoquer le Conclave » : l'étape
   Convoquer apparaît, puis Observer démarre après l'ouverture réelle du SSE.
4. Observer les tours agentiques et outils en direct dans le panneau de
   démonstration ; l'outil Sécurité n'est jamais appelé ni même envoyé au
   modèle (configuration figée de l'analyse).
5. Voir les synthèses publiques grandir par deltas, puis recharger la page
   pendant l'arbitrage : le flux reprend depuis le dernier identifiant hydraté,
   sans doublon ni ré-déclenchement du job.
6. Obtenir le verdict final et montrer la timeline, les tokens, le coût, la
   latence et la configuration figée des outils de cette analyse.

Test manuel complémentaire avec le backend et MiniMax réels :

1. Ouvrir l'onglet Réseau du navigateur sur la connexion SSE
   (`/api/analyses/{id}/events`).
2. Voir les appels d'outils (`tool.started` / `tool.completed`) apparaître avant
   la réponse du rôle.
3. Voir au moins deux `agent.response.delta` pour un même rôle avant
   `agent.response.completed`.
4. Vérifier `/tools list`, `/tools disable <nom>`, F5, puis `/tools enable <nom>`.

Mesures à relever : temps avant le premier delta, nombre de deltas par rôle et
durée totale.

## Dépannage minimal

| Symptôme | Cause probable | Correctif |
| --- | --- | --- |
| « Impossible de joindre le backend » | back non lancé, mauvais port, ou `VITE_API_BASE_URL` erronée | lancer le back puis recharger la page ; revérifier `frontend/.env` |
| Code 422 à la soumission | document vide ou hors bornes | remplir le champ ; document ≤ 12 000 caractères |
| Code 500 | clé ou configuration backend absente | vérifier `MINIMAX_API_KEY` dans `.env` racine |
| Code 502 | fournisseur MiniMax indisponible | attendre puis réessayer |
| « Analyse introuvable (404) » | analyse supprimée ou base remise à zéro | la référence locale est nettoyée ; relancer une analyse |
| Outil inactif dans le panneau | outil désactivé via `/tools` ou `DISABLED_TOOLS` | réactiver via la barre `/tools enable <nom>` ; l'état SQLite fait foi |
| L’analyse finit en échec avec le Comptable rejeté | tarifs MiniMax absents (0.0) → l’estimation de coût est indisponible | renseigner `MINIMAX_INPUT_USD_PER_MILLION` / `MINIMAX_OUTPUT_USD_PER_MILLION` dans le `.env` racine |
