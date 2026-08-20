# CONCLAVE — Palier 4 « Le Contrat Conclave »

Le front soumet un **document** au backend (`POST /api/analyses`) ; trois experts
(**Avocat**, **Procureur**, **Comptable**) l'analysent en parallèle, puis un
**Arbitre** rend un verdict. L'interface parcourt six étapes — **Soumettre,
Convoquer, Observer, Comparer, Arbitrer, Décider** — et survit à un
rechargement de page : le snapshot vient de `GET /api/analyses/{id}` et le flux
SSE est rejoué depuis zéro (`?after=0`) pour reconstruire brouillons et traces.

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

### Persistance et rechargement (F5)

L'UUID de l'analyse est stocké dans `localStorage` (clé
`conclave.currentAnalysisId.v1`) et dans l'URL (`?uuid=`). Au montage, le front
charge d'abord le snapshot (`GET /api/analyses/{id}`), puis ouvre la connexion
SSE avec **`after=0`** pour rejouer tous les événements de SQLite et reconstruire
brouillons et traces que le state React a perdus. L'idempotence par `event.id`
absorbe les rejeux, et le même `EventSource` utilise sa reconnexion native avec
`Last-Event-ID` en cas de coupure. Le dernier id reçu reste écrit en
localStorage à titre de diagnostic, jamais pour sauter un historique. Le POST
de création n’est jamais relancé au rechargement ; une analyse introuvable
(404) nettoie la référence locale.

**Panneau outils** : le panneau charge le catalogue au montage et expose une
barre de commande complète : un champ « Commande outils », un bouton
« Exécuter » et une aide copiable. La grammaire supportée est exactement
`/tools`, `/tools list`, `/tools enable <nom>` et `/tools disable <nom>` — la
chaîne est envoyée telle quelle au backend via `POST /api/tool-commands`, qui
renvoie toujours le catalogue complet dans `response.tools` ; après toute
réponse 200 la liste locale est remplacée par ce catalogue, et une erreur 422
conserve l’état précédent (persisté dans SQLite `tool_states`).

## Comportements hors du contrat

Les garde-fous du Palier 4 s'appliquent : aucune valeur inventée, un seul
outil par tour, sorties validées avant affichage, événements SSE bornés. Une
chute du flux SSE déclenche la reconnexion native de l'EventSource ; un
événement malformé est ignoré et signalé sans vider le snapshot. Un document
conforme à `Happy_path.md` doit parcourir les six étapes et finir sur
`go_with_conditions` (à condition que les tarifs MiniMax soient configurés pour
l'estimation du Comptable).

## Procédure de test du streaming

```bash
cd frontend
npm ci
npm test -- --run
npm run lint
npm run build
```

Depuis la racine :

```bash
.venv/bin/python -m pytest backend/tests -q   # suite backend de Yohan
git status --short
git diff --check
```

Test manuel avec le backend et MiniMax réels :

1. Ouvrir l'onglet Réseau du navigateur sur la connexion SSE
   (`/api/analyses/{id}/events`).
2. Lancer une analyse avec un texte inventé.
3. Voir les appels d'outils (`tool.started` / `tool.completed`) apparaître avant
   la réponse du rôle.
4. Voir au moins deux `agent.response.delta` pour un même rôle avant
   `agent.response.completed`.
5. Voir le texte de la carte grandir avant la sortie complète.
6. Recharger (F5) pendant un flux : le snapshot puis le rejeu depuis zéro
   reconstruisent le brouillon interrompu ; le POST de création n'est pas répété.
7. Vérifier `/tools list`, `/tools disable <nom>`, F5, puis `/tools enable <nom>`.

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