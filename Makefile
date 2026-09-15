.PHONY: eval test lint build secrets all

## Rejoue le jeu d'évaluation (eval/cases.md) et sort un score chiffré.
## Aucune clé fournisseur nécessaire : le fournisseur est simulé.
eval:
	python3 eval/run_eval.py

## Suites de tests backend + frontend.
test:
	python3 -m pytest backend/tests -q
	cd frontend && npm test -- --run

lint:
	cd frontend && npm run lint

build:
	cd frontend && npm run build

## Contrôle anti-fuite de secrets (bundle + base SQLite).
secrets:
	./scripts/check-no-secrets.sh

all: test lint build eval secrets
