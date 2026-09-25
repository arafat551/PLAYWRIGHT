"""Standardized action library for SDET scenarios.

Every action follows a professional model:
    { type, target, value, expected }

This replaces free-text descriptions with structured, universal actions
that any QA can understand and the engine can execute deterministically.
"""

# ---------------------------------------------------------------------------
# Action type definitions
# ---------------------------------------------------------------------------

ACTION_TYPES = {
    "NAVIGUER": {
        "label": "Naviguer",
        "description": "Aller à une URL ou page",
        "fields": ["target"],
        "target_label": "URL / chemin",
        "target_placeholder": "/clients ou https://...",
        "examples": ["/clients", "/login", "https://app.example.com/dashboard"],
    },
    "CLIQUEER": {
        "label": "Cliquer",
        "description": "Cliquer sur un bouton, lien ou élément",
        "fields": ["target"],
        "target_label": "Nom du bouton / élément",
        "target_placeholder": "Ajouter, Enregistrer, Supprimer...",
        "examples": ["Ajouter un client", "Enregistrer", "Confirmer", "Supprimer"],
    },
    "REMPLIR": {
        "label": "Remplir",
        "description": "Saisir une valeur dans un champ de formulaire",
        "fields": ["target", "value"],
        "target_label": "Nom du champ",
        "target_placeholder": "Nom, Email, Téléphone...",
        "value_label": "Valeur",
        "value_placeholder": "Texte à saisir",
        "examples": [
            {"target": "Nom", "value": "QA_CLIENT_2509"},
            {"target": "Email", "value": "qa@example.test"},
            {"target": "Téléphone", "value": "90123456"},
        ],
    },
    "SELECTIONNER": {
        "label": "Sélectionner",
        "description": "Choisir une option dans un menu déroulant",
        "fields": ["target", "value"],
        "target_label": "Nom du champ",
        "target_placeholder": "Statut, Catégorie, Type...",
        "value_label": "Option",
        "value_placeholder": "Option à sélectionner",
        "examples": [
            {"target": "Statut", "value": "Actif"},
            {"target": "Type", "value": "Particulier"},
        ],
    },
    "COCHER": {
        "label": "Cocher",
        "description": "Cocher une case à cocher",
        "fields": ["target"],
        "target_label": "Nom de la case",
        "target_placeholder": "Accepter, Actif, Activer...",
        "examples": ["Accepter les conditions", "Actif", "Activer la notification"],
    },
    "DECOCHER": {
        "label": "Décocher",
        "description": "Décocher une case à cocher",
        "fields": ["target"],
        "target_label": "Nom de la case",
        "target_placeholder": "Accepter, Actif...",
        "examples": ["Accepter les conditions", "Notifications"],
    },
    "RECHERCHER": {
        "label": "Rechercher",
        "description": "Rechercher un élément dans une barre de recherche",
        "fields": ["target"],
        "target_label": "Terme de recherche",
        "target_placeholder": "Nom de l'élément à trouver",
        "examples": ["QA_CLIENT_2509", "Client Test"],
    },
    "VERIFIER": {
        "label": "Vérifier",
        "description": "Vérifier qu'un élément ou texte est présent",
        "fields": ["target"],
        "target_label": "Élément à vérifier",
        "target_placeholder": "Message de succès, nom dans la liste...",
        "examples": [
            "message de succès",
            "QA_CLIENT_2509 visible",
            "la page se charge sans erreur",
        ],
    },
    "ATTENDRE": {
        "label": "Attendre",
        "description": "Attendre un temps défini (millisecondes)",
        "fields": ["value"],
        "value_label": "Durée (ms)",
        "value_placeholder": "2000",
        "examples": ["1000", "2000", "3000"],
    },
    "ECRAN": {
        "label": "Capture d'écran",
        "description": "Prendre une capture d'écran",
        "fields": ["target"],
        "target_label": "Nom de la capture",
        "target_placeholder": "etat_final, avant_modification...",
        "examples": ["etat_final", "apres_creation", "avant_suppression"],
    },
    "SELECTIONNER_LIGNE": {
        "label": "Sélectionner ligne",
        "description": "Sélectionner une ligne dans un tableau",
        "fields": ["target"],
        "target_label": "Contenu de la ligne",
        "target_placeholder": "Nom ou identifiant de la ligne",
        "examples": ["QA_CLIENT_2509", "Client Test"],
    },
}

# ---------------------------------------------------------------------------
# CRUD scenario templates
# ---------------------------------------------------------------------------

