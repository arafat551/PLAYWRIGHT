# SPÉCIFICATION V2 — PLATEFORME SDET AUTOMATISÉE

## 1. Objectif

Refondre l’application QA actuelle pour construire une plateforme **SDET légère mais professionnelle**.

L’objectif n’est pas de créer un framework gigantesque ni une plateforme extrêmement complexe.

Le système doit simplement respecter la méthode de travail d’un **SDET expérimenté** :

```text
EXPLORER
   ↓
COMPRENDRE
   ↓
PLANIFIER
   ↓
EXÉCUTER
   ↓
VÉRIFIER
   ↓
RAPPORTER
```

Le système ne doit surtout pas fonctionner comme :

```text
Trouver un bouton
↓
Cliquer
↓
PASS
```

Un clic n’est jamais une preuve qu’une fonctionnalité fonctionne.

---

# 2. Stack technique

Conserver une architecture simple :

```text
Backend :
Python + Flask

Automatisation :
Playwright Python

Framework de tests :
pytest

Base :
SQLite

Frontend :
HTML + CSS + JavaScript

IA :
OpenAI API en assistance du moteur SDET

Rapports :
HTML + screenshots
```

## Principe important

**Playwright devient le moteur principal de navigation et d’exécution.**

OpenAI sert principalement à :

* comprendre certains éléments ambigus ;
* classifier les actions ;
* aider à comprendre un formulaire ;
* générer des données pertinentes ;
* aider à évaluer un résultat lorsque les règles classiques ne suffisent pas.

Ne pas utiliser l’IA pour cliquer aléatoirement dans toute l’application.

---

# 3. Architecture Medium

Ne pas tout mettre dans `main.py`.

Mais ne pas créer non plus 50 fichiers.

Utiliser cette structure :

```text
sdet_app/
│
├── app.py
├── config.py
├── database.py
├── models.py
│
├── sdet/
│   ├── browser.py
│   ├── explorer.py
│   ├── planner.py
│   ├── runner.py
│   ├── forms.py
│   ├── verifier.py
│   └── reporter.py
│
├── templates/
│   ├── login.html
│   ├── dashboard.html
│   ├── projects.html
│   ├── project_form.html
│   ├── project_detail.html
│   ├── exploration.html
│   ├── test_run.html
│   └── report.html
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
│
├── reports/
├── screenshots/
├── tests/
│   ├── test_explorer.py
│   ├── test_planner.py
│   └── test_runner.py
│
├── .env
├── requirements.txt
└── README.md
```

Chaque fichier possède une responsabilité simple.

---

# 4. Responsabilités principales

## `app.py`

Uniquement :

* Flask ;
* routes ;
* sessions ;
* affichage des pages ;
* lancement des explorations/tests ;
* communication avec le moteur SDET.

Ne pas mettre toute la logique Playwright ici.

---

## `explorer.py`

Responsable de :

* parcourir l’application ;
* découvrir les pages ;
* récupérer les boutons ;
* récupérer les formulaires ;
* récupérer les champs de recherche ;
* récupérer les tableaux ;
* détecter les colonnes Actions ;
* enregistrer la cartographie.

---

## `planner.py`

Responsable de réfléchir avant d’agir.

Il transforme les éléments découverts en scénarios.

Exemple :

```text
Page : Clients

Bouton :
Ajouter un client

Table :
Liste clients

Actions :
Visualiser
Modifier
Désactiver
Supprimer
```

Le Planner peut produire :

```text
PLAN CLIENT

1. Ouvrir Clients
2. Cliquer Ajouter un client
3. Remplir le formulaire
4. Enregistrer
5. Vérifier la création
6. Rechercher la ligne créée
7. Visualiser
8. Modifier
9. Vérifier la modification
10. Désactiver si disponible
11. Réactiver si disponible
12. Supprimer EN DERNIER
13. Vérifier la suppression
```

---

## `runner.py`

Exécute le plan avec Playwright.

Il ne décide pas lui-même aléatoirement des actions.

---

## `forms.py`

Analyse les formulaires et génère les données QA.

---

## `verifier.py`

Détermine réellement :

```text
PASS
FAIL
WARNING
SKIPPED
```

---

## `reporter.py`

Produit :

