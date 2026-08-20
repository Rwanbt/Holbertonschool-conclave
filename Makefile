.PHONY: eval test lint build all

## Rejoue le jeu d'évaluation (eval/cases.md) et sort un score chiffré.
## Aucune clé MiniMax nécessaire : le fournisseur est simulé.
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

all: test lint build eval
