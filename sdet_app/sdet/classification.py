"""Action classification rules (section 19-20).

The planner classifies each selected action into a semantic type:
CREATE / READ / UPDATE / DELETE / SEARCH / STATUS / NAVIGATION / AUTH / FORM / GENERIC

Keyword + DOM-attribute based. OpenAI may help only when still ambiguous.
"""

# Emoji / symbol -> human meaning (used when a button is icon-only)
ICON_LABELS = {
    "👁": "Visualiser",
    "✏": "Modifier",
    "✎": "Modifier",
    "🗑": "Supprimer",
    "📝": "Modifier",
    "➕": "Ajouter",
    "＋": "Ajouter",
    "+": "Ajouter",
    "🖊": "Modifier",
    "🔍": "Recherche",
    "🔎": "Recherche",
    "⏸": "Désactiver",
    "⏹": "Arrêter",
    "▶": "Activer",
    "✓": "Valider",
    "✔": "Valider",
    "✅": "Valider",
    "❌": "Supprimer",
    "⬇": "Exporter",
    "📥": "Exporter",
    "📤": "Importer",
    "🔒": "Verrouiller",
    "🔓": "Déverrouiller",
    "🙈": "Désactiver",
}

CREATE_HINTS = ("ajouter", "ajout", "nouveau", "nouvelle", "new", "add",
                "créer", "creer", "create", "création", "creation")
READ_HINTS = ("visualiser", "voir", "détail", "detail", "afficher", "affiche",
              "consulter", "view", "show", "open", "ouvrir")
UPDATE_HINTS = ("modifier", "éditer", "editer", "edit", "mettre à jour", "update",
                "modification", "changer")
DELETE_HINTS = ("supprimer", "delete", "remove", "effacer", "archiver", "trash",
                "suppression")
SEARCH_HINTS = ("recherch", "search", "query", "filtrer", "filter", "trouver")
STATUS_HINTS = ("désactiver", "desactiver", "activer", "enable", "disable",
                "suspendre", "statut", "status", "archiver", "réactiver", "reactiver",
                "basculer", "toggle")
NAV_HINTS = ("accueil", "home", "dashboard", "tableau de bord", "retour", "back",
             "aller", "going", "menu", "navigation")

SENSITIVE_HINTS = ("paiement", "payer", "pay", "checkout", "virement", "transfert",
                   "envoi sms", "envoi whatsapp", "send sms", "deploiement", "deploy",
                   "reset", "réinitialis", "reinitialis", "suppression massive",
                   "bulk delete", "marketing email", "send email", "impression facture")


def normalize(label):
    n = (label or "").lower()
    n = n.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    n = n.replace("ç", "c").replace("ô", "o").replace("î", "i").replace("û", "u").replace("â", "a")
    return n


def action_type(label, dom_hint=""):
    n = normalize(label)

    if any(h in n for h in CREATE_HINTS):
        return "CREATE"
    if any(h in n for h in DELETE_HINTS):
        return "DELETE"
    if any(h in n for h in UPDATE_HINTS):
        return "UPDATE"
    if any(h in n for h in SEARCH_HINTS) or "search" in dom_hint.lower():
        return "SEARCH"
    if any(h in n for h in STATUS_HINTS):
        return "STATUS"
    if any(h in n for h in READ_HINTS):
        return "READ"
    if any(h in n for h in NAV_HINTS):
        return "NAVIGATION"
    return "GENERIC"


def is_sensitive(label):
    n = normalize(label)
    return any(h in n for h in SENSITIVE_HINTS)


def is_dangerous_delete(label):
    return action_type(label) == "DELETE"


def is_create(label):
    return action_type(label) == "CREATE"