* résultats ;
* anomalies ;
* screenshots ;
* pourcentage ;
* rapport final.

---

# 5. Parcours utilisateur

Le parcours général doit rester très simple.

```text
CONNEXION
   ↓
TABLEAU DE BORD
   ↓
PROJETS
   ↓
AJOUTER PROJET
   ↓
VISUALISER PROJET
   ↓
EXPLORER
   ↓
CARTOGRAPHIE
   ↓
CHOISIR CE QUI DOIT ÊTRE TESTÉ
   ↓
LANCER TEST
   ↓
RÉSULTATS
   ↓
RAPPORT
```

La création d’un projet ne lance aucun test automatiquement.

---

# 6. Création d’un projet

Le formulaire doit contenir :

```text
Nom du projet

URL

Email

Mot de passe

Type d'authentification

Commentaires / Instructions
```

Types d’authentification :

```text
Aucune

Simple
```

Ajouter :

```text
Environnement

DEV
TEST
STAGING
PRODUCTION
```

---

# 7. Instructions personnalisées

Conserver la zone :

```text
Commentaires / Instructions QA
```

Exemple :

```text
Tester tous les modules.

Ne pas effectuer de paiement réel.

Ne pas supprimer les utilisateurs existants.

Créer des données QA pour les tests CRUD.

Tester particulièrement la gestion client.
```

Le moteur doit toujours respecter ces instructions.

---

# 8. Page Projets

Conserver une DataTable simple :

| Projet | URL | Auth | Environnement | Dernier test | Réussite | Statut | Actions |
| ------ | --- | ---- | ------------- | ------------ | -------: | ------ | ------- |

Actions :

```text
👁 Visualiser

✏ Modifier

⏸ Désactiver

🗑 Supprimer
```

Conserver également :

```text
+ Ajouter un projet
```

---

# 9. Fiche projet

La visualisation doit présenter :

```text
Nom
URL
Environnement
Authentification
Statut
Créé par
Dernière modification
Dernière exploration
Dernier test
Taux de réussite
Instructions
```

Actions principales :

```text
[ EXPLORER L'APPLICATION ]

[ LANCER UNE RÉGRESSION ]

[ MODIFIER ]
```

Le bouton de régression est disponible uniquement lorsqu’une première exploration existe.

---

# 10. Étape 1 — Exploration

L’exploration doit être réalisée avec **Playwright**.

Elle commence depuis l’URL configurée.

Si authentication :

```text
ouvrir login
↓
email
↓
mot de passe
↓
connexion
```

Puis le moteur commence réellement son exploration.

---

# 11. Exploration comme un QA humain

L’exploration doit suivre une logique **du haut vers le bas**.

Pour chaque page :

```text
1. Attendre le chargement

2. Identifier le titre

3. Lire la navigation principale

4. Observer la partie haute

5. Observer le contenu central

6. Descendre progressivement

7. Observer la partie basse

8. Recenser les actions

9. Passer ensuite à la prochaine page
```

Ne pas faire une exploration chaotique.

---

# 12. Ordre des éléments

Les éléments interactifs doivent être ordonnés selon leur position visuelle :

```text
Y
puis
X
```

Cela permet d’obtenir :

```text
Ajouter un client
Exporter
Rechercher
Visualiser
Modifier
Supprimer
```

dans un ordre proche de celui vu réellement par l’utilisateur.

---

# 13. Éléments à récupérer

Pour chaque page, récupérer principalement :

```text
Titre

URL

HTTP status

Boutons

Liens de navigation

Formulaires

Champs de recherche

Tableaux

Colonne Actions
```

Ne pas chercher à analyser chaque `<div>` de l’application.

---

# 14. Détection des boutons

Scanner :

```text
button

[role="button"]

input[type="submit"]

input[type="button"]

a réellement interactif
```

Pour chaque bouton, récupérer son **vrai nom**.

Ordre :

```text
1. texte visible

2. aria-label

3. title

4. value

5. tooltip

6. icône

7. fallback
```

---

# 15. Interdiction des mauvais noms

Ne plus enregistrer principalement :

```text
button

button

button

Bouton #5
```

Si le bouton possède une signification identifiable, enregistrer :

```text
Ajouter un client

Visualiser

Modifier

Supprimer

Exporter PDF
```

