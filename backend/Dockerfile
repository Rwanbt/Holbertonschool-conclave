# Backend CONCLAVE — image de production (ASGI persistant).
#
# Contexte de build : le dossier `backend/` (docker build -f backend/Dockerfile backend).
# Déployer sur tout fournisseur compatible Docker/ASGI : le code ne dépend
# d'aucun fournisseur d'hébergement précis.
#
# Variables d'environnement documentées (voir backend/README ou .env.example) :
#   DATABASE_PATH       (défaut ./data/conclave.db) — MONTER UN VOLUME DURABLE
#                       sur /app/data. SQLite est MONO-INSTANCE : pas de
#                       filesystem éphémère ni de scale-out.
#   FRONTEND_ORIGINS    origines autorisées (CORS), séparées par des virgules.
#   ALLOW_SERVER_PROVIDER_CREDENTIALS  false en production (BYOK uniquement).
#   SESSION_COOKIE_SECRET  secret stable pour signer les sessions.
#   Les clés LLM ne sont PAS nécessaires au démarrage.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DATABASE_PATH=/app/data/conclave.db

WORKDIR /app

COPY requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY . /app/backend

# Volume persistant SQLite (obligatoire : jamais un filesystem éphémère).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]