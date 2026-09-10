"""Unit tests for the Option C generator (sdet/generator.py)."""
from sdet_app.sdet.generator import (
    _field_value, _norm, _suffix, _CREATE_ICONS, _CREATE_WORDS,
)


def test_suffix_is_unique_enough():
    a = _suffix()
    assert a.isdigit() and len(a) >= 6


def test_norm():
    assert _norm("Libellé Écran") == "libelle ecran"
    assert _norm("  NOM  ") == "nom"


def test_create_words_present():
    assert any(w in _CREATE_WORDS for w in ("ajouter", "créer", "new"))


def test_icons_present():
    assert "fa-plus" in _CREATE_ICONS


def test_name_field_gets_entity():
    field = {"tag": "input", "type": "text", "label": "Nom *",
             "required": True, "choices": []}
    kind, value = _field_value(field, "011509", "QA_CATEGORIE_011509")
    assert kind == "fill"
    assert value == "QA_CATEGORIE_011509"


def test_email_field():
    field = {"tag": "input", "type": "email", "label": "Email",
             "required": True, "choices": []}
    kind, value = _field_value(field, "011509", "QA_CATEGORIE_011509")
    assert kind == "fill"
    assert value == "qa011509@example.test"


def test_select_takes_first_choice():
    field = {"tag": "select", "type": "", "label": "Type",
             "required": False, "choices": ["", "AXE", "B"]}
    kind, value = _field_value(field, "011509", "QA_CATEGORIE_011509")
    assert kind == "select"
    assert value == "AXE"


def test_checkbox():
    field = {"tag": "input", "type": "checkbox", "label": "Actif",
             "required": False, "choices": []}
    kind, value = _field_value(field, "011509", "QA_CATEGORIE_011509")
    assert kind == "check"
    assert value is True


def test_optional_unknown_field_skipped():
    field = {"tag": "input", "type": "text", "label": "Remarque",
             "required": False, "choices": []}
    assert _field_value(field, "011509", "QA_CATEGORIE_011509") is None


def test_required_unknown_field_gets_entity():
    field = {"tag": "input", "type": "text", "label": "Code client *",
             "required": True, "choices": []}
    kind, value = _field_value(field, "011509", "QA_CATEGORIE_011509")
    assert kind == "fill"
    assert value == "QA_CATEGORIE_011509"


# ---------------------------------------------------------------------------
# URL-based module / functionality discovery
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, all_hrefs=None):
        self.all_hrefs = all_hrefs or []

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script):
        if "getAllHrefs" in script or "scanAllHrefs" in script:
            # discover_functionalities reads all anchors for hrefs
            return self.all_hrefs
        return None


def _module_discovery_links():
    """A sidebar that mirrors the real app: dashboard (root), then /crm and
    /ims modules with their sub-pages, plus generic utility links."""
    return [
        {"label": "Tableau de bord", "href": "https://kpip.kprimesoft.com/", "tag": "a"},
        {"label": "Dashboard", "href": "https://kpip.kprimesoft.com/dashboard", "tag": "a"},
        {"label": "CRM", "href": "https://kpip.kprimesoft.com/crm", "tag": "a"},
        {"label": "Clients", "href": "https://kpip.kprimesoft.com/crm/clients", "tag": "a"},
        {"label": "Client interactions", "href": "https://kpip.kprimesoft.com/crm/client_interaction", "tag": "a"},
        {"label": "IMS", "href": "https://kpip.kprimesoft.com/ims", "tag": "a"},
        {"label": "Factures", "href": "https://kpip.kprimesoft.com/ims/factures", "tag": "a"},
        {"label": "Déconnexion", "href": "https://kpip.kprimesoft.com/logout", "tag": "a"},
        {"label": "Aide", "href": "https://kpip.kprimesoft.com/help", "tag": "a"},
    ]