Pour les icônes :

```text
👁 = Visualiser

✏ = Modifier

🗑 = Supprimer

+ = Ajouter
```

Le texte réel ou le `title` reste prioritaire.

---

# 16. Navigation

Le système doit rester :

```text
même domaine
```

et exclure notamment :

```text
logout

déconnexion

mailto:

tel:

fichiers CSS

images

JS
```

Éviter les boucles.

Une URL déjà visitée ne doit pas être parcourue continuellement.

---

# 17. Cartographie

Une fois l’exploration terminée, afficher :

```text
CARTOGRAPHIE

Dashboard
   3 actions

Clients
   7 actions

Factures
   5 actions

Articles
   9 actions

Rapports
   4 actions
```

En ouvrant Clients :

```text
CLIENTS

URL : /clients
HTTP : 200

Boutons :
Ajouter un client
Exporter

Recherche :
Rechercher un client

Table :
Liste clients

Actions ligne :
Visualiser
Modifier
Désactiver
Supprimer
```

---

# 18. Sélection avant test

Après exploration :

```text
CHOISIR LES ÉLÉMENTS À TESTER
```

Exemple :

```text
[x] Clients

    [x] Ajouter un client
    [x] Recherche
    [x] Visualiser
    [x] Modifier
    [x] Désactiver
    [x] Supprimer


[x] Factures

    [x] Ajouter facture
    [x] Visualiser
    [ ] Envoyer
```

Fonctions :

```text
Tout cocher

Tout décocher
```

L’utilisateur garde le contrôle.

---

# 19. Le Planner SDET

Avant l’exécution, le Planner doit analyser les actions sélectionnées.

Il doit comprendre notamment :

```text
CREATE
READ
UPDATE
DELETE
SEARCH
STATUS
NAVIGATION
GENERIC
```

Classification par mots-clés et propriétés DOM.

Exemple :

```text
Ajouter / Nouveau / Créer
→ CREATE

Voir / Visualiser / Détail
→ READ

Modifier / Edit
→ UPDATE

Supprimer / Delete
→ DELETE
```

OpenAI peut aider lorsqu’une action reste ambiguë.

---

# 20. Règle importante pour les CRUD

Une page CRUD ne doit PAS être testée bouton par bouton sans contexte.

Exemple incorrect :

```text
Cliquer Ajouter
PASS

Cliquer Visualiser
PASS

Cliquer Supprimer
PASS
```

Le fonctionnement correct est :

```text
CRÉER
  ↓
VÉRIFIER
  ↓
RETROUVER LA DONNÉE
  ↓
VISUALISER
  ↓
MODIFIER
  ↓
VÉRIFIER MODIFICATION
  ↓
AUTRES ACTIONS
  ↓
SUPPRIMER EN DERNIER
  ↓
VÉRIFIER SUPPRESSION
```

---

# 21. Exemple client

Si la page contient :

```text
Ajouter un client
```

le système doit :

```text
Cliquer Ajouter un client

↓

Analyser formulaire

↓

Créer :

SDET_CLIENT_4821

↓

Enregistrer

↓

Vérifier message

↓

Retour liste

↓

Rechercher SDET_CLIENT_4821

↓

Client trouvé ?

OUI → continuer
NON → FAIL

↓

Visualiser la ligne SDET_CLIENT_4821

↓

Vérifier les informations

↓

Modifier

↓

Changer une information

↓

Enregistrer

↓

Actualiser

↓

Vérifier la modification

↓

Désactiver si disponible

↓

Vérifier statut

↓

Réactiver si disponible

↓

Supprimer EN DERNIER

↓

Confirmer

↓

Rechercher SDET_CLIENT_4821

↓

Absent ?

OUI → PASS
NON → FAIL
```

C’est ce comportement qui doit caractériser le système SDET.

---

# 22. Colonne Actions

Lorsqu’une table possède :

```text
Actions
Action
Options
Opérations
```

le système doit comprendre que les boutons appartiennent à chaque ligne.

Exemple :

```text
SDET_CLIENT_4821

👁
✏
⏸
🗑
```

Playwright doit agir **sur cette ligne précise**.

Il ne faut jamais utiliser simplement :

