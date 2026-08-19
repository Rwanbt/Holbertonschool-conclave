# CONCLAVE — Frontend (Palier 3 « Le premier outil »)

Application React + TypeScript + Vite qui envoie un document et une instruction
naturelle au backend `POST /api/p3/agent` puis affiche la réponse de
MiniMax-M3, la trace des outils appelés et les métriques d'exécution.

## Démarrage rapide

Copiez le fichier d'exemple d'environnement (variable non secrète) :

```bash
cp frontend/.env.example frontend/.env
```

`VITE_API_BASE_URL` pointe par défaut sur `http://localhost:8000`.
Aucune clé MiniMax ne doit être placée dans le front : elle vit côté backend.

Installez puis lancez le serveur de développement :

```bash
cd frontend
npm install
npm run dev
```

## Commandes utiles

```bash
npm run lint    # ESLint
npm run build   # tsc -b + vite build (vérification de types incluse)
```

Le parcours complet (clone → backend → frontend → test) est décrit dans le
[README.md](../README.md) à la racine du dépôt.