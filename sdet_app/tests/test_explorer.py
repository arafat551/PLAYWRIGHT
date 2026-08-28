from sdet_app.sdet import explorer


class FakeEl:
    def __init__(self, attrs=None):
        self.attrs = attrs or {}

    def get_attribute(self, name):
        return self.attrs.get(name)


def test_plain_row_action_links_captured():
    assert explorer._is_action_anchor("Modifier", FakeEl({"href": "/clients/1/edit"}))
    assert explorer._is_action_anchor("Supprimer", FakeEl({"href": "/clients/1/delete"}))
    assert explorer._is_action_anchor("Visualiser", FakeEl({"href": "/clients/1"}))
    assert explorer._is_action_anchor("Ajouter un client", FakeEl({"href": "/clients/create"}))
    assert explorer._is_action_anchor("Exporter", FakeEl({"href": "/clients/export"}))


def test_nav_or_entity_links_excluded():
    assert not explorer._is_action_anchor("Jean Dupont", FakeEl({"href": "/clients/1"}))
    assert not explorer._is_action_anchor("", FakeEl({"href": "/clients/1"}))
    assert not explorer._is_action_anchor("Accueil", FakeEl({"href": "/"}))
    assert not explorer._is_action_anchor("Suivant", FakeEl({"href": "/clients?page=2"}))
    assert not explorer._is_action_anchor("KPIP PROD il y a 1 an CRM IMS HRM", FakeEl({"href": "/clients"}))


def test_aria_title_links_kept():
    assert explorer._is_action_anchor("Voir le profil", FakeEl({"href": "/clients/1", "aria-label": "Voir le profil"}))
    assert explorer._is_action_anchor("Détails", FakeEl({"href": "/clients/1", "title": "Détails"}))


def test_onclick_link_regarded_as_action():
    assert explorer._is_action_anchor("Supprimer", FakeEl({"href": "/clients/1", "onclick": "confirmDel()"}))


class FakeIcon:
    def __init__(self, text="", attrs=None, icon_classes=""):
        self.text = text
        self.attrs = attrs or {}
        self.icon_classes = icon_classes

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def evaluate(self, js):
        return self.icon_classes


def test_icon_class_labels():
    assert explorer.element_label(FakeIcon(icon_classes="fa fa-plus")) == "Ajouter"
    assert explorer.element_label(FakeIcon(icon_classes="bi bi-pencil")) == "Modifier"
    assert explorer.element_label(FakeIcon(icon_classes="far fa-trash-alt")) == "Supprimer"
    assert explorer.element_label(FakeIcon(icon_classes="fas fa-eye")) == "Visualiser"
    assert explorer.element_label(FakeIcon(icon_classes="mdi mdi-magnify")) == "Recherche"
    assert explorer.element_label(FakeIcon(icon_classes="fa fa-download")) == "Exporter"


def test_icon_class_respects_text_priority():
    assert explorer.element_label(FakeIcon(text="Modifier", icon_classes="fa fa-eye")) == "Modifier"
    assert explorer.element_label(FakeIcon(text="", attrs={"aria-label": "Modifier"},
                                          icon_classes="fa fa-eye")) == "Modifier"


def test_icon_class_unknown_falls_back():
    assert explorer.element_label(FakeIcon(icon_classes="material-icons arrow-right")) == "Bouton #1"