def test_discover_modules_groups_by_first_path_segment(monkeypatch):
    from sdet_app.sdet import generator as g
    monkeypatch.setattr(g, "_expand_accordion_menus", lambda page: False)
    monkeypatch.setattr(g, "_nav_links", lambda page: _module_discovery_links())
    page = _FakePage()

    mods = g.discover_modules(page, "https://kpip.kprimesoft.com/")
    names = [(m["name"], m["url"]) for m in mods]
    assert ("CRM", "https://kpip.kprimesoft.com/crm") in names
    assert ("IMS", "https://kpip.kprimesoft.com/ims") in names
    # the dashboard, logout and help must NOT be reported as modules
    assert not any(url in ("https://kpip.kprimesoft.com/",
                           "https://kpip.kprimesoft.com/dashboard")
                   for _, url in names)
    assert not any("logout" in url or "help" in url for _, url in names)


def test_discover_modules_falls_back_to_labels_when_no_hrefs(monkeypatch):
    from sdet_app.sdet import generator as g
    monkeypatch.setattr(g, "_expand_accordion_menus", lambda page: False)
    monkeypatch.setattr(g, "_scan_menu_items", lambda page: [
        {"label": "CRM", "tag": "a", "href": "", "in_nav": True},
        {"label": "IMS", "tag": "a", "href": "", "in_nav": True},
        {"label": "Dashboard", "tag": "a", "href": "", "in_nav": True},
    ])
    page = _FakePage()

    mods = g.discover_modules(page, "https://kpip.kprimesoft.com/")
    labels = [m["name"] for m in mods]
    assert "CRM" in labels
    assert "IMS" in labels
    assert "Dashboard" not in labels


def test_discover_functionalities_only_lists_under_module_prefix(monkeypatch):
    from sdet_app.sdet import generator as g
    # Sidebar shows: dashboard + CRM subpages + IMS subpages
    monkeypatch.setattr(g, "_expand_accordion_menus", lambda page: False)
    monkeypatch.setattr(g, "_nav_links", lambda page: _module_discovery_links())
    # No extra anchors on the page body
    page = _FakePage(all_hrefs=[])

    fns = g.discover_functionalities(
        page, "https://kpip.kprimesoft.com/crm", "CRM")
    urls = [(f["name"], f["url"]) for f in fns]
    assert ("Clients", "https://kpip.kprimesoft.com/crm/clients") in urls
    assert ("Client interactions",
            "https://kpip.kprimesoft.com/crm/client_interaction") in urls
    # nothing from IMS, the dashboard, or utility pages leaks in
    assert not any("/ims" in u for _, u in urls)
    assert not any(u in ("https://kpip.kprimesoft.com/",
                         "https://kpip.kprimesoft.com/dashboard")
                   for _, u in urls)
    # the module home itself is not a functionality
    assert not any(u == "https://kpip.kprimesoft.com/crm" for _, u in urls)


def test_discover_functionalities_filters_action_links(monkeypatch):
    from sdet_app.sdet import generator as g
    monkeypatch.setattr(g, "_expand_accordion_menus", lambda page: False)
    monkeypatch.setattr(g, "_nav_links", lambda page: [
        {"label": "Clients", "href": "https://kpip.kprimesoft.com/crm/clients", "tag": "a"},
        {"label": "Nouveau", "href": "https://kpip.kprimesoft.com/crm/clients/new", "tag": "a"},
        {"label": "Import", "href": "https://kpip.kprimesoft.com/crm/clients/import", "tag": "a"},
    ])
    page = _FakePage(all_hrefs=[])

    fns = g.discover_functionalities(
        page, "https://kpip.kprimesoft.com/crm", "CRM")
    urls = [f["url"] for f in fns]
    assert "https://kpip.kprimesoft.com/crm/clients" in urls
    assert "https://kpip.kprimesoft.com/crm/clients/new" not in urls
    assert "https://kpip.kprimesoft.com/crm/clients/import" not in urls


