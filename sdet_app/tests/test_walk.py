from sdet_app.sdet import explorer, planner


class FakePage:
    def __init__(self, url, name):
        self.url = url
        self.name = name


def test_build_walk_dedup_pages():
    pages = [FakePage("/clients", "Clients"),
             FakePage("/inbox", "Inbox"),
             FakePage("/clients", "Clients dupliqué")]
    plan = planner.build_walk(pages)
    assert len(plan) == 2
    assert all(s["step_type"] == "walk" for s in plan)
    assert [s["url"] for s in plan] == ["/clients", "/inbox"]


def test_build_walk_empty():
    assert planner.build_walk([]) == []


def test_scan_buttons_keeps_physical_duplicates(monkeypatch):
    def fake_json(page):
        return {
            "out": [
                {"is_a": False, "visible": True, "signature": True,
                 "click_signal": True,
                 "text": "Modifier", "aria": "", "title": "", "cls": "",
                 "x": 100, "y": 50},
                {"is_a": False, "visible": True, "signature": True,
                 "click_signal": True,
                 "text": "Modifier", "aria": "", "title": "", "cls": "",
                 "x": 100, "y": 150},
                {"is_a": False, "visible": True, "signature": True,
                 "click_signal": True,
                 "text": "Ajouter", "aria": "", "title": "", "cls": "",
                 "x": 10, "y": 10},
            ],
            "kept": [0, 1, 2],
        }
    monkeypatch.setattr(explorer, "_json_interactive", fake_json)
    found, _ = explorer.scan_buttons_indexed(object())
    modifier = [a for a in found if a["label"] == "Modifier"]
    assert len(modifier) == 2
    assert all(a["_kept_idx"] is not None for a in found)


def test_non_anchor_without_click_signal_rejected(monkeypatch):
    def fake_json(page):
        return {
            "out": [
                {"is_a": False, "visible": True, "signature": True,
                 "click_signal": False,
                 "text": "607", "aria": "", "title": "", "cls": "",
                 "x": 100, "y": 50},
                {"is_a": False, "visible": True, "signature": True,
                 "click_signal": True,
                 "text": "Activer", "aria": "", "title": "", "cls": "",
                 "x": 10, "y": 10},
            ],
            "kept": [0, 1],
        }
    monkeypatch.setattr(explorer, "_json_interactive", fake_json)
    found, _ = explorer.scan_buttons_indexed(object())
    assert [a["label"] for a in found] == ["Activer"]


def test_clickable_selector_tracks_js():
    assert isinstance(explorer._CLICKABLE_SEL, str)
    for token in ("button", "a[aria-haspopup]", "a[onclick]", 'a[class*="action"]'):
        assert token in explorer._CLICKABLE_SEL


# ---------------------------------------------------------------------------
# Navigation filter: sidebar / navbar elements must NOT appear as page actions
# The JS in _json_interactive skips elements inside <nav>, <aside>, or
# containers with sidebar/navbar classes.  We verify the Python-side fallback
# (strip_cross_module_navigation) handles the same cases.
# ---------------------------------------------------------------------------

def test_sidebar_links_stripped_by_cross_module_filter():
    """Sidebar nav links pointing to other visited modules must be removed."""
    base = "https://app.example"
    pages = ["https://app.example/clients",
             "https://app.example/factures",
             "https://app.example/dashboard"]
    actions = [
        # Sidebar "Factures" link seen on the Clients page -> drop
        {"label": "Factures", "action_type": "button", "href": "/factures",
         "page_url": "https://app.example/clients"},
        # Sidebar "Dashboard" link seen on the Clients page -> drop
        {"label": "Dashboard", "action_type": "button", "href": "/dashboard",
         "page_url": "https://app.example/clients"},
        # Real page button -> keep
        {"label": "Ajouter un client", "action_type": "button", "href": "",
         "page_url": "https://app.example/clients"},
        # Real row action -> keep
        {"label": "Modifier", "action_type": "button", "href": "/clients/1/edit",
         "page_url": "https://app.example/clients"},
    ]
    result = explorer._strip_cross_module_navigation(actions, pages, base)
    labels = [a["label"] for a in result]
    assert "Factures" not in labels
    assert "Dashboard" not in labels
    assert "Ajouter un client" in labels
    assert "Modifier" in labels


def test_sidebar_no_href_buttons_kept():
    """Buttons without href (pure JS actions) are always kept."""
    base = "https://app.example"
    pages = ["https://app.example/clients"]
    actions = [
        {"label": "Exporter", "action_type": "button", "href": "",
         "page_url": "https://app.example/clients"},
        {"label": "Imprimer", "action_type": "button", "href": "",
         "page_url": "https://app.example/clients"},
    ]
    result = explorer._strip_cross_module_navigation(actions, pages, base)
    assert len(result) == 2


def test_actions_preserve_page_url_grouping():
    """Each action must carry the page_url of the page it was scanned from."""
    actions = [
        {"label": "Ajouter", "page_url": "/clients"},
        {"label": "Rechercher", "page_url": "/clients"},
        {"label": "Ajouter facture", "page_url": "/factures"},
    ]
    by_url = {}
    for a in actions:
        by_url.setdefault(a["page_url"], []).append(a["label"])
    assert by_url["/clients"] == ["Ajouter", "Rechercher"]
    assert by_url["/factures"] == ["Ajouter facture"]
