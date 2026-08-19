# CONCLAVE — Palier 4 « Le Contrat Conclave »

Le front soumet un **document** au backend (`POST /api/analyses`) ; trois experts
(**Avocat**, **Procureur**, **Comptable**) l'analysent en parallèle, puis un
**Arbitre** rend un verdict. L'interface parcourt six étapes — **Soumettre,
Convoquer, Observer, Comparer, Arbitrer, Décider** — et survit à un
rechargement de page : le snapshot et le flux SSE repris via `?after=` sont
rejoués depuis SQLite.

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
  chaque sortie validée apparaît dès qu'elle est prête, avec constats, note et
  recommandations.
- **Arbitrer / Décider** : l'arbitre rend un verdict (`go`, `go_with_conditions`
  ou `no_go`), un score, les désaccords, les risques, les actions et le
  compromis accepté.

**Persistance** : l'UUID de l'analyse est stocké dans `localStorage`
(clé `conclave.currentAnalysisId.v1`) et dans l'URL (`?uuid=`). Un
rafraîchissement relit le snapshot (`GET /api/analyses/{id}`) puis reprend le
flux SSE à `?after=<dernier événement reçu>`. Une analyse introuvable (404)
nettoie la référence locale sans rien re-poster.

**Panneau outils** : chaque bouton envoie une commande
`/tools enable|disable <nom>` via `POST /api/tool-commands` ; l'état vit dans
SQLite (`tool_states`), jamais de redémarrage nécessaire. La commande
`/tools list` renvoie un 422 côté serveur (`GET /api/tools` fait foi).

**Panneau « Démonstration »** : connexion SSE, dernière événement reçu, tours
d'outils par rôle et usage agrégé, entièrement reconstruits depuis les
événements observés (aucun timer fictif).

## Comportements hors du contrat

Les garde-fous du Palier 4 s'appliquent : aucune valeur inventée, un seul
outil par tour, sorties validées avant affichage, événements SSE bornés. Une
chute du flux SSE déclenche une reconnexion automatique ; un événement
malformé est ignoré et signalé sans vider le snapshot. Un document conforme à
`Happy_path.md` doit parcourir les six étapes et finir sur `go_with_conditions`.

## Commandes de vérification

```bash
cd frontend
npm ci
npm run lint
npm run build
npm test          # vitest — validators, étapes, stockage, commandes /tools
```

Depuis la racine, vérifier que rien d'étranger n'est indexé :

```bash
python -m pytest backend/tests -q   # suite backend de Yohan, inchangée
git status --short
git diff --check
```

## Dépannage minimal

| Symptôme | Cause probable | Correctif |
| --- | --- | --- |
| « Impossible de joindre le backend » | back non lancé, mauvais port, ou `VITE_API_BASE_URL` erronée | lancer le back puis recharger la page ; revérifier `frontend/.env` |
| Code 422 à la soumission | document vide ou hors bornes | remplir le champ ; document ≤ 12 000 caractères |
| Code 500 | clé ou configuration backend absente | vérifier `MINIMAX_API_KEY` dans `.env` racine |
| Code 502 | fournisseur MiniMax indisponible | attendre puis réessayer |
| « Analyse introuvable (404) » | analyse supprimée ou base remise à zéro | la référence locale est nettoyée ; relancer une analyse |
| Outil inactif dans le panneau | outil désactivé via `/tools` ou `DISABLED_TOOLS` | réactiver via le bouton ; l'état SQLite fait foi |