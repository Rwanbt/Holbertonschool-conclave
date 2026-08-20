#!/usr/bin/env bash
# Vérifie qu'aucun SECRET ne se retrouve dans le bundle frontend publié.
#
# Nuance importante : on cherche des VALEURS de secret, pas des noms de
# variable. Le nom « MINIMAX_API_KEY » apparaît légitimement dans les messages
# d'aide affichés à l'utilisateur (« vérifiez MINIMAX_API_KEY côté serveur ») ;
# le signaler serait un faux positif qui décrédibiliserait le contrôle.
#
# Sortie 0 = propre, 1 = secret détecté.
set -uo pipefail

DIST="${1:-frontend/dist}"

if [ ! -d "$DIST" ]; then
  echo "erreur : $DIST introuvable — lancez d'abord 'cd frontend && npm run build'" >&2
  exit 2
fi

status=0

report() {
  echo "SECRET DÉTECTÉ — $1" >&2
  status=1
}

# 1. Motif de clé de type OpenAI/MiniMax : sk- suivi d'au moins 16 caractères.
#    (Le seuil évite de confondre avec un mot anglais contenant « sk- ».)
if matches=$(grep -rhoE 'sk-[A-Za-z0-9_-]{16,}' "$DIST" 2>/dev/null) && [ -n "$matches" ]; then
  report "motif de clé API dans le bundle :"
  printf '%s\n' "$matches" | sort -u | sed 's/^/    /' >&2
fi

# 2. La valeur réelle de la clé, si elle est présente dans l'environnement.
if [ -n "${MINIMAX_API_KEY:-}" ]; then
  if grep -rqF -- "$MINIMAX_API_KEY" "$DIST" 2>/dev/null; then
    report "la valeur de \$MINIMAX_API_KEY est présente dans le bundle"
  fi
fi

# 3. La clé éventuellement définie dans le .env local.
if [ -f .env ]; then
  env_key=$(grep -E '^MINIMAX_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"'"'"' \r')
  if [ -n "$env_key" ] && [ "$env_key" != "replace_with_your_minimax_api_key" ]; then
    if grep -rqF -- "$env_key" "$DIST" 2>/dev/null; then
      report "la clé du fichier .env est présente dans le bundle"
    fi
  fi
fi

if [ "$status" -eq 0 ]; then
  echo "OK — aucun secret détecté dans $DIST"
  echo "     (le NOM 'MINIMAX_API_KEY' peut y figurer : c'est un texte d'aide, pas une valeur)"
fi

exit "$status"
