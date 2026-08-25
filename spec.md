# Spécification fonctionnelle – Application Web QA Automation avec Flask et OpenAI

## 1. Objectif du projet

L’objectif est de créer une **application web simple de tests automatisés** permettant à un utilisateur autorisé de :

* se connecter à l’application ;
* créer et gérer plusieurs projets de test ;
* renseigner les accès d’une application à tester ;
* choisir le type d’authentification ;
* gérer les authentifications avec OTP/2FA ;
* lancer automatiquement un test fonctionnel complet ;
* utiliser **OpenAI comme cerveau du QA** pour analyser l’application et décider des tests à effectuer ;
* suivre l’avancement et les résultats ;
* consulter les rapports générés ;
* désactiver ou supprimer un projet/test.

L’utilisateur n’aura **aucun accès au code Python, aux instructions OpenAI ou aux secrets techniques**.

---

# 2. Architecture générale

La V1 doit rester légère.

```text
Application
│
├── main.py
│   ├── Flask
│   ├── logique utilisateur
│   ├── gestion projets
│   ├── moteur QA
│   ├── OpenAI
│   ├── Computer Use / navigateur
│   └── génération rapports
│
├── .env
│   └── secrets techniques
│
├── qa.db
│   └── base SQLite
│
├── templates/
│   ├── login.html
│   ├── dashboard.html
│   ├── projects.html
│   ├── project-form.html
│   ├── test.html
│   └── report.html
│
└── static/
    └── style.css
```

Technologies recommandées :

```text
Backend : Python + Flask
Frontend : HTML + CSS + JavaScript léger
Base de données : SQLite
IA : OpenAI API
Navigation QA : Computer Use
Playwright : uniquement si nécessaire
```

---

# 3. Authentification de l’utilisateur

## AUTH-001 – Page de connexion

L’application doit disposer d’une page :

```text
CONNEXION

Email
[________________________]

Mot de passe
[________________________]

[ Se connecter ]
```

L’utilisateur doit obligatoirement être authentifié avant d’accéder aux projets et aux tests.

---

## AUTH-002 – Accès sécurisé

Une personne non connectée essayant d’accéder directement à :

```text
/dashboard
/projects
/reports
```

doit automatiquement être redirigée vers :

```text
/login
```

---

## AUTH-003 – Déconnexion

L’interface doit proposer un bouton :

```text
Déconnexion
```

permettant de fermer la session utilisateur.

---

# 4. Tableau de bord

Après connexion, l’utilisateur arrive automatiquement sur :

```text
/dashboard
```

Le tableau de bord constitue la **première page principale**.

Il doit donner une vision rapide de l’activité QA.

Exemple :

```text
TABLEAU DE BORD

Projets
12

Tests exécutés
48

Tests réussis
85 %

Tests échoués
15 %

Dernier test
COBRA – 20/08/2026
```

Le tableau de bord pourra afficher :

* nombre total de projets ;
* nombre de projets actifs ;
* nombre de tests réalisés ;
* nombre de tests réussis ;
* nombre de tests échoués ;
* taux moyen de réussite ;
* derniers tests effectués.

La V1 doit rester simple visuellement.

---

# 5. Menu principal

Après connexion, l’utilisateur dispose d’un menu minimal.

```text
LOGO

Tableau de bord
Projets
Tests / Rapports

----------------

Utilisateur
Déconnexion
```

Aucun menu supplémentaire n’est nécessaire dans la première version.

---

# 6. Page Projets

La deuxième page principale doit être :

```text
/projects
```

Elle contient :

```text
PROJETS

[ + Ajouter un projet ]
```

Puis une DataTable contenant l’ensemble des projets.

---

# 7. Création d’un projet

Lorsque l’utilisateur clique sur :

```text
+ Ajouter un projet
```

un formulaire doit être affiché.

---

## PROJECT-001 – Nom du projet

Champ :

```text
Nom du projet
[________________________]
```

Exemple :

