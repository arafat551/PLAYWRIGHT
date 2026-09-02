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


def test_label_from_data_uses_child_icon_classes():
    assert explorer._label_from_data({
        "text": "", "aria": "", "title": "", "value": "",
        "data_tooltip": "", "cls": "row-edit", "icls": "row-edit fa fa-pencil",
    }) == "Modifier"
    assert explorer._label_from_data({
        "text": "", "aria": "", "title": "", "value": "",
        "data_tooltip": "", "cls": "row-del", "icls": "row-del fas fa-trash-alt",
    }) == "Supprimer"


def test_action_anchor_data_accepts_icon_only_link():
    d = {"href": "/clients/1", "onclick": "", "data_action": "",
         "aria": "", "title": "", "text": "",
         "cls": "row-view", "icls": "row-view fa fa-eye"}
    assert explorer._is_action_anchor_data("Visualiser", d)


def test_action_anchor_data_rejects_plain_nav_link():
    d = {"href": "/about", "onclick": "", "data_action": "",
         "aria": "", "title": "", "text": "À propos",
         "cls": "", "icls": "", }
    assert not explorer._is_action_anchor_data("À propos", d)


class FakeLinkPage:
    def __init__(self, links):
        self.links = links

    def eval_on_selector_all(self, _sel, _js):
        return [{"href": h, "text": "x"} for h in self.links]


def test_find_links_normalized_dedupe_skip_utilities():
    page = FakeLinkPage([
        "https://app.example/crm/clients?page=1",
        "https://app.example/crm/clients?page=2#frag",
        "https://app.example/locale/fr",
        "https://app.example/language/en",
        "https://app.example/crm/clients/42/edit",
        "https://other.example/x",
        "https://app.example/reports.pdf",
        "https://app.example/inbox",
        "https://app.example/settings?tab=2",
    ])
    links = explorer.find_links(page, "app.example",
                                "https://app.example/crm/clients")
    assert links == ["https://app.example/inbox",
                     "https://app.example/settings"]


def test_find_links_keeps_same_page_twice_only_once():
    page = FakeLinkPage([
        "https://app.example/crm/clients",
        "https://app.example/crm/clients/",
        "https://app.example/crm/clients#top",
    ])
    links = explorer.find_links(page, "app.example",
                                "https://app.example/dashboard")
    assert links == ["https://app.example/crm/clients"]


class FakeH1Page:
    def __init__(self, h1=None, title=""):
        self._h1 = h1
        self._title = title

    def query_selector(self, _sel):
        if self._h1 is None:
            return None
        return FakeIcon(text=self._h1)

    def title(self):
        return self._title


def test_page_title_falls_back_when_h1_is_image_alt():
    assert explorer._page_title(
        FakeH1Page("img62a88a533cd65.jpg (200×200)", "Tableau de bord"),
        "https://app.example/") == "Tableau de bord"
    assert explorer._page_title(
        FakeH1Page("Clients", "Clients - KPIP"),
        "https://app.example/crm/clients") == "Clients"


def test_strip_cross_module_navigation_removes_other_module_links():
    base = "https://app.example"
    page_urls = ["https://app.example/crm/orders",
                 "https://app.example/crm/clients",
                 "https://app.example/crm/notifications"]
    actions = [
        # Nav link to ANOTHER module -> must be dropped
        {"label": "Clients", "action_type": "button", "href": "/crm/clients",
         "page_url": "https://app.example/crm/orders"},
        # Button (no href) -> kept
        {"label": "Ajouter", "action_type": "button", "href": "",
         "page_url": "https://app.example/crm/orders"},
        # Link to same page (current module) -> kept
        {"label": "Commandes", "action_type": "button", "href": "/crm/orders",
         "page_url": "https://app.example/crm/orders"},
        # Record link with numeric id (not a page) -> kept
        {"label": "Voir les détails", "action_type": "button", "href": "/crm/orders/42",
         "page_url": "https://app.example/crm/orders"},
    ]
    result = explorer._strip_cross_module_navigation(actions, page_urls, base)
    labels = [a["label"] for a in result]
    assert labels == ["Ajouter", "Commandes", "Voir les détails"]


def test_strip_cross_module_navigation_keeps_button_actions():
    base = "https://app.example"
    page_urls = ["https://app.example/crm/orders", "https://app.example/crm/clients"]
    actions = [
        {"label": "Modifier les détails", "action_type": "button", "href": "",
         "page_url": "https://app.example/crm/orders"},
        {"label": "Ajouter", "action_type": "button", "href": "",
         "page_url": "https://app.example/crm/orders"},
    ]
    result = explorer._strip_cross_module_navigation(actions, page_urls, base)
    assert len(result) == 2
