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
│  ├─ mailbox.py     # lecture IMAP + extraction du code OTP (2FA)
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

## Authentification 2FA / OTP

Pour un projet dont le type d'authentification est **2FA / OTP**, le moteur
détecte le champ de code après le login :

1. **Récupération automatique** : le code de vérification à 6 chiffres est lu
   dans la boîte mail (IMAP Gmail, `imap.gmail.com:993`) et saisi
   automatiquement. Les identifiants sont réglés dans *Paramètres*
   (`imap_user` / `imap_password`) ; s'ils sont vides, l'utilisateur et le mot
   de passe **SMTP** sont réutilisés (même compte Gmail).
2. **Saisie manuelle de secours** : si l'e-mail est introuvable ou l'IMAP non
   configuré, le run passe en `waiting_otp` et l'opérateur saisit le code dans
   l'interface (le navigateur reste ouvert).

Activez / désactivez la récupération automatique dans *Paramètres* via le
curseur `otp_auto_fetch`.

## Démarrage

```bash
pip install -r requirements.txt
python -m playwright install chromium
python sdet_app/run.py          # http://localhost:5000
```

## Version Python requise

**Minimum : Python 3.9. Ne pas utiliser une syntaxe plus récente que Python 3.11**
sur le code applicatif — le serveur de production tourne sur une version antérieure
à 3.12, alors que le poste de développement peut être plus récent.

Le piège principal : un f-string non triple-quoté qui s'étale sur plusieurs lignes.
Avant Python 3.12 (PEP 701) le tokenizer lit le f-string entier comme un seul jeton,
donc **un saut de ligne à l'intérieur même d'une expression `{...}` provoque un
`SyntaxError`**. Ce code est valide sur le poste de dev, les tests passent, et le
serveur plante au démarrage.

```python
# INVALIDE avant Python 3.12
f'{mailer.info_note("Changez ce mot de passe "
                   f"des votre premiere connexion.", "warning")}'

# VALIDE partout
note = mailer.info_note(
    "Changez ce mot de passe "
    "des votre premiere connexion.", "warning")
contenu = f'<p>Bonjour</p>{note}'
```

`sdet_app/tests/test_python_compat.py` vérifie cela automatiquement (ainsi que la
syntaxe PEP 695). Il fait partie de la suite standard : **un push qui l'ignore ne
doit pas être déployé.**

## Tests

```bash
python -m pytest -q              # 220 tests (auth, 2FA/OTP, classification, planner, report, compat Python)
```

Les tests ne touchent pas la base réelle : `conftest.py` redirige
`config.env.DATABASE_PATH` vers une base de test isolée.

## Déploiement

Le serveur de production et le poste de dev doivent rester sur le même commit.

```bash
# poste de dev
git add -A
git commit -m "..."
git push origin staging

# serveur
cd ~/PLAYWRIGHT
git fetch origin
git reset --hard origin/main
pkill -f gunicorn
nohup python3 -m gunicorn -w 1 -b 127.0.0.1:5000 sdet_app.app:app > gunicorn.log 2>&1 &
```

Ne jamais utiliser `git push --force` : cela rend le dépôt local du serveur
divergent et oblige à un `reset` manuel. Les fichiers SQLite (`*.db`, `*.db-wal`,
`*.db-shm`) sont ignorés par Git et ne doivent jamais être commités — ils changent à
chaque exécution et provoquent des conflits impossibles à fusionner.

## Sécurité

- Jamais de suppression de données pré-existantes : le moteur ne supprime que les
  lignes portant son marqueur `SDET_`, et la suppression est toujours **en dernier**.
- En PRODUCTION : suppression désactivée, création marquée sensible (désactivée par
  défaut), tests "smoke" uniquement.
