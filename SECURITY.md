# Sécurité — CONCLAVE

## 1. Aucune clé côté front

La clé MiniMax vit dans `.env` à la racine (ignoré par git) ou dans les
variables d'environnement du serveur. Elle est lue par `backend/app/config.py`
et n'existe que dans le processus backend.

Le frontend ne connaît **qu'une** variable, `VITE_API_BASE_URL`, qui n'est pas
un secret : c'est l'URL du backend. Tout ce que Vite injecte dans le bundle est
public par construction — donc rien de sensible n'y est mis.

Vérifiable :

```bash
cd frontend && npm run build && cd ..
./scripts/check-no-secrets.sh          # sortie 0 = propre, 1 = secret détecté
```

Le script cherche des **valeurs** de secret (motif `sk-…`, valeur de
`$MINIMAX_API_KEY`, clé du `.env` local), pas des noms de variable : le nom
`MINIMAX_API_KEY` apparaît légitimement dans un message d'aide affiché à
l'utilisateur (« vérifiez MINIMAX_API_KEY côté serveur »). Un `grep` naïf sur
ce nom produit un faux positif — c'est d'ailleurs ce qui s'est produit lors de
la mise au point de ce contrôle, et pourquoi il est scripté plutôt que laissé à
une commande improvisée.

Le harnais d'évaluation vérifie aussi qu'aucune clé n'apparaît dans les
événements SSE persistés (cas 4).

## 2. « Que se passe-t-il si l'utilisateur écrit *ignore tes instructions précédentes* ? »

C'est la question du checkpoint. Réponse en trois temps.

### Ce qui se passe concrètement

1. La soumission est **acceptée** — refuser le document serait à la fois
   contournable et inutile : un document légitime peut parler d'injection de
   prompt, et un attaquant peut reformuler.
2. Le serveur repère les tournures d'instruction et remonte
   `security.prompt_injection_suspected` avec les motifs identifiés. C'est
   affiché dans le panneau **« Pourquoi ce résultat ? »**.
3. Le document est transmis au modèle **encadré comme donnée** :

   ```
   === DOCUMENT_UTILISATEUR_DEBUT_<nonce> ===
   ...contenu de l'utilisateur...
   === DOCUMENT_UTILISATEUR_FIN_<nonce> ===
   ```

   Le prompt système déclare que tout ce qui est entre ces bornes est une
   donnée fournie par un tiers non fiable, jamais une consigne. Le `nonce` est
   régénéré à chaque analyse : le document ne peut pas deviner sa propre borne
   fermante pour « sortir » de la zone de données, ce qu'un délimiteur fixe et
   connu (```` ``` ````, `---`, `<document>`) permettrait.
4. L'analyse se déroule normalement et le verdict est rendu.

### Pourquoi la détection n'est PAS la défense

Le détecteur (`backend/app/security.py`) est **heuristique et contournable** :
translittération, autre langue, encodage détourné, formulation indirecte. Il
sert à *informer* l'utilisateur, pas à autoriser ou refuser. Le présenter comme
une barrière serait un mensonge — exactement ce que le checkpoint sanctionne.

### La vraie défense est structurelle

Elle ne dépend pas du texte reçu, donc ne se contourne pas par reformulation :

| Barrière | Effet |
|---|---|
| Outils figés côté serveur à la création de l'analyse (`analysis_tool_states`) | Un document ne peut **pas** activer un outil désactivé : son schéma n'est même pas envoyé au modèle, et `execute_tool` revérifie la liste figée avant exécution. |
| Les trois outils ne prennent **aucun argument** | Le document ne peut atteindre aucun paramètre d'outil. |
| Sortie validée par schéma Pydantic strict | Un modèle qui obéirait à l'injection et répondrait en texte libre voit sa sortie **rejetée**, pas affichée. Le verdict ne peut pas être « imposé » par le document. |
| Requêtes SQLite toutes paramétrées | `DROP TABLE analyses; --` est stocké comme du texte, jamais interprété (cas 3 de l'éval). |
| Clé jamais placée dans un prompt | Aucune injection ne peut exfiltrer un secret d'un contexte où il n'est pas. |

**Conclusion honnête** : une injection réussie peut au pire dégrader la
*qualité* d'une analyse — un expert qui écrit n'importe quoi, ce que la
validation de schéma rattrape généralement. Elle ne peut ni étendre les
capacités du système, ni exfiltrer un secret, ni modifier des données.

## 3. Résistance aux abus

| Entrée | Réponse |
|---|---|
| Corps de requête > 1 Mo | HTTP **413** décidé sur `Content-Length`, **avant** lecture du corps (un envoi de 40 Mo n'est jamais chargé en mémoire). |
| Document vide ou > 12 000 caractères | HTTP **422**, aucune analyse créée. |
| Dix clics sur « Convoquer » | Bouton verrouillé pendant la soumission côté front ; côté serveur, au-delà de `MAX_CONCURRENT_ANALYSES` (3 par défaut) analyses actives, HTTP **429** avec la raison et la marche à suivre. |
| `/start` appelé plusieurs fois | Compare-and-set SQL atomique `queued → running` : une seule tâche démarre, les autres reçoivent `already_started`. |
| Émojis, cyrillique, RTL | Stockés et rendus à l'identique (UTF-8 de bout en bout). |

## 4. Échouer bruyamment, jamais mentir

Le piège explicite du palier est le `try/except` qui avale tout pour que « ça ne
plante plus ». Deux garde-fous :

- **Aucune exception n'est avalée en silence.** `run_expert` et `run_arbiter`
  capturent `ProviderError` et toute exception inattendue, les **journalisent
  avec leur trace**, sortent le run de l'état `running` et émettent
  `expert.failed` / `arbiter.failed` avec le vrai code.
- **La cause dominante prime sur la conséquence.** Une coupure réseau est
  remontée comme `provider_unavailable`, jamais requalifiée en
  `insufficient_expertise`. C'est le cas 5 de l'évaluation, et c'était le
  défaut le plus grave de la version précédente : l'application annonçait
  « pas assez d'experts » sur une panne d'infrastructure, et laissait les trois
  experts en `running` indéfiniment (spinner infini).

Chaque code d'erreur est traduit en français dans le panneau « Pourquoi ce
résultat ? », avec l'action corrective correspondante.

## 5. Reproduire les contrôles

```bash
make eval        # 5 cas hostiles, score chiffré, sans clé MiniMax
make test        # suites backend + frontend
```