```text
COBRA
KPrimeStore
KPrimePay
POS
LEOTA
```

Champ obligatoire.

---

## PROJECT-002 – URL

Champ :

```text
URL de l'application
[ https://________________ ]
```

Cette URL constitue l’adresse de départ du test.

L’agent QA doit commencer depuis cette URL.

---

## PROJECT-003 – Email

Champ :

```text
Email de connexion
[________________________]
```

L’email servira uniquement à l’authentification sur l’application à tester.

---

## PROJECT-004 – Mot de passe

Champ :

```text
Mot de passe
[________________________]
```

Le mot de passe doit être masqué dans l’interface.

Exemple :

```text
•••••••••••••
```

Il ne doit jamais être affiché dans les rapports.

---

# 8. Type d’authentification

Le formulaire doit proposer :

```text
Type d'authentification

[ Simple ▼ ]
```

Options minimales :

```text
Simple
2FA / OTP
Aucune authentification
```

---

## Authentification simple

Le système utilise :

```text
Email
+
Mot de passe
```

puis poursuit automatiquement les tests.

---

## Authentification 2FA

Si l’utilisateur choisit :

```text
2FA / OTP
```

le système doit savoir qu’une intervention humaine peut être nécessaire pendant la connexion.

---

# 9. Zone Commentaire / Instructions

Le formulaire doit contenir une zone :

```text
Commentaires / Instructions supplémentaires

┌─────────────────────────────────────────┐
│                                         │
│                                         │
│                                         │
└─────────────────────────────────────────┘
```

Cette zone permet par exemple de renseigner :

```text
Ne pas tester les paiements réels.

Tester tous les modules disponibles.

Ne pas supprimer les utilisateurs administrateurs.

Pour les créations utiliser des données QA.

Tester particulièrement la gestion client.
```

Ces informations doivent être transmises au moteur OpenAI comme **instructions complémentaires du test**.

---

# 10. Enregistrement du projet

Le formulaire doit proposer :

```text
[ Annuler ]      [ Enregistrer ]
```

Une fois enregistré, le projet apparaît automatiquement dans la DataTable.

---

# 11. DataTable des projets

La page Projets doit afficher par exemple :

| Projet | URL        | Authentification | Dernier test | Tests | Réussite | Statut | Actions |
| ------ | ---------- | ---------------- | ------------ | ----: | -------: | ------ | ------- |
| COBRA  | staging... | 2FA              | 20/08/2026   |   148 |     87 % | Actif  | Actions |
| POS    | staging... | Simple           | 19/08/2026   |    92 |     96 % | Actif  | Actions |

---

# 12. Informations principales de la DataTable

La table doit au minimum afficher :

### Nom du projet

```text
COBRA
```

### Type d’authentification

```text
Simple
2FA
Aucune
```

### Dernière exécution

```text
20/08/2026 14:30
```

### Nombre de tests

```text
148
```

### Pourcentage de réussite

```text
87 %
```

### Statut

```text
Actif
Désactivé
En cours
```

---

# 13. Colonne Actions

La dernière colonne doit être :

```text
Actions
```

avec au minimum trois possibilités :

```text
👁 Visualiser

⏸ Désactiver

🗑 Supprimer
```

---

## ACTION-001 – Visualiser

Le bouton :

```text
Visualiser
```

ouvre la fiche complète du projet.

Elle doit présenter :

```text
Nom
URL
Type d'authentification
Statut

Dernier test
Nombre de tests
PASS
FAIL
WARNING
SKIPPED

Pourcentage de réussite

Commentaires

Historique des tests
```

Un bouton doit permettre de lancer un nouveau test :

```text
[ LANCER LE TEST ]
```

---

## ACTION-002 – Désactiver

Le bouton :

```text
Désactiver
```

doit permettre de rendre temporairement le projet indisponible pour les nouveaux tests.

Un projet désactivé :

```text
reste dans la base
reste consultable
conserve son historique
ne peut plus lancer de test
```

