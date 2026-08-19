# CONCLAVE — Palier 3 « Le premier outil »

Le front envoie un **document** et une **instruction naturelle** au backend
(`POST /api/p3/agent`) ; le modèle MiniMax-M3 choisit lui-même l'outil à
appeler puis répond. Le front affiche la réponse, la **trace des outils** et
les **métriques d'exécution**.

- **Front** : React + TypeScript + Vite dans `frontend/` (branche `erwan`).
- **Back** : FastAPI + Python dans `backend/` (branche `yo`, travail de Yohan).

## Prérequis

- Node.js ≥ 18 et npm.
- Python ≥ 3.10.
- Deux terminaux : un pour le backend, un pour le frontend.

## Démarrage en moins de cinq minutes

La suite part de la branche `dev`, qui contient déjà front et backend.

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

## Tester l'agent

Deux champs s'affichent :

- **Document** : le texte à analyser, limité à 12 000 caractères (compteur
  affiché) ;
- **Instruction** : votre demande en langage naturel, sans choisir d'outil.

Puis cliquez sur « Tester l'agent ». Le backend valide l'entrée, choisit et
exécute l'outil (ou signale son échec dans la trace), interroge MiniMax-M3 et
renvoie la réponse, la trace et les usages. Une instruction naturelle suffit :
le front ne sélectionne jamais l'outil à la place du modèle.

Trois exemples sont proposés dans l'interface (métriques, indices de sécurité,
estimation de coût) et se remplissent d'un clic.

Test direct de la route de ce palier :

```bash
curl -s -X POST http://localhost:8000/api/p3/agent \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"Calcule les métriques du document","document":"Bonjour Conclave"}'
```

Réponse attendue : `{"answer": "...", "model": "MiniMax-M3", "trace": [...], "usage": {...}}`.

Codes HTTP du contrat : `422` entrée invalide · `500` configuration serveur
absente · `502` fournisseur MiniMax indisponible. Une erreur d'outil arrive
normalement dans une réponse `200` avec une entrée de trace `status: "error"`
et une réponse finale honnête.

## Panne contrôlée — `DISABLED_TOOLS`

Pour tester sans clé ni tarif réel le rendu d'une trace d'échec, le backend
peut accepter une variable `DISABLED_TOOLS` (vue de Yohan) qui liste les outils
simulés en erreur, séparés par des virgules. Exemple dans `.env` :

```text
DISABLED_TOOLS=measure_document
```

Aucun secret dans le front : cette variable n'est lue que par le backend, et
rien n'est affiché du document intégral dans l'interface de debug.

## Commandes de vérification

```bash
cd frontend
npm run lint
npm run build
```

Depuis la racine, vérifier que rien d'étranger n'est indexé :

```bash
git status --short
git diff --check
```

## Dépannage minimal

| Symptôme | Cause probable | Correctif |
| --- | --- | --- |
| « Impossible de joindre le backend » | back non lancé, mauvais port, ou `VITE_API_BASE_URL` erronée | lancer le back puis recharger la page ; revérifier `frontend/.env` |
| Code 422 | instruction vide ou document hors bornes | remplir les deux champs ; document ≤ 12 000 caractères |
| Code 500 | clé ou configuration backend absente | vérifier `MINIMAX_API_KEY` dans `.env` racine |
| Code 502 | fournisseur MiniMax indisponible | attendre puis réessayer |
| Trace d'outil en échec | outil réellement en échec, ou `DISABLED_TOOLS` actif | relire `error_code` de l'entrée de trace |