def test_find_module_url_matches_label_and_percentages(monkeypatch):
    from sdet_app.sdet import generator as g
    monkeypatch.setattr(g, "_nav_links", lambda page: [
        {"label": "CRM", "href": "https://kpip.kprimesoft.com/crm", "tag": "a"},
        {"label": "Clients", "href": "https://kpip.kprimesoft.com/crm/clients", "tag": "a"},
    ])
    page = _FakePage()
    found = g.find_module_url(page, "CRM", "https://kpip.kprimesoft.com/")
    assert found["url"] == "https://kpip.kprimesoft.com/crm"


def test_find_module_url_ignores_hash_only_links(monkeypatch):
    """A nav link like 'https://kpip.kprimesoft.com/#' (href='#') must NEVER
    be chosen as the module URL — it has no real path segment. Otherwise the
    scan treats the whole host as 'under CRM'. A link whose leading segment
    really is the module (e.g. /crm/clients) remains a valid candidate."""
    from sdet_app.sdet import generator as g
    monkeypatch.setattr(g, "_nav_links", lambda page: [
        {"label": "CRM", "href": "https://kpip.kprimesoft.com/#", "tag": "a"},
        {"label": "Tableau de bord",
         "href": "https://kpip.kprimesoft.com/", "tag": "a"},
    ])
    page = _FakePage()
    # Only a hash link labelled CRM: never used, the caller derives /crm.
    assert g.find_module_url(page, "CRM")["url"] == ""
    # A real link under /crm wins over the hash link.
    monkeypatch.setattr(g, "_nav_links", lambda page: [
        {"label": "CRM", "href": "https://kpip.kprimesoft.com/#", "tag": "a"},
        {"label": "CRM", "href": "https://kpip.kprimesoft.com/crm", "tag": "a"},
        {"label": "Clients",
         "href": "https://kpip.kprimesoft.com/crm/clients", "tag": "a"},
    ])
    assert g.find_module_url(page, "CRM")["url"] == \
        "https://kpip.kprimesoft.com/crm"


def test_url_under_module_rejects_hash_only_module_url():
    """A hash/root-only module URL must not cover subpages of the host."""
    from sdet_app.sdet.generator import _url_under_module
    assert _url_under_module("https://kpip.kprimesoft.com/crm/clients",
                             "https://kpip.kprimesoft.com/#") is False
    assert _url_under_module("https://kpip.kprimesoft.com/",
                             "https://kpip.kprimesoft.com/#") is True


def test_scan_module_ignores_stored_hash_url_and_derives(monkeypatch):
    """Regression: a previously persisted module URL '.../#' must not be
    scanned as-is; the scan must derive and open https://.../crm instead."""
    from sdet_app.sdet import generator as g

    class P:
        url = "https://kpip.kprimesoft.com/crm"

        def wait_for_timeout(self, ms):
            pass

    gotos = []
    monkeypatch.setattr(g, "_goto_url", lambda pg, url: gotos.append(url) or True)
    monkeypatch.setattr(g, "find_module_url",
                        lambda pg, name, base: {"name": name, "url": ""})
    monkeypatch.setattr(g, "discover_functionalities", lambda *a, **k: [])
    monkeypatch.setattr(g, "_generate_from_page",
                        lambda pg, fname, **kw: (["CRÉATION", "Accueil"], None))
    monkeypatch.setattr(g, "_suffix", lambda: "999999")

    module = {"name": "CRM", "url": "https://kpip.kprimesoft.com/#"}
    project = {"url": "https://kpip.kprimesoft.com/"}
    results, err = g._scan_module_using_session(P(), project, module)
    assert err is None
    assert gotos == ["https://kpip.kprimesoft.com/crm"]
    assert results[0]["url"] == "https://kpip.kprimesoft.com/crm"


# ---------------------------------------------------------------------------
# Module scan must land inside the module before scanning anything
# ---------------------------------------------------------------------------