Il doit pouvoir être réactivé plus tard.

---

## ACTION-003 – Supprimer

Le bouton :

```text
Supprimer
```

doit demander une confirmation.

Exemple :

```text
Voulez-vous vraiment supprimer ce projet ?

[ Annuler ] [ Supprimer ]
```

La suppression ne doit jamais être exécutée immédiatement sans confirmation.

---

# 14. Lancement d’un test

Depuis la fiche d’un projet :

```text
COBRA

[ LANCER LE TEST ]
```

L’utilisateur clique sur le bouton.

Le système doit créer une nouvelle session de test.

---

# 15. Écran d’exécution

L’utilisateur doit pouvoir suivre simplement l’état du test.

Exemple :

```text
TEST EN COURS

Projet : COBRA

Statut :
● Test en cours

Étape actuelle :
Gestion des clients

Tests exécutés :
47

PASS :
42

FAIL :
3

WARNING :
2
```

Il n’est pas nécessaire d’afficher le raisonnement interne de l’IA.

---

# 16. Fonctionnement du moteur QA

Le test doit reposer principalement sur :

```text
OpenAI
+
Computer Use
+
navigateur
```

La logique générale doit être :

```text
L'utilisateur clique LANCER

        ↓

main.py récupère le projet

        ↓

URL
Email
Password
Authentification
Instructions

        ↓

Ouverture du navigateur

        ↓

Connexion

        ↓

OpenAI observe

        ↓

OpenAI décide quoi tester

        ↓

Computer Use agit

        ↓

Nouvel écran

        ↓

OpenAI analyse

        ↓

Vérifie le résultat

        ↓

PASS / FAIL / WARNING

        ↓

Continue

        ↓

Rapport final
```

---

# 17. Comportement QA attendu

OpenAI doit recevoir une instruction globale lui indiquant qu’il agit comme :

```text
QA Analyst Senior
SDET Senior
Expert en tests fonctionnels
Expert en tests de régression
```

Il doit :

* naviguer comme un utilisateur humain ;
* utiliser les boutons et menus visibles ;
* analyser toutes les pages accessibles ;
* détecter les fonctionnalités ;
* tester les champs ;
* tester les formulaires ;
* tester les validations ;
* tester les boutons ;
* tester les tableaux ;
* tester recherche et filtres ;
* tester pagination ;
* tester les opérations CRUD ;
* vérifier les résultats.

---

# 18. Principe obligatoire : Action ≠ Test réussi

Le moteur ne doit jamais considérer :

```text
J'ai cliqué sur Enregistrer
```

comme :

```text
PASS
```

Le fonctionnement obligatoire est :

```text
Action
   ↓
Résultat attendu
   ↓
Observation
   ↓
Comparaison
   ↓
PASS / FAIL
```

Exemple :

```text
Créer client

↓
Enregistrer

↓
Message succès

↓
Retour liste

↓
Recherche du client

↓
Client retrouvé

↓
PASS
```

---

# 19. Gestion du 2FA / OTP

Si le projet utilise :

```text
2FA / OTP
```

l’agent remplit automatiquement :

```text
Email
Mot de passe
```

puis clique sur :

```text
Se connecter
```

Si l’application demande ensuite un OTP, le test doit être mis en attente.

---

# 20. Interface OTP

L’utilisateur doit voir dans l’application QA :

```text
TEST EN ATTENTE

Une authentification 2FA est requise.

Code OTP
[ _ _ _ _ _ _ ]

[ VALIDER ET CONTINUER ]
```

L’utilisateur saisit le code reçu par :

```text
Email
SMS
WhatsApp
Application d'authentification
```

puis clique sur :

```text
VALIDER ET CONTINUER
```

Le moteur QA transmet le code au navigateur et poursuit le test.

---

# 21. Expiration OTP

Si le code est incorrect ou expiré :

```text
Code OTP invalide ou expiré.

[ Saisir un nouveau code ]
```

