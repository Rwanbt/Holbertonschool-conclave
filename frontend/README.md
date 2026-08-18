# CONCLAVE — Frontend (Palier 2)

Application React + TypeScript + Vite qui envoie un message au backend
`POST /api/p2/llm` et affiche la réponse réelle du modèle MiniMax-M3.

## Démarrage rapide

Copiez d'abord le fichier d'exemple d'environnement :

```bash
cp frontend/.env.example frontend/.env
```

`VITE_API_BASE_URL` pointe par défaut sur `http://localhost:8000`.
Cette variable n'est pas secrète : aucune clé API ne doit être placée dans le front.

Installez puis lancez le serveur de développement :

```bash
cd frontend
npm install
npm run dev
```

## Commandes utiles

```bash
npm run lint    # ESLint
npm run build   # tsc -b + vite build
```

Le parcours complet (clone → backend → frontend → test) est décrit dans le
[README.md](../README.md) à la racine du dépôt.