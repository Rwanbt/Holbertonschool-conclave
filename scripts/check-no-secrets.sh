#!/usr/bin/env bash
# Vérifie qu'aucun SECRET ne se retrouve dans les artefacts publiés :
# bundle frontend (frontend/dist), base SQLite (data/*.db), et fichiers de
# travail courants. Multiplateforme (bash pur, pas d'outil externe).
#
# Nuance importante : on cherche des VALEURS de secret, pas des noms de
# variable. Les NOMS « MINIMAX_API_KEY », « api_key », « Authorization »…
# apparaissent légitimement dans les textes d'aide : les signaler serait un
# faux positif qui décrédibiliserait le contrôle.
#
# Sortie 0 = propre, 1 = secret détecté, 2 = artefact absent.
set -uo pipefail

DIST="${1:-frontend/dist}"
DB="${2:-data/conclave.db}"

status=0

report() {
  echo "SECRET DÉTECTÉ — $1" >&2
  status=1
}

# ---------------------------------------------------------------------------
# 1. Bundle frontend
# ---------------------------------------------------------------------------
scan_dir() {
  local dir="$1"
  [ -d "$dir" ] || { echo "erreur : $dir introuvable — lancez d'abord le build." >&2; return 2; }

  # Motifs de clés réalistes (OpenAI/MiniMax/Anthropic/Gemini).
  if matches=$(grep -rhoE 'sk-[A-Za-z0-9_-]{16,}' "$dir" 2>/dev/null) && [ -n "$matches" ]; then
    report "motif de clé API dans $dir :"
    printf '%s\n' "$matches" | sort -u | sed 's/^/    /' >&2
  fi
  if matches=$(grep -rhoE 'sk-ant-[A-Za-z0-9_-]{20,}' "$dir" 2>/dev/null) && [ -n "$matches" ]; then
    report "motif de clé Anthropic dans $dir :"
    printf '%s\n' "$matches" | sort -u | sed 's/^/    /' >&2
  fi
  if matches=$(grep -rhoE 'AIza[0-9A-Za-z_-]{20,}' "$dir" 2>/dev/null) && [ -n "$matches" ]; then
    report "motif de clé Google Gemini dans $dir :"
    printf '%s\n' "$matches" | sort -u | sed 's/^/    /' >&2
  fi
}

scan_dir "$DIST"

# ---------------------------------------------------------------------------
# 2. Valeurs des clés définies dans .env local (jamais commité, mais on
#    vérifie qu'elles ne fuient pas dans les artefacts).
# ---------------------------------------------------------------------------
env_key() {
  local var="$1"
  [ -f .env ] && grep -E "^${var}=" .env | head -1 | cut -d= -f2- | tr -d '"'"'"' \r'
}

for var in MINIMAX_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY; do
  key=$(env_key "$var")
  [ -n "$key" ] || continue
  [ "$key" = "replace_with_your_minimax_api_key" ] && continue
  if grep -rqF -- "$key" "$DIST" 2>/dev/null; then
    report "la valeur de \$$var est présente dans le bundle"
  fi
  if [ -f "$DB" ] && grep -aqF -- "$key" "$DB" 2>/dev/null; then
    report "la valeur de \$$var est présente dans la base SQLite"
  fi
done

# ---------------------------------------------------------------------------
# 3. Base SQLite : aucune clé réaliste ne doit y être persistée.
# ---------------------------------------------------------------------------
if [ -f "$DB" ]; then
  if grep -aqE 'sk-[A-Za-z0-9_-]{16,}' "$DB" 2>/dev/null; then
    report "motif de clé API dans la base SQLite"
  fi
  if grep -aqE 'sk-ant-[A-Za-z0-9_-]{20,}' "$DB" 2>/dev/null; then
    report "motif de clé Anthropic dans la base SQLite"
  fi
  if grep -aqE 'AIza[0-9A-Za-z_-]{20,}' "$DB" 2>/dev/null; then
    report "motif de clé Google Gemini dans la base SQLite"
  fi
fi

if [ "$status" -eq 0 ]; then
  echo "OK — aucun secret détecté dans $DIST"
  [ -f "$DB" ] && echo "     ni dans $DB"
  echo "     (les NOMS 'API_KEY' etc. peuvent y figurer : ce sont des textes d'aide, pas des valeurs)"
fi

exit "$status"