Le test ne doit pas être considéré comme échoué immédiatement.

---

# 22. Historique des tests

Chaque projet doit conserver son historique.

Exemple :

| Date       | Projet | Tests | PASS | FAIL | WARNING | Réussite | Rapport |
| ---------- | ------ | ----: | ---: | ---: | ------: | -------: | ------- |
| 20/08/2026 | COBRA  |   148 |  130 |   10 |       8 |     87 % | Voir    |
| 18/08/2026 | COBRA  |   142 |  120 |   15 |       7 |     84 % | Voir    |

---

# 23. Rapport détaillé

Lorsque l’utilisateur clique sur :

```text
Voir le rapport
```

il doit obtenir un rapport lisible.

Exemple :

```text
RAPPORT QA

Projet : COBRA
Date : 20/08/2026

Tests exécutés : 148

PASS : 130
FAIL : 10
WARNING : 8
SKIPPED : 0

Taux de réussite : 87 %
```

Puis les résultats par module.

---

# 24. Exemple de résultat

```text
GESTION CLIENT

✅ Accès au module

✅ Affichage liste clients

✅ Création client

✅ Validation champs obligatoires

✅ Recherche client

❌ Modification client

✅ Visualisation client

✅ Suppression client
```

---

# 25. Détail d’une anomalie

```text
Module :
Gestion Clients

Fonction :
Modification client

Résultat attendu :
Le nouveau téléphone doit être enregistré.

Résultat obtenu :
L'ancien téléphone reste affiché après actualisation.

Statut :
FAIL

Sévérité :
MAJEURE

Capture :
screenshot_024.png
```

---

# 26. Pourcentage de réussite

Le pourcentage affiché dans la DataTable doit être calculé à partir des tests réellement exécutés.

Exemple :

```text
Tests exécutés : 100

PASS : 87
FAIL : 10
WARNING : 3

Réussite : 87 %
```

Les règles exactes de calcul pourront être affinées ultérieurement.

---

# 27. Statuts possibles d’un test

Le moteur doit utiliser au minimum :

```text
PASS
FAIL
WARNING
SKIPPED
```

---

# 28. Protection des identifiants

Les informations sensibles suivantes :

```text
Email
Mot de passe
OpenAI API Key
OTP
```

ne doivent jamais être affichées dans les rapports.

Les mots de passe des projets doivent être stockés de manière sécurisée.

---

# 29. Fichier `.env`

Le `.env` doit contenir uniquement les secrets techniques de l’application.

Exemple :

```env
OPENAI_API_KEY=
APP_SECRET_KEY=
DATABASE_PATH=
ADMIN_EMAIL=
ADMIN_PASSWORD=
```

Les identifiants des projets ne doivent pas nécessiter une modification manuelle du `.env`.

Ils seront renseignés depuis l’interface.

---

# 30. Base de données

SQLite doit conserver au minimum :

```text
UTILISATEURS

PROJETS

TESTS

RESULTATS

RAPPORTS
```

---

# 31. Projet

Un projet contient notamment :

```text
ID
Nom
URL
Email
Mot de passe chiffré
Type authentification
Commentaires
Statut
Date création
Date modification
```

---

# 32. Test

Une exécution contient :

```text
ID
Projet
Date début
Date fin
Statut
Nombre tests
PASS
FAIL
WARNING
SKIPPED
Pourcentage réussite
Rapport
```

---

# 33. Interface finale attendue

Le parcours utilisateur doit rester extrêmement simple :

```text
CONNEXION

     ↓

TABLEAU DE BORD

     ↓

PROJETS

     ↓

AJOUTER PROJET

     ↓

URL
EMAIL
PASSWORD
AUTHENTIFICATION
COMMENTAIRES

     ↓

ENREGISTRER

     ↓

LANCER LE TEST

     ↓

OPENAI + COMPUTER USE

     ↓

OTP SI NÉCESSAIRE

     ↓

TEST AUTOMATIQUE

     ↓

RÉSULTATS

     ↓

RAPPORT
```