```text
premier bouton Modifier de la page
```

Il faut rechercher :

```text
ligne contenant SDET_CLIENT_4821

↓

bouton Modifier de cette ligne
```

---

# 23. Formulaires

Pour chaque formulaire, identifier :

```text
label

type

required

placeholder

select

radio

checkbox

textarea
```

Puis générer des données adaptées.

Exemple :

```text
Nom
→ SDET_Kossi_4821

Email
→ sdet4821@example.test

Téléphone
→ 90000001

Date
→ date valide

Description
→ Donnée automatique QA SDET
```

Utiliser un identifiant unique :

```text
SDET_
```

sur toutes les données créées.

---

# 24. Tests de validation

Le système doit effectuer quelques tests négatifs simples.

Pas besoin de tester 50 variantes par champ.

Tester principalement :

```text
champ obligatoire vide

email invalide

nombre incorrect

formulaire vide
```

Si une validation apparaît correctement :

```text
PASS
```

Si le formulaire accepte une donnée manifestement invalide :

```text
FAIL ou WARNING
```

selon l’importance.

---

# 25. Vérification obligatoire

`verifier.py` doit décider du résultat en observant notamment :

```text
message de succès

message d'erreur

changement URL

apparition de la donnée

disparition de la donnée

modification visible

statut HTTP

contenu de la page
```

---

# 26. Principe Action ≠ PASS

Règle impérative pour OpenCode :

```text
CLICK SUCCESS
```

ne signifie jamais :

```text
TEST PASS
```

Exemple :

```text
clic Enregistrer réussi
```

mais :

```text
aucun client créé
```

doit donner :

```text
FAIL
```

---

# 28. Actions sensibles

Ne pas exécuter automatiquement :

```text
Paiement réel

Virement

Envoi SMS réel

Envoi WhatsApp réel

Envoi email réel

Déploiement

Suppression massive

Réinitialisation
```

Ces actions sont marquées :

```text
SENSIBLE
```

et décochées par défaut.

---

# 29. Suppression

Le système peut tester la suppression d’une donnée qu’il vient lui-même de créer.

Exemple :

```text
SDET_CLIENT_4821
```

Mais il ne doit jamais sélectionner une donnée existante au hasard pour tester Supprimer.

Règle :

```text
Créé par le run SDET
→ suppression autorisée

Donnée préexistante
→ suppression interdite
```

---

# 30. Environnement Production

Si :

```text
PRODUCTION
```

alors automatiquement :

```text
DELETE désactivé

CREATE sensible désactivé

paiements désactivés

notifications réelles désactivées
```

Le mode Production doit principalement effectuer :

```text
navigation

affichage

recherche non destructive

smoke testing
```

---

# 31. Exécution

Pendant le test, afficher :

```text
TEST EN COURS

Projet : COBRA

Module actuel :
Clients

Scénario :
Création client

Progression :
18 / 42

PASS : 14
FAIL : 2
WARNING : 2

[ ARRÊTER LE TEST ]
```

---

# 32. Arrêt du test

Le bouton :

```text
ARRÊTER LE TEST
```

doit :

```text
arrêter l'exécution

fermer Playwright

conserver les résultats obtenus

marquer :

CANCELLED
```

---

# 33. Screenshots

Ne pas prendre 500 captures inutilement.

Captures obligatoires :

```text
à chaque FAIL

à chaque anomalie importante

après création importante

avant/après modification si nécessaire
```

Nom :

```text
COBRA_clients_edit_FAIL_001.png
```

---

# 34. Surveillance HTTP

Pendant navigation et actions, récupérer les erreurs importantes :

```text
400
401
403
404
422
500
502
503
```

Le rapport doit indiquer :

```text
Page

Action

URL

Code HTTP
```

---

# 35. Erreurs JavaScript

Écouter au minimum :

```text
pageerror

console.error
```

Une erreur JS importante liée à l’action testée doit apparaître dans le rapport.

---

# 36. Rapport

Le rapport doit rester professionnel mais simple.

En-tête :

```text
RAPPORT SDET

Projet : COBRA
Environnement : STAGING
Date :
Lancé par :
Durée :

Tests : 72

PASS : 61
FAIL : 7
WARNING : 4

Réussite : 84,7 %
```

---