def test_scan_module_aborts_if_url_not_verified(monkeypatch):
    from sdet_app.sdet import generator as g

    class P:
        url = "https://kpip.kprimesoft.com/login"

        def wait_for_timeout(self, ms):
            pass

    page = P()
    gotos = []
    monkeypatch.setattr(g, "_goto_url", lambda pg, url: gotos.append(url) or True)

    def boom(*a, **k):
        raise AssertionError("discover_functionalities must not run if not in module")

    monkeypatch.setattr(g, "discover_functionalities", boom)

    module = {"name": "CRM", "url": "https://kpip.kprimesoft.com/crm"}
    results, err = g._scan_module_using_session(page, {}, module)
    assert gotos == ["https://kpip.kprimesoft.com/crm"]
    assert results == []
    assert err and "n'appartient pas au module" in err


def test_scan_module_aborts_when_no_module_url(monkeypatch):
    from sdet_app.sdet import generator as g

    class P:
        def wait_for_timeout(self, ms):
            pass

    monkeypatch.setattr(g, "find_module_url",
                        lambda page, name, base: {"name": name, "url": ""})
    # Named modules fall back to base_url + name; only when that is also
    # impossible (no module name / no base) should the scan abort.
    monkeypatch.setattr(g, "_build_module_url", lambda base, name: "")
    monkeypatch.setattr(g, "discover_functionalities",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not run")))
    results, err = g._scan_module_using_session(P(), {"url": ""}, "CRM")
    assert results == []
    assert err and "Impossible de déterminer" in err


def test_scan_module_starts_with_module_home_first(monkeypatch):
    from sdet_app.sdet import generator as g

    class P:
        url = "https://kpip.kprimesoft.com/crm"

        def wait_for_timeout(self, ms):
            pass

    monkeypatch.setattr(g, "_goto_url", lambda pg, url: True)
    monkeypatch.setattr(g, "discover_functionalities",
                        lambda pg, mu, ml: [
                            {"name": "Clients",
                             "url": "https://kpip.kprimesoft.com/crm/clients"},
                            {"name": "Client interactions",
                             "url": "https://kpip.kprimesoft.com/crm/client_interaction"},
                        ])
    monkeypatch.setattr(g, "_generate_from_page",
                        lambda pg, fname, **kw: (["CRÉATION", "Accueil"], None))
    monkeypatch.setattr(g, "_generate_for_functionality",
                        lambda pg, mn, fn, fu, suffix:
                        ["CRÉATION", "Remplir le champ Nom avec X"])
    monkeypatch.setattr(g, "_suffix", lambda: "999999")

    module = {"name": "CRM", "url": "https://kpip.kprimesoft.com/crm"}
    results, err = g._scan_module_using_session(P(), {}, module)
    assert err is None
    # The module's own page (its URL) is ALWAYS the first page scanned.
    assert [r["functionality"] for r in results] == \
        ["CRM", "Clients", "Client interactions"]
    assert results[0]["url"] == "https://kpip.kprimesoft.com/crm"
    assert results[1]["url"] == "https://kpip.kprimesoft.com/crm/clients"


def test_scan_module_derives_url_from_base_and_module_name(monkeypatch):
    """A module named 'IMS' on https://kpip.kprimesoft.com/ must open
    https://kpip.kprimesoft.com/ims first — derived from the module name."""
    from sdet_app.sdet import generator as g

    class P:
        url = "https://kpip.kprimesoft.com/ims"

        def wait_for_timeout(self, ms):
            pass

    gotos = []
    monkeypatch.setattr(g, "_goto_url", lambda pg, url: gotos.append(url) or True)
    monkeypatch.setattr(g, "find_module_url",
                        lambda pg, name, base: {"name": name, "url": ""})
    monkeypatch.setattr(g, "discover_functionalities", lambda *a, **k: [])
    monkeypatch.setattr(g, "_generate_from_page",
                        lambda pg, fname, **kw: (["CRÉATION", "Accueil"], None))
    monkeypatch.setattr(g, "_suffix", lambda: "999999")

    project = {"url": "https://kpip.kprimesoft.com/"}
    results, err = g._scan_module_using_session(P(), project, "IMS")
    assert err is None
    assert gotos == ["https://kpip.kprimesoft.com/ims"]
    assert results[0]["url"] == "https://kpip.kprimesoft.com/ims"
    assert results[0]["functionality"] == "IMS"


def test_build_module_url_from_name():
    from sdet_app.sdet.generator import _build_module_url
    assert _build_module_url("https://kpip.kprimesoft.com/", "IMS") == \
        "https://kpip.kprimesoft.com/ims"
    assert _build_module_url("https://kpip.kprimesoft.com/", "CRM") == \
        "https://kpip.kprimesoft.com/crm"
    assert _build_module_url("https://kpip.kprimesoft.com/", "crm/clients") == \
        "https://kpip.kprimesoft.com/crm/clients"
    assert _build_module_url("https://kpip.kprimesoft.com/",
                             "https://x.example/crm") == \
        "https://x.example/crm"


def test_module_url_roundtrip_persisted(_init_db):
    from sdet_app import database
    data = {"name": "P", "url": "https://x.test", "email": "", "password": "",
            "auth_type": "none", "environment": "", "comments": ""}
    pid = database.create_project(data, "admin@example.com", encrypt=lambda v: v)
    try:
        mid = database.create_module(pid, "CRM", "", "https://kpip.kprimesoft.com/crm")
        mod = database.get_module(mid)
        assert mod["url"] == "https://kpip.kprimesoft.com/crm"
        database.update_module(mid, "CRM", "desc", "https://kpip.kprimesoft.com/crm2")
        assert database.get_module(mid)["url"] == "https://kpip.kprimesoft.com/crm2"
    finally:
        database.delete_project(pid)


def test_scan_survives_single_functionality_iteration_error(monkeypatch):
    """Regression: a transient 'RuntimeError: dictionary changed size during
    iteration' raised while scanning ONE functionality must not abort the whole
    module scan. The failing screen falls back to generic steps and the scan
    continues with the other functionalities."""
    from sdet_app.sdet import generator as g

    class P:
        url = "https://kpip.kprimesoft.com/crm"

        def wait_for_timeout(self, ms):
            pass

    monkeypatch.setattr(g, "_goto_url", lambda pg, url: True)
    monkeypatch.setattr(g, "find_module_url",
                        lambda pg, name, base: {"name": name, "url": ""})
    monkeypatch.setattr(g, "_suffix", lambda: "999999")
    monkeypatch.setattr(g, "discover_functionalities",
                        lambda pg, mu, ml: [
                            {"name": "Clients",
                             "url": "https://kpip.kprimesoft.com/crm/clients"},
                            {"name": "Contrats",
                             "url": "https://kpip.kprimesoft.com/crm/contrats"},
                        ])
    monkeypatch.setattr(g, "_generate_from_page",
                        lambda pg, fname, **kw: (["CRÉATION", "Accueil"], None))

    def flaky(pg, mn, fn, fu, suffix):
        if fn == "Clients":
            raise RuntimeError("dictionary changed size during iteration")
        return ["CRÉATION", "Remplir le champ Nom avec QA_CONTRAT_999999"]

    monkeypatch.setattr(g, "_generate_for_functionality", flaky)

    module = {"name": "CRM", "url": "https://kpip.kprimesoft.com/crm"}
    results, err = g._scan_module_using_session(P(), {}, module)
    assert err is None
    assert [r["functionality"] for r in results] == \
        ["CRM", "Clients", "Contrats"]
    # The flaky screen still gets runnable (navigation fallback) steps.
    assert results[1]["steps"][0]["action_type"] == "VERIFIER"
    assert "clients se charge" in results[1]["steps"][0]["target"].lower()
    assert "QA_CONTRAT_999999" in str(results[2]["steps"][1])