CRUD_TEMPLATE = {
    "name": "Scénario CRUD complet",
    "description": "Créer, visualiser, modifier, supprimer un élément",
    "actions": [
        {"type": "NAVIGUER", "target": "{url}", "value": "",
         "expected": "Page chargée"},
        {"type": "CLIQUEER", "target": "Ajouter", "value": "",
         "expected": "Formulaire ouvert"},
        {"type": "REMPLIR", "target": "{champ_nom}", "value": "{entite}",
         "expected": "Champ rempli"},
        {"type": "REMPLIR", "target": "{champ_email}", "value": "{email}",
         "expected": "Champ rempli"},
        {"type": "REMPLIR", "target": "{champ_telephone}", "value": "{telephone}",
         "expected": "Champ rempli"},
        {"type": "CLIQUEER", "target": "Enregistrer", "value": "",
         "expected": "Formulaire soumis"},
        {"type": "VERIFIER", "target": "{entite} visible dans la liste",
         "value": "", "expected": "Élément créé"},
        {"type": "RECHERCHER", "target": "{entite}", "value": "",
         "expected": "Élément trouvé"},
        {"type": "SELECTIONNER_LIGNE", "target": "{entite}", "value": "",
         "expected": "Ligne sélectionnée"},
        {"type": "CLIQUEER", "target": "Modifier", "value": "",
         "expected": "Formulaire de modification ouvert"},
        {"type": "REMPLIR", "target": "{champ_nom}", "value": "{entite_mod}",
         "expected": "Champ modifié"},
        {"type": "CLIQUEER", "target": "Enregistrer", "value": "",
         "expected": "Modification enregistrée"},
        {"type": "VERIFIER", "target": "{entite_mod} visible",
         "value": "", "expected": "Modification confirmée"},
        {"type": "CLIQUEER", "target": "Supprimer", "value": "",
         "expected": "Demande de confirmation"},
        {"type": "CLIQUEER", "target": "Confirmer", "value": "",
         "expected": "Suppression effectuée"},
        {"type": "VERIFIER", "target": "{entite_mod} absent de la liste",
         "value": "", "expected": "Élément supprimé"},
    ],
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_action_type(action_type):
    """Return the definition of an action type, or None."""
    return ACTION_TYPES.get(action_type)


def list_action_types():
    """Return all action types as a list of dicts for the UI."""
    return [
        {
            "key": key,
            "label": defn["label"],
            "description": defn["description"],
            "fields": defn["fields"],
            "target_label": defn.get("target_label", ""),
            "target_placeholder": defn.get("target_placeholder", ""),
            "value_label": defn.get("value_label", ""),
            "value_placeholder": defn.get("value_placeholder", ""),
            "examples": defn.get("examples", []),
        }
        for key, defn in ACTION_TYPES.items()
    ]


def build_structured_step(action_type, target="", value="", expected="",
                          step_order=0):
    """Create a structured step dict ready to be stored in the database."""
    return {
        "action_type": action_type,
        "target": target.strip(),
        "value": value.strip(),
        "expected": expected.strip(),
        "step_order": step_order,
        "description": _human_description(action_type, target, value),
    }


def _human_description(action_type, target, value):
    """Generate a human-readable description from structured fields."""
    defn = ACTION_TYPES.get(action_type)
    if not defn:
        return target or value or ""
    label = defn["label"]
    if action_type == "REMPLIR":
        return f"{label} le champ {target} avec {value}"
    if action_type == "SELECTIONNER":
        return f"{label} {target} avec {value}"
    if action_type == "NAVIGUER":
        return f"{label} vers {target}"
    if action_type == "ATTENDRE":
        return f"{label} {value} ms"
    if action_type in ("COCHER", "DECOCHER"):
        return f"{label} la case {target}"
    if action_type == "VERIFIER":
        return f"{label} que {target}"
    if action_type == "RECHERCHER":
        return f"{label} {target}"
    if action_type == "SELECTIONNER_LIGNE":
        return f"Sélectionner la ligne {target}"
    if action_type == "ECRAN":
        return f"Capturer {target}"
    if action_type == "CLIQUEER":
        return f"{label} sur {target}"
    return f"{label} {target}"


def generate_crud_suffix():
    """Generate a unique suffix for QA data (timestamp-based)."""
    import datetime
    return datetime.datetime.now().strftime("%d%H%M%S")


def build_crud_actions(url, champ_nom="Nom", champ_email="Email",
                       champ_telephone="Téléphone", entity_name="QA_TEST"):
    """Build a ready-to-use CRUD action list with unique entity names.

    Returns a list of structured action dicts.
    """
    suffix = generate_crud_suffix()
    entity = f"{entity_name}_{suffix}"
    entity_mod = f"{entity}_MOD"
    email = f"qa{suffix}@example.test"
    phone = "90123456"

    return [
        build_structured_step("NAVIGUER", url),
        build_structured_step("CLIQUEER", "Ajouter"),
        build_structured_step("REMPLIR", champ_nom, entity),
        build_structured_step("REMPLIR", champ_email, email),
        build_structured_step("REMPLIR", champ_telephone, phone),
        build_structured_step("CLIQUEER", "Enregistrer"),
        build_structured_step("VERIFIER", f"{entity} visible dans la liste"),
        build_structured_step("RECHERCHER", entity),
        build_structured_step("SELECTIONNER_LIGNE", entity),
        build_structured_step("CLIQUEER", "Modifier"),
        build_structured_step("REMPLIR", champ_nom, entity_mod),
        build_structured_step("CLIQUEER", "Enregistrer"),
        build_structured_step("VERIFIER", f"{entity_mod} visible"),
        build_structured_step("CLIQUEER", "Supprimer"),
        build_structured_step("CLIQUEER", "Confirmer"),
        build_structured_step("VERIFIER", f"{entity_mod} absent de la liste"),
    ]
