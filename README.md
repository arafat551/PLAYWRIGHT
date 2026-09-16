# SDET App V2

Refonte modulaire d'une plateforme de test QA (SDET) en **Python / Flask / Playwright**.
Le moteur découvre les pages d'une application web, planifie des scénarios de type
travail de production, les exécute avec **Playwright** (source de vérité) et produit
des rapports de tests. L'API **OpenAI** n'est qu'une assistance optionnelle : le
moteur fonctionne parfaitement sans elle (dégradation gracieuse).

## Pipeline (sections 19-28)

```
EXPLORER → COMPRENDRE → PLANIFIER → EXECUTER → VERIFIER → RAPPORTER
```

- **EXPLORER** (`sdet/explorer.py`) : crawl top-down du site, inventaire des pages,
  boutons, champs de recherche, formulaires et tableaux (colonne Actions), avec noms
  réels déduits du texte/aria/title/value/icônes.
- **COMPRENDRE / CLASSIFIER** (`sdet/classification.py`) : chaque action est classée
  CREATE / READ / UPDATE / DELETE / SEARCH / STATUS / NAVIGATION / FORM / GENERIC.
  Les actions sensibles (paiement, SMS/email réel, …) sont détectées.
- **PLANIFIER** (`sdet/planner.py`) : un module CRUD est testé comme un **workflow
  complet** : accès → création → vérification → recherche → lecture → modification →
  autres actions → **suppression en dernier** → vérification de la disparition.
  Restrictions PRODUCTION (DELETE désactivé, CREATE sensible).
- **EXÉCUTER** (`sdet/runner.py`) : exécution Playwright. L'action cible la **ligne
  exacte** contenant le marqueur `SDET_` créé par le run, jamais la première ligne.
  Seules les données créées par le run sont supprimées (les données pré-existantes
  sont intouchées). Captures nommées `PROJET_module_action_STATUS_001.png`.
- **VÉRIFIER** (`sdet/verifier.py`) : un clic réussi ≠ PASS. Le verdict repose sur une
  **preuve observable** (message de succès, URL, donnée créée apparue / disparue,
  changement de contenu). FAIL / PASS / WARNING / SKIPPED sont décidés réellement.
- **RAPPORTER** (`sdet/reporter.py`) : rapport HTML par modules, anomalies, sévérités,
  taux de succès.

## Architecture

```
sdet_app/
├─ app.py            # routes Flask + orchestration en threads (explore/test)
├─ config.py         # configuration via .env
├─ database.py       # schéma SQLite (6 tables) + accès CRUD
├─ models.py         # User, Project, ProjectPage, PageAction, TestRun, TestResult
├─ security.py       # chiffrement XOR+base64 des mots de passe
├─ run.py            # point d'entrée : init_db + app.run (port 5000)
├─ sdet/
│  ├─ browser.py     # cycle de vie Playwright, login, monitors HTTP/JS
│  ├─ explorer.py    # cartographie
│  ├─ classification.py
│  ├─ planner.py
│  ├─ forms.py       # analyse / remplissage / soumission des formulaires
│  ├─ runner.py
│  ├─ verifier.py
│  ├─ reporter.py
│  ├─ ai.py          # assistance OpenAI optionnelle
│  └─ engine.py      # orchestration des pipelines
├─ templates/  static/css  static/js
├─ reports/    # rapports HTML générés
├─ screenshots/
└─ tests/      # pytest (conftest fixe une base de test isolée)
```

## Configuration (.env)

```
OPENAI_API_KEY=...
APP_SECRET_KEY=...
DATABASE_PATH=qa.db
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=changeme
MAX_PAGES=50
PAGE_TIMEOUT=30000
HEADLESS=true
```

## Démarrage

```bash
pip install -r requirements.txt
python -m playwright install chromium
python sdet_app/run.py          # http://localhost:5000
```

## Tests

```bash
python -m pytest -q              # 23 tests (auth, classification, planner, report)
```

Les tests ne touchent pas la base réelle : `conftest.py` redirige
`config.env.DATABASE_PATH` vers une base de test isolée.

## Sécurité

- Jamais de suppression de données pré-existantes : le moteur ne supprime que les
  lignes portant son marqueur `SDET_`, et la suppression est toujours **en dernier**.
- En PRODUCTION : suppression désactivée, création marquée sensible (désactivée par
  défaut), tests "smoke" uniquement.