---

# 34. Périmètre de la V1

La V1 doit volontairement rester légère.

### À développer

```text
✅ Connexion
✅ Tableau de bord
✅ Gestion des projets
✅ Ajouter projet
✅ Modifier projet
✅ Visualiser projet
✅ Désactiver projet
✅ Supprimer projet
✅ Lancer test
✅ Authentification simple
✅ 2FA / OTP
✅ OpenAI QA Agent
✅ Computer Use
✅ Historique
✅ DataTable
✅ Pourcentage réussite
✅ Rapport détaillé
```

### À ne pas développer pour le moment

```text
❌ Inscription publique

❌ Gestion complexe des rôles

❌ Plusieurs organisations

❌ Abonnements

❌ Paiement

❌ Facturation

❌ Application mobile

❌ CRM

❌ Gestion commerciale

❌ Fonctionnalités administratives complexes
```

---

# 35. Vision générale

La plateforme doit donner l’impression d’utiliser un véritable outil professionnel de QA :

```text
Je crée mon projet

↓
Je renseigne les accès

↓
Je donne éventuellement des instructions

↓
Je clique sur Lancer

↓
L'IA teste l'application

↓
J'interviens uniquement pour l'OTP si nécessaire

↓
Je récupère les résultats

↓
Je consulte les bugs détectés
```

La complexité doit rester **dans le moteur QA**, pas dans l’interface.

L’interface graphique doit donc rester :

**simple, professionnelle, rapide et facile à comprendre.**

---

---

# 36. VERSION 2 – Parcours par exploration et sélection des actions

> Cette section décrit les évolutions implémentées depuis la V1. En cas de
> contradiction avec les sections précédentes, la présente section fait foi.

## 36.1. Nouveau parcours utilisateur

```text
CONNEXION

     ↓

TABLEAU DE BORD (avec graphiques)

     ↓

PROJETS

     ↓

ETAPE 1 : EXPLORER L'APPLICATION
(le système parcourt toutes les pages et inventorie
 tous les boutons / formulaires / recherches)

     ↓

ETAPE 2 : CHOISIR LES ACTIONS A TESTER
(écran à 2 niveaux : pages → actions avec cases à cocher)

     ↓

ETAPE 3 : LANCER LES TESTS
(seules les actions cochées sont exécutées,
 une seule fois chacune, dans l'ordre découvert)

     ↓

RAPPORT (graphiques + codes HTTP + auteur)
```

**Règle importante :** la création d'un projet ne lance **jamais** de test
automatiquement. Le test n'est lancé qu'après l'étape de sélection.

---

## 36.2. Étape 1 – Exploration (EXPLORE-001)

Le bouton **« Explorer l'application »** ouvre un navigateur, se connecte
avec les identifiants du projet (gère le 2FA/OTP comme en V1), puis parcourt
toutes les pages accessibles :

* navigation en largeur depuis l'URL du projet ;
* même domaine uniquement ;
* liens de déconnexion exclus (`logout`, `deconnexion`, etc.) ;
* fichiers statiques exclus (`.png`, `.pdf`, `.css`, ...).

Pour **chaque page visitée**, le système enregistre :

| Donnée | Description |
| --- | --- |
| Nom | Texte du `h1` ou titre de l'onglet |
| URL | Adresse complète |
| Code HTTP | Statut de la réponse (200, 404...) |
| Actions | Inventaire des éléments interactifs (voir EXPLORE-002) |

### EXPLORE-001 – Limite configurable

Le nombre maximum de pages explorées est défini dans le fichier `.env` :

```env
MAX_PAGES=40
```

Cette valeur est modifiable sans toucher au code.

### EXPLORE-002 – Inventaire des actions

Avant chaque scan, la page est **entièrement chargée** (réseau au repos) puis
**défilée de haut en bas** afin de déclencher le rendu du contenu différé
(lazy loading). Tous les boutons sont ainsi présents dans le DOM au moment
de l'inventaire.

