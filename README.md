# CONCLAVE — Palier 2 « Socle »

Front minimal : on envoie un message, on reçoit la réponse réelle du modèle
MiniMax-M3 via le backend FastAPI.

- **Front** : React + TypeScript + Vite dans `frontend/` (branche `erwan`).
- **Back** : FastAPI + Python dans `backend/` (branche `yo`, travail de Yohan).
  Endpoint consommé : `POST /api/p2/llm`.

## Prérequis

- Node.js ≥ 18 et npm.
- Python ≥ 3.10.
- Deux terminaux : un pour le backend, un pour le frontend.

## Clone et branches

```bash
git clone https://github.com/Rwanbt/Holbertonschool-conclave.git
cd Holbertonschool-conclave
git switch erwan
```

Le backend vit sur la branche `yo` (fournie par Yohan) :

```bash
git fetch origin
git switch yo
```

La suite suppose que le dépôt contient `frontend/` et `backend/`.

## Copie des fichiers d'environnement

Deux fichiers `.env.example` sont fournis (un par application). Aucun n'est secret.

```bash
cp frontend/.env.example frontend/.env
cp backend/.env.example backend/.env    # valeurs attendues : voir backend/README.md de Yohan
```

Le front n'attend qu'une variable : `VITE_API_BASE_URL` (défaut
`http://localhost:8000`). Aucune clé MiniMax ne doit figurer dans le front ;
la clé vit uniquement côté backend.

## Terminal 1 — Backend (branche `yo`)

```bash
git switch yo
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r backend/requirements.txt   # commande exacte : voir backend/README.md
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Le backend doit écouter sur `http://localhost:8000`.

## Terminal 2 — Frontend (branche `erwan`)

```bash
cd frontend
npm install
npm run dev
```

## URL à ouvrir

- Application : http://localhost:5173
- Documentation du backend (si fournie) : http://localhost:8000/docs

## Test de santé

Dans l'application : saisir un message non vide puis cliquer sur
« Tester le Conclave ». La réponse et le nom du modèle (`MiniMax-M3`)
doivent s'afficher.

En ligne de commande :

```bash
curl -s -X POST http://localhost:8000/api/p2/llm \
  -H 'Content-Type: application/json' \
  -d '{"message":"Bonjour Conclave"}'
```

Réponse attendue : `{"answer": "...", "model": "MiniMax-M3"}`.

Codes HTTP du contrat : `422` saisie invalide · `500` configuration serveur
absente · `502` fournisseur MiniMax indisponible.

## Commandes de vérification

```bash
cd frontend
npm run lint
npm run build
```

## Dépannage minimal

| Symptôme | Cause probable | Correctif |
| --- | --- | --- |
| « Impossible de joindre le backend » | back non lancé, mauvais port, ou `VITE_API_BASE_URL` erronée | lancer le back puis recharger la page ; revérifier `frontend/.env` |
| Réponse code 502 | fournisseur MiniMax indisponible | réessayer plus tard |
| Réponse code 500 | clé ou configuration backend absente | vérifier `backend/.env` |
| Bouton grisé en permanence | message vide ou appel en cours | saisir du texte puis réessayer |