# 37. Rapport par module

Exemple :

```text
GESTION CLIENTS

✅ Accès page Clients
✅ Ajouter un client
✅ Client visible dans la liste
✅ Visualiser client
❌ Modifier client
✅ Désactiver client
✅ Réactiver client
✅ Supprimer client
```

---

# 38. Anomalies

Pour chaque FAIL :

```text
Module :
Clients

Fonction :
Modifier client

Action :
Modifier

Donnée :
SDET_CLIENT_4821

Attendu :
Le téléphone modifié doit être conservé.

Obtenu :
Après actualisation, l'ancien téléphone apparaît.

HTTP :
200

Sévérité :
MAJOR

Screenshot :
clients_edit_FAIL.png
```

---

# 39. Statuts

Utiliser :

```text
PASS

FAIL

WARNING

SKIPPED

CANCELLED
```

Pas besoin de multiplier les statuts.

---

# 40. Sévérité

Utiliser seulement :

```text
CRITICAL

MAJOR

MINOR
```

Exemples :

```text
Impossible de se connecter
→ CRITICAL

Création client impossible
→ MAJOR

Tooltip absent
→ MINOR
```

---

# 41. Historique

Conserver :

| Date | Type | Tests | PASS | FAIL | Warning | Réussite | Lancé par | Rapport |
| ---- | ---- | ----: | ---: | ---: | ------: | -------: | --------- | ------- |

Types :

```text
Exploration

Test

Régression
```

---

# 42. Régression

Après un premier test réussi, conserver le plan.

Exemple :

```text
Clients

Create
View
Edit
Disable
Enable
Delete
```

Lors d’une nouvelle régression :

```text
Réexplorer légèrement
↓
retrouver les éléments
↓
rejouer le scénario
↓
comparer les résultats
```

Ne pas reconstruire entièrement toute l’intelligence à chaque régression.

---

# 43. OpenAI

OpenAI reste disponible mais n’est plus le conducteur aveugle du navigateur.

Architecture :

```text
PLAYWRIGHT
    ↓
Observation DOM
    ↓
Règles SDET
    ↓
PLANNER
    ↓
Ambiguïté ?
 ┌──────┴──────┐
 NON           OUI
 │              │
continuer     OpenAI
                ↓
         recommandation
                ↓
           Playwright
```

OpenAI peut répondre par exemple :

```json
{
  "action_type": "READ",
  "label": "Visualiser",
  "confidence": 0.93
}
```

Mais Playwright reste responsable du clic réel.

---

# 44. Sélecteurs Playwright

Privilégier :

```text
get_by_role

get_by_label

get_by_text

get_by_placeholder

locator CSS stable
```

Éviter les sélecteurs fragiles du genre :

```text
div:nth-child(4) > div:nth-child(7)
```

---

# 45. Gestion du temps

Ne pas utiliser partout :

```python
time.sleep(5)
time.sleep(10)
```

Utiliser les mécanismes Playwright :

```text
wait_for

expect

wait_for_load_state

locator.wait_for
```

---

# 46. Limites

Conserver quelques paramètres simples.

`.env` :

```env
OPENAI_API_KEY=

APP_SECRET_KEY=

DATABASE_PATH=qa.db

ADMIN_EMAIL=

ADMIN_PASSWORD=

MAX_PAGES=50

PAGE_TIMEOUT=30000
```

Ne plus utiliser :

```text
MAX_BUTTONS_PER_PAGE=8
```

si cela empêche de voir de vrais boutons.

Le moteur doit pouvoir inventorier tous les boutons pertinents d’une page.

---

# 47. Base SQLite

Conserver seulement les tables nécessaires :

```text
users

projects

project_pages

page_actions

test_runs

test_results
```

Pas besoin d’une base extrêmement complexe.

---

# 48. Tableau de bord

Le dashboard peut conserver :

```text
Nombre de projets

Tests réalisés

PASS

FAIL

Taux de réussite

Dernières exécutions
```

Un donut PASS/FAIL/WARNING peut être conservé.

Pas besoin de multiplier les graphiques dans cette version.

---

# 49. Tests du système SDET

Le système lui-même doit avoir quelques tests pytest.

Minimum :