Sur chaque page, le système inventorie :

* **Tous les boutons sans exception** : chaque balise `<button>` quel que
  soit son type (`button`, `submit`, `reset`), sa position et sa visibilité
  (boutons masqués, menus dépliants, modales... inclus), plus les éléments
  `[role="button"]` et les inputs `submit` / `button` / `reset`. Le libellé
  est repris du texte, de `value`, `aria-label` ou `title`, sinon
  « Bouton #n ». Seuls les boutons de déconnexion sont exclus ;
* **Formulaires** : une entrée par `<form>` (« Formulaire #1 »...) ;
* **Recherche** : champ de recherche détecté (`type=search`, noms/placeholders
  contenant search/query/recherch).

Chaque action est stockée avec sa page, son type, son libellé et son index
de sélecteur afin d'être rejouée précisément. À l'exécution, la même
séquence de chargement/défilement est appliquée pour garantir que les index
correspondent exactement. Un bouton sélectionné mais non cliquable (masqué,
désactivé) est marqué WARNING, jamais ignoré silencieusement.

Les résultats d'exploration sont consultables dans l'historique du projet
(type « Exploration », statut `explored`).

---

## 36.3. Étape 2 – Sélection des actions (SELECT-001)

La page **« Choisir les actions à tester »** affiche une liste à **deux
niveaux** :

```text
[x] CLIENTS  (200)  4 action(s)
     [x] [btn]  Ajouter un client        Bouton
     [x] [ico]  Bouton icone #2          Bouton
     [x] [doc]  Formulaire #1            Formulaire
     [x] [loupe] Recherche               Recherche

[ ] FACTURES  (404)  1 action(s)
     [ ] [btn]  Nouvelle facture         Bouton
```

Comportement :

* chaque page possède une **case maître** (coche/décoche toutes ses actions) ;
* chaque action possède sa propre case, **cochée par défaut** ;
* boutons « Tout cocher » / « Tout décocher » ;
* compteur d'actions sélectionnées en temps réel ;
* le code HTTP de la page est affiché à côté de son nom ;
* impossible de lancer un test sans au moins une action cochée.

**C'est l'utilisateur qui décide exactement quelles actions seront
exécutées** (ajouter, modifier, activer/désactiver...). Le moteur ne clique
plus rien de son propre chef.

---

## 36.4. Étape 3 – Exécution déterministe (RUN-001)

Pendant le test, pour chaque page ayant des actions sélectionnées :

1. **un seul chargement** de la page ;
2. enregistrement de l'accès (PASS/FAIL selon code HTTP) ;
3. exécution de **chaque action cochée, une seule fois**, dans l'ordre :
   * *Bouton* : clic ; si un formulaire/modale s'ouvre (ex. « Ajouter »),
     il est rempli avec des données QA et soumis ;
   * *Formulaire* : remplissage de tous les champs (données QA adaptées au
     type : email, mot de passe, téléphone, date...) puis soumission ;
   * *Recherche* : saisie du mot « test » + Entrée ;
4. verdict de chaque action : comparaison avant/après (message de succès,
   message de validation, changement d'URL, changement de contenu) ;
   si OpenAI est configuré, l'IA évalue le résultat de l'action.

Le moteur ne navigue plus aléatoirement : il ne recharge une page que si
l'action précédente a provoqué une navigation. Les boîtes de dialogue
(`confirm()`) sont acceptées automatiquement.

### RUN-002 – Arrêt d'un test

Un bouton **« ARRETER LE TEST »** est disponible pendant l'exécution
(test, exploration, attente OTP). L'arrêt est demandé au moteur qui ferme
proprement le navigateur et marque le test `cancelled`.

### RUN-003 – Traçabilité

* chaque test mémorise **qui l'a lancé** (`Lance par`) ;
* chaque projet mémorise **qui l'a créé** et **qui l'a modifié en dernier**.

Ces informations sont affichées dans : l'historique du projet, la liste des
rapports, les tests récents du tableau de bord, l'en-tête du rapport et la
fiche du projet.

---

## 36.5. Codes HTTP dans le rapport (REPORT-001)

Le rapport contient une section **« Statut HTTP des pages »** listant chaque
page testée avec son code, coloré :

| Code | Couleur |
| --- | --- |
| 2xx | Vert |
| 3xx | Orange |
| 4xx / 5xx | Rouge |
| inconnu | Gris |

Le code apparaît aussi à côté de chaque résultat « Accès au module ».
Un accès en 4xx/5xx est compté FAIL (sévérité MAJOR/CRITICAL).

---

## 36.6. Tableau de bord graphique (DASH-001)

Le tableau de bord affiche trois graphiques (Chart.js) :

* **Donut** : répartition PASS / FAIL / WARNING / SKIPPED ;
* **Barres empilées** : résultats par projet ;
* **Courbe** : évolution du taux de réussite sur les derniers tests.

---

## 36.7. Rapport enrichi (REPORT-002)

Le rapport comprend :

* cercle de taux de réussite (vert ≥ 80 %, orange ≥ 50 %, rouge < 50 %) ;
* cartes PASS / FAIL / WARNING / SKIPPED ;
* donut de répartition + barres empilées par module ;
* anomalies par sévérité (CRITICAL / MAJOR / MINOR) ;
* modules dépliables avec barre de progression colorée ;
* détail attendu/obtenu des échecs + capture d'écran cliquable ;
* statuts HTTP des pages (voir REPORT-001) ;
* auteur du test (« Lancé par »).

---

## 36.8. Boutons d'action en icônes (UI-001)

Les colonnes « Actions » utilisent des icônes avec infobulle :

| Icône | Action |
| --- | --- |
| Œil | Visualiser |
| Crayon | Modifier |
| Power / Flèche circulaire | Désactiver / Réactiver |
| Poubelle | Supprimer (avec confirmation) |
| Lecture | Suivre un test en cours |

---

## 36.9. Base de données V2

Nouvelles tables / colonnes :

```text
project_pages   : id, project_id, name, url, last_status, discovered_at
page_actions    : id, project_id, page_url, page_name,
                  action_type (button|form|search), label, el_index
tests           : + selected_modules, selected_actions, created_by
projects        : + created_by, updated_by
results         : + http_status
```

---

## 36.10. Fichier `.env` V2

```env
OPENAI_API_KEY=
APP_SECRET_KEY=
DATABASE_PATH=
ADMIN_EMAIL=
ADMIN_PASSWORD=

# Nombre maximum de pages à explorer par projet
MAX_PAGES=40
# Nombre maximum de formulaires / boutons inventoriés et testés par page
MAX_FORMS_PER_PAGE=5
MAX_BUTTONS_PER_PAGE=8
# Cliquer aussi les boutons destructeurs (supprimer...) : true/false
TEST_DANGEROUS_ACTIONS=true
```

---

## 36.11. Périmètre V2

```text
✅ Exploration avec limite configurable (MAX_PAGES)
✅ Inventaire des boutons (y compris icônes), formulaires et recherches
✅ Sélection pages → actions avec cases à cocher
✅ Exécution déterministe : actions choisies uniquement, 1 fois chacune
✅ Remplissage automatique des modales ouvertes par les boutons « Ajouter »
✅ Données QA réalistes selon le type de champ
✅ Verdicts heuristiques + évaluation OpenAI si configurée
✅ Bouton ARRETER LE TEST fonctionnel
✅ Codes HTTP par page (exploration + rapport)
✅ Traçabilité : créateur / modificateur projet, auteur du test
✅ Graphiques tableau de bord (Chart.js)
✅ Rapport enrichi (graphiques, sévérités, captures, codes HTTP)
✅ Boutons d'action en icônes (œil, crayon, power, poubelle)
```
