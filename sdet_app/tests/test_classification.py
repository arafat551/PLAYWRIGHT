from sdet_app.sdet import classification as c
from sdet_app.sdet.explorer import element_label, icon_label


class FakeEl:
    def __init__(self, text="", aria="", title="", value=""):
        self._text = text
        self._attrs = {"aria-label": aria, "title": title, "value": value}

    def inner_text(self):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)


def test_button_label_prefers_visible_text():
    el = FakeEl(text="Ajouter un client", aria="K", title="T")
    assert element_label(el) == "Ajouter un client"


def test_button_label_uses_aria_when_no_text():
    el = FakeEl(aria="Ajouter un client")
    assert element_label(el) == "Ajouter un client"


def test_button_label_uses_title_when_no_text_aria():
    el = FakeEl(title="Exporter PDF")
    assert element_label(el) == "Exporter PDF"


def test_button_label_fallback_numbered():
    el = FakeEl()
    assert element_label(el, idx=4) == "Bouton #5"


def test_icon_label():
    assert icon_label("👁") == "Visualiser"
    assert icon_label("✏") == "Modifier"
    assert icon_label("🗑") == "Supprimer"


def test_action_classification_crud():
    assert c.action_type("Ajouter un client") == "CREATE"
    assert c.action_type("Visualiser") == "READ"
    assert c.action_type("Modifier") == "UPDATE"
    assert c.action_type("Supprimer") == "DELETE"
    assert c.action_type("Rechercher") == "SEARCH"
    assert c.action_type("Désactiver") == "STATUS"


def test_sensitive_detection():
    assert c.is_sensitive("Payer la commande") is True
    assert c.is_sensitive("Exporter PDF") is False