```text
test_login

test_project_creation

test_button_label_detection

test_action_classification

test_form_detection

test_planner

test_report
```

Pas besoin d’une énorme suite de tests pour la première version.

---

# 50. Ce qu’OpenCode ne doit PAS faire

Ne pas construire une architecture d’entreprise inutilement complexe.

Ne pas tout coder dans `main.py`.

Ne pas utiliser Vue.js.

Ne pas transformer le système en crawler généraliste.

Ne pas cliquer aléatoirement.

Ne pas appeler tous les éléments `button`.

Ne pas tester seulement les URLs.

Ne pas considérer un clic réussi comme un test réussi.

Ne pas supprimer des données existantes.

Ne pas envoyer de notifications réelles.

Ne pas effectuer de paiement.

Ne pas dépendre entièrement d’OpenAI.

Ne pas écrire des scénarios spécifiques directement dans le code pour :

```text
COBRA
KPrimeStore
KPIP
```

Le même moteur doit pouvoir tester plusieurs applications.

---

# 51. Priorité de développement pour OpenCode

OpenCode doit développer dans cet ordre.

## Phase 1

```text
Flask
Login
Dashboard
CRUD Projet
SQLite
```

## Phase 2

```text
Playwright
Authentification
Exploration
Cartographie
```

## Phase 3

```text
Détection boutons
Détection formulaires
Détection tableaux
Détection colonne Actions
```

## Phase 4

```text
Planner
Classification CRUD
Sélection des actions
```

## Phase 5

```text
Runner
Création QA
Visualisation
Modification
Activation/Désactivation
Suppression
```

## Phase 6

```text
Verifier
PASS/FAIL
Screenshots
HTTP errors
JS errors
```

## Phase 7

```text
Historique
Rapports
Régression
OpenAI assistance
```

Ne pas commencer la Phase 5 tant que l’exploration de la Phase 3 n’est pas fiable.

---

# 52. Critère principal de réussite

Le système est considéré fonctionnel lorsqu’il peut prendre une plateforme STAGING inconnue et faire :

```text
Connexion
↓
Exploration
↓
Cartographie des pages
↓
Détection des vrais boutons
↓
Sélection des tests
↓
Planification
↓
Création donnée QA
↓
Vérification création
↓
Visualisation
↓
Modification
↓
Vérification modification
↓
Actions supplémentaires
↓
Suppression en dernier
↓
Vérification suppression
↓
Rapport
```

sans nécessiter de modifier le code Python pour chaque nouveau projet.

---

# 53. Philosophie finale

Le comportement recherché n’est pas :

```text
AUTOMATISATION DE CLICS
```

mais :

```text
AUTOMATISATION DU TRAVAIL QA
```

Chaque scénario doit suivre :

```text
OBJECTIF
   ↓
ACTION
   ↓
RÉSULTAT ATTENDU
   ↓
EXÉCUTION
   ↓
OBSERVATION
   ↓
VÉRIFICATION
   ↓
PASS / FAIL
   ↓
PREUVE
```

C’est cette règle qui doit guider toute l’implémentation de cette V2.

---

# 54. RÉSUMÉ POUR OPENCODE

Construire une plateforme SDET Python/Flask légère.

L’interface permet de créer un projet, renseigner URL/credentials/authentification/instructions, explorer l’application, voir les pages et actions détectées, sélectionner les tests et lancer l’exécution.

Playwright réalise la navigation.

L’exploration s’effectue proprement du haut vers le bas.

Les boutons doivent être enregistrés avec leur vrai titre.

Le Planner transforme les éléments découverts en scénarios cohérents.

Les CRUD doivent être testés comme des workflows complets :

```text
Créer
→ vérifier
→ retrouver
→ visualiser
→ modifier
→ vérifier
→ autres actions
→ supprimer en dernier
→ vérifier
```

Le système ne supprime que les données créées par le SDET.

Chaque action doit être vérifiée avant de recevoir PASS.

OpenAI intervient seulement comme intelligence complémentaire lorsque le moteur classique ne peut pas déterminer correctement la signification ou le résultat d’une action.

Le résultat final doit être un **outil SDET professionnel de complexité moyenne**, simple à utiliser et suffisamment robuste pour effectuer de vraies explorations et régressions sur des plateformes STAGING.
